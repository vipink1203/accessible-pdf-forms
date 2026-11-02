"""Turn the detected fields into a guided interview: plain questions, asked only when they apply,
each backed by a quote from the form or its instructions.

Muse writes the interview once per form, from the field list and the form's own text; no answers
are sent. Every quote is checked word for word against the PDF text, and every field must be
either asked about or skipped with a reason, so the interview cannot invent a rule or drop a field.

    uv run interview.py
"""
from __future__ import annotations

import base64
import json
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

from spike import MODEL, OUT, PRICE_PER_M, SRC, load_key

MAX_CALLS = 2
MAX_OUT = 100_000  # tokens; the W-4 (50 fields) took 30,264, so 150 fields may need about 90,000

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "intro", "steps", "skipped"],
    "properties": {
        "title": {"type": "string"},
        "intro": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "question", "help", "source", "answer", "required", "choices", "fields", "show_if"],
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "help": {"type": "string"},
                    "source": {"type": "string"},
                    "answer": {"type": "string", "enum": ["choice", "text", "digits", "confirm"]},
                    "required": {"type": "boolean"},
                    "choices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["label", "value", "field", "strike"],
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "field": {"type": "integer"},
                                "strike": {"type": "string"},
                            },
                        },
                    },
                    "fields": {"type": "array", "items": {"type": "integer"}},
                    "show_if": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["step", "values"],
                        "properties": {
                            "step": {"type": "string"},
                            "values": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "skipped": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "reason"],
                "properties": {"field": {"type": "integer"}, "reason": {"type": "string"}},
            },
        },
    },
}

PROMPT = """A blind person will fill in this form with a screen reader, one question at a time.
Write the interview that replaces reading the whole form.

FIELDS, by id (page, kind, digit cells if it is a row of digit boxes, tick-box group, the form's words for it):
{fields}

FORM TEXT, the pages with fields:
{page1}

INSTRUCTIONS, the form's own instruction pages:
{instructions}

Rules:
- Ask in the order a person would answer, the fewest questions that fill every field that applies to them.
- question: plain words, at most 20 words, second person ("What is your name?").
- help: at most 3 short sentences that say what to enter, using only facts stated in the FORM TEXT
  or INSTRUCTIONS above. No advice beyond them. Empty string if the question needs none.
  The person can't see the page, so when the form attaches a note, an exception or an "instead" to an
  entry or to one of its choices (who must pick a different box, who must leave it blank), the help says it.
- source: one sentence or phrase copied character for character from the FORM TEXT or INSTRUCTIONS
  that backs the question and help. Copy, never paraphrase.
- answer:
  "choice" picks one option; each choice names the field it fills (a tick box is ticked; a text
  field gets the choice's value) or field -1 when it fills nothing;
  "text" writes the answer into the one field in fields;
  "digits" is a number typed in one go and split across the digit-box fields listed in fields, in order;
  "confirm" asks the person to confirm they have heard and agree to text they must certify;
  put the full certification wording in help so it is read to them before they sign.
- required: true only when the form says the entry is required for this person.
- show_if: ask this step only when the answer to an earlier step is one of values (use the choice
  values); step "" and values [] means always ask.
  When the form or its instructions say an entry is only for some people (for example "not individuals"
  or "only if"), ask it only of them with show_if, so nobody hears a question the form says isn't theirs.
- Crossing out: when the form tells the person to cross out or strike printed text in some case, ask
  whether that case applies to them, as a choice. On the choice where it applies, set strike to the exact
  printed text to cross out, copied character for character from the FORM TEXT, with field -1.
  Every other choice has strike "".
- Every field id must appear in exactly one step (in fields or in a choice) or in skipped.
  Skip a field only when the filler never writes there (for example, a box the requester fills, or one marked
  for office use only), with the reason.
- Signature: if the form has a place to sign, ask the person to type their full name as their signature,
  after any certification.
- title: the form's name; intro: one or two sentences telling the person what this form is for and how
  many questions to expect, from the FORM TEXT."""


def norm(text: str) -> str:
    """Compare quotes the way a listener would hear them: same words, any quote marks, any spacing."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                                         "\u2013": "-", "\u2014": "-", "\u00ad": ""}))
    text = re.sub(r"-\s*\n\s*", "", text)  # words hyphenated across lines
    return re.sub(r"\s+", " ", text).strip().lower()


def form_text() -> tuple[str, str]:
    doc = pymupdf.open(SRC)
    return doc[0].get_text(), "\n".join(p.get_text() for p in doc[1:])


def listing(fields: list[dict]) -> str:
    def line(i: int, f: dict) -> str:
        extra = f", {f['cells']} digit cells" if f.get("cells") else ""
        extra += f", group \"{f['group']}\"" if f.get("group") else ""
        return f"{i}: page {f.get('page', 0) + 1}, {f['kind']}{extra}, \"{f['label']}\""
    return "\n".join(line(i, f) for i, f in enumerate(fields))


def generate(fields: list[dict], page1: str, instructions: str, cache: Path = OUT / "interview-raw.json",
             pictures: list[bytes] = ()) -> dict:
    """Muse Spark's interview, cached in `cache`. `pictures` are the pages with every field outlined
    and numbered by id, so it can tell which field is which when the form's words around it are unclear."""
    if cache.exists():
        print(f"reusing cached {cache.name} (no API call)")
        return json.loads(cache.read_text())
    from openai import OpenAI

    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=load_key())
    prompt = PROMPT.format(fields=listing(fields), page1=page1, instructions=instructions)
    if pictures:
        prompt += ("\n\nThe pictures show each page with fields, every field outlined in red with its id beside it. "
                   "Where the form's words for a field above are unclear, use the picture to see what it asks for.")
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}
        for png in pictures]
    spent = 0.0
    for attempt in range(1, MAX_CALLS + 1):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "interview", "schema": SCHEMA, "strict": True}},
            max_completion_tokens=MAX_OUT,
        )
        u, finish = resp.usage, resp.choices[0].finish_reason
        cost = (u.prompt_tokens * PRICE_PER_M["input"] + u.completion_tokens * PRICE_PER_M["output"]) / 1e6
        spent += cost
        print(f"interview call {attempt}: {u.prompt_tokens} in, {u.completion_tokens} out, {finish}, "
              f"${cost:.4f} (total ${spent:.4f})", file=sys.stderr, flush=True)
        if finish == "length":  # the same budget would run out again
            raise ValueError(f"interview cut off at {MAX_OUT} tokens (${spent:.4f} spent)")
        try:
            result = json.loads(resp.choices[0].message.content or "")
        except json.JSONDecodeError:
            continue
        result["_usage"] = {"model": resp.model, "calls": attempt, "prompt_tokens": u.prompt_tokens,
                            "completion_tokens": u.completion_tokens, "cost_usd": round(spent, 5)}
        cache.write_text(json.dumps(result, indent=2))
        return result
    raise ValueError(f"no valid JSON after {MAX_CALLS} calls (${spent:.4f} spent)")


def add_strikes(interview: dict, fields: list[dict], doc: pymupdf.Document) -> list[str]:
    """Turn each choice that crosses out printed text into a field of its own, kind "strike", placed
    on the lines of that text, so picking the choice draws a line through it like a pen would.
    Text that can't be found on a page with fields is not struck, and the choice says so in the notes."""
    notes, pages = [], sorted({f.get("page", 0) for f in fields})
    for s in interview["steps"]:
        for c in s["choices"]:
            text = " ".join(c.pop("strike", "").split())
            if not text:
                continue
            hit = next(((n, rects) for n in pages if (rects := doc[n].search_for(text))), None)
            if not hit:
                notes.append(f"{s['id']}: text to cross out is not on the form, not struck: {text[:60]!r}")
                continue
            n, rects = hit
            box = pymupdf.Rect(rects[0])
            for r in rects[1:]:
                box |= r
            c["field"] = len(fields)
            fields.append({"label": f"Cross out: {text[:100]}", "kind": "strike", "page": n, "group": "",
                           "rect": [round(v, 1) for v in box], "cells": 0, "widget": 0,
                           "lines": [[round(v, 1) for v in r] for r in rects]})
    return notes


def check(interview: dict, fields: list[dict], page1: str, instructions: str) -> list[str]:
    """Problems that make the interview untrustworthy; an empty list means it passed."""
    problems = []
    corpus = norm(page1 + "\n" + instructions)
    ids = {s["id"] for s in interview["steps"]}
    seen: dict[int, str] = {}
    for s in interview["steps"]:
        if norm(s["source"]) not in corpus:
            problems.append(f"{s['id']}: source is not word for word in the form: {s['source'][:80]!r}")
        if s["answer"] == "confirm" and norm(s["help"]) not in corpus:
            problems.append(f"{s['id']}: the text to certify is not word for word in the form")
        if s["show_if"]["step"] and s["show_if"]["step"] not in ids:
            problems.append(f"{s['id']}: show_if names unknown step {s['show_if']['step']!r}")
        used = list(s["fields"]) + [c["field"] for c in s["choices"] if c["field"] >= 0]
        for f in used:
            if not 0 <= f < len(fields):
                problems.append(f"{s['id']}: unknown field {f}")
            elif f in seen and seen[f] != s["id"]:
                problems.append(f"field {f} is filled by both {seen[f]} and {s['id']}")
            else:
                seen[f] = s["id"]
        if s["answer"] == "choice" and not s["choices"]:
            problems.append(f"{s['id']}: choice step has no choices")
        if s["answer"] in ("text", "digits") and not s["fields"]:
            problems.append(f"{s['id']}: {s['answer']} step fills no field")
        if s["answer"] == "digits" and any(not fields[f].get("cells") for f in s["fields"] if 0 <= f < len(fields)):
            problems.append(f"{s['id']}: digits step uses a field with no digit cells")
    for k in interview["skipped"]:
        if k["field"] in seen:
            problems.append(f"field {k['field']} is both asked ({seen[k['field']]}) and skipped")
        seen.setdefault(k["field"], "skipped")
    for i in range(len(fields)):
        if i not in seen:
            problems.append(f"field {i} ({fields[i]['label'][:40]}) is neither asked nor skipped")
    return problems


def repair(interview: dict, fields: list[dict], page1: str, instructions: str) -> list[str]:
    """Fix in place whatever check() would reject, so any uploaded form gets an interview that fills
    every field and quotes only the form's own words. Returns what was changed.

    A quote that is not word for word is dropped rather than kept as if it were the form's. A field
    the interview leaves out gets a plain question of its own, built from the form's words for it,
    so nothing on the form is silently left blank."""
    notes, corpus, seen = [], norm(page1 + "\n" + instructions), set()
    steps, ids = [], set()
    for s in interview["steps"]:
        valid = lambda f: 0 <= f < len(fields) and f not in seen
        s["fields"] = [f for f in s["fields"] if valid(f)]
        for c in s["choices"]:
            c["field"] = c["field"] if valid(c["field"]) else -1
        if s["answer"] == "digits" and any(not fields[f].get("cells") for f in s["fields"]):
            s["answer"], s["fields"] = "text", s["fields"][:1]
        if (s["answer"] == "choice" and not s["choices"]) or (s["answer"] in ("text", "digits") and not s["fields"]):
            notes.append(f"{s['id']}: dropped, it filled nothing")
            continue
        if s["source"] and norm(s["source"]) not in corpus:
            notes.append(f"{s['id']}: source not word for word, dropped")
            s["source"] = ""
        if s["answer"] == "confirm" and norm(s["help"]) not in corpus:
            if s["source"]:
                s["help"] = s["source"]
            else:
                notes.append(f"{s['id']}: the text to certify is not word for word in the form")
        while s["id"] in ids:
            s["id"] += "_2"
        ids.add(s["id"])
        seen.update(s["fields"])
        seen.update(c["field"] for c in s["choices"] if c["field"] >= 0)
        steps.append(s)
    for s in steps:
        if s["show_if"]["step"] and s["show_if"]["step"] not in ids:
            s["show_if"] = {"step": "", "values": []}
    interview["skipped"] = [k for k in interview["skipped"] if 0 <= k["field"] < len(fields) and k["field"] not in seen]
    seen.update(k["field"] for k in interview["skipped"])
    added = []
    for i, f in enumerate(fields):
        if i in seen:
            continue
        label = " ".join(f["label"].split())[:120]
        step = {"id": f"field_{i}", "help": "", "source": "", "required": False, "choices": [], "fields": [i],
                "show_if": {"step": "", "values": []}}
        if f["kind"] == "checkbox":
            step |= {"question": f'Should I tick "{label}"?', "answer": "choice", "fields": [],
                     "choices": [{"label": "Yes", "value": "yes", "field": i}, {"label": "No", "value": "no", "field": -1}]}
        else:
            step |= {"question": f'What should I write for "{label}"?', "answer": "digits" if f.get("cells") else "text"}
        added.append(step)
        notes.append(f"field {i}: not in the interview, asked on its own")
    # Asked before any certification and signature, which come last on a form.
    at = next((k for k, s in enumerate(steps) if s["answer"] == "confirm"), len(steps))
    interview["steps"] = steps[:at] + added + steps[at:]
    return notes


def main() -> None:
    # Fields to cross out come from the interview, so they are made again on every run.
    fields = [f for f in json.loads((OUT / "fields.json").read_text()) if f["kind"] != "strike"]
    page1, instructions = form_text()
    interview = generate(fields, page1, instructions)
    problems = add_strikes(interview, fields, pymupdf.open(OUT / "fw9-flat.pdf"))
    (OUT / "fields.json").write_text(json.dumps(fields, indent=2))
    problems += check(interview, fields, page1, instructions)
    for p in problems:
        print("FAIL", p)
    corpus = norm(page1 + "\n" + instructions)
    for s in interview["steps"]:
        s["help_verbatim"] = bool(s["help"]) and norm(s["help"]) in corpus
    steps = interview["steps"]
    print(f"{len(steps)} steps, {sum(not s['show_if']['step'] for s in steps)} always asked, "
          f"{len(interview['skipped'])} fields skipped, {len(problems)} problems; help text word for word "
          f"from the form in {sum(s['help_verbatim'] for s in steps)} of {sum(bool(s['help']) for s in steps)} steps")
    out = {k: v for k, v in interview.items() if k != "_usage"}
    out["fields"] = [{"label": f["label"], "kind": f["kind"], "cells": f.get("cells", 0)} for f in fields]
    out["problems"] = problems
    (OUT / "interview.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

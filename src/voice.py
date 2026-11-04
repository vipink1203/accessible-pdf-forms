"""Hear one spoken reply: Muse Voice Transcribe turns the audio into text, then Muse Spark turns
the text into one action for the current question (an answer, yes, no, repeat, back, skip, ...).

The audio is transcribed twice at once. The transcriber's mistakes are partly random (a doubled word,
a misheard name), so two attempts rarely make the same one, and Muse Spark reads both. On real
accented speech this halved word errors (see accents.py); it costs one more transcription, not one
more Muse Spark call.

Audio is a mono 16-bit WAV at 16 or 24 kHz. Both calls use Meta's standard tier, which does not
train on the content. Nothing is written to disk unless VOICE_CACHE names a folder: tests use it
to keep Muse Spark's replies, keyed by transcript, so a re-run pays only for transcription.

    uv run voice.py voice-samples/ssn-en.wav en ssn
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from spike import MODEL, OUT, PRICE_PER_M, load_key

URL = "https://api.meta.ai/v1/asr/transcribe"
ASR_MODEL = "muse-voice-transcribe-1.0"
PRICE_PER_HOUR = 0.18
ACTIONS = ["answer", "yes", "no", "repeat", "back", "skip", "explain", "change", "pause", "unclear"]
CACHE = Path(os.environ["VOICE_CACHE"]) if os.environ.get("VOICE_CACHE") else None


def transcribe(wav: bytes, language: str, keywords: list[str] | None = None) -> dict:
    """Return {"transcript", "audioDurationMs", ...}; language is a name such as "Spanish"."""
    request = {"mode": "PUSH_TO_TALK", "model": ASR_MODEL, "audioEncoding": "WAV", "languageBias": [language]}
    if keywords:
        request["keywords"] = keywords
    response = httpx.post(
        URL,
        headers={"Authorization": f"Bearer {load_key()}"},
        files={"request": (None, json.dumps(request), "application/json"), "audio": ("answer.wav", wav, "audio/wav")},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "value", "step"],
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "value": {"type": "string"},
        "step": {"type": "string"},
    },
}

PROMPT = """You are the ears of a voice assistant that helps a blind person fill in an English paper form.
The person speaks {language}, with any accent or dialect, possibly as a second language. Speech-to-text turned their
reply into the text below, and it makes mistakes, most of all with accents and names: words can come out as
similar-sounding words, be repeated, or be cut short. Use the question to decide what they most likely meant
("sold proprietor" for a tax classification is "sole proprietor"), and return one action.

Actions:
- answer: they gave an answer to the question. Put it in value.
- yes / no: only when the assistant read an answer back, asked them to certify, or asked to fill in the form.
  If they reject a read-back and give a new answer in the same reply, use answer with the new value instead.
  For a question whose choices are yes and no, use answer with the value.
- repeat: they want to hear the question again.
- back: they want the previous question.
- skip: they want to leave this question blank or move on without answering.
- explain: they want more explanation or the form's exact words.
- pause: they want to stop for now or take a break ("stop", "wait", "hold on", "pause").
- change: only while reviewing; they name an answer to change. Put that question's id in step.
- unclear: anything else, including an answer that does not fit the question (a digit count that does not fit
  is still an answer).

How to write value for an answer:
- choice: exactly one of the listed values (the value, not the label), or use unclear. The choices are read aloud
  with numbers, so a number picks that choice ("two", "number 2", "the second one"). Use answer only when the reply
  names, describes or numbers one choice. Speech-to-text also picks up other people talking nearby: a sentence about
  something else that merely shares a word with a choice ("another" and "Other") is unclear, never a guess.
- digits: only the digits 0 to 9 in the order spoken. "double five" is 55, "oh" is 0. Never add or drop a digit,
  and give every digit you heard even when the count looks wrong: the assistant checks the count itself.
- text: what to write on the form, as the person said it: drop lead-ins such as "my name is" or "it's",
  keep names and places as spoken with normal capitals, write numbers as numerals, write dates as MM/DD/YYYY
  (today is {today}), no full stop at the end. Do not translate names or addresses. Drop a word the speech-to-text
  repeated by mistake ("Rivera Rivera"). When they spell a word letter by letter ("N G U Y E N", "N as in Nancy"),
  write the spelled word.
Leave value empty for every other action, and step empty unless the action is change.

{context}

{reply}"""


def reply(transcripts: list[str]) -> str:
    if len(set(transcripts)) == 1:
        return f'The person\'s reply: "{transcripts[0]}"'
    return ("Two speech-to-text attempts of the same reply; where they differ, decide what was most likely said:\n"
            + "\n".join(f'{i}. "{t}"' for i, t in enumerate(transcripts, 1)))


def context(form: dict, mode: str, step_id: str, pending: str) -> str:
    """Describe what the assistant just asked, from the server's own copy of the interview."""
    if mode == "review":
        lines = [f"- {s['id']}: {s['question_en'] if 'question_en' in s else s['question']}" for s in form["steps"]]
        return ("The assistant read all answers back and asked: should I fill in the form now, "
                "or which answer do you want to change? yes means fill it in. The questions:\n" + "\n".join(lines))
    s = next(s for s in form["steps"] if s["id"] == step_id)
    question = s.get("question_en", s["question"])
    if s["answer"] == "confirm":
        return f'The assistant read out a certification and asked: "{question}" yes means they certify.'
    out = f'The question: "{question}"\nAnswer kind: {s["answer"]}'
    if s["answer"] == "choice":
        out += "\nChoices (number. value: label):\n" + "\n".join(
            f"- {n}. {c['value']}: {c.get('label_en', c['label'])} / {c['label']}" for n, c in enumerate(s["choices"], 1))
    if s["answer"] == "digits":
        out += f"\nIt needs {sum(form['fields'][f]['cells'] for f in s['fields'])} digits."
    if mode == "confirming":
        out += f'\nThe assistant read back "{pending}" and asked if that is right.'
    return out


def understand(transcripts: list[str], form: dict, mode: str, step_id: str, pending: str = "") -> dict:
    from openai import OpenAI

    prompt = PROMPT.format(language=form["lang"]["name"], today=datetime.date.today().strftime("%m/%d/%Y"),
                           context=context(form, mode, step_id, pending), reply=reply(transcripts))
    resp = OpenAI(base_url="https://api.meta.ai/v1", api_key=load_key()).chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": "reply", "schema": SCHEMA, "strict": True}},
        reasoning_effort="minimal",
        max_completion_tokens=2000,
    )
    u = resp.usage
    result = json.loads(resp.choices[0].message.content or "{}")
    result["cost"] = (u.prompt_tokens * PRICE_PER_M["input"] + u.completion_tokens * PRICE_PER_M["output"]) / 1e6
    return result


def checked(result: dict, form: dict, mode: str, step_id: str) -> dict:
    """Keep only actions and values the current question can take."""
    action, value = result.get("action"), " ".join(str(result.get("value", "")).split())[:200]
    if action not in ACTIONS:
        return {"action": "unclear", "value": "", "step": ""}
    if action == "change":
        ids = {s["id"] for s in form["steps"]}
        return {"action": "change", "value": "", "step": result["step"]} if result.get("step") in ids else \
            {"action": "unclear", "value": "", "step": ""}
    if mode == "question" and action in ("yes", "no"):
        s = next(s for s in form["steps"] if s["id"] == step_id)
        action, value = ("answer", action) if action in {c["value"] for c in s["choices"]} else ("unclear", "")
    if action == "answer" and mode != "review":
        s = next(s for s in form["steps"] if s["id"] == step_id)
        if s["answer"] == "choice" and value not in {c["value"] for c in s["choices"]}:
            return {"action": "unclear", "value": "", "step": ""}
        if s["answer"] == "digits":
            value = "".join(ch for ch in value if ch.isdigit())
        if s["answer"] == "confirm" or not value:
            return {"action": "unclear", "value": "", "step": ""}
    return {"action": action, "value": value if action == "answer" else "", "step": ""}


DIGIT_WORDS = {w: str(i) for i, ws in enumerate([
    "zero oh cero", "one uno una", "two dos", "three tres", "four cuatro", "five cinco", "six seis", "seven siete",
    "eight ocho", "nine nueve"]) for w in ws.split()}
REPEATS = {"double": 2, "doble": 2, "triple": 3}


def spoken_digits(transcript: str) -> str | None:
    """The digits of a reply that is nothing but digits ("one two three, double five" is 12355), else None.

    Muse Spark reads every other reply, but it can quietly add a digit to reach the count the question
    needs, which would defeat the count check; a reply made only of digits needs no reading."""
    out, repeat = [], 1
    for w in plain(transcript).split():
        if w in REPEATS:
            repeat = REPEATS[w]
            continue
        digits = DIGIT_WORDS.get(w, w if w.isdigit() else None)
        if digits is None:
            return None
        out.append(digits[:1] * repeat + digits[1:])
        repeat = 1
    return "".join(out) or None


def plain(text: str) -> str:
    """Lower case, no accents or punctuation: "Sí." and "si" match."""
    text = "".join(ch for ch in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(ch))
    return " ".join("".join(ch for ch in text if ch.isalnum() or ch.isspace() or ch == "'").split())


def yes_or_no(transcript: str, form: dict) -> str | None:
    """Answer a plain yes or no without a model call."""
    said, yes = plain(transcript), {plain(w) for w in form["yes_words"]}
    words = said.split()
    if said in yes or (words and words[0] in yes and len(words) <= 3 and "no" not in words[1:]):
        return "yes"
    if words[:1] == ["no"] and len(words) <= 2:
        return "no"
    return None


def same_digits(digits: list[str]) -> str:
    """The digits every hearing agrees on, or "".

    The transcriber sometimes says one digit twice ("seven eight eight nine"); when one hearing is
    the other with one digit doubled, the shorter is what was said."""
    values = set(digits)
    if len(values) == 1:
        return digits[0]
    if len(values) == 2:
        short, long = sorted(values, key=len)
        if len(long) == len(short) + 1 and any(
                long[i] == long[i - 1] and long[:i] + long[i + 1:] == short for i in range(1, len(long))):
            return short
    return ""


def hear(wav: bytes, form: dict, mode: str, step_id: str, pending: str = "") -> dict:
    """One spoken reply to {transcript, action, value, step, ms, cost}."""
    started = time.monotonic()
    keywords, step = None, next((s for s in form["steps"] if s["id"] == step_id), {})
    if mode == "question":
        keywords = [c["label"] for c in step["choices"] if len(c["label"]) <= 30] or None  # the API's limit
    with ThreadPoolExecutor(2) as pool:
        heard = list(pool.map(lambda _: transcribe(wav, form["lang"]["name"], keywords), range(2)))
    cost = sum((h.get("audioDurationMs") or 0) / 3_600_000 * PRICE_PER_HOUR for h in heard)
    transcripts = [t for h in heard if (t := h.get("transcript", "").strip())]
    transcript = transcripts[0] if transcripts else ""
    quick = {yes_or_no(t, form) for t in transcripts}
    digits = [d for t in transcripts if (d := spoken_digits(t))] if step.get("answer") in ("digits", "choice") else []
    picked = {int(d) for d in digits if 1 <= int(d) <= len(step.get("choices", []))} if step.get("answer") == "choice" else set()
    if not transcripts:  # the page heard sound, but there were no words in it
        result = {"action": "unclear", "value": "", "step": ""}
    elif mode == "question" and len(digits) == len(transcripts) and len(picked) == 1 and len(set(digits)) == 1:
        result = {"action": "answer", "value": step["choices"][picked.pop() - 1]["value"], "step": ""}  # "two" picks choice 2
    elif mode in ("confirming", "certify", "review") and len(quick) == 1 and None not in quick:
        result = {"action": quick.pop(), "value": "", "step": ""}
    elif (mode in ("question", "confirming") and step.get("answer") == "digits" and len(digits) == len(transcripts)
          and (agreed := same_digits(digits))):
        result = {"action": "answer", "value": agreed, "step": ""}
    elif mode in ("question", "confirming") and step.get("answer") == "digits" and len(digits) == len(transcripts) > 1:
        result = {"action": "unclear", "value": "", "step": ""}  # the two hearings disagree on the digits: never pick one
    else:
        today = datetime.date.today().isoformat()  # "today" means a different date tomorrow
        key = hashlib.sha256(json.dumps([sorted(transcripts), form["lang"]["code"], mode, step_id, pending, today]).encode())
        cached = CACHE / f"{key.hexdigest()[:24]}.json" if CACHE else None
        if cached and cached.exists():
            raw = json.loads(cached.read_text())
        else:
            raw = understand(transcripts, form, mode, step_id, pending)
            cost += raw.pop("cost")
            if cached:
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(raw, ensure_ascii=False))
        result = checked(raw, form, mode, step_id)
        if result["action"] == "answer" and step.get("answer") == "digits" and digits and result["value"] not in digits:
            result["value"] = digits[0]  # what was said, not what the count wanted
    return result | {"transcript": transcript, "ms": round((time.monotonic() - started) * 1000), "cost": round(cost, 6)}


if __name__ == "__main__":
    path, code, step_id = sys.argv[1:4]
    form = json.loads((OUT / f"interview.{code}.json").read_text())
    print(json.dumps(hear(Path(path).read_bytes(), form, "question", step_id), ensure_ascii=False))

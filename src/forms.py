"""Turn any uploaded PDF form into a guided interview, the way the W-9 proof was made, and keep it.

A fillable PDF already says where its fields are: they are read straight from it, with no model call,
and the answers are later set in those same fields. A flat PDF goes to Muse Spark one page picture at
a time to find its blanks (only pages that have empty lines or boxes to fill), and the answers are
later written on the page. Either way Muse Spark then writes the interview from the form's own text,
and every quote is checked word for word (interview.py).

Each form lives in out/forms/<id>/, where id is the start of the file's SHA-256, so the same file
uploaded again is ready at once and costs nothing. Only the blank form is sent to Meta, never answers.

    uv run forms.py some-form.pdf     # prepare one form from the command line
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

from interview import add_strikes, generate, repair
from spike import OUT, ZOOM, detect, snap
from translate import speakable

LIBRARY = OUT / "forms"
MAX_BYTES = 20_000_000
MAX_PAGES = 10  # pages sent to Muse Spark to find blanks; about $0.03 each
MAX_FIELDS = 150
MAX_TEXT = 60_000  # characters of instructions sent with the interview request


class Rejected(Exception):
    """A form we can't take, with a code the page turns into words: not_pdf, locked, none, long, many."""

    def __init__(self, code: str, limit: int = 0):
        super().__init__(code)
        self.code, self.limit = code, limit


def form_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def ready(folder: Path) -> bool:
    return (folder / "interview.en.json").exists()


def printed(page: pymupdf.Page) -> list[tuple]:
    """The page's words that can name a field: no dot leaders or dashes, no text turned sideways."""
    return [w for w in page.get_text("words") if any(ch.isalnum() for ch in w[4])
            and not (len(w[4]) > 1 and w[3] - w[1] > 1.5 * (w[2] - w[0]))]


def near(words: list[tuple], rect: pymupdf.Rect, kind: str) -> str:
    """The printed words around a fillable field that may name it: right of a tick box, left of a box on
    the same line, and just above it, joined with " / ". Muse Spark also sees the page with the field
    outlined, and picks the right one."""
    line = [w for w in words if abs((w[1] + w[3]) / 2 - (rect.y0 + rect.y1) / 2) < max(4.0, rect.height / 2)]

    def run(ws: list[tuple], reverse: bool = False) -> str:
        ws = sorted(ws, key=lambda w: w[0], reverse=reverse)
        out = ws[:1]
        for w in ws[1:]:
            if (out[-1][0] - w[2] if reverse else w[0] - out[-1][2]) > 12:
                break
            out.append(w)
        return " ".join(w[4] for w in sorted(out, key=lambda w: w[0]))

    right = run([w for w in line if rect.x1 - 2 <= w[0] < rect.x1 + 200]) if kind == "checkbox" else ""
    left = run([w for w in line if rect.x0 - 220 < w[2] <= rect.x0 + 2], reverse=True)
    above = " ".join(w[4] for w in sorted(
        (w for w in words if rect.y0 - 16 <= w[3] <= rect.y0 + 3 and w[2] > rect.x0 - 4 and w[0] < rect.x1),
        key=lambda w: (round(w[1]), w[0]))[:24])
    return " / ".join(dict.fromkeys(t for t in (right, left, above) if t))


def marked(doc: pymupdf.Document, fields: list[dict]) -> list[bytes]:
    """A picture of each page with fields, every field outlined in red with its id beside it, so Muse
    Spark can see which field is which (set-of-marks prompting)."""
    copy = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    pages = sorted({f["page"] for f in fields})[:MAX_PAGES]
    for i, f in enumerate(fields):
        if f["page"] in pages:
            page, r = copy[f["page"]], pymupdf.Rect(f["rect"])
            page.draw_rect(r, color=(0.85, 0, 0), width=0.8)
            page.insert_text((r.x0 + 1, r.y0 - 1), str(i), fontsize=6.5, color=(0.85, 0, 0))
    for page in copy:
        for w in list(page.widgets()):
            page.delete_widget(w)  # the outlines, not the fields' own drawings, show where they are
    return [copy[n].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png") for n in pages]


def widget_fields(doc: pymupdf.Document) -> list[dict]:
    """A fillable PDF's own fields, in page order. Push buttons fill nothing; a signature field can't
    hold text, so the typed name is written on the page there instead."""
    kinds = {pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox", pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "checkbox",
             pymupdf.PDF_WIDGET_TYPE_TEXT: "text", pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "text",
             pymupdf.PDF_WIDGET_TYPE_LISTBOX: "text", pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "text"}
    fields = []
    for page in doc:
        words = printed(page)
        for w in page.widgets():
            if w.field_type not in kinds or w.rect.is_empty:
                continue
            kind = kinds[w.field_type]
            label = " ".join((w.field_label or near(words, w.rect, kind) or w.field_name).split())
            if w.field_type in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX) and w.choice_values:
                label += f" (one of: {', '.join(str(c) for c in w.choice_values[:20])})"
            comb = w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT and w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB
            fields.append({
                "label": label[:300], "kind": kind, "page": page.number,
                "group": re.sub(r"\[\d+\]$", "", w.field_name) if kind == "checkbox" else "",
                "rect": [round(v, 1) for v in w.rect], "cells": w.text_maxlen if comb else 0,
                "widget": w.xref if w.field_type != pymupdf.PDF_WIDGET_TYPE_SIGNATURE else 0,
            })
    groups = [f["group"] for f in fields]
    for f in fields:
        if f["group"] and groups.count(f["group"]) < 2:
            f["group"] = ""  # a tick box on its own answers its own question
    return fields


def to_fill(page: pymupdf.Page) -> bool:
    """Does a flat page have places to write? Count printed lines and boxes with (almost) nothing
    written in them: a "$" before an amount line still leaves it blank. An instruction page
    has rules too, but few of them empty. A page with no text at all is a scan, and is sent anyway."""
    if not page.get_text().strip():
        return True
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]

    def blank(r: pymupdf.Rect) -> bool:
        covered = sum((w & r).width for w in words if w.intersects(r))
        return r.width > 0 and covered < 0.1 * r.width

    drawings = page.get_drawings()
    rules = [d["rect"] for d in drawings if d["rect"].height < 1.5 and d["rect"].width > 40]
    boxes = [it[1] for d in drawings for it in d["items"] if it[0] == "re" and it[1].width > 6 and it[1].height > 6]
    blanks = sum(blank(pymupdf.Rect(r.x0 + 2, r.y0 - 11, r.x1 - 2, r.y0 - 1)) for r in rules)
    blanks += sum(blank(pymupdf.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)) for r in boxes)
    return blanks >= 5


def flat_fields(doc: pymupdf.Document, folder: Path, pages: list[int], report: Callable) -> list[dict]:
    """Find the blanks on each page with Muse Spark, a few pages at a time, then snap them to the page."""
    blank = folder / "source.pdf"

    def one(number: int) -> list[dict]:
        png = folder / f"page-{number + 1}.png"
        doc[number].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM)).save(png)
        found = detect(png, folder / f"detection-{number + 1}.json", number + 1)["fields"]
        for f in found:
            f["rect"], f["page"] = [v / ZOOM for v in f["bbox"]], number
        return snap(blank, found, number)

    done, out = 0, {}
    report("finding", n=0, total=len(pages))
    with ThreadPoolExecutor(4) as pool:
        for number, fields in zip(pages, pool.map(one, pages)):
            out[number] = fields
            done += 1
            report("finding", n=done, total=len(pages))
    return [{k: v for k, v in f.items() if k != "bbox"} for n in pages for f in out[n]]


def prepare(data: bytes, report: Callable = lambda stage, **kw: None) -> Path:
    """The folder of a ready form. Raises Rejected for a file we can't take, and lets Meta's errors
    through (httpx, openai), so the caller can say the service was unreachable."""
    folder = LIBRARY / form_id(data)
    if ready(folder):
        return folder
    report("reading")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as e:
        raise Rejected("not_pdf") from e
    if doc.needs_pass:
        raise Rejected("locked")
    if not doc.page_count:
        raise Rejected("not_pdf")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.pdf").write_bytes(data)
    if any(True for page in doc for _ in page.widgets()):
        fields = widget_fields(doc)
    else:
        pages = [p.number for p in doc if to_fill(p)]
        if len(pages) > MAX_PAGES:
            raise Rejected("long", MAX_PAGES)
        fields = flat_fields(doc, folder, pages, report)
    if not fields:
        raise Rejected("none")
    if len(fields) > MAX_FIELDS:
        raise Rejected("many", MAX_FIELDS)
    with_fields = sorted({f["page"] for f in fields})
    text = "\n".join(f"--- page {n + 1} ---\n{doc[n].get_text()}" for n in with_fields)
    instructions = "\n".join(f"--- page {p.number + 1} ---\n{p.get_text()}" for p in doc
                             if p.number not in with_fields)[:MAX_TEXT]
    report("writing")
    interview = generate(fields, text, instructions, folder / "interview-raw.json", marked(doc, fields))
    notes = add_strikes(interview, fields, doc) + repair(interview, fields, text, instructions)
    (folder / "fields.json").write_text(json.dumps(fields, indent=2))
    out = {k: v for k, v in interview.items() if k != "_usage"}
    out["fields"] = [{"label": f["label"], "kind": f["kind"], "cells": f.get("cells", 0)} for f in fields]
    out["problems"] = notes
    (folder / "interview.json").write_text(json.dumps(out, indent=2))
    (folder / "interview.en.json").write_text(json.dumps(speakable("en", out, None), indent=2, ensure_ascii=False))
    return folder


if __name__ == "__main__":
    def show(stage: str, **kw) -> None:
        print(stage, kw or "")

    folder = prepare(Path(sys.argv[1]).read_bytes(), show)
    interview = json.loads((folder / "interview.json").read_text())
    print(f"{folder}: {len(interview['fields'])} fields, {len(interview['steps'])} questions, "
          f"repairs: {interview['problems'] or 'none'}")

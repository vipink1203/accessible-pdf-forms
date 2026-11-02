"""One-document proof: can Muse Spark turn a flat PDF form into an accessible web form
and write the answers back onto the original page?

Ground truth comes from the fillable IRS W-9: we strip its fields to make a flat PDF,
show Muse only a picture of page 1, and score the fields it finds against the real ones.

Spend guard: at most MAX_CALLS model calls per run, and a cached detection.json is reused,
so re-running costs nothing. Only the blank public form is sent; filled values stay local.

    uv run spike.py
"""
from __future__ import annotations

import base64
import html
import json
import os
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
SRC = HERE / "input" / "fw9.pdf"
OUT = HERE / "out"
ZOOM = 2  # image pixels per PDF point
MODEL = "muse-spark-1.3"
MAX_CALLS = 2
PRICE_PER_M = {"input": 1.25, "output": 4.25}  # muse-spark-1.3, from praxion's verified table

# Our reading of each W-9 page-1 widget, by field name suffix.
TRUTH_LABELS = {
    "f1_01[0]": "1 Name",
    "f1_02[0]": "2 Business name / disregarded entity name",
    "c1_1[0]": "3a Individual/sole proprietor",
    "c1_1[1]": "3a C corporation",
    "c1_1[2]": "3a S corporation",
    "c1_1[3]": "3a Partnership",
    "c1_1[4]": "3a Trust/estate",
    "c1_1[5]": "3a LLC",
    "f1_03[0]": "3a LLC tax classification (C, S or P)",
    "c1_1[6]": "3a Other",
    "f1_04[0]": "3a Other, describe",
    "c1_2[0]": "3b Foreign partners, owners or beneficiaries",
    "f1_05[0]": "4 Exempt payee code",
    "f1_06[0]": "4 Exemption from FATCA reporting code",
    "f1_07[0]": "5 Address",
    "f1_08[0]": "6 City, state and ZIP",
    "f1_09[0]": "Requester's name and address",
    "f1_10[0]": "7 Account number(s)",
    "f1_11[0]": "SSN part 1",
    "f1_12[0]": "SSN part 2",
    "f1_13[0]": "SSN part 3",
    "f1_14[0]": "EIN part 1",
    "f1_15[0]": "EIN part 2",
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "kind", "group", "bbox"],
                "properties": {
                    "label": {"type": "string"},
                    "kind": {"type": "string", "enum": ["text", "checkbox"]},
                    "group": {"type": "string"},
                    "bbox": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}

PROMPT = """This image is page {page} of a paper form, {w} x {h} pixels.
A blind person must fill it with a screen reader, so every place where the form expects
the filler to write or tick something must become a labelled input.

List every fillable place on the page: blank lines and boxes to write in, and tick boxes.
Skip printed instructions.
For each one give:
- label: the words a screen reader should announce, taken from the form's own text
  (include the line number if the form numbers its lines);
- kind: "text" or "checkbox";
- group: for tick boxes that answer the same question, the question text; otherwise "";
- bbox: [x0, y0, x1, y1] in image pixels, tightly around the empty area where the answer
  goes (for a tick box, the box itself), not around the printed label.
If a number is printed as rows of digit boxes split by dashes, give one field per dash-separated group."""


def load_key() -> str:
    if os.environ.get("MODEL_API_KEY"):
        return os.environ["MODEL_API_KEY"].strip()
    path = Path(os.environ.get("MODEL_API_KEY_FILE", Path.home() / "meta.key"))
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    sys.exit("No key: put the Meta Model API key in ~/meta.key or set MODEL_API_KEY")


def prepare() -> tuple[list[dict], Path, Path]:
    """Truth fields from the fillable W-9, a flat copy of page 1, and its picture."""
    doc = pymupdf.open(SRC)
    doc.select([0])
    page = doc[0]
    truth = []
    for w in page.widgets():
        suffix = w.field_name.rsplit(".", 1)[-1]
        truth.append({
            "label": TRUTH_LABELS[suffix],
            "kind": "checkbox" if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX else "text",
            "rect": [round(v, 1) for v in w.rect],
        })
    for w in list(page.widgets()):
        page.delete_widget(w)
    flat = OUT / "fw9-flat.pdf"
    doc.save(flat)
    assert not list(pymupdf.open(flat)[0].widgets()), "flat copy still has fields"
    png = OUT / "fw9-p1.png"
    pymupdf.open(flat)[0].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM)).save(png)
    return truth, flat, png


def detect(png: Path, cache: Path = OUT / "detection.json", page: int = 1) -> dict:
    """Muse Spark's list of fillable places on one page picture, cached in `cache`."""
    if cache.exists():
        print(f"reusing cached {cache.name} (no API call)")
        return json.loads(cache.read_text())
    from openai import OpenAI

    client = OpenAI(base_url=os.getenv("META_BASE_URL", "https://api.meta.ai/v1"), api_key=load_key())
    pix = pymupdf.Pixmap(str(png))
    data_url = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT.format(page=page, w=pix.width, h=pix.height)},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}]
    spent = 0.0
    for attempt in range(1, MAX_CALLS + 1):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "form_fields", "schema": SCHEMA, "strict": True}},
            max_completion_tokens=12000,
        )
        u = resp.usage
        cost = (u.prompt_tokens * PRICE_PER_M["input"] + u.completion_tokens * PRICE_PER_M["output"]) / 1e6
        spent += cost
        print(f"call {attempt}: {u.prompt_tokens} in, {u.completion_tokens} out, ${cost:.4f} (total ${spent:.4f})")
        try:
            result = json.loads(resp.choices[0].message.content or "")
        except json.JSONDecodeError:
            continue
        result["_usage"] = {"model": resp.model, "calls": attempt, "prompt_tokens": u.prompt_tokens,
                            "completion_tokens": u.completion_tokens, "cost_usd": round(spent, 5)}
        cache.write_text(json.dumps(result, indent=2))
        return result
    raise ValueError(f"no valid JSON after {MAX_CALLS} calls (${spent:.4f} spent)")


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def center_in(a: list[float], b: list[float]) -> bool:
    cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def score(truth: list[dict], found: list[dict]) -> list[dict]:
    """Greedy one-to-one match: a found field hits a truth field when its box overlaps
    (IoU >= 0.2) or either box's centre lies inside the other."""
    pairs = []
    for ti, t in enumerate(truth):
        for fi, f in enumerate(found):
            o = iou(t["rect"], f["rect"])
            if o >= 0.2 or center_in(t["rect"], f["rect"]) or center_in(f["rect"], t["rect"]):
                pairs.append((o, ti, fi))
    pairs.sort(reverse=True)
    used_t, used_f, rows = set(), set(), []
    for o, ti, fi in pairs:
        if ti in used_t or fi in used_f:
            continue
        used_t.add(ti)
        used_f.add(fi)
        rows.append({"truth": truth[ti]["label"], "found": found[fi]["label"], "iou": round(o, 2),
                     "kind_ok": truth[ti]["kind"] == found[fi]["kind"], "ti": ti, "fi": fi})
    for ti, t in enumerate(truth):
        if ti not in used_t:
            rows.append({"truth": t["label"], "found": None, "iou": 0, "kind_ok": False, "ti": ti, "fi": None})
    for fi, f in enumerate(found):
        if fi not in used_f:
            rows.append({"truth": None, "found": f["label"], "iou": 0, "kind_ok": False, "ti": None, "fi": fi})
    return rows


def snap(flat: Path, found: list[dict], number: int = 0) -> list[dict]:
    """Muse knows what each field means; the page knows exactly where its boxes are.
    Move each tick box onto the nearest drawn square, and each text field that sits on a row of
    digit cells onto the cells under it. Fields with no drawn box nearby keep Muse's position."""
    page = pymupdf.open(flat)[number]
    rects = [it[1] for d in page.get_drawings() for it in d["items"] if it[0] == "re"]
    squares = [r for r in rects if 5 < r.width < 14 and 5 < r.height < 14 and abs(r.width - r.height) < 2]
    cells = [r for r in rects if 8 < r.width < 20 and 18 < r.height < 30]
    snapped = []
    for f in found:
        r = pymupdf.Rect(f["rect"])
        c = (r.tl + r.br) / 2
        new, cells_in = None, 0
        if f["kind"] == "checkbox" and squares:
            best = min(squares, key=lambda s: abs((s.tl + s.br) / 2 - c))
            if abs((best.tl + best.br) / 2 - c) < 30:
                new = best
        elif f["kind"] == "text" and cells:
            rows = {}
            for cell in cells:
                rows.setdefault(round(cell.y0), []).append(cell)
            near = [row for row in rows.values() if abs((row[0].y0 + row[0].y1) / 2 - c.y) < 25]
            if near:
                row = sorted(min(near, key=lambda row: abs((row[0].y0 + row[0].y1) / 2 - c.y)), key=lambda c: c.x0)
                # Touching cells form one dash-separated group; take the group the field overlaps most.
                runs = [[row[0]]]
                for cell in row[1:]:
                    (runs[-1].append(cell) if cell.x0 - runs[-1][-1].x1 < 2 else runs.append([cell]))
                overlap = lambda run, r=r: max(0.0, min(r.x1, run[-1].x1) - max(r.x0, run[0].x0))
                run = max(runs, key=overlap)
                if overlap(run) > 0:
                    new = pymupdf.Rect(run[0].x0, run[0].y0, run[-1].x1, run[0].y1)
                    cells_in = len(run)
        g = dict(f)
        g["rect"] = [round(v, 1) for v in (new or r)]
        g["snapped"] = new is not None
        g["cells"] = cells_in if new is not None and f["kind"] == "text" else 0
        snapped.append(g)
    return snapped


FORM_CSS = """
:root { --bg: #f7f7f5; --surface: #fff; --text: #1b1b1f; --muted: #55565e; --line: #d9d9de; --accent: #1f4fd1; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #121316; --surface: #1b1c20; --text: #ececf1; --muted: #a8a9b3; --line: #33343b; --accent: #8fb0ff; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 1rem/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 40rem; margin: 0 auto; padding: 2.5rem 1rem 4rem; }
h1 { font-size: 1.5rem; margin: 0 0 1.5rem; }
form > div, fieldset { background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
                       padding: .9rem 1rem; margin: 0 0 .75rem; }
form > div > label { display: block; margin-bottom: .4rem; }
legend { float: left; width: 100%; font-weight: 600; padding: 0; margin-bottom: .5rem; }
legend + * { clear: both; }
fieldset div { display: flex; gap: .5rem; align-items: flex-start; padding: .2rem 0; }
form > div:has(input[type=checkbox]) { display: flex; gap: .5rem; align-items: flex-start; }
input[type=text] { width: 100%; font: inherit; padding: .55rem .7rem; color: var(--text); background: var(--surface);
                   border: 1px solid var(--muted); border-radius: 8px; }
input[type=checkbox] { width: 1.1rem; height: 1.1rem; margin: .2rem 0 0; accent-color: var(--accent); flex: none; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
"""


def build_html(found: list[dict]) -> Path:
    """Accessible form: every input has a <label>, tick boxes sharing a question sit in a fieldset."""
    parts, groups = [], {}
    for i, f in enumerate(found):
        if f["kind"] == "checkbox" and f["group"]:
            groups.setdefault(f["group"], []).append((i, f))
    done = set()
    for i, f in enumerate(found):
        if i in done:
            continue
        if f["kind"] == "checkbox" and f["group"]:
            items = groups[f["group"]]
            boxes = "".join(
                f'<div><input type="checkbox" id="f{j}" name="f{j}"><label for="f{j}">{html.escape(g["label"])}</label></div>'
                for j, g in items)
            parts.append(f"<fieldset><legend>{html.escape(f['group'])}</legend>{boxes}</fieldset>")
            done.update(j for j, _ in items)
        elif f["kind"] == "checkbox":
            parts.append(f'<div><input type="checkbox" id="f{i}" name="f{i}"><label for="f{i}">{html.escape(f["label"])}</label></div>')
        else:
            parts.append(f'<div><label for="f{i}">{html.escape(f["label"])}</label><input type="text" id="f{i}" name="f{i}"></div>')
    page = OUT / "form.html"
    page.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Form W-9, page 1</title>'
        f"<style>{FORM_CSS}</style></head>"
        '<body><main><h1>Form W-9, page 1</h1><form>' + "".join(parts) + "</form></main></body></html>")
    return page


def check_accessibility(page: Path, found: list[dict]) -> dict:
    """Ask Chromium's accessibility tree what a screen reader would announce for each input."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page()
        tab.goto(page.as_uri())
        snapshot = tab.locator("body").aria_snapshot()
        inputs = tab.locator("input").count()
        named = sum(1 for line in snapshot.splitlines()
                    if line.strip().startswith(("- textbox \"", "- checkbox \"")))
        browser.close()
    return {"inputs": inputs, "unnamed": inputs - named, "tree": snapshot}


def write_answers(blank: Path, fields: list[dict], values: dict[int, str]) -> pymupdf.Document:
    """Write answers onto the blank form, keyed by field index. A fillable field (one with a widget)
    gets its value set, so the form stays fillable. On a flat page: an X in each ticked box, one digit
    per printed cell for digit groups, and other text on the box's bottom line the way a person
    writes on a form."""
    doc = pymupdf.open(blank)
    lines: dict[int, tuple] = {}  # page number: (printed words, printed rules)
    blue = (0, 0, 0.75)
    for i, value in values.items():
        f = fields[i]
        r = pymupdf.Rect(f["rect"])
        if not value:
            continue
        page = doc[f.get("page", 0)]
        if f["kind"] == "strike":  # a line through each line of the printed text
            for line in f["lines"]:
                y = (line[1] + line[3]) / 2
                page.draw_line((line[0], y), (line[2], y), color=blue, width=1.2)
            continue
        if f.get("widget"):
            widget = page.load_widget(f["widget"])
            widget.field_value = widget.on_state() if f["kind"] == "checkbox" else value
            widget.update()
            continue
        if page.number not in lines:
            lines[page.number] = ([pymupdf.Rect(w[:4]) for w in page.get_text("words")],
                                  [d["rect"] for d in page.get_drawings() if d["rect"].height < 1 and d["rect"].width > 20])
        words, rules = lines[page.number]
        if f["kind"] == "checkbox":
            page.insert_text((r.x0 + 1, r.y1 - 1), "X", fontsize=r.height + 1, color=blue)
            continue
        if f.get("cells"):
            step = r.width / f["cells"]
            for k, ch in enumerate(value[: f["cells"]]):
                page.insert_text((r.x0 + k * step + step / 2 - 3, r.y1 - 7), ch, fontsize=11, color=blue)
            continue
        # Write on the printed rule nearest the box's bottom edge, starting after any printed
        # label that shares that line.
        under = [l.y0 for l in rules if abs(l.y0 - r.y1) < 8 and l.x0 < r.x1 and l.x1 > r.x0]
        if under:
            r.y1 = min(under, key=lambda y: abs(y - r.y1))
        printed = [w for w in words if r.y1 - 12 < (w.y0 + w.y1) / 2 < r.y1
                   and w.x1 > r.x0 and w.x0 < r.x0 + r.width / 2]
        x0 = max([r.x0] + [w.x1 for w in printed]) + 3
        size = min(10, r.height - 2)
        while size > 5 and pymupdf.get_text_length(value, fontsize=size) > r.x1 - x0 - 2:
            size -= 0.5
        page.insert_text((x0, r.y1 - 4), value, fontsize=size, color=blue)
    return doc


# Made-up answers a user would give, keyed by words in the detected label.
# Digit groups take their share of the number in order of appearance.
SAMPLE = [
    ("individual/sole proprietor", "X"),
    ("name of entity", "Jordan Rivera"),
    ("address (number", "1200 Elm Street, Apt 4"),
    ("city, state", "Springfield, IL 62701"),
    ("signature", "Jordan Rivera"),
    ("date", "10/31/2026"),
]
SSN_GROUPS = ["123", "45", "6789"]


def fill_back(flat: Path, found: list[dict]) -> Path:
    """Fill the flat page with the sample answers and render it."""
    ssn = iter(SSN_GROUPS)
    values = {}
    for i, f in enumerate(found):
        label = f["label"].lower()
        if label.startswith("social security"):
            values[i] = next(ssn, "")
        else:
            values[i] = next((v for key, v in SAMPLE if key in label), "")
    doc = write_answers(flat, found, values)
    out = OUT / "fw9-filled.pdf"
    doc.save(out)
    doc[0].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM)).save(OUT / "fw9-filled.png")
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    truth, flat, png = prepare()
    print(f"truth: {len(truth)} fields on W-9 page 1; flat copy has 0 fields")
    result = detect(png)
    raw = result["fields"]
    for f in raw:
        f["rect"] = [v / ZOOM for v in f["bbox"]]
    report = {"usage": result.get("_usage"), "truth_fields": len(truth), "found_fields": len(raw)}
    for stage, found in (("muse_only", raw), ("muse_plus_snap", snap(flat, raw))):
        rows = score(truth, found)
        hits = [r for r in rows if r["truth"] and r["found"]]
        missed = [r["truth"] for r in rows if r["truth"] and not r["found"]]
        extra = [r["found"] for r in rows if r["found"] and not r["truth"]]
        exact = sum(1 for r in hits if r["iou"] >= 0.5)
        print(f"\n[{stage}] matched {len(hits)}/{len(truth)}, box IoU >= 0.5 on {exact}; missed {len(missed)}; "
              f"extra {len(extra)} {extra}; kind correct {sum(r['kind_ok'] for r in hits)}/{len(hits)}")
        for r in sorted(rows, key=lambda r: (r["ti"] is None, r["ti"] if r["ti"] is not None else 99)):
            if r["truth"]:
                print(f"  {r['truth']:45} | {str(r['found'])[:55]:55} | iou {r['iou']}")
        report[stage] = {"matched": len(hits), "iou_ge_0_5": exact, "missed": missed, "extra": extra,
                         "kind_correct": sum(r["kind_ok"] for r in hits), "rows": rows}
    (OUT / "fields.json").write_text(json.dumps(found, indent=2))
    page = build_html(found)
    a11y = check_accessibility(page, found)
    print(f"\naccessible form: {a11y['inputs']} inputs, {a11y['unnamed']} without an accessible name")
    filled = fill_back(flat, found)
    report["accessibility"] = {k: v for k, v in a11y.items() if k != "tree"}
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    (OUT / "a11y-tree.yaml").write_text(a11y["tree"])
    print(f"wrote {page.name}, {filled.name}, fw9-filled.png, report.json in {OUT}")


if __name__ == "__main__":
    main()

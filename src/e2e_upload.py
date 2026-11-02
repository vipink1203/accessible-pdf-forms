"""End-to-end check of uploading a form: choose a PDF on the start page the way a person would,
wait while it is prepared, answer every question by keyboard, then check that every answer is on
the PDF that comes back.

Answers are made up from the question: the first choice, a tick to certify, ones for digit boxes,
and "Answer N" for text, so each one can be found on the PDF. A fillable form must come back with
its fields set; a flat form with the answers written on its pages.

Preparing a new form costs about $0.03 a page with blanks plus about $0.08 for the questions; it
is kept in out/forms/, so a re-run costs nothing.

    uv run e2e_upload.py input/fw9.pdf input/dl/fw4.pdf input/dl/fw4-flat.pdf
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pymupdf
from playwright.sync_api import sync_playwright

from e2e import URL, struck, wait_for_server
from forms import LIBRARY
from spike import HERE, OUT

SHOTS = OUT / "e2e"


def answer(tab, step: dict, n: int) -> str | bool:
    """Answer the question on screen by keyboard, and return the answer given."""
    if step["answer"] == "choice":
        tab.keyboard.press("Space")  # focus is on the first option
        tab.keyboard.press("Enter")
        return step["choices"][0]["value"]
    if step["answer"] == "confirm":
        tab.keyboard.press("Tab")
        tab.keyboard.press("Space")
        tab.keyboard.press("Enter")
        return True
    value = "1" * tab.evaluate("digitCount(steps[current])") if step["answer"] == "digits" else f"Answer {n}"
    tab.keyboard.type(value)
    tab.keyboard.press("Enter")
    return value


def run(browser, pdf: Path) -> list[str]:
    failures, name = [], pdf.stem
    tab = browser.new_page(viewport={"width": 760, "height": 900})
    tab.on("pageerror", lambda e: failures.append(f"page error: {e}"))
    tab.goto(URL)
    tab.wait_for_selector("[data-file]")
    tab.set_input_files("#file", str(pdf))
    tab.wait_for_selector("[data-reader], #error:not(:empty)", timeout=300_000)
    if tab.locator("#error:not(:empty)").count():
        return [f"not prepared: {tab.locator('#error').inner_text()}"]
    form_id = tab.evaluate("formId")
    interview = json.loads((LIBRARY / form_id / "interview.json").read_text())
    tab.screenshot(path=SHOTS / f"upload-{name}-00-start.png", full_page=True)
    tab.click("[data-reader=en]")
    tab.click("#go")
    given = {}
    for n in range(1, 200):
        if tab.locator("h2", has_text="Check your answers").count():
            break
        step = tab.evaluate("steps[current]")
        tab.screenshot(path=SHOTS / f"upload-{name}-{n:02}.png", full_page=True)
        given[step["id"]] = answer(tab, step, n)
        if tab.locator("#error:not(:empty)").count():
            return [*failures, f"{step['id']}: answer refused: {tab.locator('#error').inner_text()}"]
    tab.screenshot(path=SHOTS / f"upload-{name}-90-review.png", full_page=True)
    tab.click("#fill")
    tab.wait_for_selector("#download, #error:not(:empty)")
    if not tab.locator("#download").count():
        return [*failures, f"not filled: {tab.locator('#error').inner_text()}"]
    tab.screenshot(path=SHOTS / f"upload-{name}-99-done.png", full_page=True)
    href = tab.locator("#download").get_attribute("href")
    pdf_b64 = tab.evaluate("""async href => {
      const blob = await (await fetch(href)).blob();
      return await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result.split(",")[1]); r.readAsDataURL(blob); });
    }""", href)
    values = tab.evaluate("values()")
    tab.close()
    filled = pymupdf.open(stream=base64.b64decode(pdf_b64), filetype="pdf")
    fields = json.loads((LIBRARY / form_id / "fields.json").read_text())
    for key, value in values.items():
        f = fields[int(key)]
        page = filled[f.get("page", 0)]
        if f["kind"] == "strike":
            if not struck(page, f):
                failures.append(f"field {key} ({f['label'][:40]}): not crossed out")
        elif f.get("widget"):
            widget = page.load_widget(f["widget"])
            on = widget.field_value not in ("", "Off", None, False) if f["kind"] == "checkbox" else widget.field_value == value
            if not on:
                failures.append(f"field {key} ({f['label'][:40]}): widget holds {widget.field_value!r}, wanted {value!r}")
        else:
            words = page.get_text("text", clip=pymupdf.Rect(f["rect"]) + (-4, -8, 60, 4))
            if "".join(value.split()) not in "".join(words.split()):
                failures.append(f"field {key} ({f['label'][:40]}): {value!r} not written there")
    print(f"  {len(fields)} fields, {len(interview['steps'])} questions, {len(given)} asked, "
          f"{len(values)} fields filled; repairs: {interview['problems'] or 'none'}")
    return failures


def main() -> None:
    pdfs = [Path(p) for p in sys.argv[1:]] or sys.exit(__doc__)
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([sys.executable, str(HERE / "serve.py")], cwd=HERE)
    failures = []
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for pdf in pdfs:
                print(f"{pdf}:")
                failures += [f"{pdf.name}: {f}" for f in run(browser, pdf)]
            browser.close()
    finally:
        server.terminate()
    print("PASS" if not failures else "FAIL")
    for f in failures:
        print("FAIL", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

"""End-to-end check, keyboard only, the way a screen-reader user moves: start the server, answer
every question with typing, arrow keys, Space and Enter, then check the PDF it returns.

At each step it records what a screen reader announces for the focused element, and saves
screenshots in out/e2e/ for a visual check. Uses made-up answers only.

    uv run e2e.py
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
import urllib.request

import pymupdf
from playwright.sync_api import sync_playwright

from spike import HERE, OUT

URL = "http://127.0.0.1:4420"
SHOTS = OUT / "e2e"

# Made-up person: an individual who gives an SSN.
ANSWERS = {
    "name1": "Jordan Rivera",
    "name2": "",
    "taxclass": "Individual/sole proprietor",
    "address": "1200 Elm Street, Apt 4",
    "city_state_zip": "Springfield, IL 62701",
    "account_numbers": "",
    "tin_type": "Social Security Number",
    "ssn_digits": "123-45-6789",
    # Told by the IRS they are subject to backup withholding: item 2 must come back crossed out.
    "backup_crossout": "Yes, I was notified",
    "certify": True,
    "signature": "Jordan Rivera",
    "sign_date": "10/31/2026",
}
EXPECT_ON_PDF = ["Jordan Rivera", "1200 Elm Street, Apt 4", "Springfield, IL 62701", "10/31/2026",
                 "123", "45", "6789", "X"]


def struck(page: pymupdf.Page, field: dict) -> bool:
    """Is every line of a cross-out field's printed text crossed by a drawn line?"""
    drawn = [d["rect"] for d in page.get_drawings() if d["rect"].height < 2 and d["rect"].width > 20]
    return all(any(abs(r.y0 - (line[1] + line[3]) / 2) < 2 and r.x0 <= line[0] + 1 and r.x1 >= line[2] - 1
                   for r in drawn) for line in field["lines"])


def wait_for_server() -> None:
    for _ in range(50):
        try:
            urllib.request.urlopen(URL + "/forms/w9/interview.en.json", timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    sys.exit("server did not start")


def announced(tab) -> str:
    """What Chromium exposes to a screen reader for the focused element: role, name, description."""
    return tab.evaluate("""() => {
      const el = document.activeElement;
      const text = ids => (ids || '').split(' ').map(id => document.getElementById(id)?.textContent.trim()).filter(Boolean).join(' ');
      const role = el.type === 'radio' ? 'radio' : el.type === 'checkbox' ? 'checkbox' : el.tagName === 'INPUT' ? 'edit text' : el.tagName.toLowerCase();
      let name = el.getAttribute('aria-labelledby') ? text(el.getAttribute('aria-labelledby')) : (el.labels?.[0]?.textContent || el.textContent).trim();
      const group = el.closest('fieldset')?.querySelector('legend')?.textContent.trim();
      const desc = text(el.getAttribute('aria-describedby') || el.closest('fieldset')?.getAttribute('aria-describedby'));
      return [group, name.replace(/\\s+/g, ' '), role, desc].filter(Boolean).join(' | ');
    }""")


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([sys.executable, str(HERE / "serve.py")], cwd=HERE)
    transcript, failures = [], []
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            tab = browser.new_page(viewport={"width": 760, "height": 900})
            tab.goto(URL)
            # Focus starts on "Choose a PDF form"; the next button is the sample W-9.
            tab.wait_for_selector("[data-sample]")
            tab.screenshot(path=SHOTS / "00-pick.png", full_page=True)
            tab.keyboard.press("Tab")
            tab.keyboard.press("Enter")
            tab.wait_for_selector("[data-reader]")
            tab.screenshot(path=SHOTS / "00-start.png", full_page=True)
            # Focus starts on "Start talking"; the next button is "Use my screen reader instead".
            tab.keyboard.press("Tab")
            tab.keyboard.press("Enter")
            tab.wait_for_selector("#go")
            tab.screenshot(path=SHOTS / "00-about.png", full_page=True)
            tab.keyboard.press("Tab")
            tab.keyboard.press("Enter")
            asked = []
            for n in range(1, 40):
                if tab.locator("h2", has_text="Check your answers").count():
                    break
                question = re.split(r":\s", tab.locator("h2").first.inner_text())[-1].strip()
                step = next(s for s in json.loads((OUT / "interview.en.json").read_text())["steps"]
                            if s["question"] == question)
                asked.append(step["id"])
                transcript.append(f"{n:2}. {announced(tab)}")
                tab.screenshot(path=SHOTS / f"{n:02}-{step['id']}.png", full_page=True)
                value = ANSWERS.get(step["id"], "")
                if step["answer"] == "choice":
                    labels = [c["label"] for c in step["choices"]]
                    # Focus lands on the first option; arrow down to the wanted one, which also selects it.
                    tab.keyboard.press("Space")
                    for _ in range(labels.index(value)):
                        tab.keyboard.press("ArrowDown")
                    tab.keyboard.press("Enter")
                elif step["answer"] == "confirm":
                    tab.keyboard.press("Tab")
                    tab.keyboard.press("Space")
                    tab.keyboard.press("Enter")
                else:
                    tab.keyboard.type(value)
                    tab.keyboard.press("Enter")
            tab.screenshot(path=SHOTS / "90-review.png", full_page=True)
            if asked != list(ANSWERS):
                failures.append(f"asked {asked}, expected {list(ANSWERS)}")
            tab.locator("#fill").focus()
            tab.keyboard.press("Enter")
            tab.wait_for_selector("#download")
            transcript.append(f"done. {announced(tab)}")
            tab.screenshot(path=SHOTS / "99-done.png", full_page=True)
            href = tab.locator("#download").get_attribute("href")
            pdf_b64 = tab.evaluate("""async href => {
              const blob = await (await fetch(href)).blob();
              return await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result.split(",")[1]); r.readAsDataURL(blob); });
            }""", href)
            browser.close()
    finally:
        server.terminate()
    filled = OUT / "e2e" / "filled.pdf"
    filled.write_bytes(base64.b64decode(pdf_b64))
    text = pymupdf.open(filled)[0].get_text()
    flat_text = pymupdf.open(OUT / "fw9-flat.pdf")[0].get_text()
    for want in EXPECT_ON_PDF:
        if text.count(want) <= flat_text.count(want) and not (want.isdigit() and all(ch in text for ch in want)):
            failures.append(f"not written on the PDF: {want!r}")
    crossout = next(f for f in json.loads((OUT / "fields.json").read_text()) if f["kind"] == "strike")
    if not struck(pymupdf.open(filled)[0], crossout):
        failures.append("item 2 of the certification is not crossed out")
    pymupdf.open(filled)[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(SHOTS / "filled.png")
    print("\n".join(transcript))
    print(f"\n{len(asked)} questions asked; {'PASS' if not failures else 'FAIL'}")
    for f in failures:
        print("FAIL", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

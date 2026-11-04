"""End-to-end check of voice mode, by talking: the page reads each question aloud, a recorded
voice answers into a fake microphone, and the filled PDF it downloads is checked.

The answers are spoken by the Mac's own voices (`say`), so they are real speech that goes
through the page's recording, Muse Voice Transcribe and Muse Spark, like a person's would.
Each turn checks the page is on the expected question; the script also covers silence,
pausing, repeat, skip, explain, a wrong digit count and a spoken correction. A separate run checks
the person can pause at any time (button, Escape, or by saying so) and carry on where they were,
and that the page stops asking after three replies it can't understand.

Muse Spark's replies are cached in out/e2e/voice-cache by transcript, so a re-run pays only for
transcription (about $0.0002 a clip).

    uv run e2e_voice.py          # English, Spanish, and pausing
    uv run e2e_voice.py es       # one of en, es, pause
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pymupdf
from playwright.sync_api import TimeoutError, sync_playwright

from spike import HERE, OUT

URL = "http://127.0.0.1:4420"
SHOTS = OUT / "e2e"
CLIPS = SHOTS / "clips"
TODAY = datetime.date.today().strftime("%m/%d/%Y")
SILENCE = None
ADDRESS = r"1200 Elm Street,? (Apartment|Apartamento|Apt\.?) 4\.?"
CITY = r"Springfield, (Illinois|IL),? 62701"

# Every time the page listens, in order: (mode, step, what the person says, and for a read-back,
# a pattern the whole understood answer must match).
SCRIPTS = {
    "en": {
        "voice": "Samantha",
        "no": "No.",
        "turns": [
            ("question", "name1", "My name is Jordan Rivera.", None),
            ("confirming", "name1", "Yes.", "Jordan Rivera"),
            ("question", "name2", SILENCE, None),
            ("question", "name2", SILENCE, None),  # second silence: the page pauses; Space resumes
            ("question", "name2", "Skip.", None),
            ("question", "taxclass", "One.", None),  # choices are read with numbers; a number picks one
            ("confirming", "taxclass", "Yes, that's right.", "individual"),
            ("question", "address", "Twelve hundred Elm Street, apartment four.", None),
            ("confirming", "address", "Yes.", ADDRESS),
            ("question", "city_state_zip", "Springfield, Illinois, six two seven zero one.", None),
            ("confirming", "city_state_zip", "Yes.", CITY),
            ("question", "account_numbers", "Can you repeat the question?", None),
            ("question", "account_numbers", "Skip that one.", None),
            ("question", "tin_type", "My social security number.", None),
            ("confirming", "tin_type", "Yes.", "ssn"),
            ("question", "ssn_digits", "One two three, four five, six seven eight.", None),  # 8 digits: asked again
            ("question", "ssn_digits", "One two three, four five, six seven eight nine.", None),
            ("confirming", "ssn_digits", "Yes.", "123456789"),
            ("question", "backup_crossout", "No, I haven't been told that.", None),
            ("confirming", "backup_crossout", "Yes.", "no"),
            ("certify", "certify", "Yes, I certify.", None),
            ("question", "signature", "Jordan Rivera.", None),
            ("confirming", "signature", "Yes.", "Jordan Rivera"),
            ("question", "sign_date", "Today.", None),
            ("confirming", "sign_date", "Yes.", TODAY),
            ("review", "review", "Yes, fill it in.", None),
        ],
        "on_pdf": ["Jordan Rivera", "1200 Elm Street", "Springfield", "62701", TODAY, "X"],
        "boxes": {18: "123", 19: "45", 20: "6789"},
        # the transcriber sometimes doubles a digit in one of its two hearings; then the reply is asked again
        "said": ["Meta does not use them to train", "1 2 3, 4 5, 6 7 8 9",
                 ("I need 9 digits, and I heard 8", "Sorry, I didn't understand that.")],
    },
    "es": {
        "voice": "Paulina",
        "no": "No.",
        "turns": [
            ("question", "name1", "Me llamo Jordan Rivera.", None),
            ("confirming", "name1", "Sí.", "Jordan Rivera"),
            ("question", "name2", "Omitir.", None),
            ("question", "taxclass", "Soy trabajador independiente.", None),
            ("confirming", "taxclass", "Sí, correcto.", "individual"),
            ("question", "address", "Mil doscientos Elm Street, apartamento cuatro.", None),
            ("confirming", "address", "Sí.", ADDRESS),
            ("question", "city_state_zip", "Springfield, Illinois, seis dos siete cero uno.", None),
            ("confirming", "city_state_zip", "Sí.", CITY),
            ("question", "account_numbers", "Explícame esto.", None),
            ("question", "account_numbers", "Omitir.", None),
            ("question", "tin_type", "Número de seguro social.", None),
            ("confirming", "tin_type", "Sí.", "ssn"),
            ("question", "ssn_digits", "Uno dos tres, cuatro cinco, seis siete ocho nueve.", None),
            # A spoken correction instead of yes: the new number is read back again.
            ("confirming", "ssn_digits", "No, es uno dos tres, cuatro cinco, seis siete ocho cero.", "123456789"),
            ("confirming", "ssn_digits", "Sí.", "123456780"),
            ("question", "backup_crossout", "No, nunca me avisaron.", None),
            ("confirming", "backup_crossout", "Sí.", "no"),
            ("certify", "certify", "Sí, lo certifico.", None),
            ("question", "signature", "Jordan Rivera.", None),
            ("confirming", "signature", "Sí.", "Jordan Rivera"),
            ("question", "sign_date", "Hoy.", None),
            ("confirming", "sign_date", "Sí.", TODAY),
            ("review", "review", "Sí.", None),
        ],
        "on_pdf": ["Jordan Rivera", "1200 Elm Street", "Springfield", "62701", TODAY, "X"],
        "boxes": {18: "123", 19: "45", 20: "6780"},
        "said": ["Meta no las usa para entrenar", "1 2 3, 4 5, 6 7 8 0"],
    },
}
MAX_RETRIES = 4  # times a run may say "no" to a misheard read-back and repeat itself

# Stand-ins for the browser's speech and microphone: speech is logged instead of played, and the
# microphone is an audio stream the test plays recorded answers into.
FAKES = """
window.__spoken = [];
const pending = new Set();
window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
const voices = [{name: "Test English", lang: "en-US", localService: true},
                {name: "Test Spanish", lang: "es-MX", localService: true},
                {name: "Cloud Spanish", lang: "es-US", localService: false}];
Object.defineProperty(window, "speechSynthesis", {value: {
  getVoices: () => voices,
  addEventListener() {},
  speak(u) {
    window.__spoken.push({text: u.text, lang: u.lang, voice: u.voice && u.voice.name});
    const end = () => { pending.delete(end); u.onend && u.onend(); };
    pending.add(end);
    setTimeout(end, 5);
  },
  cancel() { [...pending].forEach(end => end()); },
}});
let mic, out;
navigator.mediaDevices.getUserMedia = async () => {
  mic = new AudioContext();
  out = mic.createMediaStreamDestination();
  // A live microphone in a quiet room: a faint hiss, never exact zeros (those mean a muted mic).
  const hiss = mic.createBuffer(1, mic.sampleRate, mic.sampleRate);
  hiss.getChannelData(0).forEach((_, i, d) => { d[i] = (Math.random() - 0.5) * 0.001; });
  const floor = mic.createBufferSource();
  floor.buffer = hiss;
  floor.loop = true;
  floor.connect(out);
  floor.start();
  return out.stream;
};
window.__play = async b64 => {
  const buffer = await mic.decodeAudioData(Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer);
  const source = mic.createBufferSource();
  source.buffer = buffer;
  source.connect(out);
  source.start();
};
"""


def clip(text: str, voice: str) -> str:
    """A recorded answer as base64 WAV, made once with the Mac's `say`."""
    import base64

    path = CLIPS / f"{voice}-{hashlib.sha1(text.encode()).hexdigest()[:10]}.wav"
    if not path.exists():
        CLIPS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            aiff = Path(tmp) / "a.aiff"
            subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(path)], check=True)
    return base64.b64encode(path.read_bytes()).decode()


def wait_for_server() -> None:
    for _ in range(50):
        try:
            urllib.request.urlopen(URL + "/forms/w9/interview.en.json", timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    sys.exit("server did not start")


def run(browser, code: str) -> list[str]:
    script, failures = SCRIPTS[code], []
    tab = browser.new_page(viewport={"width": 760, "height": 900})
    tab.add_init_script(FAKES)
    downloads = []
    tab.on("download", lambda d: downloads.append(d))
    tab.on("pageerror", lambda e: failures.append(f"page error: {e}"))
    for old in SHOTS.glob(f"voice-{code}-*"):
        old.unlink()
    tab.goto(URL + "/?form=w9")
    tab.wait_for_selector("[data-voice]")
    tab.screenshot(path=SHOTS / f"voice-{code}-00-start.png", full_page=True)
    tab.click(f"[data-voice={code}]")
    turn, k, retries, last_answer = 0, 0, 0, None
    while k < len(script["turns"]):
        mode, step, words, want = script["turns"][k]
        try:
            tab.wait_for_function("t => document.body.dataset.state === 'listening' && +document.body.dataset.turn > t",
                                  arg=turn, timeout=60_000)
        except TimeoutError:
            state = tab.evaluate("[document.body.dataset.state, document.querySelector('#vstate')?.textContent]")
            tab.screenshot(path=SHOTS / f"voice-{code}-failed.png", full_page=True)
            return [*failures, f"turn {k + 1}: page stopped listening, state {state}"]
        turn = int(tab.evaluate("document.body.dataset.turn"))
        at = tab.evaluate("[document.body.dataset.mode, document.body.dataset.step]")
        pending = tab.evaluate("document.body.dataset.pending")
        retry = True
        if at == [mode, step] and mode == "confirming" and not re.fullmatch(want, pending, re.IGNORECASE):
            words, k = script["no"], k - 1  # misheard: say no, then answer again (as a person would)
        elif at != [mode, step]:
            if not (last_answer and at == list(last_answer[:2]) and retries < MAX_RETRIES):
                failures.append(f"turn {k + 1}: page is on {at}, script expected {[mode, step]}")
                tab.screenshot(path=SHOTS / f"voice-{code}-failed.png", full_page=True)
                tab.close()
                return failures
            words, k = last_answer[2], k - 1  # asked again: repeat the answer
        else:
            retry = False
        retries += retry
        print(f"  {turn:2}. {at[0]:10} {at[1]:15} <- {words or '(silence)'}"
              + (f"   [read back {pending!r}]" if at[0] == "confirming" else ""))
        tab.screenshot(path=SHOTS / f"voice-{code}-{turn:02}-{at[1]}.png", full_page=True)
        k += 1
        if at[0] == "question" and words is not SILENCE:
            last_answer = (*at, words)
        if words is SILENCE:
            if k > 1 and script["turns"][k - 2][2] is SILENCE:
                tab.wait_for_function("document.body.dataset.state === 'paused'", timeout=20_000)
                tab.screenshot(path=SHOTS / f"voice-{code}-{turn:02}-paused.png", full_page=True)
                tab.keyboard.press("Space")
            continue
        tab.evaluate("b64 => window.__play(b64)", clip(words, script["voice"]))
    print(f"  {retries} misheard and repeated")
    tab.wait_for_function("document.body.dataset.state === 'done'", timeout=60_000)
    tab.screenshot(path=SHOTS / f"voice-{code}-99-done.png", full_page=True)
    spoken = tab.evaluate("window.__spoken")
    pdf = SHOTS / f"voice-{code}-filled.pdf"
    if downloads:
        downloads[0].save_as(pdf)
    tab.close()

    (SHOTS / f"voice-{code}-spoken.json").write_text(json.dumps(spoken, indent=2, ensure_ascii=False))
    said = " ".join(u["text"] for u in spoken)
    for want in script["said"]:
        if not any(w in said for w in (want if isinstance(want, tuple) else (want,))):
            failures.append(f"never said {want!r}")
    if any(u["voice"] == "Cloud Spanish" for u in spoken):
        failures.append("spoke with a cloud voice")
    if code != "en":
        english = [u for u in spoken if u["lang"] == "en-US"]
        if not english or any(u["lang"] not in ("en-US", "es-US") for u in spoken):
            failures.append("the form's English words were not read in English, or a wrong language was used")
    if not downloads:
        return [*failures, "no PDF was downloaded"]
    text = pymupdf.open(pdf)[0].get_text()
    flat = pymupdf.open(OUT / "fw9-flat.pdf")[0].get_text()
    for want in script["on_pdf"]:
        if text.count(want) <= flat.count(want):
            failures.append(f"not written on the PDF: {want!r}")
    page, fields = pymupdf.open(pdf)[0], json.loads((OUT / "fields.json").read_text())
    for i, digits in script["boxes"].items():
        if "".join(page.get_text(clip=fields[i]["rect"]).split()) != digits:
            failures.append(f"digit boxes {fields[i]['label']!r} do not read {digits}")
    pymupdf.open(pdf)[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(SHOTS / f"voice-{code}-filled.png")
    return failures


def listening(tab, after: int) -> int:
    """Wait until the page listens for a new reply; its turn number."""
    tab.wait_for_function("t => document.body.dataset.state === 'listening' && +document.body.dataset.turn > t",
                          arg=after, timeout=60_000)
    return int(tab.evaluate("document.body.dataset.turn"))


def run_pausing(browser) -> list[str]:
    tab = browser.new_page(viewport={"width": 760, "height": 900})
    tab.add_init_script(FAKES)
    failures = []
    tab.on("pageerror", lambda e: failures.append(f"page error: {e}"))
    tab.goto(URL + "/?form=w9")
    tab.wait_for_selector("[data-voice]")
    tab.click("[data-voice=en]")
    state = lambda: tab.evaluate("[document.body.dataset.state, document.body.dataset.step]")

    def check(what: str, ok: bool) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)
            tab.screenshot(path=SHOTS / f"voice-pause-failed-{len(failures)}.png", full_page=True)

    # 1. The Pause button stops it mid-question: no more talking or listening until Continue.
    turn = listening(tab, 0)
    tab.click("#vpause")
    said, turns = tab.evaluate("[window.__spoken.length, document.body.dataset.turn]")
    tab.wait_for_timeout(4000)
    check("Pause button pauses at once", state() == ["paused", "name1"])
    check("paused means silent and not listening",
          tab.evaluate("[window.__spoken.length, document.body.dataset.turn]") == [said, turns])
    check("the button now says Continue", tab.inner_text("#vpause").strip() == "Continue")
    tab.screenshot(path=SHOTS / "voice-pause-01-paused.png", full_page=True)
    tab.click("#vpause")
    turn = listening(tab, turn)
    check("Continue asks the same question again", state() == ["listening", "name1"])

    # 2. Answer, then Escape while it reads the next question; Space carries on there.
    tab.evaluate("b64 => window.__play(b64)", clip("My name is Jordan Rivera.", "Samantha"))
    turn = listening(tab, turn)
    tab.evaluate("b64 => window.__play(b64)", clip("Yes.", "Samantha"))
    tab.wait_for_function("document.body.dataset.step === 'name2' && document.body.dataset.state === 'speaking'")
    tab.keyboard.press("Escape")
    check("Escape pauses", state() == ["paused", "name2"])
    check("the answer given before the pause is kept", tab.evaluate("answers.name1") == "Jordan Rivera")
    tab.keyboard.press("Space")
    turn = listening(tab, turn)
    check("Space carries on at the same question", state() == ["listening", "name2"])

    # 3. Saying so pauses too.
    tab.evaluate("b64 => window.__play(b64)", clip("Hold on, can we pause for a minute?", "Samantha"))
    tab.wait_for_function("document.body.dataset.state === 'paused'", timeout=30_000)
    check("saying pause pauses", state() == ["paused", "name2"])
    tab.click("#vpause")
    turn = listening(tab, turn)

    # 4. Three replies that are no answer, here someone else talking nearby (from a real session, where
    # "another" once became the answer "Other"): it never guesses, stops asking, and lets the person choose.
    tab.evaluate("b64 => window.__play(b64)", clip("Skip.", "Samantha"))
    turn = listening(tab, turn)
    check("on the tax classification", state() == ["listening", "taxclass"])
    for k in range(3):
        tab.evaluate("b64 => window.__play(b64)", clip("Are you allowed another conjunction?", "Samantha"))
        if k < 2:
            turn = listening(tab, turn)
    tab.wait_for_function("document.body.dataset.state === 'paused'", timeout=30_000)
    stuck = tab.evaluate("P.stuck")
    check("after three replies that are no answer, it pauses", state() == ["paused", "taxclass"])
    check("and never took one as an answer", tab.evaluate("answers.taxclass") is None)
    check("it suggests moving away from people talking",
          "other people are talking nearby" in tab.evaluate("window.__spoken.map(u => u.text).join(' ')"))
    check("Continue has the focus, so Enter carries on", tab.evaluate("document.activeElement.id") == "vpause")
    check("and says what the person can do",
          tab.evaluate("window.__spoken.map(u => u.text).join(' ')").endswith(stuck))
    tab.wait_for_timeout(500)
    tab.screenshot(path=SHOTS / "voice-pause-02-stuck.png", full_page=True)
    tab.close()
    return failures


def main() -> None:
    codes = sys.argv[1:] or [*SCRIPTS, "pause"]
    SHOTS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "VOICE_CACHE": str(SHOTS / "voice-cache")}
    server = subprocess.Popen([sys.executable, str(HERE / "serve.py")], cwd=HERE, env=env)
    failures = []
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            for code in codes:
                print(f"{code}:")
                failures += [f"{code}: {f}" for f in (run_pausing(browser) if code == "pause" else run(browser, code))]
            browser.close()
    finally:
        server.terminate()
    print("PASS" if not failures else "FAIL")
    for f in failures:
        print("FAIL", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

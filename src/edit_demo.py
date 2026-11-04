"""Cut the recorded app scenes into the demo video, with the narration and captions.

Run record_demo.py first. This keeps the real app's picture and sound, cuts the waiting
between turns, lays in the narration (made with a Higgsfield voice, in out/demo/narration/clean/),
and draws a captions panel beside the app saying who speaks and what:

    uv run edit_demo.py        # writes out/demo/demo.mp4

The opening shot of a person at a kitchen table is AI-generated (Higgsfield), and says so on screen.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf
from playwright.sync_api import sync_playwright

from record_demo import DEMO
from spike import HERE

FPS, RATE = 30, 48000
W, H, APP_W = 1920, 1080, 874
PANEL_W = W - APP_W
NARRATION = DEMO / "narration" / "clean"
FONT = HERE / "web" / "fonts" / "atkinson-hyperlegible-next-latin.woff2"
ARCH = HERE.parent / "architecture" / "architecture.html"
PRODUCT = "Fill Aloud"
REPO = "github.com/vipink1203/fill-aloud"
DUCK = 0.2  # the app's voice under the narrator
GAP = 0.45  # longest wait kept between two turns; longer waits are cut

# What the narrator says; each line was checked by transcribing it back.
LINES = {
    "n01": "Blind people are sent PDF forms every week: tax forms, leases, medical intake.",
    "n02": "Most are flat pages with no fields a screen reader can use.",
    "n03": "Today they ask a sighted person, or paste the form into a chatbot that sends their answers away "
           "and checks nothing.",
    "n04": "This is the IRS W-9, filled in by voice alone.",
    "n05": "It asks one question at a time, only the ones that apply to you.",
    "n06": "Every answer is read back before it counts, because a blind person can't see a typo.",
    "n07": "You can pause at any time, and it picks up where you left off.",
    "n08": "Ask what something means, and you hear the form's own words, not a guess.",
    "n09": "It reads the legal certification word for word, then writes every answer onto the original IRS form, "
           "one digit per box. The form you send is the official one, not a copy.",
    "n10": "It speaks English or Spanish.",
    "n11": "And it works on forms it has never seen: upload one, and it reads the form's own fields, or finds "
           "the blanks on a flat page, and writes the questions once.",
    "n12": "Muse Spark looks at each page and names every blank; our code lines each one up with the box printed "
           "on the page. On the W-9, Muse alone placed 15 of 23 fields; with our lining up, 23 of 23.",
    "n13": "Muse Spark writes the questions from the form and its instructions, and our code checks that every "
           "quote matches the form word for word.",
    "n14": "Each spoken reply goes to Muse Voice Transcribe twice at once, and Muse Spark decides what the person "
           "meant. On recordings of 10 accented speakers, hearing each reply twice cut wrong words from 10% to 4%, "
           "and the read-back catches the rest.",
    "n15": "Everything you type stays on your computer, and the questions are read aloud by the computer's own voices.",
    "n16": "It is proven on IRS forms by keyboard and by voice. We have not yet tested it with blind users; "
           "that is next. The code is open source.",
}
QUOTE = (("Can we enter signatures on pdf forms either in adobe acrobat reader or using markup in ios? "
          "Or do we still need sighted help. I have a form to sign."),
         "I just use AI to sign inaccessible forms now lol.")
FOOTER = "Sample answers, not a real person. Waiting time shortened. Narrator's voice made with AI (Higgsfield)."


def seconds(path: Path) -> float:
    return len(load(path)) / RATE


_audio: dict[Path, np.ndarray] = {}


def load(path: Path) -> np.ndarray:
    """Any sound file as 48 kHz stereo samples."""
    if path not in _audio:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"],
                             capture_output=True, check=True).stdout
        _audio[path] = np.frombuffer(raw, np.float32).reshape(-1, 2)
    return _audio[path]


def loudness(x: np.ndarray) -> float:
    """Level of the speech only: the mean power of the loudest half of 50 ms blocks."""
    blocks = x[: len(x) // 2400 * 2400].reshape(-1, 2400, 2)
    power = np.sort((blocks ** 2).mean(axis=(1, 2)))
    return float(np.sqrt(power[len(power) // 2:].mean()))


@dataclass
class Shot:
    frames: int
    picture: tuple  # ("clip", take, t) | ("freeze", take, t) | ("image", png) | ("left", png) | ("broll", t, speed)
    panel: dict | None  # the captions beside the app; None when the picture fills the frame
    overlay: dict | None = None  # drawn over a full-frame picture
    panel_png: Path | None = None
    overlay_png: Path | None = None


class Edit:
    """The video as a list of shots, and its sound as a list of placed clips."""

    def __init__(self):
        self.shots: list[Shot] = []
        self.sounds: list[tuple[float, Path, float, float, float]] = []  # at, file, from, length, gain
        self.t = 0.0
        self.narrator: tuple[str, float] | None = None  # the narrator's words, shown until this time

    def add(self, dur: float, picture: tuple, panel: dict | None = None, overlay: dict | None = None) -> float:
        n = max(1, round(dur * FPS))
        if panel is not None and self.narrator and self.narrator[1] > self.t:
            panel = {**panel, "narrator": self.narrator[0]}
        self.shots.append(Shot(n, picture, panel, overlay))
        self.t += n / FPS
        return n / FPS

    def sound(self, path: Path, at: float, start: float = 0.0, length: float | None = None, gain: float = 1.0):
        self.sounds.append((at, path, start, length if length is not None else seconds(path) - start, gain))

    def narrate(self, line: str, at: float) -> float:
        path = NARRATION / f"{line}.wav"
        self.sound(path, at, gain=NARRATOR_GAIN)
        self.narrator = (LINES[line], at + seconds(path))
        return seconds(path)


class Take:
    """One recorded scene: picks the turns to keep, in order, and builds their captions."""

    def __init__(self, edit: Edit, name: str, title: str):
        self.e, self.name, self.title = edit, name, title
        self.mp4 = DEMO / f"{name}.mp4"
        self.sounds = json.loads((DEMO / f"{name}.json").read_text())["sounds"]
        self.beeps = [s["at"] for s in self.sounds if s["who"] == "beep"]
        self.sounds = [s for s in self.sounds if s["who"] != "beep"]
        self.cursor = 0
        self.last = 0.0  # the moment in the recording last shown
        self.said: list[list[str]] = []  # the captions: [who, words]

    def find(self, prefix: str) -> dict:
        for k in range(self.cursor, len(self.sounds)):
            if self.sounds[k]["text"].startswith(prefix):
                self.cursor = k + 1
                return self.sounds[k]
        raise LookupError(f"{self.name}: no '{prefix}' after sound {self.cursor}")

    def panel(self) -> dict:
        return {"title": self.title, "said": self.said[-3:]}

    def hear(self, s: dict):
        new_turn = (not self.said or self.said[-1][0] != s["who"]
                    or s["text"].startswith(("Question ", "Pregunta ", "Here is what", "Should I")))
        if new_turn:
            self.said.append([s["who"], s["text"]])
        else:
            self.said[-1][1] += " " + s["text"]

    def open(self, line: str, until: float | None = None):
        """The first moments of the recording (the start page, a press of a button) under a narrator's line."""
        end = until if until is not None else self.sounds[0]["at"]
        at = self.e.t
        length = self.e.narrate(line, at + 0.3)
        shown = self.e.add(min(end, length + 0.6), ("clip", self, 0.0), self.panel())
        if shown < length + 0.6:
            self.e.add(length + 0.6 - shown, ("freeze", self, shown), self.panel())
        self.last = shown

    def play(self, *prefixes: str, line: str | None = None):
        """Keep these sounds, in order, with the picture around them; waits between them are cut short."""
        picks = [self.find(p) for p in prefixes]
        spans = []
        for s in picks:
            if s["who"] == "person":
                beep = max((b for b in self.beeps if s["at"] - 2 < b < s["at"]), default=None)
                start = beep - 0.25 if beep is not None else s["at"] - 0.85
            else:
                start = s["at"] - 0.12
            start = max(start, self.last)
            later = [x["at"] for x in self.sounds if x["at"] > s["at"] + 0.01]
            end = s["at"] + s["seconds"] + max(0.0, min(0.25, (min(later) if later else 1e9) - s["at"] - s["seconds"] - 0.03))
            if spans and start - spans[-1][1] <= GAP:
                spans[-1][1] = max(spans[-1][1], end)
            else:
                spans.append([start, end])
        at0 = self.e.t
        length = self.e.narrate(line, at0 + 0.3) if line else 0.0
        gain = DUCK if line else 1.0
        waiting = list(picks)
        for a, b in spans:
            marks = sorted({a, *(s["at"] for s in picks if a < s["at"] < b), b})
            t = a
            for m0, m1 in itertools.pairwise(marks):
                while waiting and waiting[0]["at"] < m1:
                    s = waiting.pop(0)
                    self.hear(s)
                    if s["who"] == "person" and not self.beeps:  # an older take: the page's beep was not logged
                        self.e.sound(BEEP, max(0.0, self.e.t + s["at"] - m0 - 0.6))
                at = self.e.t
                shown = self.e.add(m1 - t, ("clip", self, t), self.panel())
                self.e.sound(self.mp4, at, t, shown, gain)
                t += shown
            self.last = t
        if line and self.e.t < at0 + length + 0.6:
            self.e.add(at0 + length + 0.6 - self.e.t, ("freeze", self, self.last - 1 / FPS), self.panel())

    def hold(self, line: str, at: float | None = None):
        """Hold one picture of the recording still while the narrator speaks."""
        t = self.last - 1 / FPS if at is None else at
        length = self.e.narrate(line, self.e.t + 0.25)
        self.e.add(length + 0.6, ("freeze", self, t), self.panel())

    def span(self, a: float, b: float):
        """A stretch of the recording as it was, picture only."""
        self.e.add(b - a, ("clip", self, a), self.panel())
        self.last = b


def full(e: Edit, line: str, picture: tuple, overlay: dict | list[dict], lead: float = 0.35, tail: float = 0.5) -> float:
    """A full-frame picture under a narrator's line, its subtitle shown a sentence at a time.

    `overlay` may be a list, one per sentence, to change the picture as the words go on."""
    length = e.narrate(line, e.t + lead)
    parts = re.split(r"(?<=\.) ", LINES[line])
    looks = overlay if isinstance(overlay, list) else [overlay] * len(parts)
    total = sum(map(len, parts))
    shown = 0.0
    for k, (part, look) in enumerate(zip(parts, looks, strict=True)):
        dur = length * len(part) / total + (lead if k == 0 else 0) + (tail if k == len(parts) - 1 else 0)
        pic = ("broll", picture[1] + shown * picture[2], picture[2]) if picture[0] == "broll" else picture
        shown += e.add(dur, pic, None, {**look, "sub": part})
    return shown


def build() -> Edit:
    e = Edit()

    # 1. The problem: the AI-generated opening shot, then the r/Blind post.
    broll = DEMO / "broll.mp4"
    want = sum(seconds(NARRATION / f"{n}.wav") + 0.85 for n in ("n01", "n02"))
    speed = min(1.0, (len(load(broll)) / RATE - 0.2) / want)  # slow the shot down a little if the lines need it
    t = 0.0
    for n in ("n01", "n02"):
        t += full(e, n, ("broll", t, speed), {"kind": "broll"}) * speed
    full(e, "n03", ("image", None), {"kind": "quote"})

    # 2. Fill in the W-9 by voice.
    f = Take(e, "fill", "Filling in the IRS W-9 by voice")
    f.open("n04", until=2.5)  # freeze on the settled start page, not on a page fading in
    f.play("Question 1 of", "What is your name", "Jordan Rivera.", "I heard:", "Jordan Rivera.", "Is that right?", "Yes.")
    f.play("Question 2 of", "What is your business name", line="n05")
    f.play("Skip.", "Skipped.")
    f.play("Question 8 of", "What is your Social Security number?", line="n06")
    f.play("One two three, four five, six seven eight.", *SSN_REPLY, "One two three, four five, six seven eight nine.",
           "I heard:", "1 2 3")
    f.play("Question 9 of", "Have you been told", line="n08")
    f.play("What does that mean?", "The form's exact words:", "You must cross out")

    # 3. Sign and finish, then the filled form.
    f.title = "Signing and finishing"
    f.said = []
    f.play("Do you certify that this is true?", "Yes, I certify.")
    f.play("Your form is filled.")
    e.add(1.6, ("left", "pdf-page"), f.panel())  # the downloaded form, then its answers up close
    full(e, "n09", ("image", None), [{"kind": "photo", "image": "pdf-b"}, {"kind": "photo", "image": "pdf-a"}])

    # 4. Spanish, and a form it has never seen.
    s = Take(e, "spanish", "En español: the same W-9 in Spanish")
    s.play("Pregunta 1 de", "¿Cuál es su nombre", line="n10")
    s.play("Me llamo Jordan Rivera.", "Escuché:", "Jordan Rivera.", "¿Es correcto?", "Sí.")
    u = Take(e, "upload", "A form it has never seen: the IRS W-4")
    u.open("n11", until=2.5)
    u.play("Question 1 of", "What is your first name", "Jordan.")

    # 5. How it works, on the architecture diagram.
    for n in ("n12", "n13", "n14", "n15"):
        full(e, n, ("image", None), {"kind": "arch", "spot": SPOTS[n]})

    # 6. What is proven, and what is not.
    full(e, "n16", ("image", None), {"kind": "close"}, tail=2.5)
    return e


# Which boxes of the architecture diagram each line is about.
SPOTS = {
    "n12": ["Finds every blank", "Lines up with boxes"],
    "n13": ["Writes plain questions", "Checks every quote"],
    "n14": ["Asks one at a time", "Understands each reply"],
    "n15": ["On the person's computer", "Asks one at a time", "Writes answers on form", "Filled original form"],
}
SSN_REPLY: tuple[str, ...] = ("I need 9 digits", "Please say it again.")
BEEP = DEMO / "speech" / "beep.wav"
NARRATOR_GAIN = 1.0


STUDIO = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: A; src: url(FONT) format("woff2"); font-weight: 400 700; }
:root { --bg: #f4efe6; --surface: #fffcf6; --sunk: #f7f2e8; --text: #1c1915; --muted: #554d43; --line: #e4dacb;
        --accent: #0b5c50; --accent-soft: #e1eee8; --live: #a8360f; --live-soft: #fbe7da; }
* { box-sizing: border-box; }
html, body { margin: 0; background: transparent; font-family: A, sans-serif; color: var(--text);
             -webkit-font-smoothing: antialiased; text-wrap: pretty; }
#stage { width: 1920px; height: 1080px; position: relative; overflow: hidden; }
#panel { width: PANELpx; height: 1080px; background: var(--bg); padding: 76px 96px 52px 36px;
         display: flex; flex-direction: column; }
.eyebrow { font-size: 21px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--accent); }
.title { font-size: 42px; font-weight: 700; line-height: 1.15; margin-top: 12px; letter-spacing: -.01em; }
.said { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; gap: 30px; padding-bottom: 8px;
        overflow: hidden; }
.turn { border-left: 5px solid var(--accent); padding: 4px 0 6px 26px; }
.turn.person { border-color: var(--live); }
.turn.old { opacity: .42; }
.who { font-size: 19px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--accent); }
.person .who { color: var(--live); }
.words { font-size: 36px; line-height: 1.32; font-weight: 500; margin-top: 6px; }
.person .words { font-weight: 700; }
.narrator { margin-top: 30px; background: var(--surface); border: 1px solid var(--line); border-radius: 22px;
            padding: 22px 28px 24px; box-shadow: 0 1px 2px rgb(70 45 10 / .06), 0 12px 32px -8px rgb(70 45 10 / .12); }
.narrator .who { color: var(--muted); }
.narrator .words { font-size: 30px; font-weight: 500; }
.footer { margin-top: 26px; font-size: 18px; line-height: 1.4; color: var(--muted); }
.sub { position: absolute; left: 50%; bottom: 64px; transform: translateX(-50%); width: max-content; max-width: 1480px;
       background: rgb(20 18 15 / .78); color: #fff; font-size: 40px; line-height: 1.3; font-weight: 500;
       padding: 18px 34px 20px; border-radius: 18px; text-align: center; text-wrap: balance; }
.chip { position: absolute; left: 48px; top: 44px; background: rgb(20 18 15 / .72); color: #fff; font-size: 21px;
        font-weight: 700; padding: 10px 18px; border-radius: 12px; letter-spacing: .02em; }
.card { width: 1920px; height: 1080px; background: var(--bg); padding: 80px 200px 230px; display: flex;
        flex-direction: column; justify-content: center; }
.quote { font-size: 50px; line-height: 1.3; font-weight: 500; max-width: 1400px; }
.quote.reply { margin-top: 44px; padding-left: 40px; border-left: 6px solid var(--live); font-weight: 700; }
.source { margin-top: 40px; font-size: 26px; color: var(--muted); }
.close h1 { font-size: 64px; margin: 0 0 40px; letter-spacing: -.015em; }
.close .row { display: flex; gap: 22px; align-items: baseline; font-size: 34px; line-height: 1.35; margin-top: 18px; }
.close .tag { flex: none; width: 210px; font-size: 21px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase;
              color: var(--accent); }
.close .tag.not { color: var(--live); }
.close .repo { margin-top: 56px; font-size: 34px; font-weight: 700; color: var(--accent); }
.close .made { margin-top: 10px; font-size: 24px; color: var(--muted); }
.arch { width: 1920px; height: 1080px; background: #f9f9f9 no-repeat; position: relative; }
.spot { position: absolute; border-radius: 20px; border: 4px solid #0b5c50;
        box-shadow: 0 0 0 4000px rgb(20 18 15 / .5); }
</style></head><body><div id="stage"></div><script>
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const WHO = {app: "The app", person: "Person"};
function panel(p) {
  const turns = p.said.map((t, k) => `<div class="turn ${t[0]} ${k < p.said.length - 1 ? "old" : ""}">
      <div class="who">${WHO[t[0]]}</div><div class="words">${esc(t[1])}</div></div>`).join("");
  const narr = p.narrator ? `<div class="narrator"><div class="who">Narrator</div>
      <div class="words">${esc(p.narrator)}</div></div>` : "";
  return `<div id="panel"><div class="eyebrow">PRODUCT</div><div class="title">${esc(p.title)}</div>
    <div class="said">${turns}</div>${narr}<div class="footer">FOOTER</div></div>`;
}
function overlay(o) {
  const sub = o.sub ? `<div class="sub">${esc(o.sub)}</div>` : "";
  if (o.kind === "broll") return `<div class="chip">AI-generated footage (Higgsfield)</div>${sub}`;
  if (o.kind === "quote") return `<div class="card"><div class="eyebrow">r/Blind, October 2026</div>
      <div class="quote" style="margin-top:36px">&ldquo;${esc(o.quote[0])}&rdquo;</div>
      <div class="quote reply">A reply: &ldquo;${esc(o.quote[1])}&rdquo;</div></div>${sub}`;
  if (o.kind === "photo") return `<div class="arch" style="background-image:url(${o.image});background-size:cover"></div>${sub}`;
  if (o.kind === "arch") return `<div class="arch" style="background-image:url(${o.image});background-size:${1920 * o.z}px ${1080 * o.z}px;
      background-position:${-o.x0 * o.z}px ${-o.y0 * o.z}px">${o.rects.map(r =>
      `<div class="spot" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px"></div>`).join("")}</div>${sub}`;
  if (o.kind === "close") return `<div class="card close"><div class="eyebrow">PRODUCT</div>
      <h1 style="margin-top:14px">What is proven, and what is not</h1>${o.rows.map(r =>
      `<div class="row"><div class="tag ${r[0] === "Not yet" ? "not" : ""}">${r[0]}</div><div>${esc(r[1])}</div></div>`).join("")}
      <div class="repo">REPO</div><div class="made">Built with Meta's Muse Spark and Muse Voice Transcribe.</div></div>${sub}`;
}
function show(html) { document.getElementById("stage").innerHTML = html; return document.fonts.ready; }
</script></body></html>"""

CLOSE_ROWS = [
    ["Proven", "Fills the IRS W-9 end to end by keyboard, and by voice in English and Spanish."],
    ["Proven", "Places 23 of 23 fields on the flat W-9; Muse alone placed 15."],
    ["Proven", "Hearing each reply twice cut word errors from 10.3% to 4.1% on 10 accented speakers."],
    ["Proven", "Upload works on three IRS forms, every answer on the returned PDF."],
    ["Not yet", "Tested with blind users. Next: 3 screen-reader users each fill the W-9."],
]


def pictures(e: Edit, work: Path):
    """Draw every panel, card and overlay the shots need, each once."""
    html = (STUDIO.replace("FONT", FONT.as_uri()).replace("PANEL", str(PANEL_W)).replace("PRODUCT", PRODUCT)
            .replace("FOOTER", FOOTER).replace("REPO", REPO))
    (work / "studio.html").write_text(html)
    drawn: dict[str, Path] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        arch = browser.new_page(viewport={"width": W, "height": H}, color_scheme="light", device_scale_factor=2)
        arch.goto(ARCH.as_uri())
        arch.wait_for_timeout(2500)
        arch.evaluate("""() => { for (const b of document.querySelectorAll('button')) {
            const t = b.textContent.trim();
            if (['Export', 'Still', 'Light', 'Classic'].includes(t) || t.startsWith('ROUTE'))
              b.parentElement.style.visibility = 'hidden'; } }""")
        arch.screenshot(path=work / "arch.png")
        boxes = {}
        for labels in SPOTS.values():
            for label in labels:
                boxes[label] = arch.evaluate("""label => {
                  const el = [...document.querySelectorAll('svg text, svg tspan')].find(x => x.textContent.trim().startsWith(label));
                  let n = el; while (n && n.getBoundingClientRect().width < 150) n = n.parentElement;
                  const r = n.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; }""", label)
        pdf(work)
        tab = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        tab.goto((work / "studio.html").as_uri())

        def draw(kind: str, data: dict, size: tuple[int, int], clear: bool = False) -> Path:
            key = hashlib.sha1(json.dumps([kind, data], sort_keys=True).encode()).hexdigest()[:16]
            if key not in drawn:
                tab.evaluate(f"d => show({kind}(d))", data)
                drawn[key] = work / f"{key}.png"
                tab.screenshot(path=drawn[key], clip={"x": 0, "y": 0, "width": size[0], "height": size[1]},
                               omit_background=clear)
            return drawn[key]

        for shot in e.shots:
            if shot.panel is not None:
                shot.panel_png = draw("panel", shot.panel, (PANEL_W, H))
            if shot.overlay is not None:
                o = dict(shot.overlay)
                if o["kind"] == "quote":
                    o["quote"] = QUOTE
                if o["kind"] == "close":
                    o["rows"] = CLOSE_ROWS
                if o["kind"] == "arch":
                    o |= {"image": (work / "arch.png").as_uri(), **zoom(spot([boxes[label] for label in o.pop("spot")]))}
                if o["kind"] == "photo":
                    o["image"] = (work / f"{o['image']}.png").as_uri()
                shot.overlay_png = draw("overlay", o, (W, H), clear=o["kind"] == "broll")
        browser.close()


def zoom(r: dict) -> dict:
    """Move in on the boxes being talked about, keeping them clear of the subtitle."""
    z = max(1.0, min(2.0, W / (r["w"] + 480), 820 / (r["h"] + 160)))
    vw, vh = W / z, H / z
    x0 = min(max(r["x"] + r["w"] / 2 - vw / 2, 0), W - vw)
    y0 = min(max(r["y"] + r["h"] / 2 - vh * 0.4, 0), H - vh)
    return {"z": z, "x0": x0, "y0": y0,
            "rects": [{"x": (r["x"] - x0) * z, "y": (r["y"] - y0) * z, "w": r["w"] * z, "h": r["h"] * z}]}


def spot(rects: list[dict]) -> dict:
    x0 = min(r["x"] for r in rects) - 22
    y0 = min(r["y"] for r in rects) - 22
    x1 = max(r["x"] + r["w"] for r in rects) + 22
    y1 = max(r["y"] + r["h"] for r in rects) + 22
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def pdf(work: Path):
    """The filled W-9 as the person gets it: the whole first page, then its filled lines up close."""
    page = pymupdf.open(DEMO / "fill-filled.pdf")[0]
    whole = page.get_pixmap(dpi=300)
    _place(whole, min((APP_W - 72) / whole.width, (H - 72) / whole.height), work / "pdf-page.png", APP_W)
    for name, rect in PDF_ZOOMS.items():
        pix = page.get_pixmap(dpi=300, clip=pymupdf.Rect(*rect))
        _place(pix, W / pix.width, work / f"{name}.png", W)


# Up close, the full width of the page, in points: the title and lines 1 to 6; the SSN, certification and signature.
PDF_ZOOMS = {"pdf-a": (0, 20, 612, 365), "pdf-b": (0, 345, 612, 690)}


def _place(pix, scale: float, out: Path, width: int):
    src = out.with_suffix(".src.png")
    pix.save(src)
    w, h = round(pix.width * scale), round(pix.height * scale)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                    f"scale={w}:{h}:flags=lanczos,pad={width}:{max(H, h)}:(ow-iw)/2:(oh-ih)/2:color=#f4efe6,crop={width}:{H}:0:0", str(out)], check=True)


def render(e: Edit, work: Path, out: Path):
    enc = ["-r", str(FPS), "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", "-an"]
    listing = []
    for k, shot in enumerate(e.shots):
        seg = work / f"seg{k:04}.mp4"
        kind = shot.picture[0]
        if kind in ("clip", "freeze"):
            take, t = shot.picture[1], shot.picture[2]
            left = ("[0:v]trim=end_frame=1,loop=loop=-1:size=1,setpts=N/30/TB[l]" if kind == "freeze"
                    else "[0:v]setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=30[l]")
            inputs = ["-ss", f"{t:.3f}", "-i", str(take.mp4), "-loop", "1", "-i", str(shot.panel_png)]
            graph = f"{left};[l][1:v]hstack=2,format=yuv420p"
        elif kind == "left":
            inputs = ["-loop", "1", "-i", str(work / f"{shot.picture[1]}.png"), "-loop", "1", "-i", str(shot.panel_png)]
            graph = "[0:v][1:v]hstack=2,format=yuv420p"
        elif kind == "broll":
            t, speed = shot.picture[1], shot.picture[2]
            inputs = ["-ss", f"{t:.3f}", "-i", str(DEMO / "broll.mp4"), "-loop", "1", "-i", str(shot.overlay_png)]
            graph = (f"[0:v]setpts=(PTS-STARTPTS)/{speed:.4f},scale={W}:{H}:flags=lanczos,fps={FPS},"
                     f"tpad=stop_mode=clone:stop_duration=30[b];[b][1:v]overlay=0:0,format=yuv420p")
        else:
            inputs = ["-loop", "1", "-i", str(shot.overlay_png)]
            graph = "[0:v]format=yuv420p"
        if k == 0:
            graph += ",fade=in:d=0.8"
        if k == len(e.shots) - 1:
            graph += f",fade=out:st={shot.frames / FPS - 1.2:.3f}:d=1.2"
        # Every segment in one format: the screencast is full-range JPEG, and a join of mixed formats drops shots.
        graph += ",scale=out_range=tv,format=yuv420p,setsar=1"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-frames:v", str(shot.frames),
                        *enc, str(seg)], check=True)
        listing.append(f"file '{seg}'")
    (work / "list.txt").write_text("\n".join(listing) + "\n")
    video = work / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                    "-c", "copy", str(video)], check=True)

    mix = np.zeros((int((e.t + 1) * RATE), 2), np.float32)
    for at, path, start, length, gain in e.sounds:
        x = load(path)[int(start * RATE): int((start + length) * RATE)]
        i = int(at * RATE)
        x = x[: len(mix) - i]
        ramp = min(len(x) // 2, 240)  # 5 ms in and out, so cuts never click
        if ramp:
            env = np.ones(len(x), np.float32)
            env[:ramp] = np.linspace(0, 1, ramp)
            env[-ramp:] = np.linspace(1, 0, ramp)
            x = x * env[:, None]
        mix[i: i + len(x)] += x * gain
    audio = work / "audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "-",
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(RATE), str(audio)],
                   input=mix.tobytes(), check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
                   check=True)


def main() -> None:
    global NARRATOR_GAIN
    if not BEEP.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.12",
                        "-af", "volume=0.12", str(BEEP)], check=True)
    # The narrator as loud as the app's voice, measured on the speech itself.
    NARRATOR_GAIN = loudness(load(DEMO / "fill.mp4")) / loudness(np.concatenate(
        [load(p) for p in sorted(NARRATION.glob("n*.wav"))]))
    e = build()
    print(f"{len(e.shots)} shots, {e.t:.1f} s; narrator gain {NARRATOR_GAIN:.2f}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pictures(e, work)
        render(e, work, DEMO / "demo.mp4")
    print("saved", DEMO / "demo.mp4")


if __name__ == "__main__":
    main()

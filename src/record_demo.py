"""Record the demo video's app scenes from the real app, with sound.

The page runs for real: the real server, Muse Voice Transcribe and Muse Spark. Two things
stand in for the Mac's own: the page's speech is spoken by `say` with the same voices Chrome
would use, and the person's answers are spoken by another `say` voice into a fake microphone.
Each scene is saved as an MP4 in out/demo/, picture and sound in step, ready to edit.

The picture is taken with Chrome's own screencast, so every frame is the real page.

    uv run record_demo.py              # every scene
    uv run record_demo.py fill         # one of fill, spanish, upload
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from e2e_voice import ADDRESS, CITY, CLIPS, TODAY, URL, clip, wait_for_server
from spike import HERE, OUT

DEMO = OUT / "demo"
SIZE = {"width": 700, "height": 864}  # at 1.25 scale, every frame is 875x1080: the left of a 1080p frame
APP_VOICE = {"en-US": "Samantha", "es-MX": "Flo (Spanish (Mexico))"}
PERSON_VOICE = {"en": "Reed (English (US))", "es": "Paulina"}
CONFIRM = {"en": ("Yes.", "No."), "es": ("Sí.", "No.")}

# What the person says at each question, in order; the last reply is said again if it is asked
# again. A read-back is answered yes when it matches the pattern, else no.
SCENES = {
    "fill": {
        "code": "en",
        "say": {
            "name1": ["Jordan Rivera."],
            "name2": ["Skip."],
            "taxclass": ["I'm a sole proprietor."],
            "address": ["Twelve hundred Elm Street, apartment four."],
            "city_state_zip": ["Springfield, Illinois, six two seven zero one."],
            "account_numbers": ["Skip."],
            "tin_type": ["My social security number."],
            "ssn_digits": ["One two three, four five, six seven eight.", "One two three, four five, six seven eight nine."],
            "backup_crossout": ["What does that mean?", "No, never."],
            "certify": ["Yes, I certify."],
            "signature": ["Jordan Rivera."],
            "sign_date": ["Today."],
            "review": ["Yes, fill it in."],
        },
        "want": {"name1": "Jordan Rivera", "taxclass": "individual", "address": ADDRESS, "city_state_zip": CITY,
                 "tin_type": "ssn", "ssn_digits": "123456789", "backup_crossout": "no", "signature": "Jordan Rivera",
                 "sign_date": TODAY},
        "pause_at": "address",  # press Pause while it asks, wait, press Continue
        "until": "done",
    },
    "spanish": {
        "code": "es",
        "say": {"name1": ["Me llamo Jordan Rivera."]},
        "want": {"name1": "Jordan Rivera"},
        "until": "name2",
    },
    "upload": {
        "code": "en",
        "pdf": HERE / "input/dl/fw4.pdf",
        "say": {"first_name": ["Jordan."], "last_name": ["Rivera."]},
        "want": {"first_name": "Jordan", "last_name": "Rivera"},
        "until": "address",
    },
}

# The page's speech goes to `say`, and the page waits as long as it really takes to say it.
FAKES = """
window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
const voices = [{name: "Samantha", lang: "en-US", localService: true},
                {name: "Paulina", lang: "es-MX", localService: true}];
let timer = null, current = null;
Object.defineProperty(window, "speechSynthesis", {value: {
  getVoices: () => voices,
  addEventListener() {},
  async speak(u) {
    const ms = await window.__say(u.text, u.voice ? u.voice.lang : u.lang);
    current = u;
    timer = setTimeout(() => { current = null; u.onend && u.onend(); }, ms);
  },
  cancel() {
    if (!current) return;
    clearTimeout(timer);
    window.__hush();
    const u = current; current = null;
    u.onend && u.onend();
  },
}});
// The page's beep before it listens is logged, so the video can play it at the same moment.
const oscillator = AudioContext.prototype.createOscillator;
AudioContext.prototype.createOscillator = function () { window.__beep(); return oscillator.call(this); };
let mic, out;
navigator.mediaDevices.getUserMedia = async () => {
  mic = new AudioContext();
  out = mic.createMediaStreamDestination();
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


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


class Recorder:
    """Chrome's screencast frames and every sound, each stamped with the wall clock."""

    def __init__(self, tab, folder: Path):
        self.tab, self.folder = tab, folder
        self.frames: list[tuple[float, Path]] = []
        self.sounds: list[dict] = []
        folder.mkdir(parents=True, exist_ok=True)
        tab.expose_function("__say", self.say)
        tab.expose_function("__hush", self.hush)
        tab.expose_function("__beep", self.beep)
        self.cdp = tab.context.new_cdp_session(tab)
        self.cdp.on("Page.screencastFrame", self.frame)

    def start(self):
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 875, "maxHeight": 1080})

    def stop(self):
        self.tab.wait_for_timeout(1500)
        self.cdp.send("Page.stopScreencast")

    def frame(self, event):
        path = self.folder / f"f{len(self.frames):05}.jpg"
        path.write_bytes(base64.b64decode(event["data"]))
        self.frames.append((event["metadata"]["timestamp"], path))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def say(self, text: str, lang: str) -> int:
        voice = APP_VOICE.get(lang, "Samantha")
        path = DEMO / "speech" / f"{voice}-{hashlib.sha1(text.encode()).hexdigest()[:12]}.aiff"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["say", "-v", voice, "-o", str(path), text], check=True)
        seconds = duration(path)
        self.sounds.append({"at": time.time(), "path": path, "seconds": seconds, "who": "app", "text": text})
        return int(seconds * 1000)

    def beep(self):
        path = DEMO / "speech" / "beep.wav"  # the page's own: 880 Hz for 0.12 s at a gain of 0.12
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.12",
                            "-af", "volume=0.12", str(path)], check=True)
        self.sounds.append({"at": time.time(), "path": path, "seconds": 0.12, "who": "beep", "text": ""})

    def hush(self):
        for s in self.sounds:
            if s["at"] + s["seconds"] > time.time():
                s["seconds"] = time.time() - s["at"]

    def answer(self, words: str, voice: str):
        b64 = clip(words, voice)
        path = CLIPS / f"{voice}-{hashlib.sha1(words.encode()).hexdigest()[:10]}.wav"
        self.sounds.append({"at": time.time(), "path": path, "seconds": duration(path), "who": "person", "text": words})
        self.tab.evaluate("b64 => window.__play(b64)", b64)

    def save(self, mp4: Path):
        """Frames held until the next one, and each sound placed at the moment it was made."""
        t0 = self.frames[0][0]
        with tempfile.TemporaryDirectory() as tmp:
            listing = Path(tmp) / "frames.txt"
            lines = []
            for (t, path), (t_next, _) in zip(self.frames, [*self.frames[1:], (self.frames[-1][0] + 1, None)]):
                lines += [f"file '{path}'", f"duration {max(t_next - t, 0.001):.4f}"]
            listing.write_text("\n".join([*lines, f"file '{self.frames[-1][1]}'"]) + "\n")
            sounds = [s for s in self.sounds if s["at"] >= t0 and s["seconds"] > 0.05]
            inputs = [a for s in sounds for a in ("-i", str(s["path"]))]
            mix = "".join(f"[{i + 1}:a]atrim=0:{s['seconds']:.3f},adelay={int((s['at'] - t0) * 1000)}:all=1[a{i}];"
                          for i, s in enumerate(sounds))
            mix += "".join(f"[a{i}]" for i in range(len(sounds))) + f"amix=inputs={len(sounds)}:normalize=0[a]"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                            *inputs, "-filter_complex", mix, "-map", "0:v", "-map", "[a]",
                            "-vf", "scale=874:1080:flags=lanczos,format=yuv420p", "-r", "30",
                            "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-c:a", "aac", "-b:a", "192k",
                            "-shortest", str(mp4)], check=True)


def interview(tab, rec: Recorder, scene: dict) -> list[str]:
    """Answer each question the page asks, as the scene says, until it reaches the scene's end."""
    code, problems, turn, asked = scene["code"], [], 0, {}
    yes, no = CONFIRM[code]
    for _ in range(60):
        deadline = time.time() + 300
        while time.time() < deadline:  # polled, so the screencast's frames keep being taken
            state, mode, step, pending, now = tab.evaluate(
                "['state', 'mode', 'step', 'pending', 'turn'].map(k => document.body.dataset[k] ?? '')")
            if state == "done" or step == scene["until"] or state == "listening" and int(now or 0) > turn:
                break
            tab.wait_for_timeout(200)
        else:
            return [*problems, f"stopped at {state} {mode} {step}"]
        if state == "done" or step == scene["until"]:
            return problems
        turn = int(now)
        if tab.evaluate("document.getElementById('screen').getBoundingClientRect().bottom > innerHeight") and "cut off" not in problems:
            problems.append("cut off")  # part of the page is below the frame
        if mode == "confirming":
            ok = re.fullmatch(scene["want"].get(step, ".*"), pending, re.IGNORECASE)
            if not ok:
                problems.append(f"{step}: read back {pending!r}")
            words = yes if ok else no
        elif step == scene.get("pause_at") and step not in asked:
            asked[step] = 0
            tab.click("#vpause")
            tab.wait_for_timeout(2500)
            tab.click("#vpause")
            continue
        else:
            replies = scene["say"][step]
            n = asked.get(step, 0)
            asked[step] = n + 1
            words = replies[min(n, len(replies) - 1)]
        print(f"  {mode:10} {step:16} <- {words}" + (f"   [read back {pending!r}]" if mode == "confirming" else ""))
        tab.wait_for_timeout(600)  # a person takes a moment before answering
        rec.answer(words, PERSON_VOICE[code])
    return [*problems, "did not reach the end"]


def record(browser, name: str) -> list[str]:
    scene = SCENES[name]
    folder = DEMO / name
    for old in folder.glob("*"):
        old.unlink()
    context = browser.new_context(viewport=SIZE, device_scale_factor=1.25, accept_downloads=True)
    tab = context.new_page()
    tab.add_init_script(FAKES)
    rec = Recorder(tab, folder)
    tab.goto(URL)
    tab.wait_for_selector("[data-file]")
    rec.start()
    tab.wait_for_timeout(1500)
    if "pdf" in scene:
        tab.set_input_files("#file", str(scene["pdf"]))
    else:
        tab.click(f"[data-sample={scene['code']}]")
    tab.wait_for_selector(f"[data-voice={scene['code']}]", timeout=300_000)
    tab.wait_for_timeout(2000)
    tab.click(f"[data-voice={scene['code']}]")
    problems = interview(tab, rec, scene)
    if scene["until"] == "done":
        with tab.expect_download() as download:
            tab.click("#download")
        download.value.save_as(DEMO / f"{name}-filled.pdf")
        tab.wait_for_timeout(2000)
    else:
        while tab.evaluate("document.body.dataset.state") != "listening":
            tab.wait_for_timeout(200)  # polled: wait_for_function stalls while the screencast runs
    rec.stop()
    context.close()
    rec.save(DEMO / f"{name}.mp4")
    t0 = rec.frames[0][0]  # the timeline, for the edit: who spoke, what, and when in the video
    (DEMO / f"{name}.json").write_text(json.dumps({
        "seconds": round(rec.frames[-1][0] - t0, 2),
        "sounds": [{"at": round(x["at"] - t0, 2), "seconds": round(x["seconds"], 2), "who": x["who"], "text": x["text"]}
                   for x in rec.sounds if x["at"] >= t0],
    }, indent=2, ensure_ascii=False))
    return problems


def main() -> None:
    names = sys.argv[1:] or list(SCENES)
    DEMO.mkdir(parents=True, exist_ok=True)
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 4420)) == 0:
            sys.exit(f"something is already serving {URL}; stop it, so this records the code as it is now")
    server = subprocess.Popen([sys.executable, str(HERE / "serve.py")], cwd=HERE)
    problems = []
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for name in names:
                print(f"{name}:")
                problems += [f"{name}: {x}" for x in record(browser, name)]
                print(f"  saved {DEMO / name}.mp4")
            browser.close()
    finally:
        server.terminate()
    for x in problems:
        print("CHECK", x)


if __name__ == "__main__":
    main()

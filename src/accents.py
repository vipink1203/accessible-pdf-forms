"""How well Muse Voice Transcribe hears English spoken with different accents.

Real people from the Speech Accent Archive (George Mason University, CC BY-NC-SA 4.0) all read the
same paragraph. Each recording goes through the page's own audio path (mono, 16-bit, 24 kHz WAV)
and is scored by word error rate against the paragraph. Recordings are downloaded once into
out/accents/ and never committed. One run is about 5 minutes of audio, under $0.02.

    uv run accents.py              # every speaker
    uv run accents.py hindi1 thai1
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from spike import OUT
from voice import PRICE_PER_HOUR, transcribe

FOLDER = "https://api.osf.io/v2/nodes/yh23d/files/osfstorage/698a8f0a9a43a780c6c72b76/"  # the archive's mp3s on OSF
SPEAKERS = ["english1", "mandarin1", "mandarin2", "cantonese1", "hindi1", "tamil1", "urdu1", "vietnamese1",
            "korean1", "japanese1", "tagalog1", "thai1"]
PARAGRAPH = ("Please call Stella. Ask her to bring these things with her from the store: six spoons of fresh snow "
             "peas, five thick slabs of blue cheese, and maybe a snack for her brother Bob. We also need a small "
             "plastic snake and a big toy frog for the kids. She can scoop these things into three red bags, and we "
             "will go meet her Wednesday at the train station.")
HERE = OUT / "accents"


def words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate: substitutions, insertions and deletions over the reference length."""
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / len(ref)


def recording(name: str) -> bytes:
    wav = HERE / f"{name}.wav"
    if not wav.exists():
        HERE.mkdir(parents=True, exist_ok=True)
        query = FOLDER + "?" + urllib.parse.urlencode({"filter[name]": f"{name}.mp3"})
        with urllib.request.urlopen(query, timeout=30) as r:
            match = [f for f in json.load(r)["data"] if f["attributes"]["name"] == f"{name}.mp3"]
        mp3 = HERE / f"{name}.mp3"
        urllib.request.urlretrieve(match[0]["links"]["download"], mp3)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(mp3), str(wav)], check=True)
    return wav.read_bytes()


def main() -> None:
    ref, spent, scores = words(PARAGRAPH), 0.0, []
    for name in sys.argv[1:] or SPEAKERS:
        heard = transcribe(recording(name), "English")
        spent += (heard.get("audioDurationMs") or 0) / 3_600_000 * PRICE_PER_HOUR
        score = wer(ref, words(heard.get("transcript", "")))
        scores.append(score)
        print(f"{name:12} WER {score:5.1%}  {heard.get('transcript', '')[:90]}")
    print(f"mean WER {sum(scores) / len(scores):.1%}, ${spent:.4f}")


if __name__ == "__main__":
    main()

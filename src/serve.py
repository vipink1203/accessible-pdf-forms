"""Serve the guided interview on this computer and write the answers onto the original PDF.

The sample form is the IRS W-9 made in the proof (spike.py, interview.py, translate.py). Any other
PDF can be uploaded: forms.py turns it into an interview once, in the background, and keeps it, so
the same file opens at once next time. A form's Spanish questions are written the first time
someone picks Spanish for it.

Typed answers stay on this computer. In voice mode each spoken reply goes to Meta's Muse models
to be understood (see voice.py). Answers are never saved or logged here, unless you set VOICE_DEBUG
to a folder to keep your own spoken replies while checking a microphone.

    uv run serve.py     # then open http://127.0.0.1:4420
    VOICE_DEBUG=out/debug uv run serve.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import openai
import pymupdf

from forms import LIBRARY, MAX_BYTES, Rejected, form_id, prepare, ready
from spike import HERE, OUT, ZOOM, write_answers
from translate import LANGUAGES, speakable, translate_form
from voice import hear

PORT = 4420
SAMPLE = "w9"
STATIC = {
    "/": (HERE / "web" / "interview.html", "text/html; charset=utf-8"),
    "/font.woff2": (HERE / "web" / "fonts" / "atkinson-hyperlegible-next-latin.woff2", "font/woff2"),
}
ROUTE = re.compile(r"^/forms/(w9|[0-9a-f]{16})/(status|interview\.([a-z]{2})\.json)$")
MODES = {"question", "confirming", "certify", "review"}
# Off unless set: a folder that keeps each spoken reply (WAV) and what was heard, to diagnose a microphone.
DEBUG = Path(os.environ["VOICE_DEBUG"]) if os.environ.get("VOICE_DEBUG") else None
SERVICE = (httpx.HTTPError, openai.OpenAIError, json.JSONDecodeError)


@dataclass
class Form:
    folder: Path
    blank: Path  # the PDF the answers are written onto
    fields: list[dict]
    langs: dict[str, dict] = field(default_factory=dict)


FORMS: dict[str, Form] = {}
JOBS: dict[str, dict] = {}  # forms being prepared: id to {"stage", ...}
LOCK = threading.Lock()  # one translation at a time, so two clicks never pay twice


def load(fid: str) -> Form | None:
    if fid not in FORMS:
        if fid == SAMPLE:
            FORMS[fid] = Form(OUT, OUT / "fw9-flat.pdf", json.loads((OUT / "fields.json").read_text()))
        elif ready(LIBRARY / fid):
            folder = LIBRARY / fid
            FORMS[fid] = Form(folder, folder / "source.pdf", json.loads((folder / "fields.json").read_text()))
    return FORMS.get(fid)


def interview(form: Form, code: str) -> dict:
    """The form's interview in a language; an uploaded form's translation is made on first use."""
    if code not in form.langs:
        path = form.folder / f"interview.{code}.json"
        with LOCK:
            if not path.exists():
                source = json.loads((form.folder / "interview.json").read_text())
                shared = json.loads((OUT / f"interview.{code}.json").read_text())
                translation = translate_form(code, source, form.folder / f"translation-{code}-raw.json", shared)
                path.write_text(json.dumps(speakable(code, source, translation), indent=2, ensure_ascii=False))
        form.langs[code] = json.loads(path.read_text())
    return form.langs[code]


def work(fid: str, data: bytes) -> None:
    """Prepare an uploaded form in the background; the page follows along through /status."""
    def report(stage: str, **kw) -> None:
        JOBS[fid] = {"stage": stage, **kw}

    try:
        prepare(data, report)
        report("ready")
    except Rejected as e:
        report("failed", error=e.code, limit=e.limit)
    except SERVICE + (ValueError,) as e:
        print(f"/upload failed {type(e).__name__}", file=sys.stderr)
        report("failed", error="service")
    except Exception as e:  # noqa: BLE001 - the thread's last stop: a PDF broken in some other way
        print(f"/upload failed {type(e).__name__}: {e}", file=sys.stderr)
        report("failed", error="broken")


def clean(raw: dict, fields: list[dict]) -> dict[int, str]:
    """Accept only known fields, short single-line text, and digits in digit boxes."""
    values = {}
    for key, value in raw.items():
        i = int(key)
        if not 0 <= i < len(fields) or not isinstance(value, str):
            raise ValueError(f"bad field {key}")
        value = " ".join(value.split())[:200]
        if fields[i].get("cells") and not (value.isdigit() and len(value) == fields[i]["cells"]):
            raise ValueError(f"field {i} needs {fields[i]['cells']} digits")
        values[i] = value
    return values


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in STATIC:
            file, kind = STATIC[path]
            return self.reply(200, kind, file.read_bytes())
        match = ROUTE.match(path)
        if not match:
            return self.send_error(404)
        fid, what, code = match.groups()
        if what == "status":
            status = {"stage": "ready"} if load(fid) else JOBS.get(fid)
            return self.json(200, status) if status else self.send_error(404)
        form = load(fid)
        if not form or code not in LANGUAGES:
            return self.send_error(404)
        try:
            self.json(200, interview(form, code))
        except SERVICE + (ValueError,) as e:
            print(f"translation failed {type(e).__name__}", file=sys.stderr)
            self.json(502, {"error": "service"})

    def do_POST(self) -> None:
        if self.path not in ("/fill", "/hear", "/upload"):
            return self.send_error(404)
        size = int(self.headers.get("Content-Length", 0))
        if self.path == "/upload":
            return self.upload(size)
        try:
            body = json.loads(self.rfile.read(size))
            form = load(str(body.get("form", "")))
            if not form:
                raise ValueError("unknown form")
            if self.path == "/hear":
                return self.hear(form, body)
            values = clean(body["values"], form.fields)
        except (ValueError, KeyError, TypeError) as e:
            print(f"{self.path} 400 {type(e).__name__}", file=sys.stderr)
            return self.json(400, {"error": str(e)})
        doc = write_answers(form.blank, form.fields, values)
        first = min((form.fields[i].get("page", 0) for i, v in values.items() if v), default=0)
        png = doc[first].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM)).tobytes("png")
        self.json(200, {"pdf": base64.b64encode(doc.tobytes()).decode(), "png": base64.b64encode(png).decode()})

    def upload(self, size: int) -> None:
        """A PDF as the request body. Starts preparing it unless it is ready or already under way."""
        if size > MAX_BYTES:
            return self.json(413, {"stage": "failed", "error": "big"})
        data = self.rfile.read(size)
        fid = form_id(data)
        if load(fid):
            return self.json(200, {"id": fid, "stage": "ready"})
        with LOCK:
            if JOBS.get(fid, {}).get("stage") in (None, "failed"):
                JOBS[fid] = {"stage": "reading"}
                threading.Thread(target=work, args=(fid, data), daemon=True).start()
        self.json(200, {"id": fid, **JOBS[fid]})

    def hear(self, form: Form, body: dict) -> None:
        """One spoken reply (base64 WAV) for the question the page is on, to an action."""
        speech, mode, step = interview(form, body["lang"]), body["mode"], body.get("step", "")
        if mode not in MODES or (mode != "review" and step not in {s["id"] for s in speech["steps"]}):
            raise ValueError("bad mode or step")
        wav = base64.b64decode(body["audio"])
        if len(wav) > 2_000_000:
            raise ValueError("recording too long")
        try:
            result = hear(wav, speech, mode, step, str(body.get("pending", ""))[:200])
            if DEBUG:
                DEBUG.mkdir(parents=True, exist_ok=True)
                stem = DEBUG / f"{time.strftime('%H%M%S')}-{mode}-{step or 'review'}"
                stem.with_suffix(".wav").write_bytes(wav)
                stem.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False))
        except SERVICE as e:  # the page asks again
            status = getattr(getattr(e, "response", None), "status_code", "")
            print(f"/hear 502 {type(e).__name__} {status}", file=sys.stderr)  # never the answer
            return self.json(502, {"error": type(e).__name__})
        self.json(200, result)

    def json(self, status: int, data: dict) -> None:
        self.reply(status, "application/json", json.dumps(data, ensure_ascii=False).encode())

    def reply(self, status: int, kind: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # keep answers out of the terminal


if __name__ == "__main__":
    print(f"Guided form interview: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

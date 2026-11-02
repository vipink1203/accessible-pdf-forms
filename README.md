# Accessible PDF forms

A blind person gets a PDF form their screen reader can't fill.
This turns it into a short guided interview, asking only the questions that apply to them in plain words backed by the form's own text, and writes the answers onto the original PDF.
In voice mode the page reads each question aloud in English or Spanish, listens, reads the answer back, and moves on by itself; in screen-reader mode the person types.
Any PDF form can be uploaded on the start page: a fillable PDF's own fields are used as they are, and on a flat PDF Meta's Muse Spark finds the blanks.
Muse Spark then writes the questions and translates them, once per form; a form uploaded again opens at once.
Typed answers never leave the computer; spoken answers go to Meta's Muse Voice Transcribe and Muse Spark to be understood, and the page says so before the microphone turns on.

Status: the IRS W-9 proven end to end by keyboard and by voice in English and Spanish (2026-10-31); upload proven on three forms (W-9 fillable, W-4 fillable, W-4 made flat, 2026-11-02); a candidate second entry for the Meta Global AI Developer Hackathon 2026 (due 2026-11-04, 9 PM ET).

## Start here

1. **Diagram**: [`architecture/architecture.html`](architecture/architecture.html), open in a browser.
2. **Design**: [`docs/design.md`](docs/design.md); section 6 has the proof results, section 7 the known defects.
3. **Research**: [`docs/research/`](docs/research/), two notes.

## Pages

- [Labelled W-9 form](src/out/form.html): every blank Muse found on the flat W-9, as a labelled input.

The guided interview needs its local server, so it is not a static page: see Run it.
The filled W-9 is [`src/out/fw9-filled.pdf`](src/out/fw9-filled.pdf).

## Run it

From `src/`, with [uv](https://docs.astral.sh/uv/):

1. `uv run serve.py`, then open http://127.0.0.1:4420 in Chrome or Safari.
   Choose a PDF form, or try the sample W-9; then put on headphones and press Enter to start talking (or choose screen-reader mode).
2. `uv run e2e.py` answers every question by keyboard only and checks the filled PDF.
3. `uv run e2e_voice.py` answers every question by recorded speech, in English and Spanish, checks the PDF it downloads, and checks pausing.
4. `uv run e2e_upload.py input/fw9.pdf input/dl/fw4.pdf input/dl/fw4-flat.pdf` uploads each form on the start page, answers by keyboard, and checks every answer is on the PDF.

In voice mode, press Pause (or Escape, or say "pause") at any time; Continue picks up at the same question with your answers kept.
If voice mode mishears your microphone, run the server as `VOICE_DEBUG=out/debug uv run serve.py`: it keeps each spoken reply as a WAV with what was heard, on this computer only, so the recording can be checked.
Delete the folder afterwards.

The W-9 set-up outputs are cached in `src/out/` (`detection.json`, `interview-raw.json`, `translation-es-raw.json`), so set-up makes no API calls.
An uploaded form is prepared once and kept in `src/out/forms/<id>/`, where the id is the start of the file's SHA-256; that folder is never committed.
Preparing a new form costs about $0.12 for a fillable 50-field form and about $0.20 for a flat one (about $0.016 a page to find blanks, the rest for the questions); its Spanish costs about $0.02, the first time someone picks Spanish.
Voice mode and `e2e_voice.py` call Meta for every spoken reply, about $0.002 each, and need a Meta Model API key, either in `~/meta.key` (readable only by you: `chmod 600 ~/meta.key`) or in the `MODEL_API_KEY` environment variable; the key is never stored in this repository; the voice test caches Muse Spark's replies, so a re-run pays only for transcription.
To redo a set-up step, delete its cache file:

| Script | What it does | Model calls |
| --- | --- | --- |
| `spike.py` | Flattens the W-9, finds and lines up its blanks, scores them against the real fields, writes `fields.json` | 1 if `detection.json` is missing |
| `interview.py` | Writes and checks the interview, writes `interview.json` | 1 if `interview-raw.json` is missing |
| `translate.py` | Translates the interview and the spoken phrases, writes `interview.en.json` and `interview.es.json` | 1 if `translation-es-raw.json` is missing |
| `voice.py` | Hears one spoken reply: transcribe twice at once, then understand | 2 transcriptions and usually 1 Muse Spark call per reply |
| `forms.py` | Prepares an uploaded PDF: reads its fields or finds its blanks, writes and repairs the interview, in `out/forms/<id>/` | 1 per flat page with blanks, plus 1 for the interview |
| `serve.py` | Serves the start page and interviews, prepares uploads in the background, hears spoken replies, fills the PDF | uploads, Spanish on first use, voice mode |
| `e2e.py` | Keyboard-only end-to-end test | none |
| `e2e_voice.py` | Voice end-to-end test: English, Spanish, and pausing | about 130 transcriptions per run |
| `e2e_upload.py` | Upload end-to-end test: each PDF uploaded, interviewed by keyboard, and checked | none once the forms are prepared |
| `accents.py` | How well speech-to-text hears 12 real accented speakers (Speech Accent Archive) | 12 transcriptions, under $0.02 |

The page uses Atkinson Hyperlegible Next, a typeface the Braille Institute drew so low-vision readers can tell letters apart.
It is served from `src/web/fonts/` under the SIL Open Font License (`OFL.txt` beside it), so the page loads nothing from other sites.

## Research index

| File | Topic |
| --- | --- |
| `01-problem-and-neighbours.md` | Who is blocked, why forms stay broken, and the closest products |
| `02-voice-and-languages.md` | Speech in and out, languages, privacy of voice data, and our measured results |

## Files not in this repository

- `src/voice-samples/`: two spoken SSNs for `uv run voice.py`, made on a Mac with `say -v Samantha` and `say -v Paulina`; make your own the same way.
- `src/out/forms/`, `src/out/debug/`, `src/out/e2e/`, `src/out/accents/`: uploaded forms, your own voice recordings, test screenshots, and downloaded accent recordings (CC BY-NC-SA), all made on your computer when you run the app or the tests.

## License

The code is under the GNU Affero General Public License v3.0 ([`LICENSE`](LICENSE)), because it uses [PyMuPDF](https://pymupdf.readthedocs.io/), which is AGPL-3.0.
The Atkinson Hyperlegible Next font is under the SIL Open Font License 1.1 (`src/web/fonts/OFL.txt`).
The IRS forms in `src/input/` are US government works, in the public domain.

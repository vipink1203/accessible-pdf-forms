# Design: Fill Aloud

Status: draft v0.3 (2026-11-02).
One form proven end to end (IRS W-9, page 1), by keyboard and by voice in English and Spanish; upload of any PDF proven on three forms by keyboard; everything below the proof section is the plan.
Diagram: [`../architecture/architecture.html`](../architecture/architecture.html).
Research: [`research/01-problem-and-neighbours.md`](research/01-problem-and-neighbours.md), [`research/02-voice-and-languages.md`](research/02-voice-and-languages.md).

## 1. Summary

A blind person gets a PDF form they can't fill: a flat page with no fields a screen reader can use.
We turn it into a short guided interview: plain questions, one at a time, only the ones that apply to them, each backed by the form's own words.
In voice mode the page reads each question aloud in English or Spanish, listens to the answer, reads it back, and moves on by itself; a screen-reader mode does the same with typed answers.
At the end they check every answer, and we write the answers onto the original PDF at the exact spots, so what they send is the official form.

## 2. The problem

- Screen-reader users are sent PDF forms that have no tagged fields, so they hear an image or jumbled text.
- Fixing a form is work for its author, who rarely does it; the US government deadline slipped to 2027-2028.
- Even a form with fields is hard to fill blind: the reader must hear every field in order, including ones that don't apply, and rules that are only clear visually ("check only one", "SSN or EIN").
- Today people ask a sighted helper or paste the form into a chatbot, which sends their answers to a model and checks nothing.

Evidence and the nearest neighbours are in the research note.

## 3. The insight

1. **Meaning from the model, geometry from the page.**
   Muse Spark names every blank correctly but places some boxes 10-20 points off.
   The page's own printed lines and boxes are exact, so our code moves each field onto them.
2. **Grounded questions.**
   Muse writes the questions from the form and its instruction pages, and every quote it cites must appear word for word in the PDF, or the interview is rejected.
3. **The model sees the blank form, once.**
   Finding blanks, writing the interview and translating it run once per form and are cached, so the same interview serves everyone who gets that form.
   Typed answers never reach a model; in voice mode each spoken reply must, and the person is told before the microphone turns on.
4. **Every spoken answer is read back.**
   Speech-to-text sometimes repeats a word ("Jordan Jordan Rivera", "three three"), so an answer counts only after the person hears it read back exactly as it will be written and says yes.

## 4. How it works

The diagram shows the numbered steps.

0. **Choose a form.** The start page offers a PDF upload or the sample W-9.
   The server reads the file in the background and the page says each stage aloud and on screen (reading, finding the blanks page by page, writing the questions).
   A fillable PDF's own fields are taken as they are, with the printed words around each one as candidate labels, and no model call.
   A flat PDF goes to steps 1 and 2, for each page that has empty lines or boxes to fill (instruction pages are skipped).
1. **Find the blanks.** Render the page to an image; one Muse Spark call per page returns every blank with its label, kind (text or tick box), group, and a rough box.
2. **Line them up.** Move tick boxes onto the nearest printed square and digit fields onto the printed digit cells; save `fields.json`.
3. **Write the interview.** One Muse Spark call reads the field list, the page text and the instruction pages, and returns steps: question, help, source quote, answer type (choice, text, digits, confirm), the fields each fills, and when to ask it.
   For an upload it also sees each page with every field outlined in red and numbered, so it can tell which field is which when the words around one are unclear.
   On the W-9 a local check rejects it if any source quote or certification text isn't word for word in the PDF, any field is filled twice, or any field is neither asked nor skipped with a reason.
   On an upload a local repair fixes the same problems instead, so the person is never stuck: it drops a quote that isn't word for word, drops fields filled twice, and adds a plain question for any field left out; every repair is recorded with the form.
4. **Ask and fill.** A local page asks one question at a time and a local server writes the answers onto the original page.
5. **Talk instead of type.** In voice mode the page speaks with the computer's own voices, records the reply until the person stops talking, and sends it to the local server.
   The server sends the audio to Muse Voice Transcribe, then the text to Muse Spark with the current question, which returns one action: an answer, yes, no, repeat, back, skip, explain, or change.
   The page reads the answer back, waits for yes, and goes to the next question; at the end it reads every answer, fills the form on "yes", and saves the PDF to Downloads.
   One cached Muse Spark call per language translates the questions and phrases; the form's English words stay one request away ("explain").

## 5. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | Muse Spark 1.3 through the Meta Model API for vision and the interview | Hackathon judges score use of Meta tooling at 25%; it reads both the page image and long instruction text |
| D2 | Snap fields to the page's printed lines in code, not with a second model call | Exact and free; Muse alone matched 15 of 23 fields, with snapping 23 of 23 |
| D3 | Every quote checked word for word; paraphrased help is allowed but the exact words are always one keypress away | A blind user can't spot a wrong explanation, so the form's words must be reachable |
| D4 | Model calls only on the blank form, cached per form | Answers stay on the computer; re-runs cost nothing |
| D5 | Write answers onto the original PDF, not a lookalike | The requester gets their own official form |
| D6 | Plain HTML page, native form controls, no framework | Screen readers handle native radios, checkboxes and text inputs best |
| D7 | Answers in memory only: never saved or logged; typed answers never sent anywhere | They include SSNs; nothing to leak |
| D8 | Voice in: Muse Voice Transcribe, then Muse Spark with minimal reasoning | Meta tooling end to end; standard tier does not train on the audio; about 1.5-2.8 s and $0.0015 per answer |
| D9 | Voice out: the computer's own voices only (`localService`), never cloud voices | Read-backs include SSNs; Chrome's "Google" voices would send them to Google |
| D10 | An answer counts only after it is read back and the person says yes; plain yes and no are understood without a model call | Speech-to-text repeats words; a blind person can't see the mistake |
| D11 | Translation is one cached Muse call per language; the certification is read in translation with a note that the English text is what they certify | Same form for everyone; the legal words stay the form's own |
| D12 | Voice mode is a choice on the start screen, next to screen-reader mode | Screen-reader users keep their own reader; others get a voice; nobody gets both talking at once |
| D13 | Each spoken reply is transcribed twice in parallel; Muse Spark reads both | The transcriber's mistakes are partly random; on 10 accented speakers (of 12 tried) it cut word errors from 10.3% to 4.1%, for one extra transcription and no extra Muse Spark call |
| D14 | The person can pause at any time (Pause button, Escape, or saying "pause"); Continue re-asks the question they were on, with every answer kept | Nobody should have to listen to a machine they can't stop |
| D15 | After two silences, or three replies that give no answer, the page stops and offers Continue or typing; on the second miss of a text answer it suggests spelling it | Asking forever is the worst failure for a voice app; names are what speech-to-text gets wrong most |
| D16 | The recorder keeps half a second before speech starts, waits 1.8 s of quiet before ending, measures "speech" against the room's own noise, and sends 24 kHz audio | First words were at risk of clipping, and people who pause to find a word were cut off at 1.2 s |
| D17 | A digit answer made only of spoken digits is read by code, not by Muse Spark | Muse Spark once turned 8 spoken digits into 9 by repeating the last one, to fit the count the question needed; the count check exists to catch exactly that |
| D18 | The microphone counts as blocked only if it has sent no sound at all since voice mode began, after 4 s | A Bluetooth headset can send silence for a moment while it switches modes; that is not a blocked microphone |
| D19 | A fillable PDF's own fields are used as they are and filled in place; only flat PDFs go to Muse Spark to find blanks | The form's author already said where the fields are; that is exact and free, and the filled form keeps working fields |
| D20 | Only pages with at least 5 empty lines or boxes are sent to find blanks; a page with no text (a scan) is always sent | Instruction pages have lines too, but few empty ones; on the W-4 this sends 3 of 5 pages, on the W-9 1 of 6 |
| D21 | Muse Spark sees each page with every field outlined in red and numbered by id when it writes the questions (set-of-marks) | Words near a fillable field are often dot leaders, sideways text or the next field's label; with the picture it matched every W-9 field |
| D22 | An uploaded form's interview is repaired in code, not rejected | A rejection would cost a second paid call and still might fail; a repair is free, logged, and keeps every field reachable |
| D23 | Each uploaded form is kept by the start of its SHA-256, and its Spanish is written the first time someone picks Spanish | The same file never pays twice, and nobody pays for a language nobody used |
| D24 | Limits: 20 MB, 10 pages with blanks, 150 fields; the interview may use up to 100,000 output tokens and stops at once if cut off | A 50-field form took 30,264 output tokens; a cut-off answer is never retried with the same budget, which would pay twice for the same failure |
| D25 | When a form says to cross out printed text in some case, the interview asks whether the case applies, and "yes" fills a field of its own, kind "strike", placed on that text's lines | Crossing out is part of filling the form; a blind person can't do it on paper, and a field reuses the same read-back, review and fill as every other answer |
| D26 | Voice choices are read with numbers and a number picks one in code; a reply with no words, a sentence that only shares a word with a choice, or two hearings that disagree on digits is unclear, never a guess, except that when one hearing is the other with one digit said twice, the shorter is taken; after two misses it suggests moving away from people talking, and when it pauses, Continue has the focus | An owner test stalled on the tax question: a conversation nearby was heard, Spark guessed "Other", and after three misses the page paused with nothing saying what to do next; a doubled digit in one hearing once turned 8 spoken digits into 9, and treating every such disagreement as unclear then turned away a correctly spoken SSN in 2 of 4 tries |

## 6. Proof on one form

Form: IRS W-9 (Rev. March 2024), page 1, with its 23 fillable fields removed to make a flat page; the removed fields are the answer key.
Code and outputs: [`../src/`](../src/).

| Check | Result |
| --- | --- |
| Muse alone: blanks found | 15 of 23 (8 boxes 10-20 points off; all 23 named correctly) |
| Muse plus snapping: blanks found and placed | 23 of 23, kind right 23 of 23, 18 with box overlap of 50% or more |
| Extra blanks found | 2: signature and date, both real places to write that the IRS form doesn't define as fields |
| Interview | 18 steps, 11 asked of everyone, 1 field skipped (the requester's box); written again on 2026-11-02 with the section 7 fixes |
| Rules only for some people | The exempt-payee and FATCA questions go only to entities, never to individuals, as line 4 says |
| Crossing out | Everyone is asked whether the IRS told them they are subject to backup withholding; "yes" draws a line through each line of certification item 2 |
| Exceptions in help | The classification help says a disregarded entity ticks its owner's classification, not the LLC box |
| Grounding | every source quote and the full perjury certification word for word in the PDF; 6 of 12 help texts word for word, 6 faithful paraphrases |
| Keyboard-only run as an individual with an SSN, told they are subject to backup withholding | 12 questions, every focused control has a name, the returned PDF has every answer in place and item 2 crossed out |
| Voice run, English | 26 turns by recorded speech into a fake microphone: covers silence and pause, repeat, skip, a wrong digit count, read-back and yes, and "no" to backup withholding; PDF correct |
| Voice run, Spanish | 24 turns: covers explain (the form's words read in English), a spoken correction ("No, es ... cero"), read-back and "sí"; PDF correct |
| Speech-to-text slips | A repeated word in roughly 1 reply in 10 across the runs; every one caught by the read-back or the digit count |
| Voice latency and cost | 1.4 s median, 4.8 s slowest from end of speech to action; about $0.0015 per understood answer |
| Model spend (W-9) | Form set-up: 2 Muse Spark calls, $0.1004 ($0.0259 finding blanks, $0.0745 writing the interview); Spanish translation $0.0258 (one call); about $0.25 more on voice test runs |

### Upload, 2026-11-02

Forms: the fillable IRS W-9, the fillable IRS W-4 (2026, 5 pages), and the same W-4 with its fields removed to make it flat.
Each was uploaded on the start page in a real browser, answered by keyboard (first choice, ones in digit boxes, "Answer N" in text), and the returned PDF checked field by field (`src/e2e_upload.py`).

| Check | W-9 fillable | W-4 fillable | W-4 flat |
| --- | --- | --- | --- |
| Fields | 23 (read from the PDF) | 48 (read from the PDF) | 50 (found by Muse on 3 of 5 pages) |
| Questions written | 15 | 49 | 50 |
| Repairs | 1: signature dropped (the fillable W-9 has no signature field) | none | none |
| Run: asked, fields filled, all on the PDF | 11, 11, yes | 9, 8, yes | 10, 9, yes |
| Spanish, first use | not run | 49 questions, $0.020 | not run |
| Spend to prepare | $0.067 | $0.121 | $0.196 ($0.049 finding blanks) |

Both W-4s first failed: the questions ran past a 16,000-token output limit, twice each, about $0.34 spent for nothing, now prevented by D24.
The W-4 interviews branch correctly (exempt, multiple jobs, three jobs, credits, deductions), but the test picks the first choice, so the worksheet questions on pages 3 and 4 were written but not answered in a run.

## 7. Known defects

- The backup-withholding help quotes the form ("Cross out item 2 if ..."), which can sound like the person must do it; the page crosses it out for them on "yes".
- The disregarded-entity note is terse ("A disregarded entity checks the owner classification") and doesn't say that means a single-owner LLC.
- "Question N of M" grows by one when a follow-up appears (for example 12 becomes 13 after choosing SSN).
- The typed name on the signature line may not count as a signature for every requester; the IRS lets requesters accept electronic W-9s only under its own conditions.
- Snapping relies on printed lines, so scanned forms are untested.
- On a fillable form with no signature field, nothing is signed: the repair drops the signature question, and the person signs the printed form.
- A PDF with some fillable fields and some flat pages uses only its fillable fields.
- Only one real flat form is proven, and it was made flat from a fillable one, so its lines are cleaner than a typical flat form's.
- A 50-field form takes about 30,000 output tokens to prepare; forms over 50 fields are untested.
- An uploaded form's questions are not checked by a person before the first user hears them.
- Voice mode can't start talking on its own: browsers allow speech only after a key press, so the first Enter starts it.
- Voice mode needs a local voice for the language; without one it refuses and points to screen-reader mode.
- Spoken answers are kept by Meta "as needed" under its terms; zero data retention is not set up.
- Spoken choice labels longer than 30 characters can't be sent as recognition hints (the API's limit), so they get no hint.
- Answers keep the words the person used: "apartamento 4" stays in Spanish on the English form.
- The voice runs used recorded speech in a quiet line, not a real microphone in a real room; accent tests used real people's recordings, but of a read paragraph, not of form answers.
- Waiting 1.8 s of quiet before ending a reply makes each turn slower; Space ends a reply at once.

## 8. Open questions

1. Does Muse Spark find blanks on a scanned form, and what replaces snapping there (SAM 3.1, or line detection on the image)?
2. How does it do on 5 more real flat forms: a state benefits form, a medical intake, a lease, a school form, a multi-page form?
3. Do blind users prefer the interview to a labelled form? We need at least 3 screen-reader users to try it before claiming it helps.
4. Should a person be able to ask a free question ("what is a disregarded entity?"), answered only from the instructions?
5. How often does speech-to-text slip with a real microphone, an accent, or background noise, and is 1.2 s of silence the right moment to stop listening?

## 9. Roadmap

| Phase | Status | Scope | Exit criteria |
| --- | --- | --- | --- |
| 1. One-form proof | Done | Find, snap, interview, fill and keyboard test on the IRS W-9 | 23 of 23 blanks placed, 0 grounding problems, keyboard run passes |
| 1b. Voice mode | Done | Speak questions, listen, read back, auto-advance; English and Spanish | Voice runs pass in both languages with the right PDF |
| 2. Interview fixes | Done | Fix the section 7 interview defects in the prompt, re-run once | Individuals skip line 4; the cross-out rule is asked |
| 3. Any PDF | In progress | Upload a PDF instead of the built-in W-9; multi-page forms; fillable and flat | Done: upload, fillable and flat, multi-page, 3 forms pass. Left: 5 more real flat forms pass the same checks |
| 4. Scanned forms | Not started | Blanks on scans with no printed lines | A scanned form fills as well as the W-9 |
| 5. Blind user testing | Not started | VoiceOver, NVDA and JAWS users try screen-reader mode; blind users without a screen reader try voice mode | At least 3 users finish a form without sighted help in each mode |
| 6. Hackathon submission | Not started | Video, public repo, write-up | Submitted by 2026-11-04 9 PM ET |
| 7. Compliance | Not started | `app-compliance` pass | No failing gate before anyone outside the judges uses it |

## 10. Compliance

Not yet run.
The app handles SSNs, sends spoken answers to Meta, and uses AI models, so it must pass the `app-compliance` skill before any real user touches it; the record goes in `docs/compliance.md`.

"""Make the interview speakable in each language: questions, help, choices and the phrases the
voice assistant says, written once per language by Muse and cached.

Only the blank form's questions are sent, never answers. The form's own English words stay as
they are: in another language they are offered as "the form's exact words, in English".

    uv run translate.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from interview import MAX_CALLS
from spike import MODEL, OUT, PRICE_PER_M, load_key

# code: (name Muse Voice Transcribe takes as languageBias, BCP 47 tag for speech and <html lang>)
LANGUAGES = {"en": ("English", "en-US"), "es": ("Spanish", "es-US")}

# What the assistant says and shows besides the questions. {name} placeholders are filled in the page.
PHRASES = {
    "start_title": "Fill in this form by talking",
    "start_body": "I will read each question aloud and listen to your answer. Use headphones so I don't hear myself.",
    "start_voice": "Start talking",
    "start_reader": "Use my screen reader instead",
    "privacy": "Your spoken answers are sent to Meta's Muse models to be understood. Meta does not use them to train "
               "its models, but may keep them as its terms allow. Nothing is saved on this computer.",
    "how_to": "After the beep, answer. You can also say: repeat, go back, skip, explain, or pause. "
              "Press Escape to pause at any time, and Space to skip ahead.",
    "question_n": "Question {n} of {total}",
    "options": "Say the number or the name. {options}.",
    "optional": "This one is optional. Say skip to leave it blank.",
    "heard": "I heard: {answer}. Is that right?",
    "unclear": "Sorry, I didn't understand that.",
    "no_speech": "I didn't hear anything.",
    "again": "Please say it again.",
    "noise_hint": "If other people are talking nearby, move away from them, or use a headset microphone.",
    "digits_count": "I need {need} digits, and I heard {got}.",
    "required": "This answer is required.",
    "skipped": "Skipped.",
    "certify_intro": "Here is what you are certifying.",
    "certify_note": "",
    "certify_ask": "Do you certify that this is true? Say yes or no.",
    "certify_needed": "You need to certify this to sign the form.",
    "form_words": "The form's exact words:",
    "review_intro": "Here are your answers.",
    "review_ask": "Should I fill in the form now? Say yes, or say which answer you want to change.",
    "filling": "Filling in the form.",
    "done": "Your form is filled.",
    "saved": "I saved it in your Downloads folder as {file}.",
    "voice_error": "I couldn't reach the speech service. Press Space to try again.",
    "mic_denied": "I can't use the microphone. Allow it for this page, or use your screen reader instead.",
    "no_voice": "No voice for this language is installed on this computer, so I can't talk in it. "
                "Add one in System Settings, or use your screen reader instead.",
    "paused": "Paused. Press Space to continue.",
    "paused_user": "Paused. Your answers so far are kept. Press Continue, or Space, when you are ready.",
    "stuck": "Let's take a short break. Press Continue to try this question again, or Type instead to type the answer.",
    "spell_hint": "If it is a name, you can spell it, letter by letter.",
    "pause": "Pause",
    "resume": "Continue",
    "mic_silent": "Your microphone is sending no sound. Check that it is not muted, and that this browser may use "
                  "it in System Settings, Privacy and Security, Microphone. Then press Space.",
    "speaking": "Speaking",
    "listening": "Listening",
    "thinking": "Understanding",
    "you_said": "You said:",
    "blank": "Left blank",
    "confirmed": "Confirmed",
    "not_confirmed": "Not confirmed",
    "guided": "guided",
    "about": "About this form",
    "typed_privacy": "Your answers stay on this computer. At the end you can check every answer before the form "
                     "is filled.",
    "start": "Start",
    "back": "Back",
    "next": "Next",
    "repeat": "Repeat",
    "type_instead": "Type instead",
    "save_return": "Save and return",
    "type_digits": "Type all {need} digits; dashes and spaces are fine.",
    "optional_typed": "Optional: leave it blank and choose Next to skip.",
    "certify_box": "I have read this and certify that it is true",
    "certify_tick": "Tick the box to certify, or go back.",
    "digits_only": "Use digits only.",
    "digits_typed": "Enter {need} digits. You entered {got}.",
    "choose_one": "Choose one option.",
    "check_title": "Check your answers",
    "review_count": "{n} questions. Change any answer before the form is filled.",
    "change": "Change",
    "still_needed": "\"{question}\" still needs an answer.",
    "fill_button": "Fill the form",
    "fill_failed": "The form could not be filled:",
    "filled_title": "Your {title} is filled",
    "filled_body": "The answers are written onto the original form, ready to send to whoever asked for it.",
    "download": "Download the filled form (PDF)",
    "change_answers": "Change answers",
    "preview_alt": "Picture of the filled form, for a sighted helper to check.",
    "done_focus": "Your form is filled. The download link has focus.",
    "pick_title": "Fill in a PDF form",
    "pick_body": "Choose a blank PDF form on this computer. I will turn it into questions, one at a time, and write "
                 "your answers onto it.",
    "pick_privacy": "To find the places to fill in and write the questions, the blank form is sent to Meta's Muse "
                    "models. Choose a form that is not filled in yet.",
    "pick_file": "Choose a PDF form",
    "pick_sample": "Try it with IRS Form W-9",
    "other_form": "Choose another form",
    "prep_title": "Getting your form ready",
    "prep_reading": "Reading the form.",
    "prep_finding": "Finding the places to fill in: {n} of {total} pages done.",
    "prep_writing": "Writing the questions. This takes about a minute.",
    "prep_language": "Writing the questions in your language. This takes about half a minute.",
    "prep_failed_not_pdf": "This file is not a PDF form.",
    "prep_failed_locked": "This PDF is locked with a password. Save an unlocked copy, and choose that.",
    "prep_failed_none": "I couldn't find any places to fill in on this form.",
    "prep_failed_long": "This form has more than {limit} pages to fill in, which is too long for now.",
    "prep_failed_many": "This form has more than {limit} places to fill in, which is too many for now.",
    "prep_failed_big": "This file is larger than 20 MB, which is too large for now.",
    "prep_failed_service": "I couldn't reach Meta's Muse models. Try again in a minute.",
    "prep_failed_broken": "Something went wrong while reading this form.",
}
# Said when the form's words are in English and the person listens in another language.
TRANSLATED_NOTES = {
    "form_words": "The form's exact words, in English:",
    "certify_note": "This is a translation. What you certify is the English text on the form.",
}


def schema(steps: list[dict], phrases: bool = True) -> dict:
    text = {"type": "string"}
    out = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "intro", "steps", "phrases", "yes_words"],
        "properties": {
            "title": text,
            "intro": text,
            "steps": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "question", "help", "choices"],
                "properties": {
                    "id": text, "question": text, "help": text,
                    "choices": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False, "required": ["value", "label"],
                        "properties": {"value": text, "label": text}}},
                }}},
            "phrases": {"type": "object", "additionalProperties": False, "required": list(PHRASES),
                        "properties": {k: text for k in PHRASES}},
            "yes_words": {"type": "array", "items": text},
        },
    }
    if not phrases:
        out["required"] = ["title", "intro", "steps"]
        del out["properties"]["phrases"], out["properties"]["yes_words"]
    return out


PROMPT = """Translate this form assistant into {language} for a blind person in the United States who will
hear it read aloud by a speech synthesizer.
- Plain, warm, short spoken {language}; address the person the way a respectful public service would.
- Keep every id and choice value exactly as given; translate only the human text.
- Keep {{placeholders}} such as {{n}} and {{answer}} exactly, untranslated.
- Keep official names that the form prints in English (for example "Form W-9", "FATCA", "LLC") and
  add a short {language} explanation in parentheses the first time only if it helps.
- The certification step's help is legal text: translate it faithfully and completely, no summary.
- yes_words: 8 to 15 short ways a person says "yes" or "that's right" in {language}, lower case, no punctuation.

Interview and phrases to translate, as JSON:
{source}"""


# The same rules for an uploaded form, whose phrases and yes words are already translated.
STEPS_PROMPT = PROMPT.split("- yes_words")[0] + "\nInterview to translate, as JSON:\n{source}"

PHRASES_PROMPT = """Translate these phrases of a form assistant into {language} for a blind person in the United
States who will hear them read aloud. Plain, warm, short spoken {language}, addressed the way a respectful
public service would. Keep each key, and keep {{placeholders}} exactly, untranslated. Use the names macOS
shows in {language} for its settings.

{source}"""


def source(interview: dict, phrases: bool = True) -> dict:
    out = {
        "title": interview["title"],
        "intro": interview["intro"],
        "steps": [{"id": s["id"], "question": s["question"], "help": s["help"],
                   "choices": [{"value": c["value"], "label": c["label"]} for c in s["choices"]]}
                  for s in interview["steps"]],
    }
    return out | {"phrases": {**PHRASES, **TRANSLATED_NOTES}} if phrases else out


def ask(prompt: str, schema: dict) -> dict:
    """One Muse Spark call with a strict schema, retried up to MAX_CALLS times on bad JSON."""
    from openai import OpenAI

    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=load_key())
    spent = 0.0
    for attempt in range(1, MAX_CALLS + 1):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": {"name": "translation", "schema": schema, "strict": True}},
            reasoning_effort="low",
            max_completion_tokens=16000,
        )
        u = resp.usage
        cost = (u.prompt_tokens * PRICE_PER_M["input"] + u.completion_tokens * PRICE_PER_M["output"]) / 1e6
        spent += cost
        print(f"call {attempt}: {u.prompt_tokens} in, {u.completion_tokens} out, ${cost:.4f} (total ${spent:.4f})")
        try:
            result = json.loads(resp.choices[0].message.content or "")
        except json.JSONDecodeError:
            continue
        result["_usage"] = {"model": resp.model, "calls": attempt, "cost_usd": round(spent, 5)}
        return result
    raise ValueError(f"no valid JSON after {MAX_CALLS} calls (${spent:.4f} spent)")


def translate(code: str, interview: dict) -> dict:
    """The cached translation; phrases added or reworded since it was made are translated alone and merged in."""
    cache = OUT / f"translation-{code}-raw.json"
    language = LANGUAGES[code][0]
    if not cache.exists():
        result = ask(PROMPT.format(language=language, source=json.dumps(source(interview), ensure_ascii=False)),
                     schema(interview["steps"])) | {"steps_en": source(interview, phrases=False)}
        cache.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    result = json.loads(cache.read_text())
    steps_en = source(interview, phrases=False)
    if result.get("steps_en") != steps_en:  # the questions were written again: translate them alone
        print("translating the changed questions")
        result |= ask(STEPS_PROMPT.format(language=language, source=json.dumps(steps_en, ensure_ascii=False)),
                      schema(interview["steps"], phrases=False)) | {"steps_en": steps_en}
        cache.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    english = {**PHRASES, **TRANSLATED_NOTES}
    was = result.get("phrases_en", english)  # the English each cached phrase was translated from
    new = {k: v for k, v in english.items() if k not in result["phrases"] or was.get(k) != v}
    if not new:
        print(f"reusing cached {cache.name} (no API call)")
        return result
    print(f"translating {len(new)} new phrases: {', '.join(new)}")
    text = {"type": "string"}
    added = ask(PHRASES_PROMPT.format(language=language, source=json.dumps(new, ensure_ascii=False)),
                {"type": "object", "additionalProperties": False, "required": list(new),
                 "properties": {k: text for k in new}})
    result["phrases"] |= {k: added[k] for k in new}
    result["phrases_en"] = english
    cache.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def translate_form(code: str, interview: dict, cache: Path, shared: dict) -> dict:
    """An uploaded form's own words in another language, cached in `cache`. The assistant's phrases
    and yes words are the same for every form, so they come from `shared`, the sample form's
    translation, and only the questions are sent."""
    if cache.exists():
        result = json.loads(cache.read_text())
    else:
        result = ask(STEPS_PROMPT.format(language=LANGUAGES[code][0],
                                   source=json.dumps(source(interview, phrases=False), ensure_ascii=False)),
                     schema(interview["steps"], phrases=False))
        cache.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result | {"phrases": shared["phrases"], "yes_words": shared["yes_words"]}


def check(translation: dict, interview: dict) -> list[str]:
    """Problems that would break the voice flow; an empty list means it passed."""
    problems = []
    want = [(s["id"], [c["value"] for c in s["choices"]]) for s in interview["steps"]]
    got = [(s["id"], [c["value"] for c in s["choices"]]) for s in translation["steps"]]
    if want != got:
        problems.append("step ids or choice values changed")
    for key, english in {**PHRASES, **TRANSLATED_NOTES}.items():
        placeholders = set(re.findall(r"{\w+}", english))
        if placeholders != set(re.findall(r"{\w+}", translation["phrases"].get(key, ""))):
            problems.append(f"phrase {key!r} lost or changed a placeholder")
    for t, s in zip(translation["steps"], interview["steps"]):
        if bool(s["help"]) != bool(t["help"]) or not t["question"]:
            problems.append(f"{s['id']}: question or help missing")
    if not translation["yes_words"]:
        problems.append("no yes_words")
    return problems


def speakable(code: str, interview: dict, translation: dict | None) -> dict:
    """The interview as the page uses it: the source steps, with text in the person's language."""
    out = json.loads(json.dumps(interview))
    out["lang"] = {"code": code, "name": LANGUAGES[code][0], "tag": LANGUAGES[code][1]}
    out["english"] = code == "en"
    if translation is None:
        out["phrases"] = dict(PHRASES)
        out["yes_words"] = ["yes", "yeah", "yep", "correct", "that's right", "right", "sure", "ok", "okay"]
        return out
    out["title"], out["intro"] = translation["title"], translation["intro"]
    out["phrases"] = translation["phrases"]
    out["yes_words"] = translation["yes_words"]
    by_id = {t["id"]: t for t in translation["steps"]}
    for s in out["steps"]:
        t = by_id[s["id"]]
        s["question_en"], s["question"], s["help_en"], s["help"] = s["question"], t["question"], s["help"], t["help"]
        labels = {c["value"]: c["label"] for c in t["choices"]}
        for c in s["choices"]:
            c["label_en"], c["label"] = c["label"], labels[c["value"]]
    return out


def main() -> None:
    interview = json.loads((OUT / "interview.json").read_text())
    for code in LANGUAGES:
        translation = None if code == "en" else translate(code, interview)
        problems = check(translation, interview) if translation else []
        for p in problems:
            print("FAIL", code, p)
        (OUT / f"interview.{code}.json").write_text(
            json.dumps(speakable(code, interview, translation), indent=2, ensure_ascii=False))
        print(f"{code}: {len(interview['steps'])} steps, {len(problems)} problems")
    if any(check(json.loads((OUT / f"translation-{c}-raw.json").read_text()), interview)
           for c in LANGUAGES if c != "en"):
        sys.exit(1)


if __name__ == "__main__":
    main()

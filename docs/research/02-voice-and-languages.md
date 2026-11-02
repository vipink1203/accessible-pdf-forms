# Voice in and voice out, in the user's own language

Collected 2026-10-31 for the Meta Global AI Developer Hackathon 2026.
Question: how a local web page plus a small local Python server can read each form question aloud in the user's language, listen to the spoken answer, confirm it, and move on, while answers include names, addresses, and Social Security numbers.
Every fact below links to the page it came from.
Anything marked "not verified" was looked for and not found in a primary source.

## 1. Meta Muse Voice Transcribe (speech to text)

### What it is

- "Muse Voice Transcribe is Meta's speech-to-text model on Meta Model API" ([Models](https://dev.meta.ai/docs/models)).
- "It returns transcript text. It does not synthesize speech or provide a speech-to-speech conversation API." ([Models](https://dev.meta.ai/docs/models)).
- It supports "streaming speaker diarization, native endpointing and voice activity detection, contextual and keyword biasing, 25 evaluated languages with code-switching, and turn-level timestamps" ([Models](https://dev.meta.ai/docs/models)).
- Model id: `muse-voice-transcribe-1.0` ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
- The cookbook recipe is "Speech to text" under the Muse Voice Transcribe heading ([Cookbook index](https://dev.meta.ai/docs/cookbook/), [recipe](https://dev.meta.ai/docs/cookbook/voice-api-fundamentals), [recipe source on GitHub](https://github.com/meta-models/meta-model-cookbook/blob/main/06_muse_voice/01_voice_api_fundamentals/README.md)).
- A second recipe, "Control Apple Chess with voice", turns spoken moves into app actions ([Cookbook index](https://dev.meta.ai/docs/cookbook/), [recipe](https://dev.meta.ai/docs/cookbook/voice-chess-cua)).

### Endpoints and request format

- It is not the OpenAI `/v1/audio/transcriptions` shape.
  Meta's own paths are `POST https://api.meta.ai/v1/asr/transcribe` for a file and `wss://api.meta.ai/v1/asr/realtime` for streaming ([Speech to text](https://dev.meta.ai/docs/speech-to-text), [Transcribe a recording](https://dev.meta.ai/docs/api-reference/voice/transcribe)).
- The Chat Completions protocol page does not mention `/v1/audio/transcriptions` or `/v1/audio/speech` and lists `audio` as unsupported: "No audio output." ([Chat Completions protocol](https://dev.meta.ai/docs/protocols/chat-completions)).
  Whether `/v1/audio/transcriptions` answers at all: not verified (no API key was used in this research).
- File endpoint: `multipart/form-data` with a `request` part (JSON settings) and an `audio` part, authorized with `Authorization: Bearer $MODEL_API_KEY` ([Transcribe a recording](https://dev.meta.ai/docs/api-reference/voice/transcribe)).
- The file endpoint can answer as buffered JSON, as `text/event-stream`, or as `text/plain` depending on the `Accept` header ([Transcribe a recording](https://dev.meta.ai/docs/api-reference/voice/transcribe)).
- Realtime endpoint: "This endpoint does not read the HTTP `Authorization` header; the credential travels in the handshake", which is the first JSON text frame and must arrive "within 10 seconds" ([Transcribe in realtime](https://dev.meta.ai/docs/api-reference/voice/realtime)).
- After the handshake, audio goes as raw PCM in binary WebSocket frames, and the client ends with `{"type": "endStream"}` ([Transcribe in realtime](https://dev.meta.ai/docs/api-reference/voice/realtime)).
- Handshake fields: `model`, `audioEncoding` (`PCM_24KHZ` or `PCM_16KHZ`, no default), `mode`, `partialMode` (`CUMULATIVE` or `DELTA`), `emitAudioProgress`, `keywords`, `languageBias`, `authorization` ([Voice schemas](https://dev.meta.ai/docs/api-reference/voice/schemas)).
- Modes: `PUSH_TO_TALK` (default, the client ends the turn), `ENDPOINTING` (the model detects speech start and end), `DIARIZATION` (adds speaker labels) ([cookbook recipe](https://dev.meta.ai/docs/cookbook/voice-api-fundamentals)).
- Push to talk is the mode the docs suggest for "voice commands, dictation, or controlled recordings": "You delimit the turn - for example, by pressing a mic button." ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
- Diarization is "not tuned for low-latency, voice-command use" ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).

### Languages and language detection

- 25 languages "with code-switching": Arabic, Bengali, Dutch, English, French, German, Hebrew, Hindi, Indonesian, Italian, Japanese, Kannada, Korean, Malay, Mandarin Chinese, Marathi, Polish, Portuguese, Spanish, Tagalog, Tamil, Telugu, Thai, Turkish, Vietnamese ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
- Auto-detection: yes.
  If `languageBias` is omitted, the model detects the language; the bias is a hint and "does not force the model to use them" ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
- `languageBias` takes language names such as `["English", "French"]`, not ISO codes ([Voice schemas](https://dev.meta.ai/docs/api-reference/voice/schemas)).
- No response schema includes a detected-language field ([Voice schemas](https://dev.meta.ai/docs/api-reference/voice/schemas)).
- Languages common among US form-fillers that are not in the list of 25 (from reading the list above): Russian, Ukrainian, Haitian Creole, Cantonese, Urdu, Farsi, Somali, Amharic.

### Streaming, latency, limits

- Streaming: yes, over the realtime WebSocket; partial transcripts are cumulative by default and a `final: true` frame ends a push-to-talk stream ([cookbook recipe](https://dev.meta.ai/docs/cookbook/voice-api-fundamentals), [Voice schemas](https://dev.meta.ai/docs/api-reference/voice/schemas)).
- Latency: no figure is published.
  The realtime page only says transcripts arrive "as the audio arrives" ([Transcribe in realtime](https://dev.meta.ai/docs/api-reference/voice/realtime)).
  Numeric latency: not verified.
- Limits: file audio up to 10 minutes and 32 MB; realtime sessions up to 60 minutes; audio must not run more than 5 seconds ahead of processing; 256 concurrent streams and 16,000 streams per hour per tenant ([Speech to text](https://dev.meta.ai/docs/speech-to-text), [Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits)).
- Not offered: "Word-level timestamps, confidence scores, sound event detection, emotion detection, and transcript reformatting are not available" ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
- Keyword biasing exists, but "Keywords bias recognition but do not guarantee an exact spelling" ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).

Measured in this project on 2026-10-31 (our own runs, not Meta's figures; code in [`../../src/voice.py`](../../src/voice.py)):

- A keyword over 30 characters is rejected with `400`: "ASR keyword 0 exceeds the maximum length of 30 characters".
- One file request of a 2-4 second answer took about 1.5-2 s; adding a Muse Spark call with `reasoning_effort: "minimal"` brought it to about 2-2.8 s.
- Numbers come back as words ("one two three"), so a second step must turn them into digits.
- The transcript sometimes repeats a word ("one two three four four five", "Jordan Jordan Rivera"): none in 10 direct uploads of one clip, and roughly 1 in 10 replies across the browser runs, in English and Spanish.

Measured on 2026-11-01 with real accented speech (code in [`../../src/accents.py`](../../src/accents.py)):
12 speakers from the Speech Accent Archive, George Mason University, CC BY-NC-SA 4.0 ([download page](https://accent.gmu.edu/download)), each reading the same paragraph; first languages English, Mandarin (2), Cantonese, Hindi, Tamil, Urdu, Vietnamese, Korean, Japanese, Tagalog, Thai.

- Whole paragraph, one upload each, `languageBias: ["English"]`: word error rate 4.3% for the native English speaker, 2.9% to 18.8% for the others, 9.3% on average.
- Most errors are a doubled word ("with her with her") or a misheard name ("call Stella" heard as "call Taylor", "Peace Corps").
- The same 10-second file sent three times gives different transcripts, 0% to 14% apart, so single-run comparisons under about 15% are noise.
- Transcribing each 10-second clip twice and asking Muse Spark which words were most likely said cut the word error rate from 10.3% to 4.1% on the 10 speakers where the merge returned text.
  The app now always transcribes twice in parallel and passes both transcripts to the Muse Spark call it already makes, so the only added cost is one more transcription (about $0.0003 per answer).

### Audio formats

- Realtime: raw PCM, "signed 16-bit little-endian, mono", 24 kHz (native) or 16 kHz (resampled server-side) ([Speech to text](https://dev.meta.ai/docs/speech-to-text), [Voice schemas](https://dev.meta.ai/docs/api-reference/voice/schemas)).
- File: "a RIFF/WAVE container with mono, 16-bit integer PCM at 16 kHz or 24 kHz"; anything else must be converted first ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
- The realtime example paces 80 ms frames (3,840 bytes at 24 kHz) ([Transcribe in realtime](https://dev.meta.ai/docs/api-reference/voice/realtime)).

### Price

- "$0.18 per hour" of audio, metered in whole seconds, same price streaming or not; failed requests and `429` responses are not billed ([Speech to text](https://dev.meta.ai/docs/speech-to-text), [Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits)).

### Data use and retention

- Standard mode: "your content is not used to train Meta models" ([Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits)).
- Terms of Service section 5.1: Meta "will not use Content submitted in a request to the Standard Services to train Meta Models" ([Terms of Service](https://dev.meta.ai/legal/terms-of-service)).
- Contributor mode (training in exchange for a discount) exists only for some Muse Spark variants, "not for Muse Wonder or Muse Voice Transcribe" ([Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits)).
  So Muse Voice Transcribe is always the non-training tier.
- Terms section 6.2 (Contributor only): "You must not submit sensitive, confidential, or personal information to the Contributor Services." ([Terms of Service](https://dev.meta.ai/legal/terms-of-service)).
- Retention in Standard mode: "Meta will retain your Content and Usage Data as needed" (section 4.4); no fixed period is stated ([Terms of Service](https://dev.meta.ai/legal/terms-of-service)).
- Data Commitments: "Meta will retain your Content as described in the Terms and Privacy Policy" and "You can view and delete your Content through your Account" ([Data Commitments](https://dev.meta.ai/legal/commitments)).
- Zero data retention (ZDR): prompts and responses "aren't stored by Meta after the response is returned", except for legal or misuse reasons; it is enabled per organization through the contact-sales form, and "ZDR support may vary by model" ([ZDR help article](https://dev.meta.ai/help/policies-and-privacy/zero-data-retention)).
- ZDR "is priced at parity with Standard mode" ([Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits)).
- Whether ZDR covers Muse Voice Transcribe audio: not verified.
- Default retention period for audio in Standard mode: not verified (no page states one).

### Audio input to Muse Spark (a second Meta path)

- Muse Spark 1.3 takes "Text, image, video, audio, PDF" as input and returns text ([Models](https://dev.meta.ai/docs/models)).
- "Audio files can be uploaded via `/v1/files` or sent inline using `input_audio`", formats `audio/mpeg` and `audio/wav` ([File handling](https://dev.meta.ai/docs/file-handling)).
- It has no audio output ([Chat Completions protocol](https://dev.meta.ai/docs/protocols/chat-completions)).

## 2. Meta text-to-speech in the Model API

- None.
  The models page lists no speech-output model; Muse Voice Transcribe "does not synthesize speech or provide a speech-to-speech conversation API" ([Models](https://dev.meta.ai/docs/models)).
- The docs navigation has no text-to-speech page, and the cookbook has no TTS recipe ([Docs index](https://dev.meta.ai/docs/), [Cookbook index](https://dev.meta.ai/docs/cookbook/)).
- Pricing lists no TTS price ([Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits)).
- Chat Completions rejects audio output: "`audio`: No audio output." and "`modalities`: Not supported" ([Chat Completions protocol](https://dev.meta.ai/docs/protocols/chat-completions)).

## 3. Meta open-weight speech models on a Mac

### Omnilingual ASR (speech to text only)

- "over 1,600 languages", with language codes like `eng_Latn` ([GitHub README](https://github.com/facebookresearch/omnilingual-asr)).
- License: code and models "released under the Apache 2.0" license, so commercial use is allowed ([GitHub README](https://github.com/facebookresearch/omnilingual-asr)).
- Families: CTC, LLM-ASR, LLM Unlimited, each at 300M, 1B, 3B, 7B, plus a 7B zero-shot model; current cards are `omniASR_CTC_{300M,1B,3B,7B}_v2`, `omniASR_LLM_{...}_v2`, `omniASR_LLM_Unlimited_{...}_v2`, `omniASR_LLM_7B_ZS` ([inference README](https://github.com/facebookresearch/omnilingual-asr/tree/main/src/omnilingual_asr/models/inference)).
- Size: inference memory from about 2 GiB (CTC 300M) to about 17 GiB (LLM 7B), measured on an A100 in BF16; downloads 1.3 GiB to 30 GiB ([GitHub README](https://github.com/facebookresearch/omnilingual-asr)).
- "Currently only audio files shorter than 40 seconds are accepted for inference on CTC and LLM model suites"; the Unlimited variants lift that ([GitHub README](https://github.com/facebookresearch/omnilingual-asr)).
- LLM models take an optional language code and the authors recommend passing it; CTC models "don't support language conditioning" ([inference README](https://github.com/facebookresearch/omnilingual-asr/tree/main/src/omnilingual_asr/models/inference)).
- Mac: install with `pip install omnilingual-asr`; the README says the reference pipeline "works across platforms" and Macs need `brew install libsndfile` ([GitHub README](https://github.com/facebookresearch/omnilingual-asr)).
- The pipeline picks `"cuda" if torch.cuda.is_available() else "cpu"` and accepts a `device` argument ([pipeline.py](https://github.com/facebookresearch/omnilingual-asr/blob/main/src/omnilingual_asr/models/inference/pipeline.py)).
  So it falls back to CPU on a Mac; running on MPS (Apple GPU): not verified.
- TTS: none; the project is recognition only ([GitHub README](https://github.com/facebookresearch/omnilingual-asr)).

### SeamlessM4T v2 and Seamless

- SeamlessM4T v2 Large: "101 languages for speech input", "96 Languages for text input/output", "35 languages for speech output" ([model card](https://huggingface.co/facebook/seamless-m4t-v2-large)).
- Size: 2.3B parameters ([model card](https://huggingface.co/facebook/seamless-m4t-v2-large), [GitHub](https://github.com/facebookresearch/seamless_communication)).
- License: "cc-by-nc-4.0", so non-commercial only ([model card](https://huggingface.co/facebook/seamless-m4t-v2-large)); SeamlessExpressive and Seamless use a separate Seamless license plus an acceptable use policy; the code is MIT ([GitHub](https://github.com/facebookresearch/seamless_communication)).
- Speech output: it does text-to-speech translation (T2ST), so it can speak text, but only in its speech-output languages ([GitHub](https://github.com/facebookresearch/seamless_communication)).
- Mac: fairseq2 has "pre-built packages available only for Linux x86-64 and Apple-silicon Mac computers"; `unity.cpp` runs speech-to-text and ASR on CPU without Python via GGML ([GitHub](https://github.com/facebookresearch/seamless_communication)).
- Also in Hugging Face Transformers as `SeamlessM4Tv2Model` ([model card](https://huggingface.co/facebook/seamless-m4t-v2-large)).
- MPS speed or support: not verified.

### MMS (Massively Multilingual Speech)

- ASR: `mms-1b-all`, 1B parameters, CC-BY-NC 4.0, the card says it "supports 1162 languages", 16 kHz audio ([model card](https://huggingface.co/facebook/mms-1b-all)).
- ASR switches language by loading a small adapter (about 2 million parameters) per language ([Transformers MMS docs](https://huggingface.co/docs/transformers/model_doc/mms)).
- TTS: yes; MMS-TTS is a VITS model with "a separate model checkpoint for each of the 1100+ languages" ([Transformers MMS docs](https://huggingface.co/docs/transformers/model_doc/mms)).
- TTS size: `facebook/mms-tts-eng` is 36.3M parameters; license "CC-BY-NC 4.0" ([model card](https://huggingface.co/facebook/mms-tts-eng)).
- TTS quirks: some scripts (Arabic, Mandarin, Hindi, Korean) need the `uroman` Perl romanizer before tokenizing; checkpoints are trained "on lower-cased, un-punctuated text"; speaking rate is adjustable via `model.speaking_rate` ([Transformers MMS docs](https://huggingface.co/docs/transformers/model_doc/mms)).
- Language identification models for 126 to 4,017 languages also exist ([Transformers MMS docs](https://huggingface.co/docs/transformers/model_doc/mms)).
- Mac: the Transformers examples load with `device_map="auto"`; the docs do not state Mac support.
  CPU use is plausible given the 36M size, but Mac speed and MPS support: not verified.
- Commercial use: the NC license forbids it; whether a hackathon demo counts as non-commercial depends on the hackathon rules (not verified here).

## 4. Browser Web Speech API in 2026

### speechSynthesis (text to speech)

- Baseline: "available across browsers since September 2018" ([MDN SpeechSynthesis](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis)).
- `getVoices()` returns "all the available voices on the current device", and Chrome needs a `voiceschanged` listener before the list fills ([MDN SpeechSynthesis](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis), [MDN Using the Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)).
- `SpeechSynthesisVoice.localService` tells "whether the voice is supplied by a local speech synthesizer service (`true`), or a remote speech synthesizer service (`false`)" ([MDN localService](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice/localService)).
- Safari on macOS: WebKit builds its voice list from `AVSpeechSynthesisVoice`, keeps only `isSystemVoice` voices, and marks every one `localService: true` ([WebKit PlatformSpeechSynthesizerCocoa.mm](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/cocoa/PlatformSpeechSynthesizerCocoa.mm)).
  So Safari voices are the Mac's own voices and run on the device.
- Chrome on macOS: its native backend also uses `AVSpeechSynthesisVoice` ([Chromium tts_mac.mm](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/speech/tts_mac.mm)).
- Chrome also ships a "Google Network Speech" component: "Component extension providing speech via the Google network text-to-speech service", with 19 voices such as "Google US English", each `"remote": true`, in 18 locales (de-DE, en-GB, en-US, es-ES, es-US, fr-FR, hi-IN, id-ID, it-IT, ja-JP, ko-KR, nl-NL, pl-PL, pt-BR, ru-RU, zh-CN, zh-HK, zh-TW) ([Chromium manifest](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/resources/network_speech_synthesis/mv3/manifest.json)).
  Text spoken with a "Google ..." voice therefore goes to Google; local Mac voices do not.
- macOS voices come from System Settings > Accessibility > Read & Speak; extra voices are "automatically downloaded from Apple (you must be connected to the internet)", and "Voices are not available in all languages." ([Apple Support](https://support.apple.com/guide/mac-help/change-the-voice-your-mac-uses-to-speak-text-mchlp2290/mac)).
- Offline: local voices need no network once installed (follows from `localService: true` above); Chrome's Google voices need the network.
  A live offline test on this Mac: not done.
- Exact list of languages with a built-in macOS voice in 2026: not verified.

### SpeechRecognition (speech to text)

- MDN: "On some browsers, like Chrome, using Speech Recognition on a web page involves a server-based recognition engine. Your audio is sent to a web service for recognition processing, so it won't work offline." ([MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)).
- Chrome's source confirms the destination: the default engine posts to `https://www.google.com/speech-api/full-duplex/v1`, and its network annotation says "Audio is sent to Google's servers (upstream) and text is returned (downstream)", `destination: GOOGLE_OWNED_SERVICE` ([Chromium network_speech_recognition_engine_impl.cc](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/speech/network_speech_recognition_engine_impl.cc)).
- On-device option: `recognition.processLocally = true`, plus static `SpeechRecognition.available({langs, processLocally})` and `SpeechRecognition.install({langs})` to fetch a language pack; "once installed, the functionality will be available offline" ([MDN Using the Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)).
- If the pack is missing, `start()` fails with `language-not-supported` ([MDN Using the Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)).
- Status: the "On-device Web Speech API" is in the Chrome 139 release notes; its purpose is "to ensure that neither audio nor transcribed speech are sent to a third-party service for processing" ([Chrome 139 release notes](https://developer.chrome.com/release-notes/139), [Chrome Platform Status](https://chromestatus.com/feature/6090916291674112)).
- Browser compat data: `processLocally`, `available()`, `install()` added in desktop Chrome 139, not in Chrome Android, not in Safari, Firefox "preview"; all three still flagged experimental ([MDN browser-compat-data](https://github.com/mdn/browser-compat-data/blob/main/api/SpeechRecognition.json)).
- The intent to ship said "Initially supported on Windows, Mac, and Linux with ChromeOS support to follow" and each language pack is about 60 MB ([blink-dev Intent to Ship](https://groups.google.com/a/chromium.org/g/blink-dev/c/VNOok2dbmHM/m/TQpe9shjCgAJ)).
- Which languages have on-device packs: not verified.
- `phrases` (contextual biasing) arrived in Chrome 142, experimental ([MDN browser-compat-data](https://github.com/mdn/browser-compat-data/blob/main/api/SpeechRecognition.json)).
- Safari: speech recognition is "powered by the same speech engine as Siri", covers "over 50 languages and dialects", and needs Siri enabled ([WebKit blog, Safari 14.1](https://webkit.org/blog/11648/new-webkit-features-in-safari-14-1/)).
- Apple's privacy page: unless the device shows on-device processing, "the things you say are sent to and processed by Apple"; for apps using Speech Recognition, "the audio data to be transcribed may be sent to Apple"; audio "is not stored by Apple unless you opt in to Improve Siri and Dictation" ([Apple, Siri, Dictation & Privacy](https://www.apple.com/legal/privacy/data/en/ask-siri-dictation/)).
  The page does not mention web pages, so whether Safari's Web Speech audio stays on the Mac: not verified.
- Safari has no `processLocally` ([MDN browser-compat-data](https://github.com/mdn/browser-compat-data/blob/main/api/SpeechRecognition.json)).
- Known iOS bug: "SpeechRecognition stops producing results (no onresult/onerror/onend) after an <audio>/<video> element plays, on iOS Safari", filed 2026-08-10, still NEW ([WebKit bug 321436](https://bugs.webkit.org/show_bug.cgi?id=321436)).

## 5. App speech while a screen reader is running

- No current WAI or WebAIM guidance specific to a self-voicing web app next to a screen reader was found.
- WCAG 1.4.2 Audio Control: audio that plays automatically for more than 3 seconds needs a pause or stop control or an independent volume control, because "Individuals who use screen reading software can find it hard to hear the speech output if there is other audio playing at the same time"; WCAG encourages "that the sound be started by an action initiated by the user" ([Understanding SC 1.4.2](https://www.w3.org/WAI/WCAG22/Understanding/audio-control.html)).
- Older W3C list discussion: self-voicing apps "are NOT substitutes for screen readers", and users avoid clashes by putting the screen reader into sleep mode for that app ([wai-xtech, 2007](https://lists.w3.org/Archives/Public/wai-xtech/2007Sep/0058.html)).
- A 2010 W3C proposal: browser speech should be "togglable by the user and/or disabled if a screen reader is detected" ([wai-xtech, 2010](https://lists.w3.org/Archives/Public/wai-xtech/2010Aug/0072.html)).
- Pages cannot reliably detect a screen reader; Mozilla's accessibility lead argued detection is a privacy risk ("running around the web waving a red flag") and that fixes belong in ARIA and keyboard support ([Marco Zehe, 2014](https://marcozehe.de/why-screen-reader-detection-on-the-web-is-a-bad-thing/)).
  We found no standard API for detection; that absence is from search, not from a spec statement.
- Live regions are how a page hands text to the screen reader: `aria-live="polite"` is spoken "whenever the user is idle"; `assertive` "will interrupt any announcement" and "should only be used sparingly"; regions should exist in the initial markup ([MDN Live regions](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Guides/Live_regions)).
- `role="alert"` plus `aria-live="assertive"` "causes double speaking issues in VoiceOver on iOS" ([MDN Live regions](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Guides/Live_regions)).
- Microphone echo: a dictation vendor's VoiceOver guide warns of "a feedback loop if your microphone picks up VoiceOver's speech output while dictation is active" and says "Wear headphones before you begin" ([Carescribe TalkType help](https://help.carescribe.io/en/using-voiceover-with-talktype-)).
  The same risk applies to our own TTS: the mic can transcribe the app's own question.
- `speechSynthesis.cancel()` "Removes all utterances from the utterance queue", and `pause()`/`resume()` exist, so the app can stop talking on a key press ([MDN SpeechSynthesis](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis)).

## 6. Digits, spelled names, and addresses

- SNuC corpus (LREC 2022): spoken alphanumeric identifiers are hard because letters like "pee vs bee, em vs en, tee vs dee" differ "by a single phoneme", and people say "double nine six" for 996 or "six oh four" for 604 ([SNuC paper](https://aclanthology.org/2022.lrec-1.212.pdf)).
- In SNuC, a baseline recognizer had 96.6% per-character accuracy on real-world data but only 77% of whole identifiers right, "almost 1 in 4 spoken identifiers containing a recognition error"; adapting to SNuC raised whole-identifier accuracy to 91.7% ([SNuC paper](https://aclanthology.org/2022.lrec-1.212.pdf)).
- The same corpus found 6.2% of readings had a speaker error, and about two thirds of those were "unnoticed by the speaker" ([SNuC paper](https://aclanthology.org/2022.lrec-1.212.pdf)).
  So read-back confirmation catches both machine and human errors.
- Amazon Lex: unusual surnames confuse recognition, so Lex offers "spell by letter" and "spell by word" ("a as in alpha b as in boy"), because letters "such as "t," "b," "p", that sound similar"; it recommends natural capture first and re-prompting with a spelling style on failure ([AWS Lex V2 spelling styles](https://docs.aws.amazon.com/lexv2/latest/dg/spelling-styles.html)).
- Google Cloud Speech-to-Text: "You can use class tokens to boost whole sets of words like digit sequences or addresses", and "post-processing might be necessary to fit required format" ([Google Cloud troubleshooting](https://docs.cloud.google.com/speech-to-text/docs/troubleshooting)).
- Muse Voice Transcribe does no transcript reformatting and returns no confidence scores ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).
  Whether it writes "one two three" or "123", and how it handles spelled letters: not verified.
- Muse Voice Transcribe keyword biasing helps with names but does "not guarantee an exact spelling" ([Speech to text](https://dev.meta.ai/docs/speech-to-text)).

## What this means for our design

- Speech to text: use Muse Voice Transcribe over the realtime WebSocket in `PUSH_TO_TALK` (one press per answer), with `languageBias` set to the language the user picked; it is a Meta tool, it is always the non-training tier, and it costs $0.18 per audio hour.
  For languages outside its 25, fall back to Omnilingual ASR (Apache 2.0) on the local server, after we test its CPU speed on this Mac.
- Text to speech: the Meta API has none, so speak with the browser's `speechSynthesis` using only voices with `localService: true` (in Chrome, never the "Google ..." voices, which send the text, including read-back SSNs, to Google).
  MMS-TTS is the Meta option for languages the Mac has no voice for, but it is CC-BY-NC 4.0, so the owner must accept that license for the hackathon.
- Where audio goes: the browser captures 16-bit PCM and streams it to our local Python server, which holds the API key and relays to `wss://api.meta.ai/v1/asr/realtime`; the key never reaches the page.
  Do not use Chrome's default `SpeechRecognition` (audio to Google) or Safari's (audio may go to Apple) for answers; only `processLocally = true` in Chrome is acceptable as an offline fallback.
  Tell the user plainly that spoken answers go to Meta, are not used for training, and are kept "as needed" unless we get ZDR.
- Languages: the user picks a language once at the start; Muse Spark translates each question, the app picks a local voice for that language, and the same language name goes into `languageBias`.
  If there is no local voice for the language, say so and offer screen-reader mode.
- Screen readers: at the start, ask which voice to use, and never start speaking before a user action (WCAG 1.4.2).
  "Screen reader mode" puts each question in a polite live region and uses no app voice; "App voice mode" speaks with TTS, stops on a key press, and asks for headphones.
  Always stop TTS before opening the mic, so the app never transcribes its own question.
- Sensitive and spelled answers: ask for the SSN in its 3-2-4 groups, normalize words to digits on the server, check the format, and read it back grouped for a yes/no confirm; for names, offer spell-by-word ("B as in boy") on the second try, and always let the user type instead.

# Demo video

For the Meta Global AI Developer Hackathon 2026 submission (due 2026-11-04, 9 PM ET).
The rules publish no length limit; the cut runs about 3 minutes 35 seconds.
Judging: Impact 30%, Use of Meta AI tooling 25%, Demo 25%, Technical depth 20%; each scene below says which one it serves.
Every number spoken is in the README's Evidence section under Proven; nothing else is claimed.

## How it is made

Two scripts in `src/` make the video, so it can be made again after any change to the app:

1. `uv run record_demo.py` records the app scenes from the real app in Chromium, with sound, into `src/out/demo/`: the W-9 filled by voice (`fill`), its first question in Spanish (`spanish`), and the W-4 uploaded (`upload`).
   The app speaks with the Mac's own voices, as it does for a person; the person's replies are recorded speech played into the microphone, and every reply goes to Meta's models as in real use.
   Each take also writes a timeline (`fill.json` and so on) of who said what, and when.
2. `uv run edit_demo.py` cuts the takes into `src/out/demo/demo.mp4`, 1920 by 1080: the app on the left, and on the right a panel with the words being spoken, the narrator's line, and the footer.
   It keeps each sound whole and shortens only the waiting between them.

Two parts are made with AI on Higgsfield, and both are labelled in the video:

- the narrator's voice (Seed Audio, voice "Holden"), in `src/out/demo/narration/`;
- the opening shot of a person at a table with a laptop and a paper form (Seedance 1.5), in `src/out/demo/broll.mp4`, marked "AI-generated footage (Higgsfield)".

The footer on every app scene says: "Sample answers, not a real person. Waiting time shortened. Narrator's voice made with AI (Higgsfield)."
None of the files in `src/out/demo/` are committed.

## Scenes

### 1. The problem - Impact

On screen: the AI-generated opening shot with subtitles, then the r/Blind post and its reply as a card.

> "Can we enter signatures on pdf forms either in adobe acrobat reader or using markup in ios? Or do we still need sighted help. I have a form to sign."
> "I just use AI to sign inaccessible forms now lol."

Narration:
Blind people are sent PDF forms every week: tax forms, leases, medical intake.
Most are flat pages with no fields a screen reader can use.
Today they ask a sighted person, or paste the form into a chatbot that sends their answers away and checks nothing.

### 2. Fill it by talking - Demo

On screen: the start page, then the W-9 interview by voice.

| The page asks | The person says | What it shows |
| --- | --- | --- |
| Your name | "Jordan Rivera." | It reads the name back; "Yes." |
| Business name | "Skip." | It moves on by itself |
| Social Security number | "One two three, four five, six seven eight." | "I need 9 digits": a missed digit is caught |
| Social Security number | "One two three, four five, six seven eight nine." | It reads the digits back |
| Backup withholding | "What does that mean?" | It explains, then reads the form's exact words: "You must cross out item 2 above if ..." |

Narration, over the app with its voice turned down:
This is the IRS W-9, filled in by voice alone.
It asks one question at a time, only the ones that apply to you.
Every answer is read back before it counts, because a blind person can't see a typo.
Ask what something means, and you hear the form's own words, not a guess.

### 3. Sign and finish - Demo

On screen: the certification is read and the person says "Yes, I certify."; then "Your form is filled.", the downloaded PDF, and its filled fields up close.

Narration:
It reads the legal certification word for word, then writes every answer onto the original IRS form, one digit per box.
The form you send is the official one, not a copy.

### 4. Spanish, and any form - Demo, Impact

On screen: the W-9's first question in Spanish, answered "Me llamo Jordan Rivera." and "Sí."; then the IRS W-4 uploaded on the start page and its first question.

Narration:
It speaks English or Spanish.
And it works on forms it has never seen: upload one, and it reads the form's own fields, or finds the blanks on a flat page, and writes the questions once.

### 5. How it works - Meta AI tooling, Technical depth

On screen: `architecture/architecture.html`, zoomed in on the boxes each line is about.

Narration:
Muse Spark looks at each page and names every blank; our code lines each one up with the box printed on the page.
On the W-9, Muse alone placed 15 of 23 fields; with our lining up, 23 of 23.
Muse Spark writes the questions from the form and its instructions, and our code checks that every quote matches the form word for word.
Each spoken reply goes to Muse Voice Transcribe twice at once, and Muse Spark decides what the person meant.
On recordings of 10 accented speakers, hearing each reply twice cut wrong words from 10% to 4%, and the read-back catches the rest.
Everything you type stays on your computer, and the questions are read aloud by the computer's own voices.

### 6. Honest close - Impact

On screen: a card with what is proven and what is not yet, the repo address, and "Built with Meta's Muse Spark and Muse Voice Transcribe."

Narration:
It is proven on IRS forms by keyboard and by voice.
We have not yet tested it with blind users; that is next.
The code is open source.

"The code is open source" is true only once the repository is public.

## Lines never to say

These are under "Can't claim" in the README:
- that it helps blind users, or that blind users prefer it;
- that it works on any PDF, or on scanned forms;
- that spoken answers stay private: they go to Meta to be understood;
- that a typed name is a legal signature everywhere.

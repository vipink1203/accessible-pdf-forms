# Blind users and PDF forms: the problem and what exists today

Collected 2026-10-31 for the Meta Global AI Developer Hackathon 2026.
Sources were found while choosing a project for the Meta Global AI Developer Hackathon 2026 (that idea research is not part of this repository), plus a direct read of EffortlessVI Fill.

## The person and the problem

- A blind screen-reader user who must fill and sign a PDF form: a government form, a lease, a medical intake, a school document.
- "Can we enter signatures on pdf forms either in adobe acrobat reader or using markup in ios? Or do we still need sighted help. I have a form to sign." ([r/Blind, 2026-10-15](https://www.reddit.com/r/Blind/comments/1x6t21a/signing_pdfs_with_a_screen_reader/)).
- Reply in the same thread: "I think this relies on the pdf being formatted properly because I have rarely gotten this feature to work with adobe. Not even official government forms. I just use AI to sign inaccessible forms now lol." ([comment](https://www.reddit.com/r/Blind/comments/1x6t21a/_/pgz6qgu/)).
- A student converting image-only PDFs with a chatbot: "it sure beats waiting days or weeks at a time to have documents converted by the school." ([r/Blind, 2026-09-21](https://www.reddit.com/r/Blind/comments/1wml1ns/converting_inaccessible_documents_to_readable/)).
- Form fields "float" with no labels a screen reader can find ([WebAIM discussion list](https://webaim.org/discussion/mail_message?id=39818)).
- WebAIM Screen Reader User Survey #10 (1,539 responses, Dec 2023 to Jan 2024) lists "Complex or difficult forms" among the most problematic items ([WebAIM](https://webaim.org/projects/screenreadersurvey10/)).

## Why it stays broken

- The PDF has no tags or form fields, so the screen reader sees an image or unordered text.
- Fixing a form ("remediation") is specialist work aimed at the form's author, and authors rarely do it.
- The ADA Title II web rule requires state and local government content, including PDFs used to apply for services, to meet WCAG 2.1 AA; a 2026 interim final rule moved compliance to April 26, 2027 (population 50,000 or more) and April 26, 2028 (smaller governments) ([ADA.gov](https://www.ada.gov/resources/2024-03-08-web-rule/)).
- Today's workaround: ask a sighted person, wait for the author, or paste the form into a general chatbot.

## Evidence the approach helps

- In the EICS 2020 study that the research prototype [TransPAc](https://par.nsf.gov/servlets/purl/10186661) builds on, 14 users with visual impairments filled more fields through a web-form version of a PDF form than with the PDF itself ([paper](https://www.researchgate.net/publication/342296368_Breaking_the_Accessibility_Barrier_in_Non-Visual_Interaction_with_PDF_Forms)).
  Exact completion rates were not found.
- A Vision Australia study reportedly found blind participants' PDF task completion rose from 26% to 79% with accessibility-optimised PDFs.
  This came from a search summary; the primary report has not been opened, so treat it as unverified.

## Nearest neighbours

| Product or project | What it does | What it leaves open |
| --- | --- | --- |
| [EffortlessVI Fill](https://effortlessvi.com/fill) (version 2026-09-15) | Windows app: "Open a PDF in Fill", navigate it with keyboard and screen reader, "type your answers", "Save a completed copy of the PDF". Free for personal use. | "Fill works with text-based PDFs. This version does not include OCR, so it cannot read the contents of scanned or image-only PDFs." No mention of explaining the form, skipping what doesn't apply, AI, or privacy. Windows only. |
| [TransPAc](https://par.nsf.gov/servlets/purl/10186661) | Research prototype that turns a PDF form into a web form | Handles only forms that already have fields |
| [PREP](https://www.continualengine.com/blog/creation-of-accessible-pdf-forms-made-easy-with-prep/), Adobe Acrobat, CommonLook | Remediation tools for the form's author | The reader can't fix a form they were sent |
| [Chrome's built-in OCR](https://developers.google.com/chromeos/app-development/news/frictionless-pdf-accessibility-with-ml-and-ocr?hl=it) | Makes scanned PDFs readable by screen readers | Nothing found says it makes them fillable |
| [iPadOS 17 form detection](https://techcrunch.com/2023/06/05/ipad-os-17s-live-collaboration-on-pdfs-could-challenge-google-docs) | Detects fields, including in scans | Whether VoiceOver users can use the detected fields is undocumented |
| Acrobat auto-detect with NVDA or JAWS | Detects fields for the reader | Detection is "hit or miss" ([NC DIT training](https://it.nc.gov/documents/training-docs-images/pdf-accessibility-training-day-two-slides/open)); form-filling bugs through 2025-2026 ([Adobe community](https://community.adobe.com/questions-9/acrobat-crashing-when-attempting-to-read-forms-with-nvda-1304063), [Freedom Scientific](https://blog.freedomscientific.com/download-the-latest-adobe-reader-update-to-resolve-an-issue-filling-out-pdf-forms/)) |
| General chatbots | Read the form aloud, sometimes fill it | The person sends their answers to a model, and nothing checks the explanation against the form |

## What this means for us

- No neighbour found does all three: works on flat forms, explains the form in plain words grounded in its own text, and writes the answers onto the original PDF.
- EffortlessVI Fill is the closest for text-based PDFs, so the demo should lead with a flat form and the guided interview, where it stops.
- Unverified and worth checking before the pitch: whether iPadOS field detection works with VoiceOver, and the Vision Australia figure.

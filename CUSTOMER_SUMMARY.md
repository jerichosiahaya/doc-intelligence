# Ask your reports: a one-page summary

**For:** a business owner who reads long reports and wants fast, trustworthy answers.

## What it does

You add a PDF report. You type a question in plain words, for example "How many electric trucks did we add in 2025?" In a few seconds you get a short answer. Every answer shows **the pages it came from**. You can open any of those pages and check the answer yourself.

It reads more than the text. It also reads tables, charts, summary boxes, diagrams, and even a scanned page.

## What we tested

We tested it on a 13-page annual report with 19 questions: facts in text, tables, charts, a diagram, a scanned page, and some tricky cases.

| | Result |
|---|---|
| Questions answered correctly | **19 of 19** |
| Found the right pages | 17 of 17 answerable questions |
| Time for one answer | about 4 seconds |

Two of the tricky cases are worth knowing:

- **Two pages that disagree.** The CEO's letter says 96 electric trucks. A correction page says 82. The system answers **82** and tells you about the conflict.
- **Questions the report cannot answer.** It says "not in the document." It does not guess. Where a rule needs data that the report does not contain (for example quarterly figures when only yearly figures are given), it says so.

## What to keep in mind

- **Check the pages for important decisions.** The system can still make mistakes. It once read a figure wrongly in our early tests. The cited page lets you see for yourself.
- **The test is small.** One person wrote the 19 questions, on one report. We tuned the system on those questions, so the real accuracy on your own reports may be lower. Please try it on your own documents and questions first.
- **A new report takes time to add.** About two minutes for 13 pages. A computer reads each page one by one.
- **It does not remember earlier questions.** Write each question in full ("What was profit in 2024?" and not "and in 2024?").
- **Your data.** Page images and your questions go to the AI service that your company chooses. The search itself runs on your own computer. Use a service that meets your privacy rules.

## What you need

A computer or server that runs the system, and an AI service key (the system can use many AI models). Three commands start it. See the README.

## Suggested next steps

1. Add two or three of your own reports and ask the questions that you really ask.
2. Ask a colleague to write 10 questions without seeing ours. We run them and report the score.
3. Decide who may use it. Today there is one shared password, with no separate users.

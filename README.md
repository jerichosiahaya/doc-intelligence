# Document Intelligence

This project answers questions about a company annual report. Each answer shows the page it comes from. The system refuses to answer when the report does not contain the answer.

The sample document is the fictional "Kirana Logistics Group Annual Report 2025" (13 pages). The pipeline also works on other PDF files without code changes.

## Contents

1. [Quick start](#quick-start)
2. [How the system works](#how-the-system-works)
3. [Interfaces](#interfaces)
4. [Configuration](#configuration)
5. [Evaluation](#evaluation)
6. [Key decisions and trade-offs](#key-decisions-and-trade-offs)
7. [Known limitations](#known-limitations)
8. [What to do with another week](#what-to-do-with-another-week)
9. [How AI tools were used](#how-ai-tools-were-used)
10. [Repository files](#repository-files)

## Quick start

### Requirements

- Python 3. The project was tested with Python 3.14 only.
- A LiteLLM proxy (or any OpenAI-compatible API) with these models:
  - a vision model that accepts images,
  - a text model for answers,
  - a text model for grading (can be the same model as the answer model).
- Internet access for the first run. The local embedding model downloads once.

### Run in five commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env (see Configuration)
python ingest.py path/to/report.pdf
python server.py
```

After the last command, open `http://localhost:8000/` in a browser.

### Ask a question without the server

1. Stop `server.py` if it is running. See [Known limitations](#known-limitations) for the reason.
2. Run:

   ```bash
   python ask.py "What was net profit in 2024?"
   ```

### Ingest a new PDF

Run `ingest.py` again with the new file:

```bash
python ingest.py path/to/new_report.pdf
```

The system keeps each document under its file name. Ingesting the same file again replaces its old data. Ingesting a different file adds it to the index.

## How the system works

### Data flow

```
PDF ──► ingest.py ──► chunks ──► embeddings ──► Qdrant
                                                  │
 question ──► embed ──► top-k search ◄────────────┘
                              │
                              ▼
                  answer model (LiteLLM)
                              │
                              ▼
        { answer, citations, refused, retrieved_pages }
```

### Ingestion

The ingestion step reads each page in three ways.

| Pass | Tool | What it finds |
|---|---|---|
| Text | PyMuPDF `get_text()` | Paragraphs, headings, captions, notes |
| Tables | PyMuPDF `find_tables()` | Tables with rows and columns |
| Vision | Page image (170 dpi) sent to the vision model | Charts, summary tiles, diagrams, scanned pages |

The system makes chunks from the results:

- **Prose chunks.** The text of a page is split at paragraph breaks. Each chunk has at most 1,200 characters.
- **Table chunks.** Each table is one chunk in Markdown format. The table caption is stored with it.
- **Table continuation.** If a table continues on the next page with the same header row, the system joins both parts into one chunk.
- **Vision chunks.** Each page has one chunk with the full vision transcript.

Every chunk stores these fields: `doc`, `page`, `type`, `source`, `caption`, and `text`.

The system writes all chunks to `chunks.jsonl`. Read this file after each ingestion to find extraction errors.

The vision results are cached in `.cache/vision/`. The cache key is a hash of the model name and the page image. A second ingestion of the same page does not call the model again.

### Retrieval

1. The system embeds the question with the local model `BAAI/bge-small-en-v1.5`.
2. Qdrant returns the `TOP_K` chunks with the highest cosine similarity. The default is 6.

### Answering

The answer model receives the retrieved chunks, each marked with its page number. The system prompt gives these rules:

- Use only the supplied excerpts.
- Cite the page of each fact as `[p.N]`.
- If the excerpts do not contain the answer, set `refused` to true. Do not guess.
- If two excerpts disagree, report both and cite both pages.

The model returns JSON. If the JSON cannot be parsed, the system returns the raw text with `refused` set to false.

## Interfaces

All four interfaces call the same function, `ask.answer()`.

| Interface | Start | Address |
|---|---|---|
| CLI | `python ask.py "question"` | Terminal |
| REST | `python server.py` | `http://localhost:8000/ask` |
| gRPC | `python server.py` | `localhost:50051` |
| Web UI | `python server.py` | `http://localhost:8000/` |

### CLI

```bash
python ask.py "What was net profit in 2024?"          # readable output
python ask.py "What was net profit in 2024?" --json   # full JSON
```

### REST

```bash
curl -s localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "What was net profit in 2024?"}'
```

Response fields:

| Field | Type | Meaning |
|---|---|---|
| `answer` | string | The answer text |
| `citations` | list of integers | Pages the model cited |
| `refused` | boolean | True when the report does not support an answer |
| `retrieved_pages` | list of integers | Pages of the chunks sent to the model |
| `seconds` | number | Time to answer |
| `tokens` | integer | Total tokens used by the answer call. Zero if the proxy does not report usage. |

Status codes:

| Code | Cause |
|---|---|
| 200 | Success |
| 422 | The question is empty or longer than 2,000 characters |
| 502 | The model proxy or the vector store failed |

`GET /health` returns `{"ok": true}`.

### gRPC

The service is defined in `docintel.proto`. It has one method: `DocIntel.Ask(AskRequest) returns (AskReply)`. The reply has the same fields as the REST response.

Status codes: `INVALID_ARGUMENT` for an empty question. `UNAVAILABLE` for a backend failure.

Example with `grpcurl`:

```bash
grpcurl -plaintext -proto docintel.proto \
  -d '{"question": "What was net profit in 2024?"}' \
  localhost:50051 docintel.DocIntel/Ask
```

To regenerate the Python stubs after you change the proto file:

```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. docintel.proto
```

### Web UI

Open `http://localhost:8000/`. Type a question and select **Ask**. The page shows the answer, the cited pages, and a "Not in the document" badge when the system refuses. The page supports light and dark mode.

## Configuration

Set these values in `.env`. Do not commit this file. The file `.gitignore` excludes it.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `LITELLM_URL` | Yes | | Base URL of the proxy. Include `/v1` if the proxy needs it. |
| `LITELLM_KEY` | Yes | | API key for the proxy |
| `VISION_MODEL` | Yes | | Model that reads page images |
| `ANSWER_MODEL` | Yes | | Model that writes answers |
| `JUDGE_MODEL` | For `eval.py` | | Model that grades answers. Use a different model from `ANSWER_MODEL` if possible. |
| `TOP_K` | No | 6 | Number of chunks sent to the answer model |
| `QDRANT_URL` | No | | URL of a Qdrant server. If not set, the system uses a local folder (`qdrant_data/`). |
| `HOST` | No | `127.0.0.1` | Bind address of `server.py` |
| `PORT` | No | 8000 | REST and web UI port |
| `GRPC_PORT` | No | 50051 | gRPC port |

## Evaluation

### Method

The file `eval.yaml` contains 19 questions that were written by hand from the sample report. Each question has an expected answer and the pages that support it.

| Content type | Questions |
|---|---|
| Prose | 2 |
| Table lookup | 3 |
| Table with a calculation | 2 |
| Chart | 3 |
| Diagram | 2 |
| Summary tile (image) | 1 |
| Conflicting statements | 1 |
| Scanned page | 2 |
| Cannot be answered | 2 |
| Partly answerable | 1 |

Run the evaluation:

```bash
python eval.py
```

The script measures two things separately:

- **Retrieval.** A question passes if all of its supporting pages are in the retrieved pages. This checks the search, not the model.
- **Answer quality.** The judge model compares the answer with the expected answer. For the questions that cannot be answered, a question passes only if the system refuses.

The script prints a table by content type and writes every result to `results.json`.

### Results

**The evaluation has not been run yet.** Do not treat any number as a result until `python eval.py` has produced it. After the first run, record here:

- the retrieval and answer scores for each content type,
- every failed question, with the cause of the failure,
- the change that was made to fix the weakest part, and the scores before and after.

### Notes on the question set

- The expected answers for questions 4 and 5 are calculations. Check them again by hand.
- The judge is a language model. It can make mistakes. Read `results.json` for the failed questions.
- One person wrote all 19 questions. The set is small. A pass rate on this set does not prove the same rate on other reports.

## Key decisions and trade-offs

| Decision | Reason | Cost |
|---|---|---|
| Run the vision pass on every page | The report has facts only in images (the tiles on page 3, the charts, the diagram, the scan on page 13). One rule for all pages works on a new PDF. | One model call for each page. The cache reduces repeat cost. |
| Use PyMuPDF for tables as well as vision | Native table text is exact. It gives a second source to compare with the vision text. | Tables without ruled lines may not be found. |
| Keep each table in one chunk | A split table loses its header. Answers then use the wrong column. | Large tables make large chunks. |
| Join tables that continue on the next page | Table 2 (facilities) spans pages 7 and 8. | The rule needs the header row to repeat. |
| Use dense vector search only | This gives a simple baseline that works end to end. The report has 13 pages. | Exact terms such as `ID-CBT` can rank lower than they should. |
| Use a local embedding model | No second API key. The evaluation runs again at no cost. | The model is small (384 dimensions). |
| Use embedded Qdrant | Setup needs no server or container. | One process can open the index at a time. |
| Run REST, gRPC and the UI in one process | The embedded index allows only one process. | The three interfaces share one failure point. |
| Set the model temperature to 0 | Answers are more repeatable for evaluation. | Not fully repeatable on all providers. |

## Known limitations

- **Vision errors.** The vision model can read a number from a chart incorrectly. Nothing checks chart values automatically. Table values are not cross-checked either. Read `chunks.jsonl` after each ingestion.
- **Citations are not verified.** The `citations` field comes from the model. The system does not check that the cited page was in the retrieved set.
- **One process for the index.** With embedded Qdrant, do not run `server.py`, `ask.py`, `ingest.py`, and `eval.py` at the same time. Set `QDRANT_URL` to use a Qdrant server and remove this limit.
- **No authentication.** The REST and gRPC interfaces have no login and no encryption. By default, they accept connections from the local machine only. Do not set `HOST=0.0.0.0` on an open network.
- **No document filter.** If you ingest more than one document, a question searches all of them. The answer can mix sources.
- **Rules that need missing data.** The model must say what the report does not state. For example, the report gives only annual utilisation, but its watch-list rule uses quarterly values. Question 19 tests this case. The model can still fail it.
- **No conversation memory.** Each question is independent.
- **Cost and speed.** The system records seconds and tokens for each answer. It does not convert tokens to money.
- **Tested only on one document.** Other layouts (for example, scanned pages with no text layer, or tables without lines) are not tested.

## What to do with another week

1. Add hybrid search (dense and sparse vectors in Qdrant) and a reranker. Keep each one only if the evaluation shows a gain.
2. Verify citations. Reject any cited page that is not in the retrieved set.
3. Cross-check numbers. Compare table text with vision text. Check chart values against totals (for example, the four quarters must add up to the yearly revenue).
4. Show the cited page as an image in the web UI.
5. Add a document filter and multi-document questions.
6. Add authentication, request logging, and a cost report.
7. Move Qdrant to a server and run the three interfaces as separate processes.
8. Add more questions to the evaluation set, from a second reviewer.
9. Add automated tests and a continuous integration run.

## How AI tools were used

The author used Claude Code (an AI coding assistant) to plan the design, write the first version of the code and this document, and run the first extraction tests. The author chose the stack, reviewed the decisions, and is responsible for the result. AI tools did not write the evaluation answers without a check against the source PDF.

## Repository files

| File | Purpose |
|---|---|
| `ingest.py` | Reads a PDF, makes chunks, and fills the index |
| `ask.py` | Finds chunks and gets an answer. Also the CLI. |
| `server.py` | REST, gRPC, and web UI in one process |
| `rag.py` | Shared setup: model client, embeddings, Qdrant |
| `eval.py` and `eval.yaml` | The evaluation script and its 19 questions |
| `docintel.proto` | gRPC service definition |
| `docintel_pb2.py`, `docintel_pb2_grpc.py` | Generated gRPC code. Do not edit. |
| `static/index.html` | Web UI |
| `.env.example` | Template for the configuration |

Files that the system creates (excluded from Git): `.env`, `qdrant_data/`, `.cache/`, `chunks.jsonl`, `results.json`.

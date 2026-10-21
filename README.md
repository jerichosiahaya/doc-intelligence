# Document Intelligence

This project answers questions about company reports. Each answer shows the pages it comes from. The system refuses to answer when the reports do not contain the answer.

The sample document is the fictional "Kirana Logistics Group Annual Report 2025" (13 pages). The pipeline also works on other PDF files without code changes. You can ingest more than one document and ask questions across all of them.

## Contents

1. [Quick start](#quick-start)
2. [How the system works](#how-the-system-works)
3. [Interfaces](#interfaces)
4. [Configuration](#configuration)
5. [Logging and cost](#logging-and-cost)
6. [Evaluation](#evaluation)
7. [Tests and continuous integration](#tests-and-continuous-integration)
8. [Key decisions and trade-offs](#key-decisions-and-trade-offs)
9. [Known limitations](#known-limitations)
10. [Roadmap status](#roadmap-status)
11. [How AI tools were used](#how-ai-tools-were-used)
12. [Repository files](#repository-files)

## Quick start

### Requirements

- Python 3. The project was tested with Python 3.14 only.
- A LiteLLM proxy (or any OpenAI-compatible API) with these models:
  - a vision model that accepts images,
  - a text model for answers,
  - a text model for grading (can be the same model as the answer model).
- Internet access for the first run. The local embedding models download once.
- Docker, only if you want to run Qdrant as a server (optional).

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
   python ask.py "What was net profit in 2024?" --doc report_name   # one document only
   ```

### Ingest a new PDF

Run `ingest.py` again with the new file:

```bash
python ingest.py path/to/new_report.pdf
```

The system keeps each document under its file name (without `.pdf`). Ingesting the same file name again replaces the old data. Ingesting a different file name adds a document to the index.

Ingestion also copies the PDF to `docs/`. The web UI uses this copy to show cited pages.

## How the system works

### Data flow

```
PDF ──► ingest.py ──► chunks ──► embeddings ──► Qdrant
                                                  │
 question ──► embed ──► search (+ rerank) ◄───────┘
                              │
                              ▼
                  answer model (LiteLLM)
                              │
                              ▼
                 check the cited pages against
                    the pages that were retrieved
                              │
                              ▼
   { answer, citations, rejected_citations, refused, retrieved }
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
- **Table continuation.** If a table continues on the next page with the same header row, the system joins both parts into one chunk. The chunk stores every page it covers.
- **Vision chunks.** Each page has one chunk with the full vision transcript.

Every chunk stores these fields: `doc`, `page`, `pages`, `type`, `source`, `caption`, and `text`.

The system writes all chunks to `chunks.jsonl`. Read this file after each ingestion to find extraction errors.

The vision results are cached in `.cache/vision/`. The cache key is a hash of the model name and the page image. A second ingestion of the same page does not call the model again.

### Number checks during ingestion

After extraction, the system checks the numbers and writes each problem to `warnings.json` and to the terminal:

- **Totals.** For each table row that starts with "Total", the system adds the rows above it. A difference of more than 0.5 gives a warning.
- **Table against vision.** The system looks for each number of a native table in the vision transcript of the same pages. A missing number gives a warning. This check needs the vision pass.

A warning does not stop ingestion. It tells you where to look in `chunks.jsonl`.

### Retrieval

1. The system embeds the question with the local model `BAAI/bge-small-en-v1.5`.
2. Qdrant returns the `TOP_K` chunks with the highest cosine similarity. The default is 6.

Two options change this step. Both are off by default:

| Option | Setting | What it does |
|---|---|---|
| Hybrid search | `SEARCH=hybrid` | Searches with the dense vector and with BM25 keywords. Reciprocal rank fusion (RRF) joins the two result lists. |
| Reranker | `RERANK=1` | Fetches `RERANK_N` chunks (default 20). A local cross-encoder scores each chunk against the question. The system keeps the best `TOP_K`. |

Both options run on the local machine and need no API key. See [Evaluation](#evaluation) for the evidence about them.

### Answering

The answer model receives the retrieved chunks. Each chunk has a label, for example `[report p.4]` or `[report p.7-8]`. The system prompt gives these rules:

- Use only the supplied excerpts.
- Cite each fact with the label of its excerpt.
- If the excerpts do not contain the answer, set `refused` to true. Do not guess.
- If two excerpts disagree, report both and cite both.

The model returns JSON. If the JSON cannot be parsed, the system returns the raw text with `refused` set to false and no citations.

### Citation check

The system compares each cited page with the pages that it retrieved. A citation for a page that was not retrieved is moved from `citations` to `rejected_citations`. A bare page number is accepted only when all retrieved chunks come from one document.

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
python ask.py "..." --doc report_name                 # one document only
```

### REST

```bash
curl -s localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: your-key' \
  -d '{"question": "What was net profit in 2024?", "doc": "report_name"}'
```

The fields `doc` and the header `X-API-Key` are optional. See [Authentication](#authentication).

Request fields:

| Field | Type | Meaning |
|---|---|---|
| `question` | string | 1 to 2,000 characters |
| `doc` | string or null | Name of one ingested document. If null, the search covers all documents. |

Response fields:

| Field | Type | Meaning |
|---|---|---|
| `answer` | string | The answer text |
| `citations` | list of `{doc, page}` | Pages the model cited that were also retrieved |
| `rejected_citations` | list of `{doc, page}` | Pages the model cited that were not retrieved |
| `refused` | boolean | True when the reports do not support an answer |
| `retrieved` | list of `{doc, page}` | Pages of the chunks sent to the model |
| `seconds` | number | Time to answer |
| `tokens` | integer | Total tokens used by the answer call. Zero if the proxy does not report usage. |

Other routes:

| Route | Result |
|---|---|
| `GET /health` | `{"ok": true}`. No key needed. |
| `GET /documents` | List of ingested document names |
| `GET /page/{doc}/{page}.png` | Image of one page of an ingested document |
| `GET /` | The web UI. No key needed. |

Status codes:

| Code | Cause |
|---|---|
| 200 | Success |
| 401 | The API key is missing or wrong (only when `API_KEY` is set) |
| 404 | The document is unknown, or the page is outside the document |
| 422 | The question is empty or longer than 2,000 characters |
| 502 | The model proxy or the vector store failed |

### gRPC

The service is defined in `docintel.proto`. It has one method: `DocIntel.Ask(AskRequest) returns (AskReply)`. The messages have the same fields as the REST request and response. A `Source` message holds `doc` and `page`.

Status codes: `INVALID_ARGUMENT` for an empty question. `NOT_FOUND` for an unknown document. `UNAUTHENTICATED` for a missing or wrong key. `UNAVAILABLE` for a backend failure.

Example with `grpcurl`:

```bash
grpcurl -plaintext -proto docintel.proto \
  -H 'x-api-key: your-key' \
  -d '{"question": "What was net profit in 2024?"}' \
  localhost:50051 docintel.DocIntel/Ask
```

To regenerate the Python stubs after you change the proto file:

```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. docintel.proto
```

### Web UI

Open `http://localhost:8000/`. Type a question, optionally choose one document, and select **Ask**. The page shows:

- the answer,
- a "Not in the document" badge when the system refuses,
- one button for each cited page. Select a button to show or hide the image of that page,
- a note when the system removed a citation that it could not verify.

If the server needs a key, the page shows a key field. The page keeps the key in the browser session only. The page supports light and dark mode.

### Authentication

Set `API_KEY` in `.env` to turn on authentication. Use a comma-separated list for more than one key. REST clients send the header `X-API-Key`. gRPC clients send the metadata `x-api-key`.

The routes `/health` and `/` need no key. The page `/` contains no data.

If `HOST` is not a local address, `server.py` refuses to start without `API_KEY`.

### Run the interfaces as separate processes

By default, `python server.py` runs REST and gRPC in one process. To run them separately:

```bash
docker compose up -d                       # Qdrant server (set QDRANT_PORT if port 6333 is taken)
# set QDRANT_URL=http://localhost:6333 in .env, then ingest again:
python ingest.py path/to/report.pdf
python server.py rest                      # REST and web UI
python server.py grpc                      # gRPC only
```

With a Qdrant server, the CLI, `eval.py`, and `ingest.py` can also run while the servers run.

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
| `SEARCH` | No | `dense` | `dense` or `hybrid` |
| `RERANK` | No | off | Set to `1` to turn on the reranker |
| `RERANK_N` | No | 20 | Number of candidates that the reranker scores |
| `RERANK_MODEL` | No | `Xenova/ms-marco-MiniLM-L-12-v2` | Local cross-encoder. Other supported models include `BAAI/bge-reranker-base`. |
| `QDRANT_URL` | No | | URL of a Qdrant server. If not set, the system uses a local folder (`qdrant_data/`). |
| `QDRANT_PORT` | No | 6333 | Host port that `docker-compose.yml` uses |
| `API_KEY` | No | | One or more API keys, separated by commas. If not set, there is no authentication. |
| `HOST` | No | `127.0.0.1` | Bind address of `server.py` |
| `PORT` | No | 8000 | REST and web UI port |
| `GRPC_PORT` | No | 50051 | gRPC port |
| `PRICE_IN`, `PRICE_OUT` | No | | Answer model price, in USD per million tokens |
| `PRICE_VISION_IN`, `PRICE_VISION_OUT` | No | | Vision model price, in USD per million tokens |

> **Note:** If you change the Qdrant collection layout (for example, after you update from an older version), delete `qdrant_data/` and ingest again.

## Logging and cost

Each model call and each request adds one line to `logs/requests.jsonl`. The file is excluded from Git. A line contains these fields (not all fields appear in every line):

| Field | Meaning |
|---|---|
| `ts` | Unix time |
| `kind` | `ask` (answer call) or `vision` (page transcription during ingestion) |
| `via` | `cli`, `rest`, `grpc`, `eval`, or `ingest` |
| `status` | `ok`, `error`, or `unauthorized` |
| `question` | The first 300 characters of the question |
| `seconds`, `prompt_tokens`, `completion_tokens`, `tokens` | Time and token use |
| `refused`, `citations`, `rejected`, `retrieved` | Result of the answer |
| `error` | The error text, if the call failed |

> **Warning:** The log contains the text of the questions. Protect the `logs/` folder in the same way as the questions.

To see a summary, run:

```bash
python costs.py
```

The report shows, for each kind and source: the number of calls, the number of errors, the median and 95th percentile time, the tokens, and the cost. The cost needs the `PRICE_*` values in `.env`. Without them, the report shows tokens only.

## Evaluation

### Method

The file `eval.yaml` contains 19 questions that were written by hand from the sample report. Each question has an expected answer, the pages that support it, and (for answerable questions) the evidence strings that must appear in the retrieved text.

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
python eval.py                          # retrieval and answer quality
python eval.py --retrieval-only         # retrieval only; needs no model API
python eval.py --retrieval-only --doc report_name
```

The script measures two things separately:

- **Retrieval.** A question passes only if all supporting pages are in the retrieved pages **and** all evidence strings are in the retrieved text. A page-only test is too weak: every page has some chunk, so a page can be "retrieved" without its facts.
- **Answer quality.** The judge model compares the answer with the expected answer. For the questions that cannot be answered, a question passes only if the system refuses.

The script prints a table by content type and writes every result to `results.json`.

When you use `--doc`, make sure the evaluation questions apply to that document. When you ingest several documents, a run without `--doc` searches all of them.

### Preliminary retrieval comparison

This comparison used the report **without the vision pass** (18 chunks, no model API). It tests only questions that native text and tables can answer. The chart, tile, and scan questions cannot pass in this setup, by design.

| Configuration | Retrieval passes (of 17 answerable questions) | Prose | Table | Table with calculation |
|---|---|---|---|---|
| Dense search | 8 | 1 of 2 | 3 of 3 | 2 of 2 |
| Hybrid search | 9 | 2 of 2 | 3 of 3 | 2 of 2 |
| Dense search with reranker | 9 | 2 of 2 | 3 of 3 | 2 of 2 |
| Hybrid search with reranker | 9 | 2 of 2 | 3 of 3 | 2 of 2 |

Each option fixed the same one question (the cause of the fourth-quarter service dip, where the keyword "flood" matters). The evidence is **weak**: it is one question, on a small index with no vision data. For this reason, hybrid search and the reranker stay **off by default**. Repeat the comparison on the full index (with vision data) and turn an option on only if it shows a clear gain.

### Results

**The full evaluation (with vision and answer quality) has not been run yet.** Do not treat any number as a result until `python eval.py` has produced it. After the first run, record here:

- the retrieval and answer scores for each content type,
- every failed question, with the cause of the failure,
- the change that was made to fix the weakest part, and the scores before and after.

### Notes on the question set

- The expected answers for questions 4 and 5 are calculations. Check them again by hand.
- The judge is a language model. It can make mistakes. Read `results.json` for the failed questions.
- One person wrote all 19 questions. The set is small. A pass rate on this set does not prove the same rate on other reports. A second reviewer has not yet added questions.

## Tests and continuous integration

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest -q                             # 26 fast tests; no model download, no API
RUN_MODEL_TESTS=1 pytest -q           # also runs 4 tests with the real local models
```

| File | What it tests |
|---|---|
| `tests/test_checks.py` | Number parsing, total checks, table-against-vision check |
| `tests/test_ingest_helpers.py` | Markdown tables, captions, prose chunking |
| `tests/test_ask.py` | Citation check, source merging, page labels, JSON fallback, logging (with a fake model) |
| `tests/test_server.py` | REST routes, validation, error codes, authentication, page images, path-traversal attempt |
| `tests/test_retrieval.py` | Dense, hybrid, and reranked search, the document filter, and re-ingestion, with the real local models (opt-in) |

The workflow `.github/workflows/ci.yml` runs `pytest -q` on Python 3.12 and 3.14 for each push and pull request. The workflow has **not run yet**; it runs after the first push.

## Key decisions and trade-offs

| Decision | Reason | Cost |
|---|---|---|
| Run the vision pass on every page | The report has facts only in images (the tiles on page 3, the charts, the scan on page 13). One rule for all pages works on a new PDF. | One model call for each page. The cache reduces repeat cost. |
| Use PyMuPDF for tables as well as vision | Native table text is exact. It gives a second source to compare with the vision text. | Tables without ruled lines may not be found. |
| Keep each table in one chunk | A split table loses its header. Answers then use the wrong column. | Large tables make large chunks. |
| Join tables that continue on the next page | Table 2 (facilities) spans pages 7 and 8. | The rule needs the header row to repeat. |
| Keep hybrid search and the reranker off by default | The first test shows a small gain on one question only. | A possible quality gain is not used until the full test. |
| Use local models for embeddings, keyword search, and reranking | No second API key. Evaluation runs again at no cost. | The models are small (384 dimensions for embeddings). |
| Evaluate retrieval with evidence strings | A page-only check passes without the needed fact. | You must write evidence strings for each question. |
| Check citations against retrieved pages | The model can cite a page it never saw. | The check cannot tell if a retrieved page really supports the fact. |
| Use embedded Qdrant by default | Setup needs no server or container. | One process can open the index at a time. A compose file gives a Qdrant server. |
| Log every model call in one JSON-lines file | One source for latency, errors, and cost. | The file contains question text. |
| Use one static API key | Simple, and enough for an internal service. | No user identity, no key rotation, no rate limit, no TLS. |
| Set the model temperature to 0 | Answers are more repeatable for evaluation. | Not fully repeatable on all providers. |

## Known limitations

- **Vision errors.** The vision model can read a number from a chart incorrectly. The checks cover table totals and table numbers. They do **not** check chart values against totals (for example, that four quarters add up to the yearly revenue).
- **Citation check is partial.** The system rejects a page that was not retrieved. It cannot check that a retrieved page supports the cited fact.
- **One process for embedded Qdrant.** With embedded Qdrant, do not run `server.py`, `ask.py`, `ingest.py`, and `eval.py` at the same time. Use a Qdrant server (see [Run the interfaces as separate processes](#run-the-interfaces-as-separate-processes)).
- **Simple security.** Authentication is one shared key per deployment. The REST and gRPC interfaces have no encryption (TLS). Put a reverse proxy with TLS in front of them on any network that is not trusted.
- **Shared top-k across documents.** A question about several documents uses one top-k list. One document can fill the list and hide another. The answer can then miss a document.
- **Rules that need missing data.** The model must say what the report does not state. For example, the report gives only annual utilisation, but its watch-list rule uses quarterly values. Question 19 tests this case. The model can still fail it.
- **No conversation memory.** Each question is independent.
- **Cost is an estimate.** Cost comes from the token counts that the proxy reports and from the prices that you set. If the proxy does not report tokens, the report shows zero.
- **Few documents tested.** The pipeline was tested on one report. Other layouts (for example, scanned pages with no text layer, or tables without lines) are not tested.
- **Not yet verified end to end.** The vision pass, the answer step, and the judge were not run with a real model in this repository state. Only the code paths that need no model API were run.

## Roadmap status

| Item | Status |
|---|---|
| Hybrid search | Built. Off by default until the full-index evaluation. |
| Local reranker | Built. Off by default until the full-index evaluation. |
| Citation check | Done |
| Cross-check of numbers | Partly done: totals and table-against-vision. Chart-against-total is not built. |
| Cited page image in the web UI | Done |
| Document filter and multi-document questions | Done. Top-k is shared across documents. |
| Authentication, request logging, cost report | Done (one static key, no rate limit, no TLS) |
| Qdrant server and separate processes | Done (`docker-compose.yml`, `server.py rest\|grpc`) |
| More evaluation questions from a second reviewer | Not done. This needs a person. |
| Automated tests and continuous integration | Tests done. The CI workflow has not run yet. |

### Next steps

1. Run `python ingest.py` and `python eval.py` with the real models. Record the results above.
2. Repeat the retrieval comparison with the vision data. Keep hybrid search or the reranker only if the results improve.
3. Add a chart-against-total check.
4. Ask a second reviewer for more evaluation questions.
5. Add TLS (through a proxy), a rate limit, and a retention rule for `logs/`.

## How AI tools were used

The author used Claude Code (an AI coding assistant) to plan the design, write the first version of the code and this document, and run the first extraction tests. The author chose the stack, reviewed the decisions, and is responsible for the result. AI tools did not write the evaluation answers without a check against the source PDF.

## Repository files

| File | Purpose |
|---|---|
| `ingest.py` | Reads a PDF, makes chunks, runs number checks, and fills the index |
| `ask.py` | Finds chunks, gets an answer, and checks citations. Also the CLI. |
| `server.py` | REST, gRPC, and web UI (one process, or separate with `rest` or `grpc`) |
| `rag.py` | Shared setup: model client, embeddings, reranker, Qdrant |
| `checks.py` | Number checks (totals, table against vision) |
| `reqlog.py` | Writes `logs/requests.jsonl` |
| `costs.py` | Summary of time, tokens, and cost |
| `eval.py` and `eval.yaml` | The evaluation script and its 19 questions |
| `docintel.proto` | gRPC service definition |
| `docintel_pb2.py`, `docintel_pb2_grpc.py` | Generated gRPC code. Do not edit. |
| `static/index.html` | Web UI |
| `docker-compose.yml` | Qdrant server (optional) |
| `tests/`, `pytest.ini`, `requirements-dev.txt` | Automated tests |
| `.github/workflows/ci.yml` | Continuous integration |
| `.env.example` | Template for the configuration |

Files that the system creates (excluded from Git): `.env`, `qdrant_data/`, `docs/`, `.cache/`, `logs/`, `chunks.jsonl`, `warnings.json`, `results.json`.

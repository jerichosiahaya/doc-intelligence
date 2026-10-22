# Document Intelligence

This project answers questions about company reports. Each answer shows the pages it comes from. The system refuses to answer when the reports do not contain the answer.

The project has three services:

| Service | Folder | Technology | Role |
|---|---|---|---|
| Server | `server/` | Python, FastAPI, gRPC | Reads PDFs, searches, answers, keeps history |
| UI | `ui/` | React, TypeScript, Vite | Web app. It talks to the server only through the REST API. |
| MCP server | `mcp-server/` | Python, MCP SDK | Lets AI assistants (Claude, opencode, and others) use the server as tools. It talks to the server only through the REST API. |

The sample document is the fictional "Kirana Logistics Group Annual Report 2025" (13 pages). The pipeline also works on other PDF files without code changes. You can ingest more than one document and ask questions across all of them.

## Screenshots

**Web UI.** The page after start-up, with conversation history turned on and no question asked yet.

![Web UI](assets/ui.png)

**CLI.** Real output of `ingest.py` and `eval.py --retrieval-only`, rendered as an image. The run used no vision pass, so the chart, tile, and scan questions fail by design.

![CLI output](assets/cli.png)

> **Note:** These images show no answers, because answers need a model API. Take new screenshots of answers after you fill in `.env`.

## Contents

1. [Quick start](#quick-start)
2. [How the system works](#how-the-system-works)
3. [Interfaces](#interfaces)
4. [Conversation history](#conversation-history)
5. [MCP server](#mcp-server)
6. [Configuration](#configuration)
7. [Logging and cost](#logging-and-cost)
8. [Evaluation](#evaluation)
9. [Tests and continuous integration](#tests-and-continuous-integration)
10. [Key decisions and trade-offs](#key-decisions-and-trade-offs)
11. [Known limitations](#known-limitations)
12. [Roadmap status](#roadmap-status)
13. [How AI tools were used](#how-ai-tools-were-used)
14. [Repository files](#repository-files)

## Quick start

### Requirements

- Python 3 (for the server). The project was tested with Python 3.14 only.
- Node 22 or later (for the UI, if you do not use Docker).
- A LiteLLM proxy (or any OpenAI-compatible API) with these models:
  - a vision model that accepts images,
  - a text model for answers,
  - a text model for grading (can be the same model as the answer model).
- Internet access for the first run. The local embedding models download once.
- Docker, to run the whole stack with one command, or to run Qdrant and Postgres as servers (optional).

### Run with Docker (three commands)

This starts Qdrant, Postgres, the server, and the UI.

```bash
cp server/.env.example server/.env      # then edit server/.env (see Configuration)
API_KEY=choose-a-key docker compose up -d --build
docker compose run --rm -v "$PWD/report.pdf:/data/report.pdf:ro" server python ingest.py /data/report.pdf
```

Open `http://localhost:8080` in a browser. The page asks for the API key that you chose.

### Run without Docker

Run the Python commands in the `server/` folder.

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env (see Configuration)
python ingest.py path/to/report.pdf
python server.py                # REST on :8000, gRPC on :50051
```

In a second terminal, start the UI:

```bash
cd ui
npm install
npm run dev                     # http://localhost:5173
```

### Ask a question without the server

1. Run these commands in `server/`.
2. Stop `server.py` if it is running. See [Known limitations](#known-limitations) for the reason.
3. Run:

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

Ingestion also copies the PDF to `server/docs/`. The server uses this copy to show cited pages.

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

All four interfaces call the same function, `ask.answer()`. The CLI, REST, and gRPC interfaces belong to the server. The web UI is a separate service that uses the REST interface.

| Interface | Start | Address |
|---|---|---|
| CLI | `python ask.py "question"` (in `server/`) | Terminal |
| REST | `python server.py` (in `server/`) | `http://localhost:8000/ask` |
| gRPC | `python server.py` (in `server/`) | `localhost:50051` |
| Web UI | `npm run dev` (in `ui/`), or Docker | `http://localhost:5173` (development), `http://localhost:8080` (Docker) |

The server has no web page of its own. The route `/` does not exist.

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
| `conversation_id` | UUID or null | Continue a saved conversation. If null, the system starts a new one. Needs [history](#conversation-history). |

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
| `conversation_id` | string or null | The conversation that holds this turn. Null when history is off or the save failed. |

Other routes:

| Route | Result |
|---|---|
| `GET /health` | `{"ok": true, "history": <true or false>}`. No key needed. |
| `GET /documents` | List of ingested document names |
| `POST /documents` | Add a PDF (multipart field `file`). Status 202 and a job. A file with the name of an existing document replaces it. |
| `DELETE /documents/{name}` | Remove a document: its chunks and its PDF (status 204). Status 409 while it is being ingested. |
| `GET /jobs`, `GET /jobs/{id}` | Ingest jobs: `status` (`queued`, `running`, `done`, `error`), `stage`, `done` and `total` pages, `chunks`, `error` |
| `GET /stats` | `{"chunks": N, "documents": {name: N}}`: chunks in the vector database |
| `GET /models` | `{"default": ..., "models": [...]}`: models that the LiteLLM proxy offers |
| `POST /ask/stream` | Same input as `/ask`, plus an optional `model`. Server-sent events: `candidates` (the search result), `token` (pieces of the answer text), then `done` (the same body as `/ask`) or `error` |
| `GET /page/{doc}/{page}.png` | Image of one page of an ingested document |
| `GET /conversations` | The 50 most recent conversations. Needs history. |
| `GET /conversations/{id}` | One conversation with all its messages. Needs history. |
| `DELETE /conversations/{id}` | Delete a conversation and its messages (status 204). Needs history. |

Status codes:

| Code | Cause |
|---|---|
| 200 | Success |
| 400 | A `conversation_id` was sent, but history is off |
| 401 | The API key is missing or wrong (only when `API_KEY` is set) |
| 404 | The document, page, model, or conversation is unknown. Also for the history routes when history is off. |
| 400 | Also: an upload is not a PDF, cannot be opened, or has too many pages |
| 413 | An upload is larger than `MAX_UPLOAD_MB` |
| 422 | The question is empty or too long, or an id is not a UUID |
| 502 | The model proxy or the vector store failed |

### gRPC

The service is defined in `docintel.proto`. It has one method: `DocIntel.Ask(AskRequest) returns (AskReply)`. The messages have the same fields as the REST request and response. A `Source` message holds `doc` and `page`. `conversation_id` is a string (empty means none).

Status codes: `INVALID_ARGUMENT` for an empty question or an id that is not a UUID. `NOT_FOUND` for an unknown document or conversation. `FAILED_PRECONDITION` for a conversation id when history is off. `UNAUTHENTICATED` for a missing or wrong key. `UNAVAILABLE` for a backend failure.

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

The UI is a separate service in `ui/`. It is a React and TypeScript app that Vite builds. It uses no UI library and no state library.

Open `http://localhost:5173` (development) or `http://localhost:8080` (Docker). The page has a navigation column on the left and one large question box. Type a question and press Enter (Shift+Enter starts a new line). In the box you can:

- choose one document, or all documents,
- choose the answer **model** from the list that the LiteLLM proxy offers (`GET /models`). The default is `ANSWER_MODEL`,
- select the paperclip to **add a PDF**.

An answer appears in this order:

- **Sources**: one card for each chunk that was sent to the model, numbered, with its page. A card with an accent border is cited by the answer. Select a card to show the image of its page,
- **Answer**: the text **streams** word by word. A number in the text, for example `[report p.4]`, becomes a chip that opens its source card. A "Not in the document" badge shows when the system refuses,
- **Retrieval details**: how many candidates the search found, how many went to the model, and the relevancy score of each one,
- a note when the system removed a citation that it could not verify.

Select **Documents** in the navigation column to see the documents, the chunk count of each, and the total in the vector database. In the same dialog you can drop or choose a PDF. The page shows the progress ("Reading page 4 of 13"). A vision model reads every page, so a 13-page report takes about two minutes. Uploads run one at a time. **Remove** deletes a document (it asks twice).

When history is on, the navigation column also lists the saved conversations. Select one to reopen it (the candidate list is not saved, so an old answer shows only its pages). Select **New thread** to start another. **Delete** asks you to select it twice.

If the server needs a key, the page shows a key field. The page keeps the key in the browser session only. The page supports light and dark mode.

#### How the UI reaches the server

The browser always calls `/api/...` on the host that served the page. Something then forwards the call to the server and removes the `/api` prefix:

| Where | What forwards the call | Setting |
|---|---|---|
| `npm run dev` | The Vite dev server | `API_PROXY` (default `http://localhost:8000`) |
| Docker | nginx in the UI container | `API_UPSTREAM` (default `http://server:8000`), `NGINX_RESOLVER` (default `127.0.0.11`) |
| Static hosting, no proxy | Nothing. The page calls the server directly. | Build with `VITE_API_URL=https://api.example.com`, and set `CORS_ORIGINS` on the server |

Because the browser sees one host, the Docker setup needs no CORS setting.

#### UI source

| File | Role |
|---|---|
| `ui/src/api.ts` | The only code that calls the server. Adds the API key header, turns errors into `ApiError`. |
| `ui/src/App.tsx` | State of the page: conversations, current thread, errors, key prompt |
| `ui/src/components/` | `Composer` (question box, document and model choice), `AnswerCard` (sources, streaming text, candidates), `Sidebar`, `DocumentsPanel` (upload and remove), `KeyBar` |
| `ui/src/useUploads.ts` | Uploads a PDF and follows its ingest job |
| `ui/src/*.test.ts(x)` | 33 tests with Vitest and Testing Library, against a fake of the server API |
| `ui/Dockerfile`, `ui/nginx.conf.template` | Production image: Vite build, served by nginx, `/api` forwarded to the server |

Commands, in `ui/`: `npm run dev`, `npm run typecheck`, `npm test`, `npm run build`.

### Authentication

Set `API_KEY` in `.env` to turn on authentication. Use a comma-separated list for more than one key. REST clients send the header `X-API-Key`. gRPC clients send the metadata `x-api-key`.

The route `/health` needs no key. It returns no data about the documents.

If `HOST` is not a local address, `server.py` refuses to start without `API_KEY`.

### Run the interfaces as separate processes

By default, `python server.py` runs REST and gRPC in one process. To run them separately (in `server/`):

```bash
docker compose up -d qdrant                # Qdrant server (set QDRANT_PORT if port 6333 is taken)
# set QDRANT_URL=http://localhost:6333 in server/.env, then ingest again:
python ingest.py path/to/report.pdf
python server.py rest                      # REST only
python server.py grpc                      # gRPC only
```

With a Qdrant server, the CLI, `eval.py`, and `ingest.py` can also run while the servers run.

## Conversation history

When you set `DATABASE_URL`, the server saves every question and answer in Postgres. The web UI shows the saved conversations and lets you reopen or delete them. Without `DATABASE_URL`, history is off and everything else works as before.

### Turn it on

In the Docker stack, history is already on. For a local server (in `server/`):

```bash
docker compose up -d postgres        # set POSTGRES_PORT if port 5432 is taken
# add to server/.env:
# DATABASE_URL=postgresql://docintel:docintel@localhost:5432/docintel
python server.py
```

The server creates the tables when it starts. If `DATABASE_URL` is set but the database cannot be reached, the server stops at start-up with an error.

### What is saved

| Table | Contents |
|---|---|
| `conversations` | `id` (UUID), `title` (the first 60 characters of the first question), `created_at`, `updated_at` |
| `messages` | `conversation_id`, `role` (`user` or `assistant`), `content`, `doc`, and for answers: `citations`, `rejected_citations`, `retrieved` (JSON), `refused`, `seconds`, `tokens` |

Deleting a conversation deletes its messages.

### Rules

- A request without `conversation_id` starts a new conversation. A request with one adds to it.
- The server checks the conversation **before** it calls the model. An unknown id does not cost a model call.
- If the save fails after the model answered, the server still returns the answer. The failure goes to `logs/requests.jsonl` (kind `history`). The reply then has `conversation_id` set to null, or to the id that the client sent.
- REST and gRPC share the same history.
- The CLI (`ask.py`) does not save history.
- The system stores the conversation, but it does **not** use it as context. See [Known limitations](#known-limitations).

> **Note:** The stored messages contain the text of questions and answers. Protect the database in the same way as the questions.

## MCP server

The folder `mcp-server/` holds an MCP (Model Context Protocol) server. It lets an AI assistant ask questions about your documents, and look at the cited pages, as tools.

The MCP server does not read PDFs and does not use the index. Each tool calls the REST API of the server service. **The server service must be running.**

### Tools

| Tool | What it does | Changes data |
|---|---|---|
| `ask_documents(question, doc?, conversation_id?)` | Answers a question and returns the cited pages. `refused` is true when the documents do not contain the answer. | Saves the turn in history, if history is on |
| `list_documents()` | Lists the document names | No |
| `get_page_image(doc, page)` | Returns one page as a PNG image | No |
| `list_conversations(limit?)` | Lists saved conversations, newest first. Needs history. | No |
| `get_conversation(conversation_id)` | Returns one saved conversation. Needs history. | No |

The server also gives the assistant these rules: say so when `refused` is true, and write every question in full, because the history is not used as context.

When a call fails, the tool returns an error that says what to do. For example: "Cannot reach the Document Intelligence server at ... Is it running? Set DOCINTEL_URL." or "The server rejected the API key. Set DOCINTEL_API_KEY ...".

### Install

```bash
cd mcp-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then start the server service (see [Quick start](#quick-start)).

### Settings

The AI client passes these to the MCP server as environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DOCINTEL_URL` | `http://localhost:8000` | Base URL of the REST API. Through the UI container, use `http://localhost:8080/api`. |
| `DOCINTEL_API_KEY` | | The server's `API_KEY`, if the server has one |
| `DOCINTEL_TIMEOUT` | 180 | Seconds to wait for a reply. An answer calls a language model. |

### Connect a client

Use absolute paths in every client. The command is always the Python of the `mcp-server/.venv` folder, with `server.py` as its argument.

**Claude Code**

```bash
claude mcp add docintel \
  -e DOCINTEL_URL=http://localhost:8000 -e DOCINTEL_API_KEY=your-key \
  -- /absolute/path/to/mcp-server/.venv/bin/python /absolute/path/to/mcp-server/server.py
claude mcp list
```

The tools then have the names `mcp__docintel__ask_documents`, `mcp__docintel__list_documents`, and so on. Tested: Claude Code loaded the server from a configuration file, called `list_documents` and `get_page_image`, and read the heading from the returned page image.

**Claude Desktop** uses the same JSON format in `claude_desktop_config.json`. Not tested.

```json
{
  "mcpServers": {
    "docintel": {
      "command": "/absolute/path/to/mcp-server/.venv/bin/python",
      "args": ["/absolute/path/to/mcp-server/server.py"],
      "env": { "DOCINTEL_URL": "http://localhost:8000", "DOCINTEL_API_KEY": "your-key" }
    }
  }
}
```

**opencode.** Add this to `opencode.json`. The format comes from the opencode documentation. Not tested, because opencode was not installed on the test machine. The format can differ between opencode versions, so check the documentation for yours.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "docintel": {
      "type": "local",
      "command": ["/absolute/path/to/mcp-server/.venv/bin/python", "/absolute/path/to/mcp-server/server.py"],
      "environment": { "DOCINTEL_URL": "http://localhost:8000", "DOCINTEL_API_KEY": "your-key" },
      "enabled": true
    }
  }
}
```

**pi.** pi has no built-in MCP support. Install an MCP adapter extension for pi, and register the same command and environment as above. Not tested with a published adapter. If you write your own bridge, it must:

1. send `initialize`, wait for the reply, and then send the notification `notifications/initialized`, before any other request;
2. run a tool with the method `tools/call` and the parameters `{name, arguments}`, not with the tool name as the method;
3. match each reply to its request by `id` (and fail the waiting requests when the server exits);
4. when it turns a tool's JSON schema into pi parameters, make an argument optional unless the schema lists it in `required`, and read "string or null" (`anyOf` with a `null` branch) as an optional string. Otherwise the assistant must fill in every argument of `ask_documents` on every call.

A bridge that skips these steps cannot use any standard MCP server, including this one. The tool `get_page_image` returns a large image (about 90 KB as text). If your bridge cannot pass images to the model as images, leave this tool out.

The file `mcp-server/pi/docintel.ts` is an example pi extension for a bridge of this kind. Copy it to `~/.pi/agent/extensions/`, and change the two paths in it. It leaves out `get_page_image`. It was tested with a bridge that does the four things above, in pi's `--mode rpc`, which registered the four tools, and with a direct call of each tool's `execute` function. It was **not** tested with a real model call, because the model key of the test machine was invalid.

### Security

- The AI client starts the MCP server on your machine. The API key is in the client's configuration file. Anyone who can read that file can read the key.
- The questions, the answers, and the page images pass to the AI model of your client. Do not connect a client to confidential reports unless you accept that.

### Limits

- The MCP server uses stdio only. It runs on the same machine as the client.
- `ask_documents` writes to history, so a client can fill the history with test questions.
- The assistant decides when to call a tool. The system does not check that the assistant reports `refused` honestly.

## Configuration

### Server

Set these values in `server/.env`. Do not commit this file. The file `.gitignore` excludes it. In the Docker stack, the server container reads the same file.

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
| `QDRANT_URL` | No | | URL of a Qdrant server. If not set, the system uses a local folder (`server/qdrant_data/`). |
| `QDRANT_PORT` | No | 6333 | Host port that `docker-compose.yml` uses |
| `API_KEY` | No | | One or more API keys, separated by commas. If not set, there is no authentication. |
| `DATABASE_URL` | No | | Postgres connection string. If set, history is on. |
| `POSTGRES_PORT`, `POSTGRES_PASSWORD` | No | 5432, `docintel` | Host port and password that `docker-compose.yml` uses. Change the password on any shared host. |
| `HOST` | No | `127.0.0.1` | Bind address of `server.py` |
| `PORT` | No | 8000 | REST port |
| `GRPC_PORT` | No | 50051 | gRPC port |
| `MAX_UPLOAD_MB` | No | 30 | Largest PDF that `POST /documents` accepts |
| `MAX_PAGES` | No | 100 | Most pages in an uploaded PDF. Each page is one vision call. |
| `CORS_ORIGINS` | No | | Origins (separated by commas) that may call the REST API from a browser. Needed only when the UI is on another host without a proxy. |
| `PRICE_IN`, `PRICE_OUT` | No | | Answer model price, in USD per million tokens |
| `PRICE_VISION_IN`, `PRICE_VISION_OUT` | No | | Vision model price, in USD per million tokens |

> **Note:** If you change the Qdrant collection layout (for example, after you update from an older version), delete `server/qdrant_data/` and ingest again.

### Docker Compose

Set these in your shell or in a `.env` file next to `docker-compose.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `API_KEY` | | Required for the `server` service. The server refuses to listen on a non-local address without it. |
| `UI_PORT` | 8080 | Host port of the UI |
| `SERVER_PORT`, `GRPC_PORT` | 8000, 50051 | Host ports of the server |
| `QDRANT_PORT` | 6333 | Host port of Qdrant. Change it if another service uses 6333. |
| `POSTGRES_PORT`, `POSTGRES_PASSWORD` | 5432, `docintel` | Host port and password of Postgres. Change the password on any shared host. |

### UI

| Variable | When | Meaning |
|---|---|---|
| `API_PROXY` | `npm run dev` | Where Vite forwards `/api`. Default `http://localhost:8000`. |
| `VITE_API_URL` | Build time | Call the server directly instead of through `/api`. Needs `CORS_ORIGINS` on the server. |
| `API_UPSTREAM`, `NGINX_RESOLVER` | UI container start | Where nginx forwards `/api`, and which DNS server it uses to find it |

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

Run the server tests (in `server/`):

```bash
pip install -r requirements-dev.txt
pytest -q                             # 53 fast tests; no model download, no API, no database
RUN_MODEL_TESTS=1 pytest -q           # also runs 4 tests with the real local models
DATABASE_URL=postgresql://docintel:docintel@localhost:5432/docintel pytest -q   # also runs 8 history tests
```

Run the UI tests (in `ui/`):

```bash
npm test                              # 33 tests
npm run typecheck
```

Run the MCP server tests (in `mcp-server/`):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q                             # 23 tests
```

| File | What it tests |
|---|---|
| `tests/test_checks.py` | Number parsing, total checks, table-against-vision check |
| `tests/test_ingest_helpers.py` | Markdown tables, captions, prose chunking |
| `tests/test_ask.py` | Citation check, source merging, page labels, JSON fallback, logging (with a fake model) |
| `tests/test_server.py` | REST routes, validation, error codes, authentication, CORS, page images, path-traversal attempt |
| `tests/test_history.py` | The history tables against a real Postgres: titles, ordering, follow-ups, deletion (opt-in) |
| `tests/test_server_history.py` | History in the REST routes: history off, a failed save, the full flow with Postgres |
| `tests/test_retrieval.py` | Dense, hybrid, and reranked search, the document filter, and re-ingestion, with the real local models (opt-in) |
| `mcp-server/tests/test_tools.py` | Each tool against a mock network: request format, key header, URL encoding, error messages |
| `mcp-server/tests/test_protocol.py` | The real server over stdio, called with the MCP SDK client: handshake, tools, annotations, structured results, images, errors |
| `ui/src/api.test.ts` | Key header, error handling, request format |
| `ui/src/components/AnswerCard.test.tsx` | Refusal badge, citation buttons, page image, load errors |
| `ui/src/App.test.tsx` | Asking, errors, follow-ups in one conversation, history list, reopen, two-step delete, new chat, key prompt |

The UI tests run against an in-memory fake of the server API. They do not test the real server.

The workflow `.github/workflows/ci.yml` has three jobs for each push and pull request (`server`, `ui`, and `mcp-server`). The `server` job runs `pytest -q` on Python 3.12 and 3.14 with a Postgres service, so the history tests run. The `ui` job runs the type-check, the tests, and the build on Node 22. The `server` and `ui` jobs passed on the last push. The `mcp-server` job has **not run yet**.

## Key decisions and trade-offs

| Decision | Reason | Cost |
|---|---|---|
| Run the vision pass on every page | The report has facts only in images (the tiles on page 3, the charts, the scan on page 13). One rule for all pages works on a new PDF. | One model call for each page. The cache reduces repeat cost. |
| Use PyMuPDF for tables as well as vision | Native table text is exact. It gives a second source to compare with the vision text. | Tables without ruled lines may not be found. |
| Keep each table in one chunk | A split table loses its header. Answers then use the wrong column. | Large tables make large chunks. |
| Join tables that continue on the next page | Table 2 (facilities) spans pages 7 and 8. | The rule needs the header row to repeat. |
| Keep hybrid search and the reranker off by default | The first test shows a small gain on one question only. | A possible quality gain is not used until the full test. |
| Make the UI a separate service (React, TypeScript, Vite) that uses only the REST API | The UI and the server can change, deploy, and scale on their own. The REST API is the contract between them. | Two images and two build steps. The UI cannot call the CLI or gRPC. |
| Forward `/api` through the UI's own web server (nginx or Vite) | The browser sees one host, so no CORS and no cross-site rules. | The UI image must know where the server is (`API_UPSTREAM`). |
| Make the MCP server a thin client of the REST API | It never touches the index (embedded Qdrant allows one process). It works with a local or a Docker server. The REST API stays the only contract. | The server service must be running. One more hop for each call. |
| Use no UI library and no state library | The app is small. Fewer dependencies to keep up to date. | More hand-written CSS. Revisit if the UI grows. |
| Use local models for embeddings, keyword search, and reranking | No second API key. Evaluation runs again at no cost. | The models are small (384 dimensions for embeddings). |
| Evaluate retrieval with evidence strings | A page-only check passes without the needed fact. | You must write evidence strings for each question. |
| Check citations against retrieved pages | The model can cite a page it never saw. | The check cannot tell if a retrieved page really supports the fact. |
| Use embedded Qdrant by default | Setup needs no server or container. | One process can open the index at a time. A compose file gives a Qdrant server. |
| Log every model call in one JSON-lines file | One source for latency, errors, and cost. | The file contains question text. |
| Store history in Postgres, one connection for each call | Postgres is the standard choice for this data. A connection for each call is the simplest code. | Slower under high load. Add a connection pool then. |
| Do not fail a request when the history save fails | The model call already cost time and money. | A conversation can have a missing turn. The log shows it. |
| Use one static API key | Simple, and enough for an internal service. | No user identity, no key rotation, no rate limit, no TLS. |
| Set the model temperature to 0 | Answers are more repeatable for evaluation. | Not fully repeatable on all providers. |

## Known limitations

- **Vision errors.** The vision model can read a number from a chart incorrectly. The checks cover table totals and table numbers. They do **not** check chart values against totals (for example, that four quarters add up to the yearly revenue).
- **Citation check is partial.** The system rejects a page that was not retrieved. It cannot check that a retrieved page supports the cited fact.
- **One process for embedded Qdrant.** With embedded Qdrant, do not run `server.py`, `ask.py`, `ingest.py`, and `eval.py` at the same time. Use a Qdrant server (see [Run the interfaces as separate processes](#run-the-interfaces-as-separate-processes)).
- **Simple security.** Authentication is one shared key per deployment. The REST and gRPC interfaces have no encryption (TLS). Put a reverse proxy with TLS in front of them on any network that is not trusted.
- **Shared top-k across documents.** A question about several documents uses one top-k list. One document can fill the list and hide another. The answer can then miss a document.
- **Rules that need missing data.** The model must say what the report does not state. For example, the report gives only annual utilisation, but its watch-list rule uses quarterly values. Question 19 tests this case. The model can still fail it.
- **History is stored but not used.** The model does not see earlier turns. A follow-up such as "And in 2023?" does not know the earlier question. A query rewrite that uses the history is not built.
- **No users.** Every client with a valid key sees all conversations. There is no per-user separation.
- **History is a server feature.** The CLI does not save history.
- **UI security.** The UI keeps the API key in the browser session. The nginx container has no TLS. Put a TLS proxy in front of it on any network that is not trusted.
- **UI tests use a fake server.** A change to the REST API that the fake does not copy can pass the UI tests and still break the page.
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
| MCP server for AI assistants | Done: `mcp-server/`, 5 tools, 23 tests. Tested with the MCP SDK client and Claude Code. pi: tools register and run through a fixed bridge, but no model call was made. Not tested with opencode. |
| UI as a separate React service | Done: `ui/`, with its own tests, image, and CI job |
| Conversation history in Postgres | Done: save, list, reopen, delete. History is not used as model context. |
| Qdrant server and separate processes | Done (`docker-compose.yml`, `server.py rest\|grpc`) |
| More evaluation questions from a second reviewer | Not done. This needs a person. |
| Automated tests and continuous integration | Tests done. The CI workflow has not run yet. |

### Next steps

1. Run `python ingest.py` and `python eval.py` with the real models. Record the results above.
2. Repeat the retrieval comparison with the vision data. Keep hybrid search or the reranker only if the results improve.
3. Add a chart-against-total check.
4. Ask a second reviewer for more evaluation questions.
5. Use the history as context: rewrite a follow-up question into a full question before the search.
6. Add users (so that each person sees only their own conversations), TLS (through a proxy), a rate limit, and a retention rule for `logs/` and for the history.

## How AI tools were used

The author used Claude Code (an AI coding assistant) to plan the design, write the first version of the code and this document, and run the first extraction tests. The author chose the stack, reviewed the decisions, and is responsible for the result. AI tools did not write the evaluation answers without a check against the source PDF.

## Repository files

```
server/                      The server service (Python)
  ingest.py                  Reads a PDF, makes chunks, runs number checks, and fills the index
  ask.py                     Finds chunks, gets an answer, checks citations. Also the CLI.
  server.py                  REST and gRPC (one process, or separate with `rest` or `grpc`)
  rag.py                     Shared setup: model client, embeddings, reranker, Qdrant
  checks.py                  Number checks (totals, table against vision)
  history.py                 Conversation history in Postgres
  reqlog.py, costs.py        Request log, and the summary of time, tokens, and cost
  eval.py, eval.yaml         The evaluation script and its 19 questions
  docintel.proto             gRPC service definition
  docintel_pb2*.py           Generated gRPC code. Do not edit.
  tests/                     Server tests
  Dockerfile, .env.example   Image and configuration template
mcp-server/                  The MCP service (Python, MCP SDK)
  server.py                  The five tools. Calls the REST API.
  tests/                     Tool tests and a protocol test over stdio
  pi/docintel.ts             Example pi extension (needs an MCP bridge for pi)
ui/                          The UI service (React and TypeScript)
  src/                       App, components, API client, tests
  Dockerfile                 Vite build, served by nginx
  nginx.conf.template        Forwards /api to the server
docker-compose.yml           Qdrant, Postgres, server, and UI
assets/                      Screenshots for this README
.github/workflows/ci.yml     Continuous integration (server job and UI job)
```

Files that the server creates (excluded from Git), in `server/`: `.env`, `qdrant_data/`, `docs/`, `.cache/`, `logs/`, `chunks.jsonl`, `warnings.json`, `results.json`.

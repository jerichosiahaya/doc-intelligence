"""Usage: python server.py [both|rest|grpc]

The API service. The web UI is a separate service (../ui) that calls this REST API.
both (default): REST on :8000 and gRPC on :50051 in one process.
rest / grpc: one interface per process. Embedded Qdrant locks its folder to one
process, so run separate processes only with QDRANT_URL set (see docker-compose.yml).
"""
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import grpc
import pymupdf
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import history
import docintel_pb2 as pb
import docintel_pb2_grpc as pb_grpc
import ingest
from ask import answer, answer_stream
from rag import COLLECTION, llm, models, qdrant
from reqlog import log

DOCS = Path("docs")  # ingest.py keeps a copy of each PDF here
API_KEYS = [k.strip() for k in os.environ.get("API_KEY", "").split(",") if k.strip()]  # empty = no auth (local use)
MAX_UPLOAD = int(float(os.environ.get("MAX_UPLOAD_MB", 30)) * 1024 * 1024)
MAX_PAGES = int(os.environ.get("MAX_PAGES", 100))  # each page costs one vision call
LOCAL = ("127.0.0.1", "localhost", "::1")


def key_ok(key):
    return not API_KEYS or any(secrets.compare_digest((key or "").encode(), k.encode()) for k in API_KEYS)


def require_key(x_api_key: str | None = Header(None)):
    if not key_ok(x_api_key):
        log(kind="ask", via="rest", status="unauthorized")
        raise HTTPException(401, "invalid or missing API key")


class KeyInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, details):
        if key_ok(dict(details.invocation_metadata).get("x-api-key")):
            return continuation(details)

        def deny(request, context):
            log(kind="ask", via="grpc", status="unauthorized")
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid or missing API key")

        return grpc.unary_unary_rpc_method_handler(deny)


class UnknownDoc(Exception):
    pass


class HistoryOff(Exception):
    pass


class UnknownModel(Exception):
    pass


class Source(BaseModel):
    doc: str
    page: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc: str | None = None
    model: str | None = None  # a model from GET /models; omit for the server default (ANSWER_MODEL)
    conversation_id: uuid.UUID | None = None  # continue a saved conversation; omit to start a new one


class Candidate(BaseModel):
    doc: str
    pages: list[int]
    type: str
    caption: str
    score: float
    used: bool  # sent to the model (the top K); the rest were fetched but cut
    snippet: str = ""  # the start of the chunk text, to tell sources apart


class AskReply(BaseModel):
    answer: str
    citations: list[Source]
    rejected_citations: list[Source]
    refused: bool
    retrieved: list[Source]
    seconds: float
    tokens: int
    conversation_id: str | None = None  # set when history is on and the turn was saved
    candidates: list[Candidate] = []  # REST only: every chunk the search fetched, with its relevancy score
    score_kind: str = ""  # cosine, rrf or rerank: what the scores mean


def doc_names():
    return sorted(p.stem for p in DOCS.glob("*.pdf"))


_models = (0.0, [])  # (fetched at, ids)


def model_ids() -> list[str]:
    """Models that the answer endpoint offers (or ANSWER_MODELS). Cached for 5 minutes."""
    global _models
    if time.time() - _models[0] > 300:
        fixed = [m.strip() for m in os.environ.get("ANSWER_MODELS", "").split(",") if m.strip()]
        try:
            ids = fixed or sorted(m.id for m in llm("answer").models.list())
        except Exception:  # not every provider has a model list: offer the default model only
            ids = [os.environ["ANSWER_MODEL"]] if os.environ.get("ANSWER_MODEL") else []
        _models = (time.time(), ids)
    return _models[1]


def precheck(doc, conversation_id, model):
    """Fail before the model call, not after."""
    if doc and doc not in doc_names():
        raise UnknownDoc(doc)
    if conversation_id and not history.enabled():
        raise HistoryOff()
    if conversation_id and not history.exists(conversation_id):
        raise history.UnknownConversation(str(conversation_id))
    if model and model not in model_ids():
        raise UnknownModel(model)


def build_reply(a, question, doc, via, conversation_id) -> AskReply:
    reply = AskReply(
        answer=str(a["answer"]),
        citations=a["citations"],
        rejected_citations=a["rejected_citations"],
        refused=bool(a["refused"]),
        retrieved=a["retrieved_sources"],
        seconds=a["seconds"],
        tokens=a["tokens"] or 0,
        conversation_id=str(conversation_id) if conversation_id else None,
        candidates=a.get("candidates", []),
        score_kind=a.get("score_kind", ""),
    )
    if history.enabled():
        try:  # a failed save must not lose an answer that already cost a model call
            reply.conversation_id = str(history.save_turn(question, reply.model_dump(), doc, conversation_id))
        except Exception as e:
            log(kind="history", via=via, status="error", error=str(e)[:300])
    return reply


def ask(question: str, doc: str | None = None, via: str = "rest", conversation_id: uuid.UUID | None = None,
        model: str | None = None) -> AskReply:
    precheck(doc, conversation_id, model)
    a = answer(question, doc, via=via, model=model)
    return build_reply(a, question, doc, via, conversation_id)


class DocIntel(pb_grpc.DocIntelServicer):
    def Ask(self, request, context):
        if not request.question.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "question is empty")
        try:
            cid = history.parse_id(request.conversation_id) if request.conversation_id else None
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "conversation_id is not a UUID")
        try:
            r = ask(request.question, request.doc or None, via="grpc", conversation_id=cid)
        except UnknownDoc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown document: {request.doc}")
        except history.UnknownConversation:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown conversation: {request.conversation_id}")
        except HistoryOff:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "history is not enabled (set DATABASE_URL)")
        except Exception as e:  # LLM proxy or vector store failure
            context.abort(grpc.StatusCode.UNAVAILABLE, f"backend error: {e}")
        return pb.AskReply(**{**r.model_dump(exclude={"candidates", "score_kind"}), "conversation_id": r.conversation_id or ""})


MODE = "both"


def grpc_server():
    g = grpc.server(ThreadPoolExecutor(max_workers=4), interceptors=[KeyInterceptor()])
    pb_grpc.add_DocIntelServicer_to_server(DocIntel(), g)
    g.add_insecure_port(f"{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('GRPC_PORT', 50051)}")
    return g


@asynccontextmanager
async def lifespan(_):
    if history.enabled():
        history.init()  # fail at startup, not on the first request, if the database is wrong
    g = grpc_server() if MODE == "both" else None
    if g:
        g.start()
    yield
    if g:
        g.stop(grace=2)


app = FastAPI(title="Document Intelligence", lifespan=lifespan)


def apply_cors(application, origins):
    """Only needed when the UI is served from another origin. Behind the UI's own proxy, leave it off."""
    if origins:
        application.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
                                   allow_headers=["Content-Type", "X-API-Key"])


apply_cors(app, [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()])


@app.get("/health")
def health():
    return {"ok": True, "history": history.enabled()}


@app.get("/documents", dependencies=[Depends(require_key)])
def documents():
    return doc_names()


@app.get("/stats", dependencies=[Depends(require_key)])
def stats():
    """Chunks in the vector database, in total and per document."""
    def count(flt=None):
        return qdrant().count(COLLECTION, count_filter=flt, exact=True).count

    per_doc = {d: count(models.Filter(must=[models.FieldCondition(key="doc", match=models.MatchValue(value=d))])) for d in doc_names()}
    return {"chunks": count(), "documents": per_doc}


@app.get("/models", dependencies=[Depends(require_key)])
def models_list():
    """Models that the answer endpoint offers, and the one used when a request names none."""
    try:
        return {"default": os.environ.get("ANSWER_MODEL"), "models": model_ids()}
    except Exception as e:
        raise HTTPException(502, f"backend error: {e}")


def http_error(e: Exception) -> HTTPException:
    if isinstance(e, UnknownDoc):
        return HTTPException(404, f"unknown document: {e}")
    if isinstance(e, UnknownModel):
        return HTTPException(404, f"unknown model: {e}")
    if isinstance(e, history.UnknownConversation):
        return HTTPException(404, f"unknown conversation: {e}")
    if isinstance(e, HistoryOff):
        return HTTPException(400, "history is not enabled (set DATABASE_URL)")
    return HTTPException(502, f"backend error: {e}")


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/ask/stream", dependencies=[Depends(require_key)])
def post_ask_stream(req: AskRequest):
    """Server-sent events: candidates (the search result), token (answer text, in pieces), done (the
    same body as POST /ask), or error. Problems that exist before the model call return an HTTP error."""
    try:
        precheck(req.doc, req.conversation_id, req.model)
    except Exception as e:
        raise http_error(e)

    def events():
        try:
            for kind, data in answer_stream(req.question, req.doc, via="rest", model=req.model):
                if kind == "token":
                    data = {"text": data}
                elif kind == "done":
                    data = build_reply(data, req.question, req.doc, "rest", req.conversation_id).model_dump()
                yield sse(kind, data)
        except Exception as e:
            yield sse("error", {"message": f"backend error: {e}"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/ask", response_model=AskReply, dependencies=[Depends(require_key)])
def post_ask(req: AskRequest):
    try:
        return ask(req.question, req.doc, conversation_id=req.conversation_id, model=req.model)
    except Exception as e:
        raise http_error(e)


# Uploads. One worker, so ingests run one at a time (they share the vector store and the model quota).
_ingest_pool = ThreadPoolExecutor(max_workers=1)
JOBS: dict[str, dict] = {}


def safe_name(filename: str) -> str:
    """A document name that is safe as a file name and inside a citation like [name p.4]."""
    stem = re.sub(r"[^\w.-]+", "_", Path(filename or "").stem).strip("._")[:80]
    if not stem:
        raise HTTPException(400, "the file needs a name")
    return stem


def run_ingest(job: dict, pdf: Path, existed: bool):
    def progress(stage, done, total):
        job.update(stage=stage, done=done, total=total)

    job["status"] = "running"
    try:
        job["chunks"] = ingest.main(str(pdf), progress=progress, release=False)
        job["status"] = "done"
    except Exception as e:
        job.update(status="error", error=str(e)[:300])
        if not existed:  # do not leave a document that has no chunks
            (DOCS / pdf.name).unlink(missing_ok=True)
        log(kind="ingest", via="rest", status="error", error=str(e)[:300])
    finally:
        shutil.rmtree(pdf.parent, ignore_errors=True)


@app.post("/documents", status_code=202, dependencies=[Depends(require_key)])
async def upload_document(file: UploadFile):
    """Add a PDF. Returns a job; poll GET /jobs/{id}. A file with the name of an existing document replaces it."""
    name = safe_name(file.filename)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"the file is larger than {MAX_UPLOAD // 1024 // 1024} MB")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "the file is not a PDF")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as d:
            pages = len(d)
    except Exception:
        raise HTTPException(400, "the PDF cannot be opened")
    if pages == 0 or pages > MAX_PAGES:
        raise HTTPException(400, f"the PDF must have 1 to {MAX_PAGES} pages (it has {pages})")
    tmp = Path(tempfile.mkdtemp(prefix="upload-"))
    pdf = tmp / f"{name}.pdf"
    pdf.write_bytes(data)
    job = {"id": uuid.uuid4().hex[:12], "name": name, "status": "queued", "stage": "queued", "done": 0, "total": pages,
           "chunks": None, "error": None, "replaces": name in doc_names()}
    JOBS[job["id"]] = job
    while len(JOBS) > 20:  # keep the newest 20
        JOBS.pop(next(iter(JOBS)))
    _ingest_pool.submit(run_ingest, job, pdf, job["replaces"])
    return job


@app.delete("/documents/{name}", status_code=204, dependencies=[Depends(require_key)])
def delete_document(name: str):
    """Remove a document: its chunks in the vector database and its PDF."""
    if name not in doc_names():  # also keeps a path out of the unlink below
        raise HTTPException(404, f"unknown document: {name}")
    if any(j["name"] == name and j["status"] in ("queued", "running") for j in JOBS.values()):
        raise HTTPException(409, "the document is being ingested")
    qdrant().delete(COLLECTION, models.FilterSelector(filter=models.Filter(must=[
        models.FieldCondition(key="doc", match=models.MatchValue(value=name))])))
    (DOCS / f"{name}.pdf").unlink(missing_ok=True)


@app.get("/jobs", dependencies=[Depends(require_key)])
def jobs():
    return list(JOBS.values())


@app.get("/jobs/{job_id}", dependencies=[Depends(require_key)])
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "unknown job")
    return JOBS[job_id]


def need_history():
    if not history.enabled():
        raise HTTPException(404, "history is not enabled (set DATABASE_URL)")


@app.get("/conversations", dependencies=[Depends(require_key), Depends(need_history)])
def conversations():
    return history.list_conversations()


@app.get("/conversations/{conversation_id}", dependencies=[Depends(require_key), Depends(need_history)])
def conversation(conversation_id: uuid.UUID):
    conv = history.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, f"unknown conversation: {conversation_id}")
    return conv


@app.delete("/conversations/{conversation_id}", status_code=204, dependencies=[Depends(require_key), Depends(need_history)])
def delete_conversation(conversation_id: uuid.UUID):
    if not history.delete_conversation(conversation_id):
        raise HTTPException(404, f"unknown conversation: {conversation_id}")


@app.get("/page/{doc}/{page}.png", dependencies=[Depends(require_key)])
def page_image(doc: str, page: int):
    if doc not in doc_names():  # whitelist: the name never reaches the file system unchecked
        raise HTTPException(404, f"unknown document: {doc}")
    with pymupdf.open(DOCS / f"{doc}.pdf") as pdf:
        if not 1 <= page <= len(pdf):
            raise HTTPException(404, f"{doc} has {len(pdf)} pages")
        png = pdf[page - 1].get_pixmap(dpi=110).tobytes("png")
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


if __name__ == "__main__":
    MODE = sys.argv[1] if len(sys.argv) > 1 else "both"
    if MODE not in ("both", "rest", "grpc"):
        sys.exit("usage: python server.py [both|rest|grpc]")
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in LOCAL and not API_KEYS:
        sys.exit(f"Refusing to listen on {host} without API_KEY. Set API_KEY in .env.")
    if MODE == "grpc":
        g = grpc_server()
        g.start()
        print(f"gRPC listening on {host}:{os.environ.get('GRPC_PORT', 50051)}")
        g.wait_for_termination()
    else:
        uvicorn.run(app, host=host, port=int(os.environ.get("PORT", 8000)))

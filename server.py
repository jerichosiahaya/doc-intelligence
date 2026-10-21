"""Usage: python server.py [both|rest|grpc]

both (default): REST + web UI on :8000 and gRPC on :50051 in one process.
rest / grpc: one interface per process. Embedded Qdrant locks its folder to one
process, so run separate processes only with QDRANT_URL set (see docker-compose.yml).
"""
import os
import secrets
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import grpc
import pymupdf
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import history
import docintel_pb2 as pb
import docintel_pb2_grpc as pb_grpc
from ask import answer
from reqlog import log

DOCS = Path("docs")  # ingest.py keeps a copy of each PDF here
API_KEYS = [k.strip() for k in os.environ.get("API_KEY", "").split(",") if k.strip()]  # empty = no auth (local use)
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


class Source(BaseModel):
    doc: str
    page: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc: str | None = None
    conversation_id: uuid.UUID | None = None  # continue a saved conversation; omit to start a new one


class AskReply(BaseModel):
    answer: str
    citations: list[Source]
    rejected_citations: list[Source]
    refused: bool
    retrieved: list[Source]
    seconds: float
    tokens: int
    conversation_id: str | None = None  # set when history is on and the turn was saved


def doc_names():
    return sorted(p.stem for p in DOCS.glob("*.pdf"))


def ask(question: str, doc: str | None = None, via: str = "rest", conversation_id: uuid.UUID | None = None) -> AskReply:
    if doc and doc not in doc_names():
        raise UnknownDoc(doc)
    if conversation_id and not history.enabled():
        raise HistoryOff()
    if conversation_id and not history.exists(conversation_id):  # check before the model call, not after
        raise history.UnknownConversation(str(conversation_id))
    a = answer(question, doc, via=via)
    reply = AskReply(
        answer=str(a["answer"]),
        citations=a["citations"],
        rejected_citations=a["rejected_citations"],
        refused=bool(a["refused"]),
        retrieved=a["retrieved_sources"],
        seconds=a["seconds"],
        tokens=a["tokens"] or 0,
        conversation_id=str(conversation_id) if conversation_id else None,
    )
    if history.enabled():
        try:  # a failed save must not lose an answer that already cost a model call
            reply.conversation_id = str(history.save_turn(question, reply.model_dump(), doc, conversation_id))
        except Exception as e:
            log(kind="history", via=via, status="error", error=str(e)[:300])
    return reply


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
        return pb.AskReply(**{**r.model_dump(), "conversation_id": r.conversation_id or ""})


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


@app.get("/health")
def health():
    return {"ok": True, "history": history.enabled()}


@app.get("/documents", dependencies=[Depends(require_key)])
def documents():
    return doc_names()


@app.post("/ask", response_model=AskReply, dependencies=[Depends(require_key)])
def post_ask(req: AskRequest):
    try:
        return ask(req.question, req.doc, conversation_id=req.conversation_id)
    except UnknownDoc:
        raise HTTPException(404, f"unknown document: {req.doc}")
    except history.UnknownConversation:
        raise HTTPException(404, f"unknown conversation: {req.conversation_id}")
    except HistoryOff:
        raise HTTPException(400, "history is not enabled (set DATABASE_URL)")
    except Exception as e:
        raise HTTPException(502, f"backend error: {e}")


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


@app.get("/")
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


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

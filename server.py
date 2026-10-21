"""Usage: python server.py

One process: REST + web UI on :8000, gRPC on :50051. Qdrant's embedded mode
locks its folder, so this is the only process that may open it (set
QDRANT_URL to use a Qdrant server and lift that limit).
"""
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import grpc
import pymupdf
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

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


class Source(BaseModel):
    doc: str
    page: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc: str | None = None


class AskReply(BaseModel):
    answer: str
    citations: list[Source]
    rejected_citations: list[Source]
    refused: bool
    retrieved: list[Source]
    seconds: float
    tokens: int


def doc_names():
    return sorted(p.stem for p in DOCS.glob("*.pdf"))


def ask(question: str, doc: str | None = None, via: str = "rest") -> AskReply:
    if doc and doc not in doc_names():
        raise UnknownDoc(doc)
    a = answer(question, doc, via=via)
    return AskReply(
        answer=str(a["answer"]),
        citations=a["citations"],
        rejected_citations=a["rejected_citations"],
        refused=bool(a["refused"]),
        retrieved=a["retrieved_sources"],
        seconds=a["seconds"],
        tokens=a["tokens"] or 0,
    )


class DocIntel(pb_grpc.DocIntelServicer):
    def Ask(self, request, context):
        if not request.question.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "question is empty")
        try:
            r = ask(request.question, request.doc or None, via="grpc")
        except UnknownDoc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown document: {request.doc}")
        except Exception as e:  # LLM proxy or vector store failure
            context.abort(grpc.StatusCode.UNAVAILABLE, f"backend error: {e}")
        return pb.AskReply(**r.model_dump())


@asynccontextmanager
async def lifespan(_):
    g = grpc.server(ThreadPoolExecutor(max_workers=4), interceptors=[KeyInterceptor()])
    pb_grpc.add_DocIntelServicer_to_server(DocIntel(), g)
    g.add_insecure_port(f"{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('GRPC_PORT', 50051)}")
    g.start()
    yield
    g.stop(grace=2)


app = FastAPI(title="Document Intelligence", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/documents", dependencies=[Depends(require_key)])
def documents():
    return doc_names()


@app.post("/ask", response_model=AskReply, dependencies=[Depends(require_key)])
def post_ask(req: AskRequest):
    try:
        return ask(req.question, req.doc)
    except UnknownDoc:
        raise HTTPException(404, f"unknown document: {req.doc}")
    except Exception as e:
        raise HTTPException(502, f"backend error: {e}")


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
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in LOCAL and not API_KEYS:
        sys.exit(f"Refusing to listen on {host} without API_KEY. Set API_KEY in .env.")
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", 8000)))

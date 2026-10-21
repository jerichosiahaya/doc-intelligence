"""Usage: python server.py

One process: REST + web UI on :8000, gRPC on :50051. Qdrant's embedded mode
locks its folder, so this is the only process that may open it (set
QDRANT_URL to use a Qdrant server and lift that limit).
"""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import grpc
import pymupdf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import docintel_pb2 as pb
import docintel_pb2_grpc as pb_grpc
from ask import answer

DOCS = Path("docs")  # ingest.py keeps a copy of each PDF here


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


def ask(question: str, doc: str | None = None) -> AskReply:
    if doc and doc not in doc_names():
        raise UnknownDoc(doc)
    a = answer(question, doc)
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
            r = ask(request.question, request.doc or None)
        except UnknownDoc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown document: {request.doc}")
        except Exception as e:  # LLM proxy or vector store failure
            context.abort(grpc.StatusCode.UNAVAILABLE, f"backend error: {e}")
        return pb.AskReply(**r.model_dump())


@asynccontextmanager
async def lifespan(_):
    g = grpc.server(ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_DocIntelServicer_to_server(DocIntel(), g)
    g.add_insecure_port(f"{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('GRPC_PORT', 50051)}")
    g.start()
    yield
    g.stop(grace=2)


app = FastAPI(title="Document Intelligence", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/documents")
def documents():
    return doc_names()


@app.post("/ask", response_model=AskReply)
def post_ask(req: AskRequest):
    try:
        return ask(req.question, req.doc)
    except UnknownDoc:
        raise HTTPException(404, f"unknown document: {req.doc}")
    except Exception as e:
        raise HTTPException(502, f"backend error: {e}")


@app.get("/page/{doc}/{page}.png")
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
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))

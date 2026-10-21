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
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import docintel_pb2 as pb
import docintel_pb2_grpc as pb_grpc
from ask import answer


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AskReply(BaseModel):
    answer: str
    citations: list[int]
    refused: bool
    retrieved_pages: list[int]
    seconds: float
    tokens: int


def ask(question: str) -> AskReply:
    a = answer(question)
    return AskReply(
        answer=str(a["answer"]),
        citations=[int(p) for p in a.get("citations") or []],
        refused=bool(a["refused"]),
        retrieved_pages=a["retrieved_pages"],
        seconds=a["seconds"],
        tokens=a["tokens"] or 0,
    )


class DocIntel(pb_grpc.DocIntelServicer):
    def Ask(self, request, context):
        if not request.question.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "question is empty")
        try:
            r = ask(request.question)
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


@app.post("/ask", response_model=AskReply)
def post_ask(req: AskRequest):
    try:
        return ask(req.question)
    except Exception as e:
        raise HTTPException(502, f"backend error: {e}")


@app.get("/")
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))

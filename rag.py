"""Shared setup: LiteLLM client, local embeddings, Qdrant (embedded)."""
import os

from dotenv import load_dotenv
from fastembed import TextEmbedding
from openai import OpenAI
from qdrant_client import QdrantClient, models

load_dotenv()

COLLECTION = "chunks"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384 dims, runs locally

_embedder = None
_qdrant = None


def llm():
    return OpenAI(base_url=os.environ["LITELLM_URL"], api_key=os.environ["LITELLM_KEY"])


def embed(texts):
    global _embedder
    _embedder = _embedder or TextEmbedding(EMBED_MODEL)
    return [v.tolist() for v in _embedder.embed(texts)]


def qdrant():
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(path="qdrant_data")
        if not _qdrant.collection_exists(COLLECTION):
            _qdrant.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
            )
    return _qdrant

"""Shared setup: LiteLLM client, local embeddings, Qdrant (embedded)."""
import os

from dotenv import load_dotenv
from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from openai import OpenAI
from qdrant_client import QdrantClient, models

load_dotenv()

COLLECTION = "chunks"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384 dims, runs locally
SPARSE_MODEL = "Qdrant/bm25"  # keyword side of hybrid search, runs locally
RERANK_MODEL = os.environ.get("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-12-v2")  # cross-encoder, runs locally

_embedder = None
_sparse = None
_reranker = None
_qdrant = None


def llm():
    return OpenAI(base_url=os.environ["LITELLM_URL"], api_key=os.environ["LITELLM_KEY"])


def embed(texts):
    global _embedder
    _embedder = _embedder or TextEmbedding(EMBED_MODEL)
    return [v.tolist() for v in _embedder.embed(texts)]


def sparse(texts, query=False):
    global _sparse
    _sparse = _sparse or SparseTextEmbedding(SPARSE_MODEL)
    fn = _sparse.query_embed if query else _sparse.embed
    return [models.SparseVector(indices=v.indices.tolist(), values=v.values.tolist()) for v in fn(texts)]


def rerank_scores(query, texts):
    global _reranker
    _reranker = _reranker or TextCrossEncoder(RERANK_MODEL)
    return list(_reranker.rerank(query, texts))


def qdrant():
    global _qdrant
    if _qdrant is None:
        # embedded mode locks the folder to one process; QDRANT_URL lifts that
        _qdrant = QdrantClient(url=os.environ["QDRANT_URL"]) if os.environ.get("QDRANT_URL") \
            else QdrantClient(path="qdrant_data")
        if not _qdrant.collection_exists(COLLECTION):
            _qdrant.create_collection(
                COLLECTION,
                vectors_config={"dense": models.VectorParams(size=384, distance=models.Distance.COSINE)},
                sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
    return _qdrant

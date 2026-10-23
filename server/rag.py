"""Shared setup: LiteLLM client, local embeddings, Qdrant (embedded)."""
import atexit
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


_clients = {}


def _env(role, name, legacy):
    """ROLE_NAME, else LLM_NAME, else the old LITELLM_* name."""
    for key in (f"{role.upper()}_{name}", f"LLM_{name}", legacy):
        if os.environ.get(key):
            return os.environ[key]
    return None


def llm(role="answer"):
    """A client for any OpenAI-compatible endpoint (OpenAI, DeepSeek, LiteLLM, OpenRouter, Ollama, vLLM, ...).
    role is answer, judge, or vision. Each role reads ROLE_BASE_URL and ROLE_API_KEY, and falls back to
    LLM_BASE_URL and LLM_API_KEY (then to LITELLM_URL and LITELLM_KEY), so one endpoint can serve all roles."""
    url, key = _env(role, "BASE_URL", "LITELLM_URL"), _env(role, "API_KEY", "LITELLM_KEY")
    if not url or not key:
        raise RuntimeError(f"No model endpoint for {role}: set LLM_BASE_URL and LLM_API_KEY (or {role.upper()}_BASE_URL and {role.upper()}_API_KEY)")
    if (url, key) not in _clients:
        _clients[url, key] = OpenAI(base_url=url, api_key=key)
    return _clients[url, key]


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
        atexit.register(close_qdrant)  # a clean close; the client's __del__ fails during interpreter shutdown
        if not _qdrant.collection_exists(COLLECTION):
            _qdrant.create_collection(
                COLLECTION,
                vectors_config={"dense": models.VectorParams(size=384, distance=models.Distance.COSINE)},
                sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
    return _qdrant


def close_qdrant():
    """Release the client (and the folder lock in embedded mode). The next qdrant() call reopens it."""
    global _qdrant
    if _qdrant is not None:
        _qdrant.close()
        _qdrant = None

"""Choose the knowledge-search backend: HF_SEARCH = lexical (default) | vector (in-memory embeddings) | vertex (Vertex AI). HF_PROFILE=gcp defaults to vertex."""
import os
from .base import Chunk, Result
from .lexical import LexicalBackend
from .vector import VectorBackend, HashingEmbedder, MemoryStore


def make_backend(kind=None, env=None):
    env = os.environ if env is None else env
    kind = kind or env.get("HF_SEARCH") or ("vertex" if env.get("HF_PROFILE") == "gcp" else "lexical")
    if kind == "lexical": return LexicalBackend()
    if kind == "vector": return VectorBackend(HashingEmbedder(), MemoryStore(), float(env.get("HF_VECTOR_SCORE_MIN", "0.17")))
    if kind == "vertex":
        from .vertex import VertexEmbedder, VertexVectorStore
        return VectorBackend(VertexEmbedder(), VertexVectorStore(), float(env.get("HF_VERTEX_SCORE_MIN", "0.55")), name="vertex")
    raise ValueError(f"unknown HF_SEARCH {kind!r} (lexical | vector | vertex)")

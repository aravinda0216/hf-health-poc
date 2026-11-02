"""Vector backend = an Embedder (text -> unit vector) + a VectorStore (nearest neighbours with metadata filters).

In-memory pieces (offline, deterministic): `HashingEmbedder` (tf-idf feature hashing, a lexical stand-in, NOT semantic) and `MemoryStore` (brute-force dot product).
Vertex AI pieces live in vertex.py and plug into the same two interfaces.
"""
import hashlib, math, os
from typing import Protocol, List
from .base import Result
from .lexical import tokens


class Embedder(Protocol):
    dim: int
    def fit(self, texts: List[str]) -> None: ...
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...
    def embed_query(self, text: str) -> List[float]: ...


class VectorStore(Protocol):
    managed: bool        # True when the index is built and updated outside this process (Vertex): the API then skips upsert on start
    def upsert(self, ids: List[str], vecs: List[List[float]], restricts: List[dict]) -> None: ...
    def nearest(self, vec: List[float], k: int, audience: str, use_case) -> List[tuple]: ...


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashingEmbedder:
    """Tf-idf weighted feature hashing into `dim` buckets with unigram + bigram features, L2-normalised. Deterministic and dependency-free."""
    def __init__(self, dim=4096): self.dim = dim; self.idf = {}; self.n = 0

    def _feats(self, text):
        t = tokens(text); return t + [a + "_" + b for a, b in zip(t, t[1:])]

    def _bucket(self, f):
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big"); return h % self.dim, (1 if (h >> 63) & 1 else -1)

    def fit(self, texts):
        df = {}
        for x in texts:
            for f in set(self._feats(x)): df[f] = df.get(f, 0) + 1
        self.n = len(texts); self.idf = {f: math.log(1 + self.n / d) for f, d in df.items()}

    def _vec(self, text):
        v = [0.0] * self.dim; unseen = math.log(1 + max(1, self.n))
        for f in self._feats(text):
            b, s = self._bucket(f); v[b] += s * self.idf.get(f, unseen)
        return _norm(v)

    def embed_documents(self, texts): return [self._vec(x) for x in texts]
    def embed_query(self, text): return self._vec(text)


class MemoryStore:
    managed = False
    def __init__(self): self.items = {}

    def upsert(self, ids, vecs, restricts):
        for i, v, r in zip(ids, vecs, restricts): self.items[i] = (v, r)

    def nearest(self, vec, k, audience, use_case):
        out = []
        for i, (v, r) in self.items.items():
            if r["audience"] != audience: continue
            if use_case and use_case not in r["use_cases"]: continue
            out.append((sum(a * b for a, b in zip(vec, v)), i))
        out.sort(key=lambda x: (-x[0], x[1])); return [(i, s) for s, i in out[:k]]


class VectorBackend:
    def __init__(self, embedder, store, score_min, name="vector"):
        self.embedder, self.store, self.score_min, self.name = embedder, store, score_min, name

    def build(self, chunks):
        if self.store.managed: return          # index lives in the managed service; it is published separately (see vertex_publish.py)
        texts = [c.section_path + ". " + c.text for c in chunks]; self.embedder.fit(texts)
        self.store.upsert([c.chunk_id for c in chunks], self.embedder.embed_documents(texts), [{"audience": c.audience, "use_cases": sorted(c.use_cases)} for c in chunks])

    def query(self, q, audience, use_case, k):
        near = self.store.nearest(self.embedder.embed_query(q), k, audience, use_case)
        abstain = (not near) or near[0][1] < self.score_min
        return Result([] if abstain else [(i, round(float(s), 4)) for i, s in near], abstain)

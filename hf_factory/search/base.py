"""Knowledge-search seam. The API loads ACTIVE, in-date chunks from the dataset and asks a backend which chunk ids answer a query.

Backends: `lexical` (BM25-style, default, offline), `vector` (embeddings + nearest neighbours; in-memory store and an offline hashing embedder),
`vertex` (Vertex AI text embeddings + Vertex AI Vector Search). The API always hydrates text and citations from the dataset by chunk_id, so a backend only
returns ids and scores. A backend sets abstain=True when nothing is confident enough to answer; the agent then says it does not know.
"""
from dataclasses import dataclass, field
from typing import Protocol, List, Tuple


@dataclass(frozen=True)
class Chunk:
    chunk_id: str; asset_id: str; section_path: str; text: str; audience: str
    use_cases: frozenset = field(default_factory=frozenset)


@dataclass
class Result:
    hits: List[Tuple[str, float]]       # (chunk_id, score), best first
    abstain: bool


class SearchBackend(Protocol):
    name: str
    def build(self, chunks: List[Chunk]) -> None: ...
    def query(self, q: str, audience: str, use_case, k: int) -> Result: ...

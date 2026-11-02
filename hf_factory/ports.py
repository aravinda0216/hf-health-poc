"""Ports: the seams between the engines and the infrastructure behind them.

The engines only talk to these three interfaces. The `local` profile binds them to SQLite files (everything runs and is tested). The `gcp` profile names the
production bindings (AlloyDB / Cloud SQL, Firestore, Vertex AI Vector Search) but those adapters are NOT implemented or tested here: they need a live GCP project.
"""
from typing import Protocol, Any


class DatasetStore(Protocol):
    """Read-only access to the generated dataset (member, provider, claims, ...). Rows come back as mappings."""
    def connect_ro(self) -> Any: ...


class StateStore(Protocol):
    """Writable store for what API callers create (uploads, appeals, tickets, PCP changes, bookings). Never the dataset."""
    def connect(self) -> Any: ...


class SearchIndex(Protocol):
    """Knowledge search over asset_chunk text. `search` returns [(chunk_id, score)] best first."""
    def search(self, query: str, audience: str, k: int) -> list: ...

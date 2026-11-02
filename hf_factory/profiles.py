"""Config profiles: pick the infrastructure bindings by name (HF_PROFILE, default 'local').

profile  dataset                                   state                      knowledge search (default)
local    SQLite file, read-only                    SQLite file                lexical BM25
memory   SQLite copied into RAM at start           in-memory, gone on exit    lexical BM25 (HF_SEARCH=vector for in-memory vectors)
gcp      SQLite pulled from gs://HF_DATASET_URI    SQLite on the instance     Vertex AI embeddings + Vector Search
HF_SEARCH (lexical | vector | vertex) overrides the search default in any profile.
"""
import os
from .adapters import sqlite_store as sq, gcp_store as gcp

PROFILES = ("local", "memory", "gcp")


def _profile(p):
    p = p or os.environ.get("HF_PROFILE", "local")
    if p not in PROFILES: raise ValueError(f"unknown profile {p!r} ({' | '.join(PROFILES)})")
    return p


def dataset_store(path, profile=None):
    p = _profile(profile)
    if p == "local": return sq.SqliteDataset(path)
    if p == "memory": return sq.MemoryDataset(path)
    uri = os.environ.get("HF_DATASET_URI", "")
    if uri.startswith("gs://"): return gcp.GcsSqliteDataset(uri)
    if os.path.exists(path): return sq.SqliteDataset(path)      # gcp profile with a local file (development)
    raise ValueError("gcp profile needs HF_DATASET_URI=gs://bucket/path/factory.db (or HF_DB pointing at a local file)")


def state_store(path, profile=None):
    p = _profile(profile)
    if p == "memory": return sq.MemoryState()
    return sq.SqliteState(path)         # local and gcp: SQLite file (ephemeral on Cloud Run)

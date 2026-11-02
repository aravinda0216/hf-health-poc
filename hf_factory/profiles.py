"""Config profiles: pick the infrastructure bindings by name (HF_PROFILE, default 'local')."""
import os
from .adapters import sqlite_store as sq, gcp_store as gcp


def dataset_store(path, profile=None):
    p = profile or os.environ.get("HF_PROFILE", "local")
    if p == "local": return sq.SqliteDataset(path)
    if p == "gcp": return gcp.AlloyDbDataset(os.environ.get("HF_ALLOYDB_DSN", path))
    raise ValueError(f"unknown profile {p!r} (local | gcp)")


def state_store(path, profile=None):
    p = profile or os.environ.get("HF_PROFILE", "local")
    if p == "local": return sq.SqliteState(path)
    if p == "gcp": return gcp.FirestoreState(os.environ.get("HF_GCP_PROJECT", ""))
    raise ValueError(f"unknown profile {p!r} (local | gcp)")

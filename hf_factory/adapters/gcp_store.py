"""GCP profile bindings.

Dataset : GcsSqliteDataset  - the generated SQLite dataset is published to Cloud Storage (`python -m hf_factory.gcp_publish`) and downloaded once to local disk at
          service start (Cloud Run: /tmp), then opened read-only. Works because the API is read-only on the dataset. Needs google-cloud-storage.
State   : local SQLite on the instance (ephemeral on Cloud Run: appeals, bookings and tickets are lost when the instance is replaced). A durable store
          (Firestore or Cloud SQL) is NOT implemented.
Search  : Vertex AI embeddings + Vertex AI Vector Search (hf_factory/search/vertex.py).
AlloyDB : NOT implemented (the API's SQL is SQLite dialect).

None of this has been run against a live GCP project; it is covered by unit tests with fakes.
"""
import os, sqlite3, tempfile


class NotImplementedOnGcp(RuntimeError): pass


def _no(what): raise NotImplementedOnGcp(f"{what} for the gcp profile is not implemented yet (see hf_factory/adapters/gcp_store.py)")


def split_gs(uri):
    if not uri.startswith("gs://") or "/" not in uri[5:]: raise ValueError(f"expected gs://bucket/object, got {uri!r}")
    b, _, o = uri[5:].partition("/"); return b, o


class GcsSqliteDataset:
    def __init__(self, uri, cache_dir=None, client=None):
        self.uri = uri; self.cache_dir = cache_dir or tempfile.gettempdir(); self._client = client; self._local = None

    def local_path(self):
        if self._local is None:
            bucket, obj = split_gs(self.uri); dest = os.path.join(self.cache_dir, "hf_" + obj.replace("/", "_"))
            if not os.path.exists(dest):
                client = self._client
                if client is None:
                    from google.cloud import storage
                    client = storage.Client(project=os.environ.get("HF_GCP_PROJECT") or None)
                tmp = dest + ".part"; client.bucket(bucket).blob(obj).download_to_filename(tmp); os.replace(tmp, dest)
            self._local = dest
        return self._local

    def connect_ro(self):
        c = sqlite3.connect(f"file:{self.local_path()}?mode=ro", uri=True, check_same_thread=False); c.row_factory = sqlite3.Row; return c


class AlloyDbDataset:
    def __init__(self, dsn): self.dsn = dsn
    def connect_ro(self): _no("AlloyDB dataset store")


class FirestoreState:
    def __init__(self, project): self.project = project
    def connect(self): _no("Firestore state store")

"""GCP profile: documented mapping only. NOT implemented and NOT tested (no GCP project is reachable from the build environment).

    DatasetStore -> AlloyDB for PostgreSQL (or Cloud SQL); load with the Sink writing COPY batches instead of SQLite INSERTs
    StateStore   -> Firestore or a small Cloud SQL schema (same tables as the SQLite state DB)
    SearchIndex  -> Vertex AI Vector Search over asset_chunk embeddings

Each class fails loudly rather than pretending to work, so choosing HF_PROFILE=gcp today gives a clear message.
"""


class NotImplementedOnGcp(RuntimeError): pass


def _no(what): raise NotImplementedOnGcp(f"{what} for the gcp profile is not implemented yet (see hf_factory/adapters/gcp_store.py)")


class AlloyDbDataset:
    def __init__(self, dsn): self.dsn = dsn
    def connect_ro(self): _no("AlloyDB dataset store")


class FirestoreState:
    def __init__(self, project): self.project = project
    def connect(self): _no("Firestore state store")


class VertexSearch:
    def search(self, query, audience, k): _no("Vertex AI Vector Search")

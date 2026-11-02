"""Vertex AI bindings for the vector backend: text embeddings (Vertex AI) and nearest-neighbour search (Vertex AI Vector Search).

NOT verified against a live project (none is reachable from the build environment). The client calls follow the installed google-cloud-aiplatform signatures and the
logic is unit-tested with injected fakes. Requires `pip install -r requirements-gcp.txt` and Application Default Credentials (on Cloud Run: the service account).

Index requirements: dimensions = embedding dimension (768 for text-embedding-005), distance measure DOT_PRODUCT_DISTANCE, vectors L2-normalised (done here, so the
dot product equals cosine similarity and the returned value is the similarity), update method STREAM_UPDATE. Datapoint id = chunk_id; restricts: namespace `audience`
and namespace `use_case`.
"""
import math, os
from .base import Result  # noqa: F401  (kept for symmetry with the other backends)

BATCH = 50      # texts per embedding call (the API allows up to 250 inputs / 20k tokens)


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class VertexEmbedder:
    """Vertex AI TextEmbeddingModel. `model` is injectable for tests; documents use RETRIEVAL_DOCUMENT, queries RETRIEVAL_QUERY."""
    def __init__(self, project=None, location=None, model_name=None, dim=None, model=None, input_cls=None):
        self.dim = dim or int(os.environ.get("HF_VERTEX_DIM", "768"))
        self.model_name = model_name or os.environ.get("HF_VERTEX_EMBED_MODEL", "text-embedding-005")
        if model is None:
            import vertexai
            from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
            vertexai.init(project=project or os.environ["HF_GCP_PROJECT"], location=location or os.environ.get("HF_GCP_REGION", "us-central1"))
            model, input_cls = TextEmbeddingModel.from_pretrained(self.model_name), TextEmbeddingInput
        self.model, self.input_cls = model, input_cls

    def fit(self, texts): pass

    def _embed(self, texts, task):
        out = []
        for i in range(0, len(texts), BATCH):
            batch = [self.input_cls(t, task) for t in texts[i:i + BATCH]]
            out += [_norm(list(e.values)[:self.dim]) for e in self.model.get_embeddings(batch, output_dimensionality=self.dim)]
        return out

    def embed_documents(self, texts): return self._embed(texts, "RETRIEVAL_DOCUMENT")
    def embed_query(self, text): return self._embed([text], "RETRIEVAL_QUERY")[0]


class VertexVectorStore:
    """Vertex AI Vector Search. `endpoint` (MatchingEngineIndexEndpoint) serves queries; `index` (MatchingEngineIndex) takes streaming upserts (publish only)."""
    managed = True

    def __init__(self, deployed_index_id=None, endpoint=None, index=None, namespace_cls=None, datapoint_cls=None, restriction_cls=None, project=None, location=None):
        self.deployed_index_id = deployed_index_id or os.environ["HF_VERTEX_DEPLOYED_INDEX_ID"]
        if endpoint is None and index is None:
            from google.cloud import aiplatform
            aiplatform.init(project=project or os.environ["HF_GCP_PROJECT"], location=location or os.environ.get("HF_GCP_REGION", "us-central1"))
            if os.environ.get("HF_VERTEX_INDEX_ENDPOINT"): endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=os.environ["HF_VERTEX_INDEX_ENDPOINT"])
            if os.environ.get("HF_VERTEX_INDEX"): index = aiplatform.MatchingEngineIndex(index_name=os.environ["HF_VERTEX_INDEX"])
        if namespace_cls is None:
            from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import Namespace
            from google.cloud.aiplatform_v1.types import IndexDatapoint
            namespace_cls, datapoint_cls, restriction_cls = Namespace, IndexDatapoint, IndexDatapoint.Restriction
        self.endpoint, self.index, self.ns, self.dp, self.rs = endpoint, index, namespace_cls, datapoint_cls, restriction_cls

    def upsert(self, ids, vecs, restricts):
        if self.index is None: raise RuntimeError("HF_VERTEX_INDEX is required to publish datapoints")
        for i in range(0, len(ids), 100):          # streaming upserts in small batches
            pts = [self.dp(datapoint_id=ids[j], feature_vector=vecs[j],
                           restricts=[self.rs(namespace="audience", allow_list=[restricts[j]["audience"]]), self.rs(namespace="use_case", allow_list=list(restricts[j]["use_cases"]) or ["NONE"])])
                   for j in range(i, min(i + 100, len(ids)))]
            self.index.upsert_datapoints(datapoints=pts)

    def nearest(self, vec, k, audience, use_case):
        flt = [self.ns("audience", [audience], [])] + ([self.ns("use_case", [use_case], [])] if use_case else [])
        res = self.endpoint.find_neighbors(deployed_index_id=self.deployed_index_id, queries=[vec], num_neighbors=k, filter=flt)
        return [(n.id, float(n.distance)) for n in (res[0] if res else [])]       # DOT_PRODUCT_DISTANCE on unit vectors: higher = more similar

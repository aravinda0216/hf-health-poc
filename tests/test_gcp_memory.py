"""Vector search (in-memory), memory profile, and the GCP/Vertex code paths. Google clients are replaced by fakes: nothing here talks to a real GCP project."""
import math, os, shutil, sqlite3, tempfile
import pytest
from fastapi.testclient import TestClient
from hf_factory.api.app import create_app, Config
from hf_factory.search import make_backend, Chunk
from hf_factory.search.vector import VectorBackend, MemoryStore, HashingEmbedder
from hf_factory.search.vertex import VertexEmbedder, VertexVectorStore
from hf_factory import gcp_publish
from hf_factory.adapters.gcp_store import GcsSqliteDataset

MEMBER_UCS = ("MBN", "MCI", "MPS", "MPC", "MAS")


def token(c): return {"Authorization": "Bearer " + c.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "demo-secret-change-me"}).json()["access_token"]}


# ---------------------------------------------------------------- in-memory vector search
def test_vector_backend_golden(db_path, con):
    c = TestClient(create_app(Config(db_path=db_path, state_path=tempfile.mktemp(), fault_enabled=False, search_backend=make_backend("vector")))); h = token(c)
    un = ok_un = ez = ok_ez = 0
    for uc, q, d in con.execute("SELECT use_case_code, question, difficulty FROM qa_golden WHERE difficulty IN ('UNANSWERABLE','EASY')").fetchall():
        a = c.get("/v1/knowledge/search", params={"q": q, "use_case": uc, "audience": "MEMBER" if uc in MEMBER_UCS else "PROVIDER"}, headers=h).json()["abstain"]
        if d == "UNANSWERABLE": un += 1; ok_un += a
        else: ez += 1; ok_ez += (not a)
    assert ok_un == un and ok_ez / ez >= 0.55          # the hashing embedder is a lexical stand-in: it must never answer the unanswerable; recall is lower than BM25


def test_vector_backend_respects_audience_and_use_case():
    ch = [Chunk("a", "A1", "Appeals", "how to file an appeal for a denied claim within ninety days", "PROVIDER", frozenset({"PAP"})),
          Chunk("b", "B1", "Appeals", "how to file an appeal for a denied claim within ninety days", "MEMBER", frozenset({"MCI"}))]
    b = make_backend("vector"); b.build(ch)
    assert [h[0] for h in b.query("file an appeal denied claim", "PROVIDER", None, 5).hits] == ["a"]
    assert [h[0] for h in b.query("file an appeal denied claim", "MEMBER", "MCI", 5).hits] == ["b"]
    assert b.query("file an appeal denied claim", "MEMBER", "PAP", 5).abstain


def test_make_backend_defaults_and_errors():
    assert make_backend(env={}).name == "lexical" and make_backend(env={"HF_SEARCH": "vector"}).name == "vector"
    with pytest.raises(ValueError): make_backend("nope")


# ---------------------------------------------------------------- memory profile
def test_memory_profile_serves_without_state_file(db_path, monkeypatch):
    monkeypatch.setenv("HF_PROFILE", "memory"); state = os.path.join(tempfile.mkdtemp(), "state.db")
    c = TestClient(create_app(Config(db_path=db_path, state_path=state, fault_enabled=False))); h = token(c)
    row = sqlite3.connect(db_path).execute("SELECT claim_id, billing_npi, tin FROM claim_header h JOIN provider p ON p.npi=h.billing_npi LIMIT 1").fetchone()
    r = c.get(f"/v1/claims/{row[0]}/status", params={"npi": row[1], "tin": row[2]}, headers=h)
    assert r.status_code == 200
    assert not os.path.exists(state)                                                       # nothing written to disk
    # the dataset is read-only even in memory
    from hf_factory import profiles
    with pytest.raises(sqlite3.OperationalError): profiles.dataset_store(db_path, "memory").connect_ro().execute("CREATE TABLE x (a)")


def test_memory_state_is_shared_between_connections_and_isolated_between_instances():
    from hf_factory.adapters.sqlite_store import MemoryState
    a, b = MemoryState(), MemoryState(); a.connect().execute("CREATE TABLE t (x)"); a.connect().commit()
    assert a.connect().execute("SELECT count(*) FROM t").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError): b.connect().execute("SELECT * FROM t")


# ---------------------------------------------------------------- Vertex AI (fakes)
class FakeEmb:
    def __init__(self, v): self.values = v
class FakeModel:
    """Maps text to a deterministic 8-d vector; records the task types it was called with."""
    def __init__(self): self.calls = []
    def get_embeddings(self, inputs, output_dimensionality=None):
        self.calls.append([i.task_type for i in inputs]); out = []
        for i in inputs:
            v = [0.0] * 8
            for w in i.text.lower().split(): v[sum(map(ord, w)) % 8] += 1
            out.append(FakeEmb(v))
        return out
class FakeInput:
    def __init__(self, text, task_type): self.text, self.task_type = text, task_type
class NS:
    def __init__(self, name, allow, deny): self.name, self.allow, self.deny = name, allow, deny
class DP:
    def __init__(self, datapoint_id, feature_vector, restricts): self.datapoint_id, self.feature_vector, self.restricts = datapoint_id, feature_vector, restricts
class RS:
    def __init__(self, namespace, allow_list): self.namespace, self.allow_list = namespace, allow_list
class Neighbor:
    def __init__(self, i, d): self.id, self.distance = i, d
class FakeIndex:
    def __init__(self): self.points = []
    def upsert_datapoints(self, datapoints): self.points += list(datapoints)
class FakeEndpoint:
    """Serves the points an index received, applying the same namespace filter semantics as Vector Search."""
    def __init__(self, index): self.index, self.last = index, None
    def find_neighbors(self, deployed_index_id, queries, num_neighbors, filter):
        self.last = dict(deployed_index_id=deployed_index_id, num_neighbors=num_neighbors, filter=[(f.name, f.allow) for f in filter]); out = []
        for p in self.index.points:
            r = {x.namespace: x.allow_list for x in p.restricts}
            if all(set(r.get(f.name, [])) & set(f.allow) for f in filter): out.append(Neighbor(p.datapoint_id, sum(a * b for a, b in zip(queries[0], p.feature_vector))))
        out.sort(key=lambda n: -n.distance); return [out[:num_neighbors]]


def vertex_pair():
    idx = FakeIndex(); ep = FakeEndpoint(idx)
    emb = VertexEmbedder(model=FakeModel(), input_cls=FakeInput, dim=8)
    st = VertexVectorStore(deployed_index_id="dep1", endpoint=ep, index=idx, namespace_cls=NS, datapoint_cls=DP, restriction_cls=RS)
    return emb, st, idx, ep


CH = [Chunk("c1", "A", "Appeals", "file an appeal within ninety days", "PROVIDER", frozenset({"PAP"})),
      Chunk("c2", "B", "Cards", "replacement id card ships in five days", "MEMBER", frozenset({"MBN"}))]


def test_vertex_embedder_task_types_batches_and_unit_vectors():
    emb, *_ = vertex_pair()
    docs = emb.embed_documents(["w%d x" % i for i in range(120)]); q = emb.embed_query("hello")
    assert [len(c) for c in emb.model.calls] == [50, 50, 20, 1]
    assert set(emb.model.calls[0]) == {"RETRIEVAL_DOCUMENT"} and emb.model.calls[-1] == ["RETRIEVAL_QUERY"]
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-9 for v in docs + [q])


def test_vertex_publish_then_query_with_filters():
    emb, st, idx, ep = vertex_pair()
    assert gcp_publish.publish_index(CH, emb, st, log=lambda *_: None) == 2
    p = {x.datapoint_id: {r.namespace: r.allow_list for r in x.restricts} for x in idx.points}
    assert p["c1"] == {"audience": ["PROVIDER"], "use_case": ["PAP"]}
    b = VectorBackend(emb, st, 0.1, name="vertex"); b.build(CH)               # managed store: build() must not re-upsert
    assert len(idx.points) == 2
    r = b.query("file an appeal ninety days", "PROVIDER", "PAP", 3)
    assert [h[0] for h in r.hits] == ["c1"] and not r.abstain
    assert ep.last["filter"] == [("audience", ["PROVIDER"]), ("use_case", ["PAP"])] and ep.last["deployed_index_id"] == "dep1"
    assert b.query("file an appeal ninety days", "MEMBER", None, 3).abstain or [h[0] for h in b.query("file an appeal ninety days", "MEMBER", None, 3).hits] == ["c2"]
    assert VectorBackend(emb, st, 0.99).query("zzz qqq", "PROVIDER", None, 3).abstain


def test_vertex_backend_through_the_api(db_path):
    """Serve knowledge search through a (fake) Vertex index published from the real dataset's chunks."""
    emb, st, idx, ep = vertex_pair(); chunks = gcp_publish.load_chunks(db_path); gcp_publish.publish_index(chunks, emb, st, log=lambda *_: None)
    c = TestClient(create_app(Config(db_path=db_path, state_path=tempfile.mktemp(), fault_enabled=False, search_backend=VectorBackend(emb, st, 0.0, name="vertex")))); h = token(c)
    j = c.get("/v1/knowledge/search", params={"q": "what does CO-16 mean missing information denial"}, headers=h).json()
    assert j["results"] and all(r["chunk_id"] for r in j["results"])                  # hits are hydrated from the dataset by chunk id
    assert len(idx.points) == len(chunks) > 0


def test_vertex_store_requires_an_index_to_publish():
    st = VertexVectorStore(deployed_index_id="d", endpoint=FakeEndpoint(FakeIndex()), namespace_cls=NS, datapoint_cls=DP, restriction_cls=RS)
    with pytest.raises(RuntimeError): st.upsert(["a"], [[1.0]], [{"audience": "X", "use_cases": []}])


# ---------------------------------------------------------------- GCS dataset (fake client)
class FakeBlob:
    def __init__(self, src): self.src = src
    def download_to_filename(self, dest): shutil.copy(self.src, dest)
    def upload_from_filename(self, path): shutil.copy(path, self.src)
class FakeBucket:
    def __init__(self, store): self.store = store
    def blob(self, name): return FakeBlob(self.store.setdefault(name, os.path.join(tempfile.mkdtemp(), "o.db")))
class FakeStorage:
    def __init__(self): self.store = {}
    def bucket(self, name): return FakeBucket(self.store)


def test_gcs_dataset_upload_download_and_serve(db_path, tmp_path, monkeypatch):
    gcs = FakeStorage(); uri = gcp_publish.upload_dataset(db_path, "gs://bkt/s1/factory.db", client=gcs)
    ds = GcsSqliteDataset(uri, cache_dir=str(tmp_path), client=gcs)
    assert ds.connect_ro().execute("SELECT count(*) FROM provider").fetchone()[0] > 0
    assert os.path.exists(ds.local_path()) and not os.path.exists(ds.local_path() + ".part")
    with pytest.raises(ValueError): GcsSqliteDataset("http://nope").local_path()
    monkeypatch.setenv("HF_DATASET_URI", uri); monkeypatch.setattr("hf_factory.adapters.gcp_store.GcsSqliteDataset", lambda u: GcsSqliteDataset(u, str(tmp_path), gcs))
    monkeypatch.setenv("HF_PROFILE", "gcp")
    c2 = TestClient(create_app(Config(db_path=uri, state_path=str(tmp_path / "s.db"), fault_enabled=False))); assert c2.get("/healthz").status_code == 200


def test_gcp_profile_defaults_to_vertex_search(monkeypatch):
    monkeypatch.setenv("HF_PROFILE", "gcp"); monkeypatch.delenv("HF_SEARCH", raising=False)
    import hf_factory.search.vertex as v
    monkeypatch.setattr(v, "VertexEmbedder", lambda: "E"); monkeypatch.setattr(v, "VertexVectorStore", lambda: "S")
    b = make_backend(); assert b.name == "vertex" and b.embedder == "E" and b.store == "S"

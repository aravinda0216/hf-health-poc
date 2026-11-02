"""Publish a generated dataset to GCP so a Cloud Run service (HF_PROFILE=gcp) can serve it.

  python -m hf_factory.gcp_publish --db out/s2/factory.db --project my-proj --region us-central1 \\
        --dataset-uri gs://my-bucket/s2/factory.db --index projects/.../indexes/123            # upload + embed + upsert
  python -m hf_factory.gcp_publish --create-index --project my-proj --region us-central1        # one-off: create index, endpoint, deploy (prints the env to set)

NOT run against a live project from the build environment. Needs requirements-gcp.txt and Application Default Credentials.
"""
import argparse, os, sqlite3, sys
from .search import Chunk
from .search.vertex import VertexEmbedder, VertexVectorStore


def load_chunks(db_path):
    """ACTIVE, in-date chunks, as the API would load them."""
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row; meta = dict(c.execute("SELECT k,v FROM _meta").fetchall())
    rows = c.execute("SELECT c.chunk_id,c.asset_id,c.section_path,c.text,a.audience FROM asset_chunk c JOIN knowledge_asset a ON a.asset_id=c.asset_id WHERE a.status='ACTIVE' AND a.effective_dt<=? AND a.expiry_dt>=?",
                     (meta["as_of_date"], meta["as_of_date"])).fetchall()
    links = {}
    for r in c.execute("SELECT asset_id, linked_key FROM asset_link WHERE linked_entity='USE_CASE'"): links.setdefault(r[0], set()).add(r[1])
    return [Chunk(r[0], r[1], r[2], r[3], r[4], frozenset(links.get(r[1], ()))) for r in rows]


def publish_index(chunks, embedder, store, log=print):
    texts = [c.section_path + ". " + c.text for c in chunks]
    vecs = embedder.embed_documents(texts)
    store.upsert([c.chunk_id for c in chunks], vecs, [{"audience": c.audience, "use_cases": sorted(c.use_cases)} for c in chunks])
    log(f"  upserted {len(chunks)} chunks into the Vertex AI Vector Search index"); return len(chunks)


def upload_dataset(db_path, uri, client=None):
    from .adapters.gcp_store import split_gs
    if client is None:
        from google.cloud import storage
        client = storage.Client()
    b, o = split_gs(uri); client.bucket(b).blob(o).upload_from_filename(db_path); return uri


def create_index(project, region, dim=768, name="hf-knowledge"):
    """One-off: STREAM_UPDATE tree-AH index (DOT_PRODUCT_DISTANCE) + public endpoint + deployment. Deployment takes tens of minutes and costs money while deployed."""
    from google.cloud import aiplatform
    from google.cloud.aiplatform.matching_engine.matching_engine_index_config import DistanceMeasureType
    aiplatform.init(project=project, location=region)
    idx = aiplatform.MatchingEngineIndex.create_tree_ah_index(display_name=name, dimensions=dim, approximate_neighbors_count=10, distance_measure_type=DistanceMeasureType.DOT_PRODUCT_DISTANCE, index_update_method="STREAM_UPDATE")
    ep = aiplatform.MatchingEngineIndexEndpoint.create(display_name=name + "-endpoint", public_endpoint_enabled=True)
    dep = name.replace("-", "_") + "_deployed"; ep.deploy_index(index=idx, deployed_index_id=dep)
    return {"HF_VERTEX_INDEX": idx.resource_name, "HF_VERTEX_INDEX_ENDPOINT": ep.resource_name, "HF_VERTEX_DEPLOYED_INDEX_ID": dep}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db"); ap.add_argument("--project", required=True); ap.add_argument("--region", default="us-central1")
    ap.add_argument("--dataset-uri"); ap.add_argument("--index", help="MatchingEngineIndex resource name (HF_VERTEX_INDEX)")
    ap.add_argument("--create-index", action="store_true"); ap.add_argument("--skip-index", action="store_true")
    a = ap.parse_args(argv)
    if a.create_index:
        for k, v in create_index(a.project, a.region).items(): print(f"{k}={v}")
        return 0
    if not a.db: ap.error("--db is required")
    if a.dataset_uri: print("dataset ->", upload_dataset(a.db, a.dataset_uri))
    if not a.skip_index:
        idx = a.index or os.environ.get("HF_VERTEX_INDEX") or ap.error("--index (or HF_VERTEX_INDEX) is required to publish embeddings")
        os.environ.setdefault("HF_VERTEX_DEPLOYED_INDEX_ID", "unused-at-publish")
        publish_index(load_chunks(a.db), VertexEmbedder(a.project, a.region), VertexVectorStore(index=__import__("google.cloud.aiplatform", fromlist=["x"]).MatchingEngineIndex(index_name=idx), project=a.project, location=a.region))
    return 0


if __name__ == "__main__": sys.exit(main())

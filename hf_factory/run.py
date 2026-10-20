"""Orchestrator STUB: blueprint JSON -> Data engine (s3-s7) -> API engine (s8) -> smoke test (s10) -> delivery manifest (s11).
Real orchestrator, interpreter, blueprint engine and infra engine (s1, s2, s9) are separate work."""
import argparse, json, os, sqlite3, tempfile, time, warnings
warnings.filterwarnings("ignore")
from . import blueprint, data_engine, smoke
from .api import app as api_app, build as api_build

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--blueprint", default="blueprints/s1_provider_demo.json"); ap.add_argument("--members", type=int, default=None, help="override member volume (dev: 1000)")
    ap.add_argument("--out", default="out/run"); ap.add_argument("--skip-tests", action="store_true"); ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True); t0 = time.time(); log = print
    # Stand-in for interpreter + blueprint engine: load the sample blueprint, optionally patching scale/seed.
    bp = blueprint.load(a.blueprint, members=a.members, seed=a.seed)
    json.dump(bp, open(f"{a.out}/blueprint.json", "w"), indent=1)
    vol = bp["data_spec"]["volumes"]
    log(f"[blueprint] {bp['blueprint_id']}  members={vol['members']:,} providers={vol['providers']} claims={vol['claims']:,} auths={vol['auths']:,}  hash={bp['blueprint_hash'][:23]}…")
    os.makedirs(f"{a.out}/jobs", exist_ok=True)
    # Write the per-stage job specs the real orchestrator would hand to each data worker (shape from the architecture doc).
    for st in blueprint.STAGES[:4]: json.dump(blueprint.job_spec(bp, st, []), open(f"{a.out}/jobs/{st['id']}.json", "w"), indent=1)
    db = f"{a.out}/factory.db"
    # Data engine (s3-s7). Hard stop if the data quality gate fails.
    log("[data engine] s3-s7"); res, rep, man = data_engine.run(bp, db, a.out)
    log(f"  -> {res['status']}: {man['total_rows']:,} rows, rules {man['rules_passed']}/{man['rules_total']}, privacy scan {res['privacy_scan']}")
    if res["status"] != "COMPLETED": raise SystemExit("data_quality_gate FAILED")
    log("[api engine] s8")
    # API engine (s8). Fault injection and rate limit come from the blueprint's api_spec, so the same blueprint gives the same behaviour.
    cfg = api_app.Config(db_path=db, state_path=f"{a.out}/api-state.db", audit_path=f"{a.out}/audit.jsonl", fault_enabled=True,
                         fault_error_pct=bp["api_spec"]["fault_injection"]["error_rate_pct"], fault_latency_p95_ms=bp["api_spec"]["fault_injection"]["latency_p95_ms"], rate_limit_rpm=bp["api_spec"]["rate_limit_rpm"])
    app = api_app.create_app(cfg); spec, tm = api_build.write_artifacts(app, f"{a.out}/api")
    n_ep = sum(1 for p in spec["paths"].values() for op in p.values() if op["operationId"] != "createAccessToken")
    # Contract tests run against the generated database; any failure aborts the run.
    ct = {"total": 0, "passed": 0, "failed": 0}
    if not a.skip_tests: ct = api_build.run_contract_tests(db)
    log(f"  -> {n_ep} business endpoints, {len(tm['tools'])} agent tools, contract tests {ct['passed']}/{ct['total']}")
    if ct["failed"]: raise SystemExit("contract tests FAILED\n" + ct["output"])
    # Capture one real request/response per use case from the dataset, to ship as documentation for agent builders.
    # sample calls (one per use case) captured from real data
    from fastapi.testclient import TestClient
    cfg2 = api_app.Config(db_path=db, state_path=tempfile.mktemp(), fault_enabled=False); c = TestClient(api_app.create_app(cfg2)); con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    H = {"Authorization": "Bearer " + c.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": cfg.client_id, "client_secret": cfg.client_secret}).json()["access_token"]}
    tin = lambda n: con.execute("SELECT tin FROM provider WHERE npi=?", (n,)).fetchone()[0]
    cl = con.execute("SELECT claim_id, billing_npi FROM claim_header WHERE scenario_id='PCS-03' LIMIT 1").fetchone(); au = con.execute("SELECT auth_id, requesting_npi FROM auth_request WHERE scenario_id='PAS-02' LIMIT 1").fetchone()
    pv = con.execute("SELECT npi, tin FROM provider WHERE provider_type='PCP' LIMIT 1").fetchone(); mm = con.execute("SELECT member_id FROM member_demographics WHERE scenario_id='PEV-02' LIMIT 1").fetchone()[0]
    samples = {"PCS": {"request": f"GET /v1/claims/{cl[0]}/status?npi={cl[1]}&tin={tin(cl[1])}", "response": c.get(f"/v1/claims/{cl[0]}/status", params={"npi": cl[1], "tin": tin(cl[1])}, headers=H).json()},
               "PAS": {"request": f"GET /v1/authorizations/{au[0]}/status?npi={au[1]}&tin={tin(au[1])}", "response": c.get(f"/v1/authorizations/{au[0]}/status", params={"npi": au[1], "tin": tin(au[1])}, headers=H).json()},
               "PEV": {"request": "POST /v1/eligibility/verify", "response": c.post("/v1/eligibility/verify", json={"npi": pv[0], "tin": pv[1], "date_of_service": "2026-10-01", "members": [{"member_id": mm}]}, headers=H).json()}}
    json.dump(samples, open(f"{a.out}/api/sample_calls.json", "w"), indent=1)
    s8 = dict(status="DEPLOYED(local)", openapi=f"{a.out}/api/openapi.yaml", tool_manifest=f"{a.out}/api/tools.json", endpoint_count=n_ep, tools_published=len(tm["tools"]), contract_tests=ct,
              fault_injection=bp["api_spec"]["fault_injection"], run=f"HF_DB={db} uvicorn hf_factory.api.app:from_env --factory --port 8080")
    json.dump(s8, open(f"{a.out}/s8-api.json", "w"), indent=1)
    # s10: the agent smoke gate replays every scenario through the API; the run is only READY if it passes.
    log("[smoke test] s10"); sm = smoke.run(db, log=lambda s: log(s)); json.dump(sm, open(f"{a.out}/s10-smoke.json", "w"), indent=1)
    log(f"  -> {sm['gate']}")
    if sm["status"] != "PASS": raise SystemExit("agent_smoke_gate FAILED")
    # s11 stand-in: the delivery manifest is what the orchestrator would pass back to the customer environment.
    delivery = dict(status="READY", dataset_id=man["dataset_id"], total_duration_s=round(time.time() - t0, 1),
                    endpoints=dict(local_start=s8["run"], openapi=s8["openapi"], tools=s8["tool_manifest"]), credentials=dict(type="OAuth2 client credentials", client_id=cfg.client_id, secret="env HF_CLIENT_SECRET (default is a demo value)"),
                    data=dict(db=db, total_rows=man["total_rows"], manifest=f"{a.out}/manifest.json", rules=f"{man['rules_passed']}/{man['rules_total']}", privacy_scan=res["privacy_scan"]),
                    quality=dict(agent_smoke_tests=sm["status"], contract_tests=f"{ct['passed']}/{ct['total']}"),
                    reproducibility=dict(blueprint_hash=man["blueprint_hash"], data_spec_hash=man["data_spec_hash"], root_seed=man["root_seed"], as_of_date=man["as_of_date"], check="python -m hf_factory.repro --members %d" % vol["members"]))
    json.dump(delivery, open(f"{a.out}/delivery.json", "w"), indent=1)
    log(f"[delivery] READY in {delivery['total_duration_s']}s  ->  {a.out}/delivery.json")

if __name__ == "__main__": main()

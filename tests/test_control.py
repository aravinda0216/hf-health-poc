"""Control-plane tests: job payload validation, stage ordering, idempotency, restart resume, and agreement with the one-shot run."""
import copy, tempfile, warnings
import pytest
warnings.filterwarnings("ignore")
from fastapi.testclient import TestClient
from hf_factory import blueprint, data_engine
from hf_factory.control.app import create_control_app

TOK = {"Authorization": "Bearer ctl-test"}
def mk(d, **kw): return TestClient(create_control_app(d, token="ctl-test", **kw))
def bp(members=300, seed=None): return blueprint.load("blueprints/s1_provider_demo.json", members=members, seed=seed)
def job(b, stage, run_id="run_t1"):
    st = next(s for s in blueprint.STAGES if s["id"] == stage); return blueprint.job_spec(b, st, [], run_id=run_id)
def post(c, stage, j, **kw): return c.post(f"/factory/data-engine/{stage}", json=j, headers=TOK, **kw)

@pytest.fixture(scope="module")
def full():
    """One complete run (s3..s8) shared by the read-only assertions."""
    d = tempfile.mkdtemp(); c = mk(d); b = bp(); out = {}
    for s in ("s3", "s4", "s5", "s6", "s7"): out[s] = post(c, s, job(b, s))
    out["s8"] = c.post("/factory/api-engine/s8", json=blueprint.api_job_spec(b, "run_t1"), headers=TOK)
    return c, b, out, d

def test_auth_required():
    c = mk(tempfile.mkdtemp()); r = c.post("/factory/data-engine/s3", json=job(bp(), "s3")); assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert c.get("/factory/health").status_code == 200

def test_full_run_succeeds(full):
    c, b, out, d = full
    assert all(out[s].status_code == 200 and out[s].json()["status"] == "COMPLETED" for s in ("s3", "s4", "s5", "s6", "s7")), {k: v.text[:200] for k, v in out.items()}
    s3 = out["s3"].json(); assert {e["entity"] for e in s3["entities"]} >= {"provider", "plan"} and all(len(e["checksum"]) == 16 for e in s3["entities"]) and set(s3["rule_checks"].values()) <= {"PASS", "DEFERRED_TO_S7"}
    s7 = out["s7"].json(); assert s7["validation"]["failed"] == 0 and s7["privacy_scan"] == "PASS" and s7["manifest"]["entity_checksums"]
    a = out["s8"].json(); assert a["status"] == "DEPLOYED" and a["contract_tests"]["failed"] == 0 and a["contract_tests"]["total"] == a["endpoint_count"] * 4 and a["tools_published"] == a["endpoint_count"]

def test_checksums_match_one_shot_run(full):
    c, b, out, d = full
    res, rep, man = data_engine.run(b, tempfile.mktemp(), None, log=lambda s: None)
    mine = out["s7"].json()["manifest"]["entity_checksums"]
    assert mine == {t: v["checksum"] for t, v in man["entity_checksums"].items()}      # staged control-plane run == CLI run

def test_stage_order_enforced():
    c = mk(tempfile.mkdtemp()); b = bp(); assert post(c, "s4", job(b, "s4")).status_code == 404       # unknown run
    assert post(c, "s3", job(b, "s3")).status_code == 200
    r = post(c, "s5", job(b, "s5")); assert r.status_code == 409 and r.json()["error"]["code"] == "STAGE_ORDER"
    r = c.post("/factory/api-engine/s8", json=blueprint.api_job_spec(b, "run_t1"), headers=TOK); assert r.status_code == 409

def test_idempotent_resubmission_and_conflict():
    c = mk(tempfile.mkdtemp()); b = bp(); j = job(b, "s3"); a = post(c, "s3", j); r = post(c, "s3", j)
    assert r.status_code == 200 and r.headers["idempotent-replay"] == "true" and r.json() == a.json()
    j2 = {**j, "edge_case_pct": 6.0}; r = post(c, "s3", j2); assert r.status_code == 422        # changes data_spec -> hash mismatch
    j3 = {**j, "entities": ["plan"]}; r = post(c, "s3", j3); assert r.status_code == 409 and r.json()["error"]["code"] == "PAYLOAD_CONFLICT"

def test_payload_validation():
    c = mk(tempfile.mkdtemp()); b = bp(); j = job(b, "s3")
    r = post(c, "s3", {**j, "stage_seed": j["stage_seed"] + 1}); assert r.status_code == 422 and r.json()["error"]["code"] == "SEED_MISMATCH" and "expected" in r.json()["error"]["details"]
    r = post(c, "s4", j); assert r.status_code == 422 and r.json()["error"]["code"] == "STAGE_MISMATCH"
    r = post(c, "s3", {**j, "job": "generate_members"}); assert r.json()["error"]["code"] == "JOB_MISMATCH"
    r = post(c, "s3", {**j, "data_spec_hash": "sha256:" + "0" * 64}); assert r.json()["error"]["code"] == "DATA_SPEC_MISMATCH"
    r = post(c, "s3", {**j, "generator_version": "9.9.9"}); assert r.json()["error"]["code"] == "VERSION_MISMATCH"
    bad = copy.deepcopy(j); del bad["volumes"]["members"]; r = post(c, "s3", bad); assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_PAYLOAD"
    r = post(c, "s9", j); assert r.status_code == 404

def test_row_limit_guard():
    c = mk(tempfile.mkdtemp(), max_members=200); r = post(c, "s3", job(bp(300), "s3")); assert r.status_code == 413 and r.json()["error"]["details"]["limit"] == 200

def test_run_conflict_on_other_spec():
    c = mk(tempfile.mkdtemp()); assert post(c, "s3", job(bp(300), "s3")).status_code == 200
    r = post(c, "s3", job(bp(300, seed=7), "s3")); assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_CONFLICT"

def test_restart_resumes_from_last_completed_stage():
    d = tempfile.mkdtemp(); b = bp(); c1 = mk(d)
    for s in ("s3", "s4"): assert post(c1, s, job(b, s)).status_code == 200
    c2 = mk(d)                                                  # new process, same runs dir
    for s in ("s5", "s6", "s7"): r = post(c2, s, job(b, s)); assert r.status_code == 200, r.text[:200]
    ref = tempfile.mkdtemp(); c3 = mk(ref)
    for s in ("s3", "s4", "s5", "s6", "s7"): r3 = post(c3, s, job(b, s))
    assert r.json()["manifest"]["entity_checksums"] == r3.json()["manifest"]["entity_checksums"]

def test_s8_selects_endpoints_from_payload():
    d = tempfile.mkdtemp(); c = mk(d); b = bp()
    for s in ("s3", "s4", "s5", "s6", "s7"): post(c, s, job(b, s))
    p = {**blueprint.api_job_spec(b, "run_t1"), "use_cases": ["PCS", "PAP"]}
    a = c.post("/factory/api-engine/s8", json=p, headers=TOK).json()
    assert a["status"] == "DEPLOYED" and a["operations"] == sorted(["verifyIdentity", "searchKnowledge", "getClaimStatus", "createAppeal", "getAppealStatus"]) and a["contract_tests"]["total"] == 20
    n = c.post("/factory/api-engine/s8", json={**p, "endpoints": ["getClaimStatus"]}, headers=TOK).json(); assert n["operations"] == ["getClaimStatus"] and n["revision"].endswith("00002")
    assert c.post("/factory/api-engine/s8", json={**p, "use_cases": ["XXX"]}, headers=TOK).json()["error"]["code"] == "UNKNOWN_USE_CASE"
    assert c.post("/factory/api-engine/s8", json={**p, "endpoints": ["getAppealStatus", "verifyEligibility"]}, headers=TOK).json()["error"]["code"] == "UNKNOWN_ENDPOINT"

def test_s8_idempotent_and_credentials_by_reference(full):
    c, b, out, d = full; p = blueprint.api_job_spec(b, "run_t1")
    r = c.post("/factory/api-engine/s8", json=p, headers=TOK); assert r.headers["idempotent-replay"] == "true"
    cr = c.get("/factory/runs/run_t1/credentials", headers=TOK).json(); assert cr["secret_ref"].startswith("env:") and "demo-secret" not in str(cr)
    run = c.get("/factory/runs/run_t1", headers=TOK).json(); assert run["state"] == "DEPLOYED" and run["stages"]["s7"]["status"] == "COMPLETED"
    arts = [a["path"] for a in c.get("/factory/runs/run_t1/artifacts", headers=TOK).json()["artifacts"]]; assert "api/openapi.yaml" in arts and "manifest.json" in arts and "api/tools.json" in arts

def test_failed_stage_can_be_retried(monkeypatch):
    c = mk(tempfile.mkdtemp()); b = bp(); post(c, "s3", job(b, "s3"))
    orig = data_engine.DataRun.s4; calls = {"n": 0}
    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1: orig(self); raise RuntimeError("worker died after a partial write")   # partial rows are on disk
        return orig(self)
    monkeypatch.setattr(data_engine.DataRun, "s4", flaky)
    r = post(c, "s4", job(b, "s4")); assert r.status_code == 500 and r.json()["error"]["code"] == "STAGE_FAILED"
    assert c.get("/factory/runs/run_t1", headers=TOK).json()["stages"]["s4"]["status"] == "FAILED"
    r = post(c, "s4", job(b, "s4")); assert r.status_code == 200 and r.json()["status"] == "COMPLETED"       # DB rebuilt deterministically, no duplicate rows
    assert [e["rows"] for e in r.json()["entities"] if e["entity"] == "member_demographics"] == [300]

def test_failed_quality_gate_blocks_api(monkeypatch):
    from hf_factory import rules
    c = mk(tempfile.mkdtemp()); b = bp()
    for s in ("s3", "s4", "s5", "s6"): post(c, s, job(b, s))
    monkeypatch.setattr(rules, "privacy_scan", lambda con: {"status": "FAIL"})
    r = post(c, "s7", job(b, "s7")); assert r.status_code == 200 and r.json()["status"] == "FAILED_GATE"
    r = c.post("/factory/api-engine/s8", json=blueprint.api_job_spec(b, "run_t1"), headers=TOK); assert r.status_code == 409 and r.json()["error"]["code"] == "GATE_FAILED"

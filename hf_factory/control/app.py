"""Factory control plane: the REST surface the orchestrator calls with per-stage job payloads.

  POST /factory/data-engine/{s3..s7}   run one data stage            GET /factory/runs, /factory/runs/{id}
  POST /factory/api-engine/s8          generate + 'deploy' the API   GET /factory/runs/{id}/credentials, /artifacts
Guarantees: bearer auth; payload schema (422); stage order and run identity (409); stage_seed and data_spec_hash recomputed and checked (422);
idempotent re-submission (same payload -> stored result, `Idempotent-Replay: true`; different payload for a finished stage -> 409); row-limit guard (413);
state persisted per run, so a restarted control plane resumes a run from its last completed stage."""
import hashlib, json, os, secrets, threading, time
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import blueprint, data_engine, rules, schema
from ..common import CATALOG_VERSION, GEN_VERSION, RULESET_VERSION, canon
from ..api import app as api_app, build as api_build, catalog, contract_gen
from . import payloads as P
from .store import Store, now

PREV = {"s3": None, "s4": "s3", "s5": "s4", "s6": "s5", "s7": "s6"}
STAGE_RULES = {"s3": ["R-04", "F-04", "P-02"], "s4": ["R-02", "R-05", "R-06", "T-01"], "s5": ["R-03", "R-07", "F-01", "F-02", "F-05", "T-02", "T-03", "T-04"], "s6": ["R-09", "R-10", "R-11", "T-08"]}
STAGE_TABLES = {
    "s3": ["plan", "provider", "practice_location", "appointment_slot", "network_participation", "ref_code"],
    "s4": ["member_demographics", "plan_enrollment", "pcp_assignment", "family_relationship", "member_accumulator", "risk_score", "care_gap", "sdoh_indicator"],
    "s5": ["auth_request", "auth_line", "auth_determination", "clinical_review_queue", "documentation_requirement", "claim_header", "claim_line", "claim_payment", "claim_denial", "claim_pend", "claim_adjustment", "appeal_record", "reprocessing_history", "pharmacy_claim_detail"],
    "s6": ["care_case", "transition_of_care", "knowledge_asset", "asset_chunk", "asset_link", "faq_entry", "agent_sop", "qa_golden"]}

class CtlError(Exception):
    def __init__(self, status, code, message, details=None): self.status, self.code, self.message, self.details = status, code, message, details

def create_control_app(runs_dir, token=None, max_members=None, public_base=None):
    token = token or os.environ.get("HF_CONTROL_TOKEN", "demo-control-token-change-me")
    max_members = max_members or int(os.environ.get("HF_MAX_MEMBERS", "250000"))
    public_base = public_base or os.environ.get("HF_PUBLIC_BASE_URL", "http://127.0.0.1:8080/v1")
    store = Store(runs_dir); handles = {}          # run_id -> live DataRun (in memory; rebuilt from disk after a restart)
    app = FastAPI(title="Healthcare AI Factory: Control Plane", version="0.2.0", description="Stage job endpoints for the Data engine (s3-s7) and API engine (s8). All data is synthetic.")

    @app.exception_handler(CtlError)
    async def _ctl(_, e): return JSONResponse(status_code=e.status, content={"is_synthetic": True, "error": {"code": e.code, "message": e.message, "details": e.details}})
    @app.exception_handler(RequestValidationError)
    async def _val(_, e): return JSONResponse(status_code=422, content={"is_synthetic": True, "error": {"code": "INVALID_PAYLOAD", "message": "Job payload failed schema validation", "details": json.loads(json.dumps(e.errors(), default=str))[:10]}})

    def auth(authorization: str = Header(None)):
        if not authorization or not authorization.startswith("Bearer ") or not secrets.compare_digest(authorization[7:], token):
            raise CtlError(401, "UNAUTHORIZED", "Missing or invalid control-plane token")

    def run_or_404(run_id):
        if not store.exists(run_id): raise CtlError(404, "RUN_NOT_FOUND", f"No run {run_id}")
        return store.load(run_id)

    def phash(model): return hashlib.sha256(canon(json.loads(model.model_dump_json())).encode()).hexdigest()

    def entities_result(con, stage):
        cs = rules.checksums(con, tables=set(STAGE_TABLES[stage]))
        return [{"entity": t, "rows": v["rows"], "checksum": v["checksum"]} for t, v in cs.items()]

    def rule_checks(dr, stage):
        # Per-stage view of the s7 rules: PASS/FAIL, or DEFERRED_TO_S7 when the rule has nothing to check yet because its data arrives in a later stage.
        rep = rules.build_rules(dr.con, dr.ctx, dr.vol)
        return {c: ("DEFERRED_TO_S7" if rep[c]["checked"] == 0 else rep[c]["status"]) for c in STAGE_RULES[stage] if c in rep}

    def handle(run, stage):
        """Live DataRun for the run. After a restart (or a failed attempt) rebuild: resume the DB if clean, otherwise replay the completed stages deterministically."""
        rid = run["run_id"]; h = handles.get(rid)
        if h is not None and not run["stages"].get(stage, {}).get("status") == "FAILED": return h
        if h is not None: h.close(); handles.pop(rid, None)
        bp = blueprint.from_job(run["job_s3"]); bp["blueprint_hash"] = run["blueprint_hash"]
        done = [s for s in P.DATA_STAGES if run["stages"].get(s, {}).get("status") == "COMPLETED" and P.DATA_STAGES.index(s) < P.DATA_STAGES.index(stage)]
        os.makedirs(store.dir(rid), exist_ok=True); dbp = os.path.join(store.dir(rid), "factory.db")
        if run["stages"].get(stage, {}).get("status") == "FAILED" or (done and not os.path.exists(dbp)):
            h = data_engine.DataRun(bp, dbp, store.dir(rid), log=lambda s: None)      # fresh DB, replay the earlier stages (deterministic)
            for s in done: getattr(h, s)()
        elif done: h = data_engine.DataRun(bp, dbp, store.dir(rid), log=lambda s: None, resume_done=done)
        else: h = data_engine.DataRun(bp, dbp, store.dir(rid), log=lambda s: None)
        handles[rid] = h; return h

    def validate_common(stage, j: P.DataJob):
        if j.stage != stage: raise CtlError(422, "STAGE_MISMATCH", f"Payload stage {j.stage} sent to {stage}")
        if j.job != P.JOB_NAMES[stage]: raise CtlError(422, "JOB_MISMATCH", f"Stage {stage} runs job {P.JOB_NAMES[stage]}, got {j.job}")
        exp = blueprint.stage_seed(j.root_seed, stage)
        if j.stage_seed != exp: raise CtlError(422, "SEED_MISMATCH", "stage_seed does not derive from root_seed", {"expected": exp, "received": j.stage_seed})
        for k, v in (("catalog_version", CATALOG_VERSION), ("rule_set_version", RULESET_VERSION), ("generator_version", GEN_VERSION)):
            if getattr(j, k) != v: raise CtlError(422, "VERSION_MISMATCH", f"{k} {getattr(j, k)} is not supported by this engine ({v})")
        if j.id_strategy != "UUIDv5(dataset_id, entity, seed, row_index)": raise CtlError(422, "ID_STRATEGY_UNSUPPORTED", "Only the UUIDv5 id strategy is supported")
        if j.volumes.members > max_members: raise CtlError(413, "ROW_LIMIT", f"members {j.volumes.members} exceeds this engine's limit {max_members}", {"limit": max_members})
        bp = blueprint.from_job(json.loads(j.model_dump_json()))
        if bp["data_spec"]["data_spec_hash"] != j.data_spec_hash:
            raise CtlError(422, "DATA_SPEC_MISMATCH", "data_spec_hash does not match the data-defining fields of the payload", {"computed": bp["data_spec"]["data_spec_hash"]})

    @app.get("/factory/health", tags=["ops"], operation_id="health")
    def health(): return {"status": "ok", "version": app.version, "generator_version": GEN_VERSION, "catalog_version": CATALOG_VERSION, "rule_set_version": RULESET_VERSION, "runs": len(store.list())}

    @app.post("/factory/data-engine/{stage}", dependencies=[Depends(auth)], tags=["data-engine"], operation_id="runDataStage", summary="Run one data-engine stage (s3..s7)")
    def run_data_stage(stage: str, j: P.DataJob, request: Request):
        from fastapi import Response
        if stage not in P.JOB_NAMES: raise CtlError(404, "UNKNOWN_STAGE", f"Data stages are {P.DATA_STAGES}")
        validate_common(stage, j)
        h = phash(j); rid = j.run_id
        with store.lock(rid):
            if stage == "s3":
                if store.exists(rid):
                    run = store.load(rid)
                    if run["data_spec_hash"] != j.data_spec_hash or run["root_seed"] != j.root_seed: raise CtlError(409, "RUN_CONFLICT", "run_id already exists with a different data specification")
                else:
                    run = dict(run_id=rid, dataset_id=j.dataset_id, state="RUNNING", created_at=now(), updated_at=now(), root_seed=j.root_seed, as_of_date=str(j.as_of_date),
                               data_spec_hash=j.data_spec_hash, blueprint_hash=j.blueprint_hash, volumes=j.volumes.model_dump(), use_cases=j.use_cases,
                               job_s3=json.loads(j.model_dump_json()), stages={}, api=None)
            else:
                run = run_or_404(rid)
                if run["data_spec_hash"] != j.data_spec_hash or run["root_seed"] != j.root_seed: raise CtlError(409, "RUN_CONFLICT", "payload does not belong to this run (data_spec_hash/root_seed differ)")
            prev = run["stages"].get(stage)
            if prev and prev["status"] in ("COMPLETED", "FAILED_GATE"):
                if prev["payload_hash"] == h:
                    return JSONResponse(prev["result"], headers={"Idempotent-Replay": "true"})
                raise CtlError(409, "PAYLOAD_CONFLICT", f"{stage} already ran for this run with a different payload", {"stored_payload_hash": prev["payload_hash"]})
            if run["state"] == "FAILED_GATE": raise CtlError(409, "GATE_FAILED", "The data quality gate failed for this run")
            if PREV[stage] and run["stages"].get(PREV[stage], {}).get("status") != "COMPLETED":
                raise CtlError(409, "STAGE_ORDER", f"{stage} requires {PREV[stage]} to be COMPLETED first", {"stages": {k: v["status"] for k, v in run["stages"].items()}})
            t0 = time.time(); started = now()
            try:
                dr = handle(run, stage)
                out = getattr(dr, stage)()
                if stage == "s7":
                    res = {**dr.result, "validation": {**dr.result["validation"], "report": os.path.join(store.dir(rid), "rule-report.json")},
                           "manifest_uri": os.path.join(store.dir(rid), "manifest.json"), "manifest": {k: dr.man[k] for k in ("blueprint_hash", "data_spec_hash", "root_seed", "as_of_date", "catalog_version", "generator_version", "rule_set_version")},
                           "entity_checksums": {t: v["checksum"] for t, v in dr.man["entity_checksums"].items()}, "total_rows": dr.man["total_rows"]}
                    res["manifest"]["entity_checksums"] = res.pop("entity_checksums")
                    status = res["status"]; res.pop("db", None)
                else:
                    res = {"entities": entities_result(dr.con, stage), "rule_checks": rule_checks(dr, stage)}; status = "COMPLETED"
                    if any(v == "FAIL" for v in res["rule_checks"].values()): status = "FAILED_GATE"
                result = {"run_id": rid, "stage": stage, "job": P.JOB_NAMES[stage], "status": status, "started_at": started, "finished_at": now(), "duration_s": round(time.time() - t0, 2),
                          "root_seed": j.root_seed, "stage_seed": j.stage_seed, "loaded_to": "SQLite", "database": os.path.join(store.dir(rid), "factory.db"), **res}
                run["stages"][stage] = dict(status=status, payload_hash=h, result=result)
                if status == "FAILED_GATE": run["state"] = "FAILED_GATE"
                elif stage == "s7": run["state"] = "DATA_READY"
            except CtlError: raise
            except Exception as e:
                run["stages"][stage] = dict(status="FAILED", payload_hash=h, result=None, error=repr(e)[:500]); run["state"] = "FAILED"; run["updated_at"] = now(); store.save(run)
                raise CtlError(500, "STAGE_FAILED", f"{stage} failed: {e!r}"[:300], {"retry": "re-post the same payload; completed stages are replayed deterministically"})
            run["updated_at"] = now()
            if run["state"] == "FAILED": run["state"] = "RUNNING"
            store.save(run)
            return JSONResponse(result)

    @app.post("/factory/api-engine/s8", dependencies=[Depends(auth)], tags=["api-engine"], operation_id="runApiStage", summary="Generate, test and 'deploy' the API (s8)")
    def run_api_stage(j: P.ApiJob):
        bad = [u for u in j.use_cases if u not in catalog.USE_CASE_OPS]
        if bad: raise CtlError(422, "UNKNOWN_USE_CASE", f"Unknown use case(s) {bad}", {"known": sorted(catalog.USE_CASE_OPS)})
        ops = catalog.ops_for(j.use_cases)
        if j.endpoints:
            unknown = [e for e in j.endpoints if e not in ops]
            if unknown: raise CtlError(422, "UNKNOWN_ENDPOINT", f"Endpoints {unknown} are not part of use cases {j.use_cases}", {"available": sorted(ops - {"createAccessToken"})})
            ops = {"createAccessToken", *j.endpoints}
        h = phash(j)
        with store.lock(j.run_id):
            run = run_or_404(j.run_id)
            if run["state"] == "FAILED_GATE": raise CtlError(409, "GATE_FAILED", "The data quality gate failed for this run")
            if run["stages"].get("s7", {}).get("status") != "COMPLETED": raise CtlError(409, "STAGE_ORDER", "s8 requires s7 to be COMPLETED first")
            if run["api"] and run["api"]["payload_hash"] == h and run["api"]["result"]["status"] == "DEPLOYED":
                return JSONResponse(run["api"]["result"], headers={"Idempotent-Replay": "true"})
            t0 = time.time(); d = store.dir(j.run_id); db = os.path.join(d, "factory.db"); out = os.path.join(d, "api")
            cfg = api_app.Config(db_path=db, state_path=os.path.join(d, "api-state.db"), audit_path=os.path.join(d, "audit.jsonl"), fault_enabled=True,
                                 fault_error_pct=j.fault_injection.error_rate_pct, fault_latency_p95_ms=j.fault_injection.latency_p95_ms, rate_limit_rpm=j.rate_limit.per_client_rpm, enabled_ops=ops)
            spec, tm = api_build.write_artifacts(api_app.create_app(cfg), out)
            ct = contract_gen.run(db, spec, tm, ops)
            n_ep = len(ops - {"createAccessToken"}); rev = (run["api"]["revision_n"] + 1) if run["api"] else 1
            ok = ct["failed"] == 0 and len(tm["tools"]) == n_ep
            result = {"run_id": j.run_id, "stage": "s8", "job": j.job, "status": "DEPLOYED" if ok else "CONTRACT_TESTS_FAILED", "duration_s": round(time.time() - t0, 2),
                      "gateway_url": public_base, "revision": f"api-{j.run_id}-{rev:05d}", "openapi_uri": os.path.join(out, "openapi.yaml"), "tool_manifest_uri": os.path.join(out, "tools.json"),
                      "endpoint_count": n_ep, "tools_published": len(tm["tools"]), "operations": sorted(ops - {"createAccessToken"}),
                      "contract_tests": {"total": ct["total"], "passed": ct["passed"], "failed": ct["failed"], "failures": ct["failures"][:10]},
                      "fault_injection": j.fault_injection.model_dump(), "rate_limit": j.rate_limit.model_dump(),
                      "run_command": f"HF_DB={db} HF_STATE_DB={cfg.state_path} HF_ENABLED_OPS={','.join(sorted(ops))} HF_FAULT_ERROR_PCT={j.fault_injection.error_rate_pct} HF_FAULT_P95_MS={j.fault_injection.latency_p95_ms} HF_RPM={j.rate_limit.per_client_rpm} python -m uvicorn hf_factory.api.app:from_env --factory --port 8080"}
            run["api"] = dict(payload_hash=h, revision_n=rev, result=result); run["state"] = "DEPLOYED" if ok else "API_FAILED"; run["updated_at"] = now(); store.save(run)
            return JSONResponse(result)

    @app.get("/factory/runs", dependencies=[Depends(auth)], tags=["runs"], operation_id="listRuns")
    def list_runs(): return {"runs": store.list()}

    @app.get("/factory/runs/{run_id}", dependencies=[Depends(auth)], tags=["runs"], operation_id="getRun")
    def get_run(run_id: str):
        r = run_or_404(run_id)
        return {k: r[k] for k in ("run_id", "dataset_id", "state", "created_at", "updated_at", "root_seed", "as_of_date", "data_spec_hash", "blueprint_hash", "volumes")} | {
            "stages": {s: {"status": v["status"], "duration_s": (v.get("result") or {}).get("duration_s")} for s, v in r["stages"].items()},
            "api": (r["api"] or {}).get("result")}

    @app.get("/factory/runs/{run_id}/credentials", dependencies=[Depends(auth)], tags=["runs"], operation_id="getRunCredentials", summary="API client credentials by reference (the secret value is never returned)")
    def creds(run_id: str):
        r = run_or_404(run_id)
        if not r["api"]: raise CtlError(409, "STAGE_ORDER", "No API deployed for this run yet")
        return {"run_id": run_id, "type": "OAuth2 client credentials", "client_id": "hf-demo-client", "secret_ref": "env:HF_CLIENT_SECRET", "token_url": public_base + "/oauth/token"}

    @app.get("/factory/runs/{run_id}/artifacts", dependencies=[Depends(auth)], tags=["runs"], operation_id="listRunArtifacts")
    def artifacts(run_id: str):
        run_or_404(run_id); d = store.dir(run_id); out = []
        for root, _, fs in os.walk(d):
            for f in sorted(fs):
                if f.endswith(".tmp") or f == "run.json" or f.endswith(".state") or f == "api-state.db": continue
                p = os.path.join(root, f); out.append({"path": os.path.relpath(p, d), "bytes": os.path.getsize(p)})
        return {"run_id": run_id, "artifacts": sorted(out, key=lambda x: x["path"])}

    return app

def from_env():
    return create_control_app(os.environ.get("HF_RUNS_DIR", "out/runs"))

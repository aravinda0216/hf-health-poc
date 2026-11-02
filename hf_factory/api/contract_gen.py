"""Contract tests generated at s8 from the OpenAPI spec: four checks per business endpoint (as the architecture doc specifies).
  1 auth_required         no bearer token -> 401 with the standard error body
  2 invalid_rejected      malformed request -> 4xx with the standard error body (never a 5xx, never data)
  3 response_conforms     a valid request returns 2xx and the body validates against the OpenAPI response schema, is_synthetic=true
  4 tool_published        the operation is in the agent tool manifest with an input schema and guardrail block
Happy-path inputs come from SAMPLES, which look up seeded scenario rows in the generated database."""
import sqlite3, tempfile
from fastapi.testclient import TestClient
import jsonschema
from . import app as A
from .build import resolve

def _one(con, sql, *a): return con.execute(sql, a).fetchone()

def provider_samples(con):
    tin = lambda n: _one(con, "SELECT tin FROM provider WHERE npi=?", n)[0]
    cl = lambda scn: _one(con, "SELECT claim_id, billing_npi FROM claim_header WHERE scenario_id=? ORDER BY row_idx LIMIT 1", scn)
    au = lambda scn: _one(con, "SELECT auth_id, requesting_npi FROM auth_request WHERE scenario_id=? ORDER BY row_idx LIMIT 1", scn)
    S = {}
    pv = _one(con, "SELECT npi, tin FROM provider WHERE provider_type='PCP' ORDER BY row_idx LIMIT 1")
    S["verifyIdentity"] = dict(method="POST", path="/v1/identity/verify", json={"role": "PROVIDER", "npi": pv[0], "tin": pv[1]}, bad=dict(json={"role": "NOPE"}), ok=200)
    c = cl("PCS-01"); S["getClaimStatus"] = dict(method="GET", path=f"/v1/claims/{c[0]}/status", params={"npi": c[1], "tin": tin(c[1])}, bad=dict(params={}), ok=200)
    a = au("PAS-03"); S["getAuthorizationStatus"] = dict(method="GET", path=f"/v1/authorizations/{a[0]}/status", params={"npi": a[1], "tin": tin(a[1])}, bad=dict(params={}), ok=200)
    S["submitAuthorizationDocument"] = dict(method="POST", path=f"/v1/authorizations/{a[0]}/documents", json={"npi": a[1], "tin": tin(a[1]), "doc_type": "PT_EVAL", "file_name": "eval.pdf", "size_bytes": 1234, "sha256": "a" * 64}, bad=dict(json={"npi": a[1]}), ok=201)
    m = _one(con, "SELECT member_id FROM member_demographics WHERE scenario_id='PEV-01' LIMIT 1")[0]
    S["verifyEligibility"] = dict(method="POST", path="/v1/eligibility/verify", json={"npi": pv[0], "tin": pv[1], "date_of_service": "2026-10-01", "members": [{"member_id": m}]}, bad=dict(json={"npi": pv[0]}), ok=200)
    S["searchKnowledge"] = dict(method="GET", path="/v1/knowledge/search", params={"q": "missing information claim"}, bad=dict(params={"q": "x"}), ok=200)
    p = cl("PAP-01"); S["createAppeal"] = dict(method="POST", path=f"/v1/claims/{p[0]}/appeals", json={"npi": p[1], "tin": tin(p[1]), "level": "L1", "reason": "MED_NECESSITY", "documents": ["doc-1"]}, bad=dict(json={"npi": p[1]}), ok=201)
    ap = _one(con, "SELECT a.appeal_id, h.billing_npi FROM appeal_record a JOIN claim_header h ON h.claim_id=a.claim_id WHERE a.scenario_id='PAP-02' LIMIT 1")
    S["getAppealStatus"] = dict(method="GET", path=f"/v1/appeals/{ap[0]}", params={"npi": ap[1], "tin": tin(ap[1])}, bad=dict(params={}), ok=200)
    pp = cl("PPI-01"); S["getClaimPayments"] = dict(method="GET", path=f"/v1/claims/{pp[0]}/payments", params={"npi": pp[1], "tin": tin(pp[1])}, bad=dict(params={}), ok=200)
    S["createPaymentCorrection"] = dict(method="POST", path=f"/v1/claims/{pp[0]}/payment-corrections", json={"npi": pp[1], "tin": tin(pp[1]), "issue_type": "MISDIRECTED_PAYMENT"}, bad=dict(json={"npi": pp[1]}), ok=201)
    n = _one(con, "SELECT npi FROM provider WHERE scenario_id='PON-03' LIMIT 1")[0]; loc = _one(con, "SELECT location_id FROM practice_location WHERE npi=? AND availability_loaded=0", n)[0]
    S["getProviderOnboarding"] = dict(method="GET", path=f"/v1/providers/{n}/onboarding", params={"tin": tin(n)}, bad=dict(params={}), ok=200)
    S["updateLocationAvailability"] = dict(method="PUT", path=f"/v1/providers/{n}/locations/{loc}/availability", json={"tin": tin(n), "accepting_new_patients": True, "schedule": [{"day": "MON", "open": "08:00", "close": "17:00"}]}, bad=dict(json={"tin": tin(n)}), ok=200)
    return S

SAMPLE_BUILDERS = [provider_samples]   # member_samples is appended by api/member_contract.py when the member use cases are present

def _error_ok(r):
    j = r.json()
    return isinstance(j.get("error"), dict) and bool(j["error"].get("code")) and bool(j["error"].get("message")) and j.get("is_synthetic") is True

def run(db_path, spec, tools, enabled_ops, log=print):
    """Run the four generated checks for every enabled business operation. Returns {total, passed, failed, failures[], per_operation{}}."""
    con = sqlite3.connect(db_path); S = {}
    for b in SAMPLE_BUILDERS: S.update(b(con))
    # Isolated app + state DB so write endpoints do not touch any other state; fault injection off so results are deterministic.
    cl = TestClient(A.create_app(A.Config(db_path=db_path, state_path=tempfile.mktemp(), fault_enabled=False, enabled_ops=set(enabled_ops))))
    tok = cl.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": A.Config(db_path="x").client_secret}).json()["access_token"]
    H = {"Authorization": "Bearer " + tok}
    by_id = {op["operationId"]: (path, m, op) for path, ops in spec["paths"].items() for m, op in ops.items()}
    tool_names = {t["name"]: t for t in tools["tools"]}
    out = {"total": 0, "passed": 0, "failed": 0, "failures": [], "per_operation": {}}
    def rec(op, name, ok, why=""):
        out["total"] += 1; out["passed" if ok else "failed"] += 1; out["per_operation"].setdefault(op, {})[name] = "PASS" if ok else "FAIL"
        if not ok: out["failures"].append(f"{op}.{name}: {why}")
    for op in sorted(o for o in enabled_ops if o != "createAccessToken"):
        s = S.get(op)
        if op not in by_id or s is None:
            rec(op, "response_conforms", False, "operation missing from spec or no sample"); continue
        path, method, o = by_id[op]
        call = lambda hdr=None, **kw: cl.request(s["method"], s["path"], headers=hdr, **{k: v for k, v in kw.items()})
        base = {k: s[k] for k in ("params", "json") if k in s}
        # 1 auth required
        r = call(**base); rec(op, "auth_required", r.status_code == 401 and _error_ok(r), f"status {r.status_code}")
        # 2 invalid request rejected with the standard error body
        r = call(H, **{**{k: v for k, v in base.items() if k not in s["bad"]}, **s["bad"]}); rec(op, "invalid_rejected", 400 <= r.status_code < 500 and _error_ok(r), f"status {r.status_code} {r.text[:120]}")
        # 3 valid request conforms to the declared response schema
        r = call(H, **base)
        ok = r.status_code == s["ok"]; why = f"status {r.status_code} expected {s['ok']}"
        if ok:
            sch = o["responses"].get(str(s["ok"]), {}).get("content", {}).get("application/json", {}).get("schema")
            try:
                if sch: jsonschema.validate(r.json(), {**sch, "components": spec["components"]})  # validate against the raw schema so nullable fields stay legal
                ok = r.json().get("is_synthetic") is True
                why = "is_synthetic missing"
            except jsonschema.ValidationError as e: ok, why = False, "schema: " + e.message[:150]
        rec(op, "response_conforms", ok, why)
        # 4 published as an agent tool with an input schema and a guardrail block
        t = tool_names.get(op); rec(op, "tool_published", bool(t and t["input_schema"]["properties"] is not None and "guardrails" in t), "not in tools.json")
    con.close(); return out

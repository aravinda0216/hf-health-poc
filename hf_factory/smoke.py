"""Stage s10 stub: replay the 24 provider scenarios (PCS/PAS/PEV/PAP/PPI/PON x 4) against the API and check the expected behaviour."""
import json, sqlite3, tempfile, time, warnings
warnings.filterwarnings("ignore")
from fastapi.testclient import TestClient
from .api.app import create_app, Config

# Agent smoke gate thresholds: minimum groundedness %, minimum correct-escalation %, and maximum p95 latency in ms.
THRESH = dict(grounded_pct_min=95, escalation_correct_pct_min=95, p95_latency_ms_max=2500)

# Replays every seeded scenario through the real API (in process) and checks the behaviour a good agent would rely on.
# Returns per-use-case results; the orchestrator treats the run as failed unless every scenario passes and the thresholds are met.
def run(db_path, log=print):
    app = create_app(Config(db_path=db_path, state_path=tempfile.mktemp(), fault_enabled=False)); c = TestClient(app)
    tok = c.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "demo-secret-change-me"}).json()["access_token"]
    H = {"Authorization": "Bearer " + tok}
    # Scenario rows are looked up by scenario_id in the dataset, so the tests work at any scale.
    db = sqlite3.connect(db_path); db.row_factory = sqlite3.Row
    q = lambda sql, *a: db.execute(sql, a).fetchone()
    tin = lambda n: q("SELECT tin FROM provider WHERE npi=?", n)[0]
    lat = []
    def call(m, url, **kw):
        t = time.perf_counter(); r = getattr(c, m)(url, headers=H, **kw); lat.append((time.perf_counter() - t) * 1000); return r
    def claim(scn, where=""): return q(f"SELECT claim_id, billing_npi FROM claim_header WHERE scenario_id=? {where} LIMIT 1", scn)
    def cs(scn, where="", wrong=False):
        cl = claim(scn, where); t = "99-0000000" if wrong else tin(cl[1])
        return call("get", f"/v1/claims/{cl[0]}/status", params={"npi": cl[1], "tin": t})
    def auth(scn): return q("SELECT auth_id, requesting_npi FROM auth_request WHERE scenario_id=? ORDER BY row_idx LIMIT 1", scn)
    def ast(a): return call("get", f"/v1/authorizations/{a[0]}/status", params={"npi": a[1], "tin": tin(a[1])})
    prov = q("SELECT npi, tin FROM provider WHERE provider_type='PCP' LIMIT 1")
    def elig(mid, dos="2026-10-01"): return call("post", "/v1/eligibility/verify", json={"npi": prov[0], "tin": prov[1], "date_of_service": dos, "members": [{"member_id": mid}]})
    mem = lambda scn: q("SELECT member_id FROM member_demographics WHERE scenario_id=? ORDER BY row_idx LIMIT 1", scn)[0]
    S = []  # (code, name, fn -> (ok, grounded|None, escalation_ok|None))
    # Decorator that registers a scenario check: it returns (passed, grounded, escalation_correct); None means 'not applicable'.
    def sc(code, name):
        def deco(f): S.append((code, name, f)); return f
        return deco
    @sc("PCS-01", "Status by claim number")
    def _():
        j = cs("PCS-01").json(); return j["status"] == "PAID" and j["payment"]["amount"] == j["total_paid"] and j["payment"]["trace_no"], None, None
    @sc("PCS-02", "Pended for records")
    def _():
        j = cs("PCS-02").json(); p = j.get("pend") or {}; return p.get("reason") == "RECORDS_REQUEST" and p["info_needed_from"] == "PROVIDER" and p["expected_resolution_dt"] in p["guidance"], bool(j["sources"]), None
    @sc("PCS-03", "Denied CO-16 missing information")
    def _():
        j = cs("PCS-03").json(); d = j["denial"]; return d["carc"] == "CO-16" and d["line_no"] == 2 and not d["member_can_be_billed"] and any("frequency code 7" in a for a in j["next_actions"]), bool(j["sources"]), None
    @sc("PCS-04", "Caller fails verification")
    def _():
        r = cs("PCS-04", wrong=True); return r.status_code == 403 and r.json()["error"]["code"] == "VERIFICATION_FAILED" and "status" not in r.json(), None, True
    @sc("PAS-01", "Routine status check")
    def _():
        j = ast(auth("PAS-01")).json(); return j["status"] == "IN_REVIEW" and not j["missing_documents"] and j["hours_remaining"] > 0, bool(j["sources"]), None
    @sc("PAS-02", "Urgent SNF authorization at discharge")
    def _():
        a = auth("PAS-02"); j = ast(a).json()
        ok = j["urgency"] == "URGENT" and j["routing"] == "EXPEDITED_REVIEW" and any(m["doc_type"] == "DISCHARGE_SUMMARY" for m in j["missing_documents"])
        r = call("post", f"/v1/authorizations/{a[0]}/documents", json={"npi": a[1], "tin": tin(a[1]), "doc_type": "DISCHARGE_SUMMARY", "file_name": "ds.pdf", "size_bytes": 900, "sha256": "b" * 64})
        return ok and r.status_code == 201 and r.json()["routing"] == "EXPEDITED_REVIEW" and not ast(a).json()["missing_documents"], bool(j["next_steps"]), j["escalation_available"]
    @sc("PAS-03", "Missing PT evaluation")
    def _():
        a = auth("PAS-03"); j = ast(a).json(); ok = j["clock_paused"] and j["missing_documents"][0]["doc_type"] == "PT_EVAL"
        r = call("post", f"/v1/authorizations/{a[0]}/documents", json={"npi": a[1], "tin": tin(a[1]), "doc_type": "PT_EVAL", "file_name": "pt.pdf", "size_bytes": 900, "sha256": "c" * 64})
        return ok and r.json()["clock_paused_after"] is False, bool(j["sources"]), None
    @sc("PAS-04", "Denied; asks for reviewer details")
    def _():
        j = ast(auth("PAS-04")).json(); d = j["determination"]
        return d["outcome"] == "DENIED" and d["denial_rationale"] and d["reviewer"]["name"] is None and d["reviewer"]["role"] in ("MEDICAL_DIRECTOR", "SAME_SPECIALTY_PEER") and d["appeal_rights_sent_dt"] and any("peer-to-peer" in s for s in j["next_steps"]), bool(d["criteria"]), True
    @sc("PEV-01", "Active member check")
    def _():
        j = elig(mem("PEV-01")).json(); r0 = j["results"][0]; return r0["coverage_status"] == "ACTIVE" and r0["pcp"] and r0["reference_number"] and r0["prior_authorization"]["advanced_imaging_requires_pa"], None, None
    @sc("PEV-02", "Termed retroactively")
    def _():
        m = mem("PEV-02"); a = elig(m).json()["results"][0]; b = elig(m, "2026-10-12").json()["results"][0]
        return a["coverage_status"] == "ACTIVE" and any("retroactively" in w for w in a["warnings"]) and b["coverage_status"] == "TERMED" and "not a guarantee" in elig(m).json()["disclaimer"], None, None
    @sc("PEV-03", "Enrollment pending")
    def _():
        r0 = elig(mem("PEV-03")).json()["results"][0]; return r0["coverage_status"] == "PENDING" and any("pending" in w.lower() for w in r0["warnings"]), None, True
    @sc("PEV-04", "Bulk lookup attempt")
    def _():
        ms = [{"member_id": r[0]} for r in db.execute("SELECT member_id FROM member_demographics LIMIT 25")]
        r = call("post", "/v1/eligibility/verify", json={"npi": prov[0], "tin": prov[1], "date_of_service": "2026-10-01", "members": ms})
        return r.status_code == 413 and "270/271" in r.json()["error"]["message"], None, True
    # ---------------- PAP / PPI / PON (provider appeals, payment inquiry, onboarding)
    def ap(scn):
        a = q("SELECT a.appeal_id, h.billing_npi FROM appeal_record a JOIN claim_header h USING(claim_id) WHERE a.scenario_id=?", scn); return a
    def aps(scn):
        a = ap(scn); return call("get", f"/v1/appeals/{a[0]}", params={"npi": a[1], "tin": tin(a[1])}).json()
    def pays(scn, i=0):
        x = q("SELECT claim_id, billing_npi FROM claim_header WHERE scenario_id=? ORDER BY row_idx LIMIT 1 OFFSET ?", scn, i)
        return x, call("get", f"/v1/claims/{x[0]}/payments", params={"npi": x[1], "tin": tin(x[1])}).json()
    def onb(scn):
        n = q("SELECT npi FROM provider WHERE scenario_id=?", scn)[0]; return n, call("get", f"/v1/providers/{n}/onboarding", params={"tin": tin(n)}).json()
    @sc("PAP-01", "File a first-level appeal")
    def _():
        x = claim("PAP-01"); b = {"npi": x[1], "tin": tin(x[1]), "level": "L1", "reason": "MED_NECESSITY", "documents": ["doc-1"]}
        r = call("post", f"/v1/claims/{x[0]}/appeals", json=b); j = r.json(); r2 = call("post", f"/v1/claims/{x[0]}/appeals", json=b)
        return r.status_code == 201 and j["days_left_to_file"] == 90 and "clinical_notes" in j["documents_missing"] and r2.status_code == 409, bool(j["sources"]), None
    @sc("PAP-02", "Appeal status while in review")
    def _():
        j = aps("PAP-02"); return j["status"] == "IN_REVIEW" and j["expected_decision_dt"] and not j["documents_missing"], bool(j["sources"]), None
    @sc("PAP-03", "Overturned, awaiting reprocessing")
    def _():
        j = aps("PAP-03"); return j["outcome"] == "OVERTURNED" and j["reprocessing"]["state"] == "PENDING" and j["reprocessing"]["expected_reprocess_dt"], bool(j["sources"]), None
    @sc("PAP-04", "Late filing routes to exception review")
    def _():
        j = aps("PAP-04"); return j["late_filing"] and j["routing"] == "EXCEPTION_REVIEW" and j["no_outcome_promised"], bool(j["sources"]), True
    @sc("PPI-01", "Payment date, amount and trace")
    def _():
        _x, j = pays("PPI-01"); p = j["payments"][0]; return p["method"] == "EFT" and p["trace_no"] and p["amount"] == j["total_paid_gross"], bool(j["sources"]), None
    @sc("PPI-02", "Paid less than billed")
    def _():
        _x, j = pays("PPI-02"); return {a["group_code"] for a in j["adjustments"]} == {"CO", "PR"} and j["total_paid_gross"] < j["total_billed"], bool(j["sources"]), None
    @sc("PPI-03", "Recoupment offset on another remit")
    def _():
        _x, a = pays("PPI-03", 0); _y, b = pays("PPI-03", 1)
        return a["recoupments"][0]["role"] == "RECOUPED_FROM_THIS_CLAIM" and b["recoupments"][0]["role"] == "OFFSET_ON_THIS_REMIT" and a["net_paid_after_recoupment"] < a["total_paid_gross"], bool(a["sources"]), a["escalation_available"]
    @sc("PPI-04", "Wrong payee TIN; ticket, no self-service change")
    def _():
        x, j = pays("PPI-04"); r = call("post", f"/v1/claims/{x[0]}/payment-corrections", json={"npi": x[1], "tin": tin(x[1]), "issue_type": "WRONG_PAYEE_TIN"}); k = r.json()
        return j["payee_check"]["matches_billing_tin"] is False and r.status_code == 201 and k["queue"] == "PAYMENT_INTEGRITY" and "No change" in k["agent_actions"], bool(j["sources"]), True
    @sc("PON-01", "Application status and expected live date")
    def _():
        _n, j = onb("PON-01"); a = j["applications"][0]; return a["stage"] == "CONTRACT_SENT" and a["days_to_expected_live"] == 20, bool(j["sources"]), None
    @sc("PON-02", "Pending items with deadlines")
    def _():
        _n, j = onb("PON-02"); it = j["applications"][0]["pending_items"]; return {i["item"] for i in it} == {"W9", "CONTRACT"} and all(i["due_dt"] for i in it), bool(j["sources"]), None
    @sc("PON-03", "New location not in directory; submit availability")
    def _():
        n, j = onb("PON-03"); loc = q("SELECT location_id FROM practice_location WHERE npi=? AND availability_loaded=0", n)[0]
        r = call("put", f"/v1/providers/{n}/locations/{loc}/availability", json={"tin": tin(n), "accepting_new_patients": True, "schedule": [{"day": "MON", "open": "08:00", "close": "17:00"}]})
        return sum(l["needs_attention"] for l in j["locations"]) == 1 and r.status_code == 200 and r.json()["availability_loaded"], bool(j["sources"]), None
    @sc("PON-04", "Live but claims deny out of network")
    def _():
        _n, j = onb("PON-04"); k = j["claims_load_check"]; return k["lag_suspected"] and k["affected_claims"] >= 2 and k["escalation_queue"] == "NETWORK_OPERATIONS" and "Do not resubmit" in k["recommended_action"], bool(j["sources"]), j["escalation_recommended"]
    # Run all checks. An exception counts as a failure, not a crash. Then compute grounded %, escalation-correct % and p95 latency per use case.
    res, by = [], {}
    for code, name, f in S:
        try: ok, gr, esc = f(); ok = bool(ok)
        except Exception as e: ok, gr, esc = False, None, None; name += f" [error: {e!r}]"
        res.append(dict(scenario=code, name=name, passed=ok, grounded=gr, escalation_correct=esc)); by.setdefault(code[:3], []).append(res[-1])
    lat.sort(); p95 = lat[int(len(lat) * .95) - 1] if lat else 0
    summary = []
    for u, rs in by.items():
        g = [r["grounded"] for r in rs if r["grounded"] is not None]; e = [r["escalation_correct"] for r in rs if r["escalation_correct"] is not None]
        summary.append(dict(use_case=u, scenarios_run=len(rs), passed=sum(r["passed"] for r in rs), grounded_pct=round(100 * sum(g) / len(g)) if g else 100, escalation_correct_pct=round(100 * sum(e) / len(e)) if e else 100, p95_latency_ms=round(p95)))
    gate = all(s["passed"] == s["scenarios_run"] for s in summary) and all(s["grounded_pct"] >= THRESH["grounded_pct_min"] and s["escalation_correct_pct"] >= THRESH["escalation_correct_pct_min"] for s in summary) and p95 <= THRESH["p95_latency_ms_max"]
    out = dict(status="PASS" if gate else "FAIL", thresholds=THRESH, results=summary, scenarios=res, gate="agent_smoke_gate " + ("PASSED" if gate else "FAILED"))
    for r in res: log(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['scenario']}  {r['name']}")
    return out

"""Generate the Postman collection and environment from the dev dataset.
Scenario rows are the fixed low-index rows the data engine always seeds, and ids are UUIDv5 of (dataset, seed, index), so the
values are the same on any machine that builds the same blueprint. Re-run after `make demo`:  python3 postman/generate.py out/dev/factory.db"""
import json, sqlite3, sys, os

db = sys.argv[1] if len(sys.argv) > 1 else "out/dev/factory.db"
out = os.path.dirname(os.path.abspath(__file__))
c = sqlite3.connect(db)
one = lambda sql, *a: c.execute(sql, a).fetchone()
tin = lambda npi: one("SELECT tin FROM provider WHERE npi=?", npi)[0]

V = {}   # collection variables
def claim(var, idx):
    cid, npi = one("SELECT claim_id, billing_npi FROM claim_header WHERE row_idx=? AND claim_frequency_code='1'", idx)
    V[f"{var}_claim_id"], V[f"{var}_npi"], V[f"{var}_tin"] = cid, npi, tin(npi)
def auth(var, idx):
    aid, npi = one("SELECT auth_id, requesting_npi FROM auth_request WHERE row_idx=?", idx)
    V[f"{var}_auth_id"], V[f"{var}_npi"], V[f"{var}_tin"] = aid, npi, tin(npi)
def appeal(var, scn):
    apid, npi = one("SELECT a.appeal_id, h.billing_npi FROM appeal_record a JOIN claim_header h USING(claim_id) WHERE a.scenario_id=?", scn)
    V[f"{var}_appeal_id"], V[f"{var}_npi"], V[f"{var}_tin"] = apid, npi, tin(npi)
def prov(var, idx):
    npi = one("SELECT npi FROM provider WHERE row_idx=?", idx)[0]
    V[f"{var}_npi"], V[f"{var}_tin"] = npi, tin(npi)

for v, i in [("pcs01", 0), ("pcs02", 1), ("pcs03", 2), ("pcs04", 3), ("pap01", 8), ("ppi01", 12), ("ppi02", 13), ("ppi03", 15), ("ppi03b", 16), ("ppi04", 17), ("pon04claim", 18)]: claim(v, i)
for v, i in [("pas01", 0), ("pas02", 1), ("pas03", 2), ("pas04", 3)]: auth(v, i)
appeal("pap02", "PAP-02"); appeal("pap03", "PAP-03"); appeal("pap04", "PAP-04")
for v, i in [("pon01", 14), ("pon02", 15), ("pon03", 16), ("pon04", 17)]: prov(v, i)
prov("pev", 0)   # a participating PCP is the caller for eligibility checks
V["pev01_member_id"] = one("SELECT member_id FROM member_demographics WHERE row_idx=0")[0]
V["pev02_member_id"] = one("SELECT member_id FROM member_demographics WHERE row_idx=1")[0]
V["pev03_member_id"] = one("SELECT member_id FROM member_demographics WHERE row_idx=2")[0]
V["pon03_location_id"] = one("SELECT location_id FROM practice_location WHERE npi=? AND availability_loaded=0", V["pon03_npi"])[0]
V["pev_dos"] = "2026-10-01"

def url(path, query=None):
    raw = "{{base_url}}" + path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else "")
    return {"raw": raw, "host": ["{{base_url}}"], "path": [p for p in path.strip("/").split("/")],
            "query": [{"key": k, "value": v} for k, v in (query or [])]}

def req(name, method, path, query=None, body=None, tests=None, desc="", auth=True, form=None, headers=None, notes_vars=None):
    r = {"method": method, "header": [{"key": k, "value": v} for k, v in (headers or [])], "url": url(path, query), "description": desc}
    if auth is False: r["auth"] = {"type": "noauth"}
    if body is not None:
        r["header"].append({"key": "Content-Type", "value": "application/json"})
        r["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if form is not None:
        r["body"] = {"mode": "urlencoded", "urlencoded": [{"key": k, "value": v} for k, v in form]}
    item = {"name": name, "request": r}
    if tests: item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}]
    return item

def T(status, *checks):
    """Test script: status code plus extra assertions written against `j` (the parsed JSON body)."""
    ex = [f"pm.test('status is {status}', function () {{ pm.response.to.have.status({status}); }});", "const j = pm.response.json();",
          "pm.test('response is flagged synthetic', function () { pm.expect(j.is_synthetic).to.eql(true); });"]
    for label, expr in checks: ex.append(f"pm.test({json.dumps(label)}, function () {{ pm.expect({expr}).to.be.ok; }});")
    return ex

def id_q(p): return [("npi", "{{" + p + "_npi}}"), ("tin", "{{" + p + "_tin}}")]
def id_b(p): return {"npi": "{{" + p + "_npi}}", "tin": "{{" + p + "_tin}}"}

folders = []
def folder(name, items, desc=""): folders.append({"name": name, "description": desc, "item": items})

folder("0 Auth", [req("Create access token", "POST", "/v1/oauth/token", auth=False,
    form=[("grant_type", "client_credentials"), ("client_id", "{{client_id}}"), ("client_secret", "{{client_secret}}")],
    tests=["pm.test('status is 200', function () { pm.response.to.have.status(200); });", "const j = pm.response.json();",
           "pm.test('bearer token returned', function () { pm.expect(j.access_token).to.be.a('string'); });",
           "pm.collectionVariables.set('access_token', j.access_token); pm.collectionVariables.set('token_expiry', String(Date.now() + 3300000));"],
    desc="Optional: the collection pre-request script fetches and refreshes the token automatically.")],
    "The collection fetches a token by itself before the first call; run this only to inspect the token response.")

folder("1 Common", [
    req("Verify identity (provider)", "POST", "/v1/identity/verify", body={"role": "PROVIDER", **{"npi": "{{pcs01_npi}}", "tin": "{{pcs01_tin}}"}},
        tests=T(200, ("verified is true", "j.verified === true"), ("verification id returned", "j.verification_id"))),
    req("Verify identity (provider, wrong TIN)", "POST", "/v1/identity/verify", body={"role": "PROVIDER", "npi": "{{pcs01_npi}}", "tin": "99-0000000"},
        tests=T(200, ("not verified", "j.verified === false"), ("no verification id", "j.verification_id === null"))),
    req("Search knowledge (grounded)", "GET", "/v1/knowledge/search", [("q", "claim denied for missing information"), ("audience", "PROVIDER"), ("top_k", "3")],
        tests=T(200, ("not abstaining", "j.abstain === false"), ("results have citations", "j.results.length > 0 && j.results[0].chunk_id"))),
    req("Search knowledge (should abstain)", "GET", "/v1/knowledge/search", [("q", "What is the CEO's salary"), ("audience", "PROVIDER")],
        tests=T(200, ("abstains", "j.abstain === true"), ("no results returned", "j.results.length === 0"))),
])
folder("2 PCS claim status", [
    req("PCS-01 Paid claim", "GET", "/v1/claims/{{pcs01_claim_id}}/status", id_q("pcs01"), tests=T(200, ("status PAID", "j.status === 'PAID'"), ("payment trace present", "j.payment && j.payment.trace_no"))),
    req("PCS-02 Pended for records", "GET", "/v1/claims/{{pcs02_claim_id}}/status", id_q("pcs02"), tests=T(200, ("pend reason RECORDS_REQUEST", "j.pend && j.pend.reason === 'RECORDS_REQUEST'"), ("cites a source", "j.sources.length > 0"))),
    req("PCS-03 Denied, missing information", "GET", "/v1/claims/{{pcs03_claim_id}}/status", id_q("pcs03"), tests=T(200, ("denied CO-16", "j.denial && j.denial.carc === 'CO-16'"), ("member cannot be billed", "j.denial.member_can_be_billed === false"))),
    req("PCS-04 Wrong TIN (verification fails)", "GET", "/v1/claims/{{pcs04_claim_id}}/status", [("npi", "{{pcs04_npi}}"), ("tin", "99-0000000")],
        tests=T(403, ("VERIFICATION_FAILED", "j.error.code === 'VERIFICATION_FAILED'"), ("no claim data leaked", "j.status === undefined"))),
])
folder("3 PAS authorization status", [
    req("PAS-01 Routine, in review", "GET", "/v1/authorizations/{{pas01_auth_id}}/status", id_q("pas01"), tests=T(200, ("IN_REVIEW", "j.status === 'IN_REVIEW'"), ("hours remaining positive", "j.hours_remaining > 0"))),
    req("PAS-02 Urgent SNF, needs discharge summary", "GET", "/v1/authorizations/{{pas02_auth_id}}/status", id_q("pas02"), tests=T(200, ("urgent, expedited", "j.urgency === 'URGENT' && j.routing === 'EXPEDITED_REVIEW'"), ("discharge summary missing", "j.missing_documents.some(function (d) { return d.doc_type === 'DISCHARGE_SUMMARY'; })"))),
    req("PAS-03 Missing PT evaluation", "GET", "/v1/authorizations/{{pas03_auth_id}}/status", id_q("pas03"), tests=T(200, ("clock paused", "j.clock_paused === true"))),
    req("PAS-04 Denied, reviewer name withheld", "GET", "/v1/authorizations/{{pas04_auth_id}}/status", id_q("pas04"), tests=T(200, ("DENIED", "j.determination.outcome === 'DENIED'"), ("reviewer name is null", "j.determination.reviewer.name === null"))),
    req("Submit document (PAS-02 discharge summary)", "POST", "/v1/authorizations/{{pas02_auth_id}}/documents",
        body={**id_b("pas02"), "doc_type": "DISCHARGE_SUMMARY", "file_name": "discharge-summary.pdf", "size_bytes": 48211, "sha256": "b" * 64, "notes": "Discharge summary attached."},
        tests=T(201, ("upload id returned", "j.upload_id"), ("expedited routing kept", "j.routing === 'EXPEDITED_REVIEW'")), desc="Idempotent: sending the same file again returns the same upload id. Content is never read, only metadata."),
])
folder("4 PEV eligibility", [
    req("PEV-01 Active member", "POST", "/v1/eligibility/verify", body={**id_b("pev"), "date_of_service": "{{pev_dos}}", "members": [{"member_id": "{{pev01_member_id}}"}]},
        tests=T(200, ("ACTIVE", "j.results[0].coverage_status === 'ACTIVE'"), ("PCP and reference number present", "j.results[0].pcp && j.results[0].reference_number"))),
    req("PEV-02 Termed retroactively", "POST", "/v1/eligibility/verify", body={**id_b("pev"), "date_of_service": "2026-10-12", "members": [{"member_id": "{{pev02_member_id}}"}]},
        tests=T(200, ("TERMED on that date", "j.results[0].coverage_status === 'TERMED'"), ("not-a-guarantee disclaimer", "j.disclaimer.indexOf('not a guarantee') >= 0"))),
    req("PEV-03 Enrollment pending", "POST", "/v1/eligibility/verify", body={**id_b("pev"), "date_of_service": "{{pev_dos}}", "members": [{"member_id": "{{pev03_member_id}}"}]},
        tests=T(200, ("PENDING", "j.results[0].coverage_status === 'PENDING'"))),
    req("PEV-04 Bulk lookup blocked (6 members)", "POST", "/v1/eligibility/verify", body={**id_b("pev"), "date_of_service": "{{pev_dos}}", "members": [{"member_id": "{{pev01_member_id}}"}] * 6},
        tests=T(413, ("BULK_LOOKUP_LIMIT", "j.error.code === 'BULK_LOOKUP_LIMIT'")), desc="Minimum necessary: at most 5 members per request."),
])
folder("5 PAP appeals", [
    req("PAP-01 File a level 1 appeal", "POST", "/v1/claims/{{pap01_claim_id}}/appeals", body={**id_b("pap01"), "level": "L1", "reason": "MED_NECESSITY", "documents": ["appeal_letter"]},
        tests=T(201, ("appeal id issued", "j.appeal_id && j.appeal_id.indexOf('APL-') === 0"), ("clinical notes still needed", "j.documents_missing.indexOf('clinical_notes') >= 0"), ("no outcome promised", "j.no_outcome_promised === true")),
        desc="One appeal per claim. A second call returns 409 APPEAL_EXISTS (see the next request); restart the API to clear state."),
    req("PAP-01 Repeat filing returns 409", "POST", "/v1/claims/{{pap01_claim_id}}/appeals", body={**id_b("pap01"), "level": "L1", "reason": "MED_NECESSITY", "documents": ["appeal_letter"]},
        tests=T(409, ("APPEAL_EXISTS", "j.error.code === 'APPEAL_EXISTS'")), desc="Run after the request above."),
    req("PAP-01 Level 2 is escalated", "POST", "/v1/claims/{{pap01_claim_id}}/appeals", body={**id_b("pap01"), "level": "L2", "reason": "MED_NECESSITY"},
        tests=T(409, ("LEVEL_NOT_SUPPORTED", "j.error.code === 'LEVEL_NOT_SUPPORTED'"))),
    req("PAP-02 Appeal in review", "GET", "/v1/appeals/{{pap02_appeal_id}}", id_q("pap02"), tests=T(200, ("IN_REVIEW", "j.status === 'IN_REVIEW'"), ("decision date given", "j.expected_decision_dt"))),
    req("PAP-03 Overturned, reprocessing pending", "GET", "/v1/appeals/{{pap03_appeal_id}}", id_q("pap03"), tests=T(200, ("OVERTURNED", "j.outcome === 'OVERTURNED'"), ("reprocessing pending", "j.reprocessing.state === 'PENDING'"))),
    req("PAP-04 Late filing, exception review", "GET", "/v1/appeals/{{pap04_appeal_id}}", id_q("pap04"), tests=T(200, ("late filing", "j.late_filing === true"), ("exception review", "j.routing === 'EXCEPTION_REVIEW'"), ("no outcome promised", "j.no_outcome_promised === true"))),
])
folder("6 PPI payment inquiry", [
    req("PPI-01 Payment date, amount and trace", "GET", "/v1/claims/{{ppi01_claim_id}}/payments", id_q("ppi01"), tests=T(200, ("EFT with trace", "j.payments[0].method === 'EFT' && j.payments[0].trace_no"), ("amount equals gross paid", "j.payments[0].amount === j.total_paid_gross"))),
    req("PPI-02 Paid less than billed", "GET", "/v1/claims/{{ppi02_claim_id}}/payments", id_q("ppi02"), tests=T(200, ("CO and PR adjustments", "j.adjustments.some(function (a) { return a.group_code === 'CO'; }) && j.adjustments.some(function (a) { return a.group_code === 'PR'; })"), ("paid below billed", "j.total_paid_gross < j.total_billed"))),
    req("PPI-03 Recoupment (original claim)", "GET", "/v1/claims/{{ppi03_claim_id}}/payments", id_q("ppi03"), tests=T(200, ("recouped from this claim", "j.recoupments[0].role === 'RECOUPED_FROM_THIS_CLAIM'"), ("net below gross", "j.net_paid_after_recoupment < j.total_paid_gross"))),
    req("PPI-03 Recoupment (offset remit)", "GET", "/v1/claims/{{ppi03b_claim_id}}/payments", id_q("ppi03b"), tests=T(200, ("offset on this remit", "j.recoupments[0].role === 'OFFSET_ON_THIS_REMIT'"))),
    req("PPI-04 Wrong payee TIN", "GET", "/v1/claims/{{ppi04_claim_id}}/payments", id_q("ppi04"), tests=T(200, ("payee does not match", "j.payee_check.matches_billing_tin === false"), ("TINs are masked", "j.payee_check.payee_tin_masked.indexOf('**-***') === 0"))),
    req("PPI-04 Open payment correction ticket", "POST", "/v1/claims/{{ppi04_claim_id}}/payment-corrections", body={**id_b("ppi04"), "issue_type": "WRONG_PAYEE_TIN", "description": "Payment went to a different TIN than our billing TIN."},
        tests=T(201, ("Payment Integrity queue", "j.queue === 'PAYMENT_INTEGRITY'"), ("no change made", "j.agent_actions.indexOf('No change') >= 0")), desc="Idempotent per claim and issue: a repeat returns the same ticket with already_open true."),
    req("PPI-01 Correction refused (no mismatch)", "POST", "/v1/claims/{{ppi01_claim_id}}/payment-corrections", body={**id_b("ppi01"), "issue_type": "WRONG_PAYEE_TIN"},
        tests=T(409, ("NO_MISMATCH_DETECTED", "j.error.code === 'NO_MISMATCH_DETECTED'"))),
])
folder("7 PON provider onboarding", [
    req("PON-01 Contract sent", "GET", "/v1/providers/{{pon01_npi}}/onboarding", [("tin", "{{pon01_tin}}")], tests=T(200, ("stage CONTRACT_SENT", "j.applications[0].stage === 'CONTRACT_SENT'"), ("expected live is an estimate", "j.applications[0].expected_live_is_estimate === true"))),
    req("PON-02 Pending W-9 and contract", "GET", "/v1/providers/{{pon02_npi}}/onboarding", [("tin", "{{pon02_tin}}")], tests=T(200, ("two pending items", "j.applications[0].pending_items.length === 2"), ("each has a due date", "j.applications[0].pending_items.every(function (i) { return i.due_dt; })"))),
    req("PON-03 Location needs availability", "GET", "/v1/providers/{{pon03_npi}}/onboarding", [("tin", "{{pon03_tin}}")], tests=T(200, ("one location needs attention", "j.locations.filter(function (l) { return l.needs_attention; }).length === 1"))),
    req("PON-03 Submit availability", "PUT", "/v1/providers/{{pon03_npi}}/locations/{{pon03_location_id}}/availability",
        body={"tin": "{{pon03_tin}}", "accepting_new_patients": True, "telehealth": False, "accessibility": ["WHEELCHAIR"], "schedule": [{"day": "MON", "open": "08:00", "close": "17:00"}, {"day": "TUE", "open": "08:00", "close": "17:00"}]},
        tests=T(200, ("availability loaded", "j.availability_loaded === true"), ("2 days recorded", "j.days_loaded === 2"))),
    req("PON-03 Invalid hours rejected", "PUT", "/v1/providers/{{pon03_npi}}/locations/{{pon03_location_id}}/availability",
        body={"tin": "{{pon03_tin}}", "accepting_new_patients": True, "schedule": [{"day": "MON", "open": "17:00", "close": "08:00"}]},
        tests=T(422, ("VALIDATION_ERROR", "j.error.code === 'VALIDATION_ERROR'"))),
    req("PON-04 Live but claims deny out of network", "GET", "/v1/providers/{{pon04_npi}}/onboarding", [("tin", "{{pon04_tin}}")], tests=T(200, ("lag suspected", "j.claims_load_check.lag_suspected === true"), ("escalate to Network Operations", "j.claims_load_check.escalation_queue === 'NETWORK_OPERATIONS'"), ("do not resubmit", "j.claims_load_check.recommended_action.indexOf('Do not resubmit') >= 0"))),
])
folder("8 Security and resilience", [
    req("No token returns 401", "GET", "/v1/claims/{{pcs01_claim_id}}/status", id_q("pcs01"), auth=False, tests=T(401, ("error code present", "j.error.code"))),
    req("Bad token returns 401", "GET", "/v1/claims/{{pcs01_claim_id}}/status", id_q("pcs01"), auth=False, headers=[("Authorization", "Bearer not-a-real-token")], tests=T(401, ("INVALID_TOKEN", "j.error.code === 'INVALID_TOKEN'"))),
    req("Unrelated provider gets 404", "GET", "/v1/claims/{{pcs01_claim_id}}/status", [("npi", "{{pon01_npi}}"), ("tin", "{{pon01_tin}}")], tests=T(404, ("CLAIM_NOT_FOUND", "j.error.code === 'CLAIM_NOT_FOUND'")), desc="A real provider that is not on the claim sees the same answer as a claim that does not exist."),
    req("Injected fault (503)", "GET", "/v1/claims/{{pcs01_claim_id}}/status", id_q("pcs01"), headers=[("x-inject-fault", "error")], tests=T(503, ("FAULT_INJECTED", "j.error.code === 'FAULT_INJECTED'")), desc="The x-inject-fault header forces a transient failure so an agent's retry logic can be tested."),
    req("Injected latency (800 ms)", "GET", "/v1/claims/{{pcs01_claim_id}}/status", id_q("pcs01"), headers=[("x-inject-fault", "latency:800")], tests=T(200, ("took at least 800 ms", "pm.response.responseTime >= 750"))),
])

prereq = ["// Fetch a bearer token once and refresh it before it expires (tokens last 1 hour).",
          "const exp = Number(pm.collectionVariables.get('token_expiry') || 0);",
          "const isTokenCall = pm.request.url.getPath().endsWith('/oauth/token');",
          "if (!isTokenCall && (!pm.collectionVariables.get('access_token') || Date.now() > exp)) {",
          "  pm.sendRequest({ url: pm.variables.replaceIn('{{base_url}}/v1/oauth/token'), method: 'POST',",
          "    header: { 'Content-Type': 'application/x-www-form-urlencoded' },",
          "    body: { mode: 'urlencoded', urlencoded: [{ key: 'grant_type', value: 'client_credentials' }, { key: 'client_id', value: pm.variables.replaceIn('{{client_id}}') }, { key: 'client_secret', value: pm.variables.replaceIn('{{client_secret}}') }] }",
          "  }, function (err, res) {",
          "    if (err || res.code !== 200) { console.error('Token request failed', err || res.text()); return; }",
          "    pm.collectionVariables.set('access_token', res.json().access_token);",
          "    pm.collectionVariables.set('token_expiry', String(Date.now() + 3300000));",
          "  });",
          "}"]
variables = [{"key": "base_url", "value": "http://localhost:8080"}, {"key": "client_id", "value": "hf-demo-client"}, {"key": "client_secret", "value": "demo-secret-change-me"},
             {"key": "access_token", "value": ""}, {"key": "token_expiry", "value": "0"}] + [{"key": k, "value": v} for k, v in V.items()]
coll = {"info": {"name": "Healthcare AI Factory: Provider API (S1)", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        "description": "Provider-facing synthetic API: claim status, authorization status, eligibility, appeals, payments and onboarding.\n\n1. Build the data: `make demo`\n2. Start the API: `make serve` (http://localhost:8080)\n3. Import this collection, then run it with the Collection Runner.\n\nRun the folders in order. A few requests change state (filing an appeal, submitting a document or availability, opening a ticket); restart `make serve` to reset that state. All data is synthetic."},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}]},
        "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": prereq}}],
        "variable": variables, "item": folders}
json.dump(coll, open(os.path.join(out, "hf-provider-api.postman_collection.json"), "w"), indent=2)
env = {"name": "HF Factory local", "values": [{"key": "base_url", "value": "http://localhost:8080", "enabled": True}, {"key": "client_id", "value": "hf-demo-client", "enabled": True}, {"key": "client_secret", "value": "demo-secret-change-me", "type": "secret", "enabled": True}], "_postman_variable_scope": "environment"}
json.dump(env, open(os.path.join(out, "hf-local.postman_environment.json"), "w"), indent=2)
n = sum(len(f["item"]) for f in folders)
print(f"{n} requests in {len(folders)} folders, {len(V)} scenario variables")

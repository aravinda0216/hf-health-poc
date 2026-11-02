"""Generate the member-facing Postman collection (MBN, MCI, MPS, MPC, MAS) from a dataset that includes member use cases (S2 or S4 blueprint, or the S1 dataset).
Run:  python3 postman/generate_member.py out/s2_medicaid_member/factory.db
Member calls are stateless: every call carries dob + last_name (and relationship_id + caregiver_name for a caregiver). Booking and PCP-change calls chain through test
scripts (a slot or PCP is picked from the search response), so run the folders top to bottom."""
import json, sqlite3, sys, os
db = sys.argv[1] if len(sys.argv) > 1 else "out/dev/factory.db"
out = os.path.dirname(os.path.abspath(__file__))
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
one = lambda sql, *a: c.execute(sql, a).fetchone()
V = {}
m = one("SELECT m.* FROM member_demographics m JOIN plan_enrollment e ON e.member_id=m.member_id AND e.effective_dt>='2026-01-01' AND e.status='ACTIVE' WHERE m.scenario_id='BASE' ORDER BY m.row_idx LIMIT 1")
V.update(member_id=m["member_id"], dob=m["dob"], last_name=m["last_name"])
for name, where in [("paid", "h.status='PAID'"), ("denied", "EXISTS (SELECT 1 FROM claim_denial d WHERE d.claim_id=h.claim_id AND d.liable_party='PROVIDER')"), ("pended", "h.status='PENDED'")]:
    r = one(f"SELECT m.member_id, m.dob, m.last_name, h.claim_id FROM claim_header h JOIN member_demographics m ON m.member_id=h.member_id WHERE {where} ORDER BY h.row_idx LIMIT 1")
    if r: V.update({f"{name}_member_id": r["member_id"], f"{name}_dob": r["dob"], f"{name}_last_name": r["last_name"], f"{name}_claim_id": r["claim_id"]})
cg = one("SELECT f.*, m.dob, m.last_name FROM family_relationship f JOIN member_demographics m ON m.member_id=f.member_id JOIN plan_enrollment e ON e.member_id=m.member_id AND e.status='ACTIVE' WHERE f.caregiver_flag=1 AND f.scenario_id='CAREGIVER' ORDER BY f.row_idx LIMIT 1")
if cg: V.update(cg_member_id=cg["member_id"], cg_dob=cg["dob"], cg_last_name=cg["last_name"], cg_relationship_id=cg["relationship_id"], cg_caregiver_name=cg["caregiver_name"])
V["pcp_npi"] = ""; V["slot_id"] = ""; V["appointment_id"] = ""; V["provider_npi"] = ""

def url(path, query=None):
    return {"raw": "{{base_url}}" + path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else ""), "host": ["{{base_url}}"], "path": path.strip("/").split("/"), "query": [{"key": k, "value": v} for k, v in (query or [])]}
def tests(status, *extra):
    ex = [f"pm.test('status is {status}', function () {{ pm.response.to.have.status({status}); }});", "const j = pm.response.json();"] + list(extra)
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": ex}}]
def req(name, method, path, query=None, body=None, ev=None, form=None, auth=True):
    r = {"method": method, "header": [], "url": url(path, query)}
    if not auth: r["auth"] = {"type": "noauth"}
    if body is not None: r["header"].append({"key": "Content-Type", "value": "application/json"}); r["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if form: r["body"] = {"mode": "urlencoded", "urlencoded": [{"key": k, "value": v} for k, v in form]}
    it = {"name": name, "request": r}
    if ev: it["event"] = ev
    return it
who = lambda p="": [("dob", "{{" + p + "dob}}"), ("last_name", "{{" + p + "last_name}}")]
whob = lambda p="": {"dob": "{{" + p + "dob}}", "last_name": "{{" + p + "last_name}}"}
cgq = [("dob", "{{cg_dob}}"), ("last_name", "{{cg_last_name}}"), ("relationship_id", "{{cg_relationship_id}}"), ("caregiver_name", "{{cg_caregiver_name}}")]
M = "/v1/members/{{member_id}}"
setv = lambda k, expr: f"pm.collectionVariables.set('{k}', {expr});"
F = []
F.append({"name": "0 Auth", "item": [req("Create access token", "POST", "/v1/oauth/token", auth=False, form=[("grant_type", "client_credentials"), ("client_id", "{{client_id}}"), ("client_secret", "{{client_secret}}")],
    ev=tests(200, "pm.test('token', function () { pm.expect(j.access_token).to.be.a('string'); }); pm.collectionVariables.set('access_token', j.access_token);"))]})
F.append({"name": "MBN Benefits", "item": [
    req("Plan benefits and accumulators", "GET", M + "/benefits", who(), ev=tests(200, "pm.test('member actor', function () { pm.expect(j.actor.type).to.eql('MEMBER'); });", "pm.test('oop max present', function () { pm.expect(j.cost_share.oop_max).to.be.above(0); });")),
    req("Wrong date of birth fails closed", "GET", M + "/benefits", [("dob", "1900-01-01"), ("last_name", "{{last_name}}")], ev=tests(403, "pm.test('VERIFICATION_FAILED', function () { pm.expect(j.error.code).to.eql('VERIFICATION_FAILED'); });")),
    req("Caregiver (consented scope)", "GET", "/v1/members/{{cg_member_id}}/benefits", cgq, ev=tests(200, "pm.test('caregiver actor', function () { pm.expect(j.actor.type).to.eql('CAREGIVER'); });"))]})
F.append({"name": "MCI Claims", "item": [
    req("List claims", "GET", M + "/claims", who(), ev=tests(200)),
    req("Paid claim (EOB is not a bill)", "GET", "/v1/members/{{paid_member_id}}/claims/{{paid_claim_id}}", who("paid_"), ev=tests(200, "pm.test('PAID', function () { pm.expect(j.status).to.eql('PAID'); });")),
    req("Denied, provider liable", "GET", "/v1/members/{{denied_member_id}}/claims/{{denied_claim_id}}", who("denied_"), ev=tests(200, "pm.test('not billed', function () { pm.expect(j.denial.you_should_not_be_billed).to.eql(true); });")),
    req("Pended claim", "GET", "/v1/members/{{pended_member_id}}/claims/{{pended_claim_id}}", who("pended_"), ev=tests(200, "pm.test('waiting on', function () { pm.expect(j.pend.waiting_on).to.be.ok; });"))]})
F.append({"name": "MPS Provider search", "item": [
    req("Nearest in-network providers", "GET", M + "/providers/search", who() + [("max_distance_miles", "200"), ("limit", "10")], ev=tests(200, setv("provider_npi", "(j.results[0] || {}).npi || ''"), "pm.test('in network only', function () { pm.expect(j.in_network_only).to.eql(true); });")),
    req("Spanish-speaking", "GET", M + "/providers/search", who() + [("language", "ES"), ("max_distance_miles", "200")], ev=tests(200)),
    req("No match: say so", "GET", M + "/providers/search", who() + [("specialty", "no-such-specialty")], ev=tests(200, "pm.test('count 0', function () { pm.expect(j.count).to.eql(0); });"))]})
F.append({"name": "MPC PCP", "item": [
    req("Current PCP", "GET", M + "/pcp", who(), ev=tests(200)),
    req("Find a PCP within 50 miles", "GET", M + "/providers/search", who() + [("provider_type", "PCP"), ("max_distance_miles", "49"), ("limit", "20")], ev=tests(200, setv("pcp_npi", "(j.results.find(r => r.panel_status !== 'CLOSED') || {}).npi || ''"))),
    req("Change PCP: dry run", "POST", M + "/pcp-changes", body={**whob(), "new_pcp_npi": "{{pcp_npi}}", "dry_run": True}, ev=tests(201, "pm.test('PREVIEW', function () { pm.expect(j.status).to.eql('PREVIEW'); });")),
    req("Change PCP: confirm", "POST", M + "/pcp-changes", body={**whob(), "new_pcp_npi": "{{pcp_npi}}", "reason": "Closer to home"}, ev=tests(201, "pm.test('SCHEDULED', function () { pm.expect(j.status).to.eql('SCHEDULED'); });"))]})
F.append({"name": "MAS Appointments", "item": [
    req("Find a provider", "GET", M + "/providers/search", who() + [("max_distance_miles", "200"), ("limit", "10")], ev=tests(200, setv("provider_npi", "(j.results[0] || {}).npi || ''"))),
    req("Open slots", "GET", M + "/appointment-slots", who() + [("npi", "{{provider_npi}}")], ev=tests(200, setv("slot_id", "(j.slots.find(s => s.start > '2026-10-14T12:00:00') || {}).slot_id || ''"))),
    req("Book: dry run", "POST", M + "/appointments", body={**whob(), "slot_id": "{{slot_id}}", "dry_run": True}, ev=tests(201)),
    req("Book", "POST", M + "/appointments", body={**whob(), "slot_id": "{{slot_id}}", "reason": "Annual wellness visit"}, ev=tests(201, setv("appointment_id", "j.appointment.appointment_id"))),
    req("List appointments", "GET", M + "/appointments", who(), ev=tests(200)),
    req("Cancel (outside 24h)", "DELETE", M + "/appointments/{{appointment_id}}", who(), ev=tests(200, "pm.test('slot released', function () { pm.expect(j.slot_released).to.eql(true); });"))]})
prereq = ["if (!pm.collectionVariables.get('access_token') && !pm.request.url.getPath().endsWith('/oauth/token')) {",
          " pm.sendRequest({ url: pm.variables.replaceIn('{{base_url}}/v1/oauth/token'), method: 'POST', header: { 'Content-Type': 'application/x-www-form-urlencoded' },",
          "  body: { mode: 'urlencoded', urlencoded: [{ key: 'grant_type', value: 'client_credentials' }, { key: 'client_id', value: pm.variables.replaceIn('{{client_id}}') }, { key: 'client_secret', value: pm.variables.replaceIn('{{client_secret}}') }] } },",
          "  function (e, r) { if (!e && r.code === 200) pm.collectionVariables.set('access_token', r.json().access_token); }); }"]
vars_ = [{"key": "base_url", "value": "http://localhost:8080"}, {"key": "client_id", "value": "hf-demo-client"}, {"key": "client_secret", "value": "demo-secret-change-me"}, {"key": "access_token", "value": ""}] + [{"key": k, "value": v} for k, v in V.items()]
coll = {"info": {"name": "Healthcare AI Factory: Member API (S2/S4)", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        "description": "Member-facing synthetic API. Stateless: every call carries dob + last_name. Run the folders top to bottom; booking chains through the slot search."},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}]}, "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": prereq}}], "variable": vars_, "item": F}
json.dump(coll, open(os.path.join(out, "hf-member-api.postman_collection.json"), "w"), indent=2)
print(sum(len(f["item"]) for f in F), "requests;", len(V), "variables")

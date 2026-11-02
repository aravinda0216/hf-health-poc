"""Generate a Postman collection for the blueprint-defined endpoints (api_spec.custom_endpoints) of any blueprint. Nothing here is specific to one entity:
requests, bodies and tests come from the entity and endpoint definitions plus rows of the generated dataset.
  python3 postman/generate_custom.py blueprints/s5_custom_appointments.json out/s5/factory.db
Serve the dataset with HF_CUSTOM_SPEC=out/s5/custom-spec.json (written by hf_factory.run). Run folders top to bottom; they chain through collection variables."""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from hf_factory import dynamic, schema
from hf_factory.dynamic import testgen

bp_path = sys.argv[1] if len(sys.argv) > 1 else "blueprints/s5_custom_appointments.json"; db_path = sys.argv[2] if len(sys.argv) > 2 else "out/s5/factory.db"
out = os.path.dirname(os.path.abspath(__file__)); bp = json.load(open(bp_path))
spec = dynamic.parse_spec(bp["data_spec"].get("custom_entities"), bp["api_spec"].get("custom_endpoints"), schema.core_cols())
if spec is None or not spec.endpoints: sys.exit("blueprint has no custom_endpoints")
sp = testgen.Sampler(db_path, spec, None, None); V = {}

def url(path, query=None):
    return {"raw": "{{base_url}}/v1" + path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else ""), "host": ["{{base_url}}"], "path": ["v1"] + path.strip("/").split("/"), "query": [{"key": k, "value": v} for k, v in (query or [])]}
def req(name, method, path, query=None, body=None, tests=None, desc=""):
    r = {"method": method, "header": [], "url": url(path, query), "description": desc}
    if body is not None: r["header"].append({"key": "Content-Type", "value": "application/json"}); r["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    it = {"name": name, "request": r}
    if tests: it["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}]
    return it
def T(status, *checks):
    ex = [f"pm.test('status is {status}', function () {{ pm.response.to.have.status({status}); }});", "const j = pm.response.json();"]
    ex += [f"pm.test({json.dumps(l)}, function () {{ pm.expect({e}).to.be.ok; }});" for l, e in checks]; return ex
setv = lambda k, expr: f"pm.collectionVariables.set('{k}', {expr});"
def sub(path, var, pname): return path.replace("{" + pname + "}", "{{" + var + "}}")

folders = [{"name": "0 Auth", "item": [req("Create access token", "POST", "/oauth/token", tests=T(200, ("bearer token", "typeof j.access_token === 'string'")) + [setv("access_token", "j.access_token")])]}]
folders[0]["item"][0]["request"]["auth"] = {"type": "noauth"}
folders[0]["item"][0]["request"]["header"] = []
folders[0]["item"][0]["request"]["body"] = {"mode": "urlencoded", "urlencoded": [{"key": "grant_type", "value": "client_credentials"}, {"key": "client_id", "value": "{{client_id}}"}, {"key": "client_secret", "value": "{{client_secret}}"}]}

for e in spec.entities:
    eps = [ep for ep in spec.endpoints if ep.entity.lower() == e.name.lower()]
    if not eps: continue
    pk = spec.pk(e).name; pname = lambda ep: pk if "{" + pk + "}" in ep.path else "id"; items = []; t = e.table
    ops = {ep.operation: ep for ep in eps}; ver = next((ep.verify_member for ep in eps if ep.verify_member), None)
    row = sp.row(e); V[f"{t}_row_id"] = str(row[pk]) if row else ""
    fac = sp.member_factors(row[ver]) if (row and ver) else {}
    if ver and row: V.update({f"{t}_member_id": row[ver], f"{t}_dob": fac["dob"], f"{t}_last_name": fac["last_name"]})
    F = [("dob", "{{" + t + "_dob}}"), ("last_name", "{{" + t + "_last_name}}")] if ver else []
    if "create" in ops:
        ep = ops["create"]; body = sp.create_body(ep)
        if ver and row:       # the body's member is the one verified in the other requests, so created rows show up in that member's list
            body = {**body, ver: "{{" + t + "_member_id}}", "dob": "{{" + t + "_dob}}", "last_name": "{{" + t + "_last_name}}"}
        V[f"{t}_body_note"] = "values come from the generator; dates are 1,000+ days after the as-of date so they cannot clash with generated rows"
        required = next((f.name for f in e.fields if not f.primary_key and not f.server_set and f.required and f.default is None and f.name in body and f.name != ver), None)
        items += [req(f"Create {e.name}: dry run", "POST", ep.path, [("dry_run", "true")], body, T(201, ("nothing saved is not asserted here, shape only", f"j.{pk}"))),
                  req(f"Create {e.name}", "POST", ep.path, None, body, T(201, (f"{pk} returned", f"j.{pk}"), ("flagged synthetic", "j.is_synthetic === true")) + [setv(f"{t}_id", f"j.{pk}")]),
                  req(f"Create {e.name}: repeat is idempotent", "POST", ep.path, None, body, T(201, ("same id", f"j.{pk} === pm.collectionVariables.get('{t}_id')")))]
        if required: items.append(req(f"Create {e.name}: missing {required} is rejected", "POST", ep.path, None, {k: v for k, v in body.items() if k != required}, T(422, ("standard error body", "j.error && j.error.code"))))
        if ver: items.append(req(f"Create {e.name}: wrong last name fails closed", "POST", ep.path, None, {**body, "last_name": "NotTheName"}, T(403, ("VERIFICATION_FAILED", "j.error.code === 'VERIFICATION_FAILED'"))))
        if e.unique and ver:
            m2 = sp.db.execute(f"SELECT member_id FROM member_demographics WHERE member_id!=? ORDER BY row_idx LIMIT 1", (row[ver] if row else "",)).fetchone()
            if m2:
                f2 = sp.member_factors(m2[0]); V[f"{t}_member2_id"] = m2[0]; V[f"{t}_dob2"] = f2["dob"]; V[f"{t}_last_name2"] = f2["last_name"]
                items.append(req(f"Create {e.name}: same slot, other member, 409", "POST", ep.path, None, {**body, ver: "{{" + t + "_member2_id}}", "dob": "{{" + t + "_dob2}}", "last_name": "{{" + t + "_last_name2}}"}, T(409, ("CONFLICT", "j.error.code === 'CONFLICT'"))))
    if "get" in ops:
        ep = ops["get"]; var = f"{t}_id" if "create" in ops else f"{t}_row_id"
        items.append(req(f"Get {e.name}", "GET", sub(ep.path, var, pname(ep)), F or None, None, T(200, (f"{pk} matches", f"j.{pk} === pm.collectionVariables.get('{var}')"))))
        if ver: items.append(req(f"Get {e.name}: wrong date of birth fails closed", "GET", sub(ep.path, var, pname(ep)), [("dob", "1900-01-01"), ("last_name", "x")], None, T(403, ("VERIFICATION_FAILED", "j.error.code === 'VERIFICATION_FAILED'"))))
        items.append(req(f"Get {e.name}: unknown id", "GET", ep.path.replace("{" + pname(ep) + "}", "no-such-id-0000"), F or None, None, T(404, ("NOT_FOUND", "j.error.code === 'NOT_FOUND'"))))
    if "list" in ops:
        ep = ops["list"]; q = ([(ver, "{{" + t + "_member_id}}")] + F) if ver else []
        if not ver and ep.filters: q = []
        chk = [("has items", "Array.isArray(j.items)"), ("count matches", "j.count === j.items.length")]
        if "create" in ops and ver: chk.append(("created record is listed", f"j.items.some(i => i.{pk} === pm.collectionVariables.get('{t}_id'))"))
        items.append(req(f"List {e.name}", "GET", ep.path, q + [("limit", "100")], None, T(200, *chk)))
        if ver: items.append(req(f"List {e.name}: verification factors required", "GET", ep.path, [(ver, "{{" + t + "_member_id}}")], None, T(422, ("standard error body", "j.error && j.error.code"))))
        if ep.filters and not ver:
            f0 = spec.field(e, ep.filters[0])
            if f0.type == "enum": items.append(req(f"List {e.name}: filter by {f0.name}", "GET", ep.path, [(f0.name, (f0.values if isinstance(f0.values, list) else list(f0.values))[0]), ("limit", "3")], None, T(200, ("all match", f"j.items.every(i => i.{f0.name} === {json.dumps((f0.values if isinstance(f0.values, list) else list(f0.values))[0])})"))))
    if "update" in ops and "create" in ops:
        ep = ops["update"]; en = next((spec.field(e, n) for n in ep.updatable if spec.field(e, n).type == "enum"), None)
        if en and ep.transitions and en.default in ep.transitions:
            nxt = ep.transitions[en.default][0]; fb = {"dob": "{{" + t + "_dob}}", "last_name": "{{" + t + "_last_name}}"} if ver else {}
            items += [req(f"Update {e.name}: {en.default} to {nxt}", "PATCH", sub(ep.path, f"{t}_id", pname(ep)), None, {en.name: nxt, **fb}, T(200, (f"{en.name} is {nxt}", f"j.{en.name} === {json.dumps(nxt)}"))),
                      req(f"Update {e.name}: {nxt} back to {en.default} is not allowed", "PATCH", sub(ep.path, f"{t}_id", pname(ep)), None, {en.name: en.default, **fb}, T(409, ("INVALID_TRANSITION", "j.error.code === 'INVALID_TRANSITION'"), ("allowed list returned", "Array.isArray(j.error.details.allowed)")))]
    folders.append({"name": e.name, "item": items})

prereq = ["if (!pm.collectionVariables.get('access_token') && !pm.request.url.getPath().endsWith('/oauth/token')) {",
          " pm.sendRequest({ url: pm.variables.replaceIn('{{base_url}}/v1/oauth/token'), method: 'POST', header: { 'Content-Type': 'application/x-www-form-urlencoded' },",
          "  body: { mode: 'urlencoded', urlencoded: [{ key: 'grant_type', value: 'client_credentials' }, { key: 'client_id', value: pm.variables.replaceIn('{{client_id}}') }, { key: 'client_secret', value: pm.variables.replaceIn('{{client_secret}}') }] } },",
          "  function (e, r) { if (!e && r.code === 200) pm.collectionVariables.set('access_token', r.json().access_token); }); }"]
V = {k: v for k, v in V.items() if not k.endswith("_body_note")}
variables = [{"key": "base_url", "value": "http://localhost:8080"}, {"key": "client_id", "value": "hf-demo-client"}, {"key": "client_secret", "value": "demo-secret-change-me"}, {"key": "access_token", "value": ""}] + [{"key": k, "value": v} for k, v in V.items()]
coll = {"info": {"name": "Healthcare AI Factory: Blueprint-defined API (" + os.path.basename(bp_path) + ")", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        "description": "Generated from the blueprint's custom_entities and custom_endpoints. Start the API with HF_DB=<db> HF_CUSTOM_SPEC=<out>/custom-spec.json and a fresh HF_STATE_DB, then run the folders top to bottom: records created in the run are chained through collection variables."},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}]}, "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": prereq}}], "variable": variables, "item": folders}
fn = os.path.join(out, "hf-custom-api.postman_collection.json"); json.dump(coll, open(fn, "w"), indent=2)
print(sum(len(f["item"]) for f in folders), "requests in", len(folders), "folders ->", fn)

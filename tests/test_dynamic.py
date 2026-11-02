"""Blueprint-defined entities and APIs: validation, generation, rules, generated routes, generated contract tests and smoke scenarios, control plane.
Nothing here is specific to appointments: a second, unrelated blueprint (refill requests chained to refill notes) is built from definitions alone."""
import copy, json, sqlite3, tempfile, warnings
import pytest
warnings.filterwarnings("ignore")
from fastapi.testclient import TestClient
from hf_factory import blueprint, data_engine, dynamic, schema, smoke
from hf_factory.api import app as A, build as api_build, catalog, contract_gen
from hf_factory.control.app import create_control_app

S5 = "blueprints/s5_custom_appointments.json"


def build(bp, path=None):
    path = path or tempfile.mktemp(suffix=".db"); res, rep, man = data_engine.run(bp, path, None, log=lambda *_: None); return path, res, rep, man


def client(db, bp, **kw):
    cs = {"entities": bp["data_spec"].get("custom_entities"), "endpoints": bp["api_spec"].get("custom_endpoints")}
    c = TestClient(A.create_app(A.Config(db_path=db, state_path=tempfile.mktemp(), fault_enabled=False, custom=cs, **kw)))
    H = {"Authorization": "Bearer " + c.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "demo-secret-change-me"}).json()["access_token"]}
    return c, H


@pytest.fixture(scope="module")
def s5():
    bp = blueprint.load(S5, members=800); db, res, rep, man = build(bp); return bp, db, res, rep, man


@pytest.fixture(scope="module")
def generic():
    """An unrelated custom blueprint: entities and endpoints written here, no code for them anywhere."""
    bp = blueprint.load("blueprints/s1_provider_demo.json", members=300)
    bp["data_spec"]["custom_entities"] = [
        {"name": "RefillRequest", "count": {"per_parent": {"of": "member_demographics", "min": 0, "max": 2}}, "fields": [
            {"name": "request_id", "type": "string", "primary_key": True, "pattern": "RX-{seq:6}"},
            {"name": "member_id", "type": "ref", "ref": "member_demographics.member_id"},
            {"name": "drug_name", "type": "enum", "values": ["METFORMIN", "LISINOPRIL", "ATORVASTATIN"]},
            {"name": "quantity", "type": "int", "min": 1, "max": 90, "mean": 30, "sd": 15},
            {"name": "requested_on", "type": "date", "offset_days": [-20, 0]},
            {"name": "status", "type": "enum", "values": {"PENDING": 60, "APPROVED": 30, "REJECTED": 10}, "default": "PENDING", "server_set": True}]},
        {"name": "RefillNote", "count": {"per_parent": {"of": "refill_request", "min": 0, "max": 1}}, "fields": [
            {"name": "note_id", "type": "id", "primary_key": True},
            {"name": "request_id", "type": "ref", "ref": "refill_request.request_id"},
            {"name": "note", "type": "text", "generator": "sentence"},
            {"name": "created_at", "type": "datetime", "offset_days": [-10, 0]}]}]
    bp["api_spec"]["custom_endpoints"] = [
        {"method": "POST", "path": "/refills", "entity": "RefillRequest", "operation": "create", "verify_member": "member_id"},
        {"method": "GET", "path": "/refills/{request_id}", "entity": "RefillRequest", "operation": "get", "verify_member": "member_id"},
        {"method": "GET", "path": "/refill-notes", "entity": "RefillNote", "operation": "list", "filters": ["request_id"]}]
    bp = blueprint.patch(bp); db, res, rep, man = build(bp); return bp, db, res, rep, man


# ------------------------------------------------------------------ validation
def reject(mutate, text, base=S5):
    bp = json.load(open(base)); mutate(bp)
    with pytest.raises(dynamic.SpecError, match=text): blueprint.validate_custom(bp)

ENT = lambda bp: bp["data_spec"]["custom_entities"][0]


def test_validation_messages():
    reject(lambda b: ENT(b)["fields"].pop(0), "exactly one primary_key")
    reject(lambda b: ENT(b)["fields"][1].update(ref="nope.col"), "does not exist")
    reject(lambda b: ENT(b).update(table="provider"), "collides with a built-in table")
    reject(lambda b: ENT(b)["fields"][4].update(values=[]), "enum needs values")
    reject(lambda b: ENT(b)["fields"][3].update(after="nothing"), "after is only valid|after must name")
    reject(lambda b: ENT(b).update(unique=[["provider_id", "ghost"]]), "unknown field ghost")
    reject(lambda b: b["api_spec"]["custom_endpoints"][0].update(entity="Ghost"), "unknown entity")
    reject(lambda b: b["api_spec"]["custom_endpoints"][0].update(method="GET"), "must use POST")
    reject(lambda b: b["api_spec"]["custom_endpoints"][1].update(path="/appointments/by-id"), "path must contain")
    reject(lambda b: b["api_spec"]["custom_endpoints"][3].update(transitions={"BOOKED": ["NOPE"]}), "transitions use values")
    reject(lambda b: b["api_spec"]["custom_endpoints"][0].update(verify_member="provider_id"), "verify_member must name")
    reject(lambda b: ENT(b)["fields"][0].update(name="Row-Idx"), "custom definition invalid")
    reject(lambda b: ENT(b)["fields"].append({"name": "row_idx", "type": "int"}), "reserved")


def test_cycle_detected():
    bp = json.load(open(S5)); a, b = ENT(bp), bp["data_spec"]["custom_entities"][1]
    a["fields"].append({"name": "ref_back", "type": "ref", "ref": "referral.referral_id", "required": False}); b["fields"].append({"name": "appt", "type": "ref", "ref": "appointment.appointment_id", "required": False})
    with pytest.raises(dynamic.SpecError, match="cycle"): blueprint.validate_custom(bp)


def test_route_collision_with_builtin(s5):
    bp, db, *_ = s5; cs = {"entities": bp["data_spec"]["custom_entities"], "endpoints": [{"method": "POST", "path": "/eligibility/verify", "entity": "Appointment", "operation": "create"}]}
    with pytest.raises(dynamic.SpecError, match="collides"): A.create_app(A.Config(db_path=db, state_path=tempfile.mktemp(), custom=cs))


# ------------------------------------------------------------------ data engine
def test_tables_and_counts_come_from_the_definition(s5):
    bp, db, res, rep, man = s5; c = sqlite3.connect(db)
    assert res["status"] == "COMPLETED"
    cols = [r[1] for r in c.execute("PRAGMA table_info(appointment)")]
    assert cols[:7] == ["appointment_id", "member_id", "provider_id", "appointment_date", "start_time", "status", "reason"] and {"row_idx", "scenario_id", "tenant_id", "is_synthetic"} <= set(cols)
    n = c.execute("SELECT COUNT(*) FROM member_demographics").fetchone()[0]
    assert c.execute("SELECT COUNT(*) FROM appointment").fetchone()[0] == round(0.8 * n)             # ratio
    assert c.execute("SELECT COUNT(*) FROM referral").fetchone()[0] > 0.5 * n                         # per_parent 0..2
    assert {r[0] for r in c.execute("SELECT status FROM appointment")} <= {"BOOKED", "COMPLETED", "CANCELLED"}


def test_generated_rules_pass_and_cover_custom_tables(s5):
    bp, db, res, rep, man = s5
    assert {"X-01", "X-02", "X-03", "X-05", "X-06"} <= set(rep) and all(v["status"] == "PASS" for v in rep.values()) and rep["R-01"]["status"] == "PASS"
    assert "appointment" in man["entity_checksums"] and man["total_rows"] > 0


def test_a_broken_dataset_fails_the_generated_rules(s5):
    bp, db, *_ = s5; c = sqlite3.connect(db); c.execute("UPDATE appointment SET status='BOGUS' WHERE rowid=1"); c.execute("UPDATE appointment SET member_id='nobody' WHERE rowid=2")
    from hf_factory import rules, common
    ctx = common.Ctx("custom-demo-05", 20261105, "2026-10-12"); ctx.custom = dynamic.parse_spec(bp["data_spec"]["custom_entities"], None, schema.core_cols())
    rep = rules.build_rules(c, ctx, bp["data_spec"]["volumes"]); c.rollback()
    assert rep["X-03"]["status"] == "FAIL" and rep["R-01"]["status"] == "FAIL"


def test_deterministic_and_seed_sensitive():
    a = blueprint.load(S5, members=300); _, _, _, m1 = build(a); _, _, _, m2 = build(a); _, _, _, m3 = build(blueprint.load(S5, members=300, seed=7))
    assert m1["entity_checksums"]["appointment"] == m2["entity_checksums"]["appointment"] and m1["entity_checksums"]["referral"] == m2["entity_checksums"]["referral"]
    assert m1["entity_checksums"]["appointment"] != m3["entity_checksums"]["appointment"]


def test_blueprint_change_changes_the_data_and_the_hash():
    a = blueprint.load(S5, members=300); b = copy.deepcopy(a); b["data_spec"]["custom_entities"][0]["fields"][4]["values"] = ["09:00", "09:30"]; b = blueprint.patch(b)
    assert a["data_spec"]["data_spec_hash"] != b["data_spec"]["data_spec_hash"]
    db, *_ = build(b); assert {r[0] for r in sqlite3.connect(db).execute("SELECT DISTINCT start_time FROM appointment")} <= {"09:00", "09:30"}


def test_core_dataset_unchanged_without_custom_entities():
    """The custom layer is additive: a blueprint without it produces the same built-in data and no custom tables, even after a custom run in the same process."""
    build(blueprint.load(S5, members=200)); plain = blueprint.load("blueprints/s1_provider_demo.json", members=200); _, res, rep, man = build(plain)
    assert "appointment" not in man["entity_checksums"] and not any(k.startswith("X-") for k in rep) and res["status"] == "COMPLETED"


# ------------------------------------------------------------------ generated API
def test_api_surface_comes_from_the_endpoint_definitions(s5):
    bp, db, *_ = s5; c, H = client(db, bp); spec = c.get("/openapi.json").json()
    ops = {(m.upper(), p): o["operationId"] for p, v in spec["paths"].items() for m, o in v.items()}
    assert ops[("POST", "/v1/appointments")] == "createAppointment" and ops[("PATCH", "/v1/appointments/{appointment_id}")] == "updateAppointment" and ops[("GET", "/v1/referrals")] == "listReferrals"
    props = spec["components"]["schemas"]["AppointmentCreate"]["properties"]
    assert set(props) == {"member_id", "provider_id", "appointment_date", "start_time", "reason", "dob", "last_name"} and "status" not in props      # server-set status is not accepted
    tools = {t["name"]: t for t in api_build.tool_manifest(spec)["tools"]}
    assert "createAppointment" in tools and any("verified" in n for n in tools["createAppointment"]["guardrails"]["never"])


def test_booking_flow(s5):
    bp, db, *_ = s5; c, H = client(db, bp); d = sqlite3.connect(db); d.row_factory = sqlite3.Row
    m = d.execute("SELECT member_id, dob, last_name FROM member_demographics LIMIT 1").fetchone(); m2 = d.execute("SELECT member_id, dob, last_name FROM member_demographics LIMIT 1 OFFSET 7").fetchone()
    prov = d.execute("SELECT npi FROM provider LIMIT 1").fetchone()[0]
    body = {"member_id": m["member_id"], "provider_id": prov, "appointment_date": "2028-01-10", "start_time": "10:00", "dob": m["dob"], "last_name": m["last_name"]}
    assert c.post("/v1/appointments", json=body).status_code == 401
    bad = c.post("/v1/appointments", json={**body, "last_name": "Wrong"}, headers=H); assert bad.status_code == 403 and bad.json()["error"]["code"] == "VERIFICATION_FAILED" and m["member_id"] not in bad.text
    assert c.post("/v1/appointments", json={**body, "start_time": "03:00"}, headers=H).status_code == 422
    assert c.post("/v1/appointments", json={**body, "status": "COMPLETED"}, headers=H).status_code == 422           # server-set field rejected
    assert c.post("/v1/appointments", json={**body, "provider_id": "0000000000"}, headers=H).json()["error"]["code"] == "REFERENCE_NOT_FOUND"
    dry = c.post("/v1/appointments", params={"dry_run": "true"}, json=body, headers=H); assert dry.status_code == 201
    assert c.get("/v1/appointments", params={"member_id": m["member_id"], "dob": m["dob"], "last_name": m["last_name"], "appointment_date": "2028-01-10"}, headers=H).json()["total"] == 0      # dry run saved nothing
    r = c.post("/v1/appointments", json=body, headers=H); a = r.json(); assert r.status_code == 201 and a["status"] == "BOOKED" and a["is_synthetic"]
    assert c.post("/v1/appointments", json=body, headers=H).json()["appointment_id"] == a["appointment_id"]                       # idempotent
    other = {**body, "member_id": m2["member_id"], "dob": m2["dob"], "last_name": m2["last_name"]}
    cf = c.post("/v1/appointments", json=other, headers=H); assert cf.status_code == 409 and cf.json()["error"]["code"] == "CONFLICT"      # same provider, date and time
    f = {"dob": m["dob"], "last_name": m["last_name"]}
    assert c.get(f"/v1/appointments/{a['appointment_id']}", params=f, headers=H).json()["provider_id"] == prov
    assert c.get(f"/v1/appointments/{a['appointment_id']}", params={"dob": "1900-01-01", "last_name": "x"}, headers=H).status_code == 403
    assert c.get("/v1/appointments/does-not-exist", params=f, headers=H).status_code == 404
    cancel = c.patch(f"/v1/appointments/{a['appointment_id']}", json={"status": "CANCELLED", **f}, headers=H); assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    back = c.patch(f"/v1/appointments/{a['appointment_id']}", json={"status": "BOOKED", **f}, headers=H); assert back.status_code == 409 and back.json()["error"]["code"] == "INVALID_TRANSITION"
    assert c.post("/v1/appointments", json=other, headers=H).status_code == 201                                                   # the cancelled slot can be rebooked
    lst = c.get("/v1/appointments", params={"member_id": m["member_id"], **f, "limit": 100}, headers=H).json(); assert any(i["appointment_id"] == a["appointment_id"] for i in lst["items"]) and lst["count"] == len(lst["items"])
    assert c.get("/v1/appointments", params={"member_id": m["member_id"]}, headers=H).status_code == 422                          # verification factors required


def test_dataset_rows_are_overridden_in_state_never_modified(s5):
    bp, db, *_ = s5; c, H = client(db, bp); d = sqlite3.connect(db); d.row_factory = sqlite3.Row
    r = d.execute("SELECT a.appointment_id, a.status, m.dob, m.last_name FROM appointment a JOIN member_demographics m USING(member_id) WHERE a.status='BOOKED' LIMIT 1").fetchone(); f = {"dob": r["dob"], "last_name": r["last_name"]}
    assert c.patch(f"/v1/appointments/{r['appointment_id']}", json={"status": "COMPLETED", **f}, headers=H).status_code == 200
    assert c.get(f"/v1/appointments/{r['appointment_id']}", params=f, headers=H).json()["status"] == "COMPLETED"
    assert d.execute("SELECT status FROM appointment WHERE appointment_id=?", (r["appointment_id"],)).fetchone()[0] == "BOOKED"      # the dataset is untouched


def test_read_only_entity_endpoints(s5):
    bp, db, *_ = s5; c, H = client(db, bp); j = c.get("/v1/referrals", params={"status": "OPEN", "limit": 3}, headers=H).json()
    assert j["count"] == 3 and all(i["status"] == "OPEN" for i in j["items"]) and j["total"] >= 3
    one = j["items"][0]["referral_id"]; assert c.get(f"/v1/referrals/{one}", headers=H).json()["referral_id"] == one and c.get("/v1/referrals", params={"status": "NOPE"}, headers=H).status_code == 422


def test_enabled_ops_filter_keeps_custom_routes(s5):
    bp, db, *_ = s5; c, H = client(db, bp, enabled_ops=catalog.ops_for(["PCS"])); paths = c.get("/openapi.json").json()["paths"]
    assert "/v1/appointments" in paths and "/v1/claims/{claim_id}/status" in paths and "/v1/eligibility/verify" not in paths


# ------------------------------------------------------------------ generated tests (s8 contract tests, s10 smoke)
def test_generated_contract_tests_cover_custom_endpoints(s5):
    bp, db, *_ = s5; cs = {"entities": bp["data_spec"]["custom_entities"], "endpoints": bp["api_spec"]["custom_endpoints"]}
    c, _ = client(db, bp, enabled_ops=catalog.ops_for(bp["scope"]["use_cases"])); spec = c.get("/openapi.json").json(); tm = api_build.tool_manifest(spec)
    ct = contract_gen.run(db, spec, tm, catalog.ops_for(bp["scope"]["use_cases"]), custom=cs)
    assert ct["failed"] == 0 and {"createAppointment", "getAppointment", "listAppointments", "updateAppointment", "getReferral", "listReferrals"} <= set(ct["per_operation"]) and ct["total"] == 4 * len(ct["per_operation"])


def test_generated_smoke_scenarios(s5):
    bp, db, *_ = s5; cs = {"entities": bp["data_spec"]["custom_entities"], "endpoints": bp["api_spec"]["custom_endpoints"]}
    sm = smoke.run(db, log=lambda *_: None, use_cases=bp["scope"]["use_cases"], custom=cs)
    mine = [s for s in sm["scenarios"] if s["scenario"].startswith("APP")]; assert len(mine) == 6 and all(s["passed"] for s in mine) and sm["status"] == "PASS"


def test_a_second_unrelated_blueprint_needs_no_code(generic):
    bp, db, res, rep, man = generic; assert res["status"] == "COMPLETED" and rep["X-01"]["status"] == "PASS" and rep["R-01"]["status"] == "PASS"
    c = sqlite3.connect(db); assert c.execute("SELECT COUNT(*) FROM refill_note").fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM refill_note n LEFT JOIN refill_request r ON r.request_id=n.request_id WHERE r.request_id IS NULL").fetchone()[0] == 0      # custom to custom reference
    assert c.execute("SELECT MIN(quantity), MAX(quantity) FROM refill_request").fetchone()[0] >= 1
    assert c.execute("SELECT request_id FROM refill_request ORDER BY row_idx LIMIT 1").fetchone()[0] == "RX-000000"
    cs = {"entities": bp["data_spec"]["custom_entities"], "endpoints": bp["api_spec"]["custom_endpoints"]}; cl, H = client(db, bp, enabled_ops=catalog.ops_for(bp["scope"]["use_cases"]))
    spec = cl.get("/openapi.json").json(); tm = api_build.tool_manifest(spec); ct = contract_gen.run(db, spec, tm, catalog.ops_for(bp["scope"]["use_cases"]), custom=cs)
    assert ct["failed"] == 0 and "createRefillRequest" in ct["per_operation"]
    sm = smoke.run(db, log=lambda *_: None, use_cases=bp["scope"]["use_cases"], custom=cs); assert sm["status"] == "PASS" and any(s["scenario"].startswith("REF") for s in sm["scenarios"])


# ------------------------------------------------------------------ control plane
def test_control_plane_runs_custom_entities_end_to_end():
    d = tempfile.mkdtemp(); c = TestClient(create_control_app(d, token="t")); T = {"Authorization": "Bearer t"}; bp = blueprint.load(S5, members=300)
    for st in blueprint.STAGES:
        r = c.post(f"/factory/data-engine/{st['id']}", json=blueprint.job_spec(bp, st, [], run_id="run_cx"), headers=T); assert r.status_code == 200, r.text[:300]
    assert "appointment" in {e["entity"] for e in r.json()["manifest"]["entity_checksums"].items() and [{"entity": k} for k in r.json()["manifest"]["entity_checksums"]]}
    a = c.post("/factory/api-engine/s8", json=blueprint.api_job_spec(bp, "run_cx"), headers=T).json()
    assert a["status"] == "DEPLOYED" and "createAppointment" in a["operations"] and a["contract_tests"]["failed"] == 0 and "HF_CUSTOM_SPEC=" in a["run_command"]


def test_control_plane_rejects_a_bad_custom_definition():
    c = TestClient(create_control_app(tempfile.mkdtemp(), token="t")); bp = blueprint.load(S5, members=300); st = blueprint.STAGES[0]
    j = blueprint.job_spec(bp, st, [], run_id="run_bad"); j["custom_entities"] = [{"name": "X", "count": {"fixed": 1}, "fields": [{"name": "a", "type": "string"}, {"name": "b", "type": "ref", "ref": "ghost.id"}]}]
    r = c.post("/factory/data-engine/s3", json=j, headers={"Authorization": "Bearer t"}); assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CUSTOM_SPEC"


def test_interleaved_runs_with_different_custom_entities():
    """The schema registry is process-global; stages of a custom run and a plain run, interleaved, must not disturb each other (control plane serves many runs)."""
    a = data_engine.DataRun(blueprint.load(S5, members=300), tempfile.mktemp(suffix=".db"), log=lambda *_: None)
    b = data_engine.DataRun(blueprint.load("blueprints/s1_provider_demo.json", members=300), tempfile.mktemp(suffix=".db"), log=lambda *_: None)
    for st in ("s3", "s4", "s5", "s6", "s7"): getattr(a, st)(); getattr(b, st)()
    assert a.result["status"] == "COMPLETED" and b.result["status"] == "COMPLETED"
    assert "appointment" in a.man["entity_checksums"] and "appointment" not in b.man["entity_checksums"] and any(k.startswith("X-") for k in a.rep) and not any(k.startswith("X-") for k in b.rep)

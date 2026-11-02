"""Contract tests for the member-facing API: MBN benefits, MCI claims, MPS provider search, MPC PCP change, MAS appointments, plus verification, caregiver scope and dry_run."""
import json, sqlite3, tempfile, warnings
import pytest
warnings.filterwarnings("ignore")
from fastapi.testclient import TestClient
from hf_factory.api.app import create_app, Config

def one(con, sql, *a): return con.execute(sql, a).fetchone()

@pytest.fixture()
def fresh(db_path):
    """Isolated app + state DB per test (writes must not leak between tests)."""
    c = TestClient(create_app(Config(db_path=db_path, state_path=tempfile.mktemp(), fault_enabled=False)))
    r = c.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "demo-secret-change-me"})
    return c, {"Authorization": "Bearer " + r.json()["access_token"]}

def F(m, **kw): return {"dob": m["dob"], "last_name": m["last_name"], **kw}
def active_member(con, n=0, extra=""):
    return one(con, f"SELECT m.* FROM member_demographics m JOIN plan_enrollment e ON e.member_id=m.member_id AND e.effective_dt>='2026-01-01' AND e.status='ACTIVE' WHERE m.scenario_id='BASE' {extra} ORDER BY m.row_idx LIMIT 1 OFFSET {n}")
def caregiver(con, expired=False, scope_has=None, scope_lacks=None):
    for r in con.execute("SELECT f.*, m.dob, m.last_name FROM family_relationship f JOIN member_demographics m ON m.member_id=f.member_id JOIN plan_enrollment e ON e.member_id=m.member_id AND e.status='ACTIVE' AND e.effective_dt>='2026-01-01' "
                         "WHERE f.caregiver_flag=1 AND f.scenario_id=? ORDER BY f.row_idx", ("CAREGIVER-EXPIRED" if expired else "CAREGIVER",)):
        sc = json.loads(r["authorization_scope"])
        if scope_has and not ("ALL" in sc or scope_has in sc): continue
        if scope_lacks and ("ALL" in sc or scope_lacks in sc): continue
        return r
    pytest.skip("no matching caregiver in this dataset")
def cg(r): return {"dob": r["dob"], "last_name": r["last_name"], "relationship_id": r["relationship_id"], "caregiver_name": r["caregiver_name"]}

# ------------------------------------------------------------------ auth + verification
def test_member_endpoints_require_token(fresh, con):
    c, H = fresh; m = active_member(con); assert c.get(f"/v1/members/{m['member_id']}/benefits", params=F(m)).status_code == 401

def test_wrong_factor_discloses_nothing_and_looks_the_same_as_unknown_member(fresh, con):
    c, H = fresh; m = active_member(con)
    a = c.get(f"/v1/members/{m['member_id']}/benefits", params={"dob": m["dob"], "last_name": "Nobody"}, headers=H)
    b = c.get("/v1/members/00000000-0000-0000-0000-000000000000/benefits", params=F(m), headers=H)
    assert a.status_code == b.status_code == 403 and a.json()["error"]["code"] == b.json()["error"]["code"] == "VERIFICATION_FAILED" and a.json()["error"]["message"] == b.json()["error"]["message"]
    assert "plan" not in a.text and m["first_name"] not in a.text

# ------------------------------------------------------------------ MBN
def test_benefits(fresh, con):
    c, H = fresh; m = active_member(con); j = c.get(f"/v1/members/{m['member_id']}/benefits", params=F(m), headers=H).json()
    assert j["actor"]["type"] == "MEMBER" and j["enrollment"]["coverage_active"] and j["cost_share"]["oop_max"] > 0 and set(j["accumulators"]) == {"IN", "OON"} and j["sources"] and j["is_synthetic"]
    a = j["accumulators"]["IN"]; assert round(a["oop_met"] + a["oop_remaining"], 2) == j["cost_share"]["oop_max"] or a["oop_remaining"] == 0
    assert j["member_ref"].startswith("MBR-") and m["member_id"] not in j["member_ref"]

def test_benefits_termed_member_is_flagged(fresh, con):
    c, H = fresh; m = one(con, "SELECT m.* FROM member_demographics m WHERE scenario_id='PEV-02' LIMIT 1"); j = c.get(f"/v1/members/{m['member_id']}/benefits", params=F(m), headers=H).json()
    assert j["enrollment"]["coverage_active"] is False and j["enrollment"]["status"] == "TERMED" and "Coverage ended" in j["notes"][0]

def test_caregiver_benefits_scope(fresh, con):
    c, H = fresh; ok = caregiver(con, scope_has="BENEFITS"); j = c.get(f"/v1/members/{ok['member_id']}/benefits", params=cg(ok), headers=H)
    assert j.status_code == 200 and j.json()["actor"]["type"] == "CAREGIVER" and j.json()["actor"]["caregiver_relationship"]
    no = caregiver(con, scope_lacks="BENEFITS"); r = c.get(f"/v1/members/{no['member_id']}/benefits", params=cg(no), headers=H); e = r.json()["error"]
    assert r.status_code == 403 and e["code"] == "SCOPE_NOT_GRANTED" and e["details"]["required_scope"] == "BENEFITS" and e["details"]["consent_upgrade_available"] is True and "plan" not in r.text

def test_caregiver_expired_consent(fresh, con):
    c, H = fresh; ex = caregiver(con, expired=True); r = c.get(f"/v1/members/{ex['member_id']}/benefits", params=cg(ex), headers=H)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_EXPIRED" and "plan" not in r.text

def test_caregiver_wrong_name_fails_verification(fresh, con):
    c, H = fresh; ok = caregiver(con, scope_has="BENEFITS"); r = c.get(f"/v1/members/{ok['member_id']}/benefits", params={**cg(ok), "caregiver_name": "Someone Else"}, headers=H)
    assert r.status_code == 403 and r.json()["error"]["code"] == "VERIFICATION_FAILED"

# ------------------------------------------------------------------ MCI
def claim_member(con, status):
    return one(con, "SELECT m.*, h.claim_id FROM claim_header h JOIN member_demographics m ON m.member_id=h.member_id WHERE h.status=? ORDER BY h.row_idx LIMIT 1", status)

def test_claims_list_and_detail(fresh, con):
    c, H = fresh; m = claim_member(con, "PAID"); j = c.get(f"/v1/members/{m['member_id']}/claims", params=F(m), headers=H).json()
    assert j["count"] >= 1 and j["amounts_withheld"] is False and j["claims"][0]["member_status"] and all("status" in x for x in j["claims"])
    d = c.get(f"/v1/members/{m['member_id']}/claims/{m['claim_id']}", params=F(m), headers=H).json(); assert d["status"] == "PAID" and d["lines"] and d["plan_paid"] is not None and "not a bill" in " ".join(d["next_actions"])

def test_denied_claim_explained_provider_liable(fresh, con):
    c, H = fresh
    m = one(con, "SELECT m.*, h.claim_id FROM claim_header h JOIN member_demographics m ON m.member_id=h.member_id JOIN claim_denial d ON d.claim_id=h.claim_id WHERE d.liable_party='PROVIDER' ORDER BY h.row_idx LIMIT 1")
    d = c.get(f"/v1/members/{m['member_id']}/claims/{m['claim_id']}", params=F(m), headers=H).json()
    assert d["denial"]["you_should_not_be_billed"] is True and d["denial"]["who_is_responsible"] == "Your provider" and "should not be billed" in " ".join(d["next_actions"]) and d["denial"]["reason"] in d["explanation"]

def test_pended_claim_says_what_is_needed(fresh, con):
    c, H = fresh; m = claim_member(con, "PENDED"); d = c.get(f"/v1/members/{m['member_id']}/claims/{m['claim_id']}", params=F(m), headers=H).json()
    assert d["pend"]["waiting_on"] and d["pend"]["expected_resolution_dt"] and "do not need to pay" in " ".join(d["next_actions"]).lower()

def test_claim_of_another_member_is_404(fresh, con):
    c, H = fresh; m = claim_member(con, "PAID"); o = active_member(con, 5)
    if o["member_id"] == m["member_id"]: o = active_member(con, 6)
    r = c.get(f"/v1/members/{o['member_id']}/claims/{m['claim_id']}", params=F(o), headers=H); assert r.status_code == 404 and r.json()["error"]["code"] == "CLAIM_NOT_FOUND"

def test_caregiver_without_billing_sees_no_amounts(fresh, con):
    c, H = fresh
    for r in con.execute("SELECT f.* FROM family_relationship f JOIN claim_header h ON h.member_id=f.member_id WHERE f.caregiver_flag=1 AND f.scenario_id='CAREGIVER' GROUP BY f.relationship_id ORDER BY f.row_idx"):
        sc = json.loads(r["authorization_scope"])
        if "ALL" not in sc and "CLAIMS" in sc and "BILLING" not in sc:
            m = one(con, "SELECT * FROM member_demographics WHERE member_id=?", r["member_id"]); j = c.get(f"/v1/members/{r['member_id']}/claims", params=cg({**dict(r), "dob": m["dob"], "last_name": m["last_name"]}), headers=H).json()
            assert j["amounts_withheld"] is True and all(x["billed"] is None and x["you_may_owe"] is None for x in j["claims"]) and j["count"] >= 1; return
    pytest.skip("no CLAIMS-without-BILLING caregiver with claims in this dataset")

# ------------------------------------------------------------------ MPS
def test_provider_search_is_in_network_and_sorted(fresh, con):
    c, H = fresh; m = active_member(con); j = c.get(f"/v1/members/{m['member_id']}/providers/search", params={**F(m), "max_distance_miles": 200, "limit": 10}, headers=H).json()
    assert j["count"] > 0 and j["in_network_only"] and [r["location"]["distance_miles"] for r in j["results"]] == sorted(r["location"]["distance_miles"] for r in j["results"])
    plan = one(con, "SELECT plan_id FROM plan_enrollment WHERE member_id=? AND effective_dt>='2026-01-01'", m["member_id"])[0]
    for r in j["results"]: assert one(con, "SELECT status FROM network_participation WHERE npi=? AND plan_id=?", r["npi"], plan)[0] == "PARTICIPATING"

def test_provider_search_filters(fresh, con):
    c, H = fresh; m = active_member(con); base = {**F(m), "max_distance_miles": 200, "limit": 20}
    j = c.get(f"/v1/members/{m['member_id']}/providers/search", params={**base, "provider_type": "PCP", "accepting_new_patients": True}, headers=H).json()
    assert all(r["provider_type"] == "PCP" and r["location"]["accepting_new_patients"] for r in j["results"])
    j = c.get(f"/v1/members/{m['member_id']}/providers/search", params={**base, "language": "ES"}, headers=H).json(); assert all("ES" in r["languages"] for r in j["results"])
    j = c.get(f"/v1/members/{m['member_id']}/providers/search", params={**F(m), "specialty": "zzz-nothing"}, headers=H).json(); assert j["count"] == 0 and "No in-network provider matched" in j["next_actions"][0]

def test_provider_search_flags_stale_directory(fresh, con):
    from datetime import date
    c, H = fresh; m = active_member(con); j = c.get(f"/v1/members/{m['member_id']}/providers/search", params={**F(m), "max_distance_miles": 200, "limit": 20}, headers=H).json()
    for r in j["results"]: assert r["location"]["directory_verification_stale"] == ((date(2026, 10, 12) - date.fromisoformat(r["location"]["directory_last_verified_dt"])).days > 90)

def test_termed_member_cannot_search(fresh, con):
    c, H = fresh; m = one(con, "SELECT * FROM member_demographics WHERE scenario_id='PEV-02' LIMIT 1"); r = c.get(f"/v1/members/{m['member_id']}/providers/search", params=F(m), headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "COVERAGE_NOT_ACTIVE"

# ------------------------------------------------------------------ MPC
def pcp_targets(c, H, m, n=3):
    j = c.get(f"/v1/members/{m['member_id']}/providers/search", params={**F(m), "provider_type": "PCP", "max_distance_miles": 49, "limit": 20}, headers=H).json()
    cur = c.get(f"/v1/members/{m['member_id']}/pcp", params=F(m), headers=H).json()["current_pcp"]["npi"]
    return [r for r in j["results"] if r["npi"] != cur and r["panel_status"] != "CLOSED"][:n]

def test_get_pcp(fresh, con):
    c, H = fresh; m = active_member(con); j = c.get(f"/v1/members/{m['member_id']}/pcp", params=F(m), headers=H).json()
    assert j["current_pcp"]["npi"] and j["pending_change"] is None and j["changes_used_this_year"] == 0 and j["changes_allowed_per_year"] == 2

def test_change_pcp_dry_run_then_commit(fresh, con):
    c, H = fresh; m = active_member(con); t = pcp_targets(c, H, m)[0]; body = {**F(m), "new_pcp_npi": t["npi"], "reason": "Closer to home"}; url = f"/v1/members/{m['member_id']}/pcp-changes"
    p = c.post(url, json={**body, "dry_run": True}, headers=H); assert p.status_code == 201 and p.json()["status"] == "PREVIEW" and p.json()["change_id"] is None
    assert c.get(f"/v1/members/{m['member_id']}/pcp", params=F(m), headers=H).json()["pending_change"] is None                 # dry_run saved nothing
    r = c.post(url, json=body, headers=H); j = r.json(); assert r.status_code == 201 and j["status"] == "SCHEDULED" and j["effective_dt"] == "2026-10-13" and j["previous_pcp"] and j["id_card_reissued"] and j["changes_remaining_this_year"] == 1
    g = c.get(f"/v1/members/{m['member_id']}/pcp", params=F(m), headers=H).json(); assert g["pending_change"]["new_pcp"]["npi"] == t["npi"] and g["changes_used_this_year"] == 1

def test_change_pcp_rejections(fresh, con):
    c, H = fresh; m = active_member(con); url = f"/v1/members/{m['member_id']}/pcp-changes"; cur = c.get(f"/v1/members/{m['member_id']}/pcp", params=F(m), headers=H).json()["current_pcp"]["npi"]
    code = lambda npi: (lambda r: (r.status_code, r.json()["error"]["code"]))(c.post(url, json={**F(m), "new_pcp_npi": npi}, headers=H))
    assert code(cur) == (409, "ALREADY_ASSIGNED")
    spec = one(con, "SELECT npi FROM provider WHERE provider_type='SPECIALIST' LIMIT 1")[0]; assert code(spec) == (409, "NOT_A_PCP")
    closed = one(con, "SELECT pv.npi FROM provider pv JOIN network_participation np ON np.npi=pv.npi JOIN plan_enrollment e ON e.plan_id=np.plan_id AND e.member_id=? WHERE pv.provider_type='PCP' AND pv.status='ACTIVE' AND np.panel_status='CLOSED' AND np.status='PARTICIPATING' LIMIT 1", m["member_id"])
    if closed: assert code(closed[0])[1] in ("PANEL_CLOSED", "TOO_FAR")
    assert c.post(url, json={**F(m), "new_pcp_npi": "12"}, headers=H).status_code == 422

def test_change_pcp_limit_two_per_year(fresh, con):
    c, H = fresh; m = active_member(con); ts = pcp_targets(c, H, m, 3)
    if len(ts) < 3: pytest.skip("need three eligible PCPs near the member")
    url = f"/v1/members/{m['member_id']}/pcp-changes"
    for t in ts[:2]: assert c.post(url, json={**F(m), "new_pcp_npi": t["npi"]}, headers=H).status_code == 201
    r = c.post(url, json={**F(m), "new_pcp_npi": ts[2]["npi"]}, headers=H); assert r.status_code == 409 and r.json()["error"]["code"] == "CHANGE_LIMIT"

def test_caregiver_pcp_change_needs_scope(fresh, con):
    c, H = fresh; no = caregiver(con, scope_lacks="PCP_CHANGE"); m = one(con, "SELECT * FROM member_demographics WHERE member_id=?", no["member_id"])
    r = c.post(f"/v1/members/{no['member_id']}/pcp-changes", json={**cg(no), "new_pcp_npi": "9000000000"}, headers=H); assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_NOT_GRANTED"

# ------------------------------------------------------------------ MAS
def slot_target(c, H, m):
    for r in c.get(f"/v1/members/{m['member_id']}/providers/search", params={**F(m), "max_distance_miles": 200, "limit": 20}, headers=H).json()["results"]:
        s = c.get(f"/v1/members/{m['member_id']}/appointment-slots", params={**F(m), "npi": r["npi"]}, headers=H).json()
        if s["count"] >= 2 and any(x["start"] > "2026-10-14T12:00:00" for x in s["slots"]): return r, s
    pytest.skip("no provider with open slots")

def later(s): return next(x for x in s["slots"] if x["start"] > "2026-10-14T12:00:00")     # more than 24h out, so it can be cancelled

def test_slots_listed(fresh, con):
    c, H = fresh; m = active_member(con); r, s = slot_target(c, H, m)
    assert s["in_network"] and all(x["start"] > "2026-10-12T12:00" for x in s["slots"]) and s["slots"] == sorted(s["slots"], key=lambda x: x["start"]) and s["sources"]

def test_no_slots_offers_alternatives(fresh, con):
    c, H = fresh; m = active_member(con)
    p = one(con, "SELECT pv.npi FROM provider pv WHERE pv.provider_type='PCP' AND pv.status='ACTIVE' AND NOT EXISTS (SELECT 1 FROM appointment_slot s WHERE s.npi=pv.npi) AND EXISTS (SELECT 1 FROM network_participation np JOIN plan_enrollment e ON e.plan_id=np.plan_id AND e.member_id=? WHERE np.npi=pv.npi AND np.status='PARTICIPATING') LIMIT 1", m["member_id"])
    if not p: pytest.skip("no in-network provider without slots")
    s = c.get(f"/v1/members/{m['member_id']}/appointment-slots", params={**F(m), "npi": p[0]}, headers=H).json()
    assert s["count"] == 0 and "No open times" in " ".join(s["next_actions"])

def test_book_dry_run_book_cancel(fresh, con):
    c, H = fresh; m = active_member(con); r, s = slot_target(c, H, m); sid = later(s)["slot_id"]; url = f"/v1/members/{m['member_id']}/appointments"
    p = c.post(url, json={**F(m), "slot_id": sid, "dry_run": True}, headers=H); assert p.status_code == 201 and p.json()["appointment"]["status"] == "PREVIEW" and p.json()["appointment"]["appointment_id"] is None
    assert c.get(url, params=F(m), headers=H).json()["count"] == 0
    b = c.post(url, json={**F(m), "slot_id": sid, "reason": "annual wellness visit"}, headers=H); j = b.json(); aid = j["appointment"]["appointment_id"]
    assert b.status_code == 201 and j["appointment"]["status"] == "BOOKED" and j["appointment"]["confirmation_no"].startswith("CNF-") and "24 hours" in j["cancellation_policy"]
    assert c.post(url, json={**F(m), "slot_id": sid}, headers=H).json()["appointment"]["appointment_id"] == aid                      # retry is idempotent
    assert sid not in [x["slot_id"] for x in c.get(f"/v1/members/{m['member_id']}/appointment-slots", params={**F(m), "npi": r["npi"]}, headers=H).json()["slots"]]
    assert c.get(url, params=F(m), headers=H).json()["count"] == 1
    d = c.delete(f"{url}/{aid}", params={**F(m), "dry_run": True}, headers=H); assert d.status_code == 200 and d.json()["slot_released"] is False
    d = c.delete(f"{url}/{aid}", params=F(m), headers=H); assert d.status_code == 200 and d.json()["appointment"]["status"] == "CANCELLED" and d.json()["slot_released"]
    assert sid in [x["slot_id"] for x in c.get(f"/v1/members/{m['member_id']}/appointment-slots", params={**F(m), "npi": r["npi"]}, headers=H).json()["slots"]]
    assert c.delete(f"{url}/{aid}", params=F(m), headers=H).json()["error"]["code"] == "NOT_BOOKED"

def test_slot_taken_returns_alternatives(fresh, con):
    c, H = fresh; m = active_member(con, 0); o = active_member(con, 1); r, s = slot_target(c, H, m); sid = s["slots"][0]["slot_id"]
    assert c.post(f"/v1/members/{m['member_id']}/appointments", json={**F(m), "slot_id": sid}, headers=H).status_code == 201
    x = c.post(f"/v1/members/{o['member_id']}/appointments", json={**F(o), "slot_id": sid}, headers=H); e = x.json()["error"]
    if x.status_code == 409 and e["code"] == "SLOT_TAKEN": assert isinstance(e["details"]["alternative_slot_ids"], list)
    else: assert e["code"] == "OUT_OF_NETWORK"                    # the second member's plan may not include this provider

def test_double_booking_same_time_conflicts(fresh, con):
    c, H = fresh; m = active_member(con); r, s = slot_target(c, H, m); url = f"/v1/members/{m['member_id']}/appointments"
    first = s["slots"][0]; assert c.post(url, json={**F(m), "slot_id": first["slot_id"]}, headers=H).status_code == 201
    twin = one(con, "SELECT s.slot_id FROM appointment_slot s WHERE s.slot_start=? AND s.slot_id!=? AND s.npi!=? LIMIT 1", first["start"], first["slot_id"], r["npi"])
    if twin:
        x = c.post(url, json={**F(m), "slot_id": twin[0]}, headers=H); assert x.json()["error"]["code"] in ("TIME_CONFLICT", "OUT_OF_NETWORK")

def test_unknown_slot_404_and_bad_input_422(fresh, con):
    c, H = fresh; m = active_member(con); url = f"/v1/members/{m['member_id']}/appointments"
    assert c.post(url, json={**F(m), "slot_id": "does-not-exist-123"}, headers=H).json()["error"]["code"] == "SLOT_NOT_FOUND"
    assert c.post(url, json={**F(m)}, headers=H).status_code == 422

def test_caregiver_appointments_scope(fresh, con):
    c, H = fresh; no = caregiver(con, scope_lacks="APPOINTMENTS"); r = c.get(f"/v1/members/{no['member_id']}/appointments", params=cg(no), headers=H)
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_NOT_GRANTED"
    ok = caregiver(con, scope_has="APPOINTMENTS"); assert c.get(f"/v1/members/{ok['member_id']}/appointments", params=cg(ok), headers=H).status_code == 200

def test_cancel_inside_24h_is_refused(fresh, con, db_path):
    c, H = fresh; m = active_member(con); url = f"/v1/members/{m['member_id']}/appointments"
    soon = one(con, "SELECT s.* FROM appointment_slot s WHERE s.slot_start < '2026-10-13T12:00:00' AND s.slot_start > '2026-10-12T12:00:00' LIMIT 1")
    if not soon: pytest.skip("no slot inside the 24-hour window")
    from hf_factory.api.app import Config
    # book directly into the state DB would bypass the API; instead book through the API when the member is in network, else skip
    b = c.post(url, json={**F(m), "slot_id": soon["slot_id"]}, headers=H)
    if b.status_code != 201: pytest.skip("slot's provider not in this member's network")
    d = c.delete(f"{url}/{b.json()['appointment']['appointment_id']}", params=F(m), headers=H); assert d.status_code == 409 and d.json()["error"]["code"] == "CANCEL_WINDOW_CLOSED" and d.json()["error"]["details"]["office_phone"]

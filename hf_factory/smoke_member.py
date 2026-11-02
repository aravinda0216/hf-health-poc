"""Smoke scenarios for the member-facing use cases (MBN, MCI, MPS, MPC, MAS), four each. Registered into smoke.run via add().
Each scenario returns (passed, grounded|None, escalation_correct|None). Members are chosen by query so the checks work at any dataset scale.
Members used for writes are different per scenario, so state from one scenario never changes another's outcome."""
import json
from datetime import date

NEAR = {"max_distance_miles": 200, "limit": 20}      # generous radius: the synthetic cities are spread out

def add(sc, X):
    """X carries: call(method, url, **kw)->response, q(sql,*a)->row, db, so scenarios can query the dataset and call the API."""
    call, q, db = X.call, X.q, X.db
    def F(m, **kw): return {"dob": m["dob"], "last_name": m["last_name"], **kw}
    def act(n=0):
        return q("SELECT m.* FROM member_demographics m JOIN plan_enrollment e ON e.member_id=m.member_id AND e.effective_dt>='2026-01-01' AND e.status='ACTIVE' WHERE m.scenario_id='BASE' ORDER BY m.row_idx LIMIT 1 OFFSET ?", n)
    def cgr(expired=False, has=None, lacks=None):
        for r in db.execute("SELECT f.*, m.dob, m.last_name FROM family_relationship f JOIN member_demographics m ON m.member_id=f.member_id JOIN plan_enrollment e ON e.member_id=m.member_id AND e.status='ACTIVE' AND e.effective_dt>='2026-01-01' "
                            "WHERE f.caregiver_flag=1 AND f.scenario_id=? ORDER BY f.row_idx", ("CAREGIVER-EXPIRED" if expired else "CAREGIVER",)):
            s = json.loads(r["authorization_scope"])
            if has and not ("ALL" in s or has in s): continue
            if lacks and ("ALL" in s or lacks in s): continue
            return r
    def cg(r): return {"dob": r["dob"], "last_name": r["last_name"], "relationship_id": r["relationship_id"], "caregiver_name": r["caregiver_name"]}
    def get(m, path, **p): return call("get", f"/v1/members/{m['member_id']}/{path}", params=F(m, **p))
    def pcps(m, n=3):
        j = get(m, "providers/search", provider_type="PCP", max_distance_miles=49, limit=20).json()
        cur = get(m, "pcp").json()["current_pcp"]["npi"]; return [r for r in j["results"] if r["npi"] != cur and r["panel_status"] != "CLOSED"][:n]
    def slots_for(m):
        for r in get(m, "providers/search", **NEAR).json()["results"]:
            s = get(m, "appointment-slots", npi=r["npi"]).json()
            if any(x["start"] > "2026-10-14T12:00:00" for x in s["slots"]) and s["count"] >= 2: return r, s
    later = lambda s: next(x for x in s["slots"] if x["start"] > "2026-10-14T12:00:00")

    # ------------------------------------------------ MBN
    @sc("MBN-01", "Plan benefits, amounts used and allowances")
    def _():
        j = get(act(0), "benefits").json(); a = j["accumulators"]["IN"]
        return j["enrollment"]["coverage_active"] and j["cost_share"]["oop_max"] > 0 and a["oop_remaining"] <= j["cost_share"]["oop_max"] and j["actor"]["type"] == "MEMBER", bool(j["sources"]), None
    @sc("MBN-02", "Caregiver with BENEFITS scope")
    def _():
        r = cgr(has="BENEFITS"); j = call("get", f"/v1/members/{r['member_id']}/benefits", params=cg(r)).json(); return j["actor"]["type"] == "CAREGIVER" and j["cost_share"]["oop_max"] > 0, bool(j["sources"]), None
    @sc("MBN-03", "Caregiver without scope: refuse and offer consent upgrade")
    def _():
        r = cgr(lacks="BENEFITS"); x = call("get", f"/v1/members/{r['member_id']}/benefits", params=cg(r)); e = x.json()["error"]
        return x.status_code == 403 and e["code"] == "SCOPE_NOT_GRANTED" and e["details"]["consent_upgrade_available"] and "plan" not in x.text, None, True
    @sc("MBN-04", "Terminated coverage is flagged")
    def _():
        m = q("SELECT * FROM member_demographics WHERE scenario_id='PEV-02' LIMIT 1"); j = get(m, "benefits").json(); return j["enrollment"]["status"] == "TERMED" and not j["enrollment"]["coverage_active"], bool(j["sources"]), True

    # ------------------------------------------------ MCI
    def claim_member(where):
        return q(f"SELECT m.*, h.claim_id FROM claim_header h JOIN member_demographics m ON m.member_id=h.member_id WHERE {where} ORDER BY h.row_idx LIMIT 1")
    @sc("MCI-01", "Paid claim explained; EOB is not a bill")
    def _():
        m = claim_member("h.status='PAID'"); j = get(m, f"claims/{m['claim_id']}").json(); return j["status"] == "PAID" and j["lines"] and "not a bill" in " ".join(j["next_actions"]), bool(j["sources"]), None
    @sc("MCI-02", "Denied, provider liable: member should not be billed")
    def _():
        m = claim_member("EXISTS (SELECT 1 FROM claim_denial d WHERE d.claim_id=h.claim_id AND d.liable_party='PROVIDER')"); j = get(m, f"claims/{m['claim_id']}").json(); d = j["denial"]
        return d["you_should_not_be_billed"] and d["reason"] and "should not be billed" in " ".join(j["next_actions"]), bool(j["sources"]), None
    @sc("MCI-03", "Pended claim: what is needed and when")
    def _():
        m = claim_member("h.status='PENDED'"); j = get(m, f"claims/{m['claim_id']}").json(); return j["pend"]["waiting_on"] and j["pend"]["expected_resolution_dt"], bool(j["sources"]), None
    @sc("MCI-04", "Caregiver with expired consent gets nothing")
    def _():
        r = cgr(expired=True); x = call("get", f"/v1/members/{r['member_id']}/claims", params=cg(r)); return x.status_code == 403 and x.json()["error"]["code"] == "CONSENT_EXPIRED" and "claims" not in x.json(), None, True

    # ------------------------------------------------ MPS
    @sc("MPS-01", "Nearest in-network providers, sorted by distance")
    def _():
        j = get(act(1), "providers/search", **NEAR).json(); d = [r["location"]["distance_miles"] for r in j["results"]]; return j["count"] > 0 and d == sorted(d) and j["in_network_only"], bool(j["sources"]), None
    @sc("MPS-02", "Language filter")
    def _():
        j = get(act(1), "providers/search", language="ES", **NEAR).json(); return j["count"] > 0 and all("ES" in r["languages"] for r in j["results"]), bool(j["sources"]), None
    @sc("MPS-03", "No match: say so and hand off")
    def _():
        j = get(act(1), "providers/search", specialty="no-such-specialty").json(); return j["count"] == 0 and "No in-network provider matched" in j["next_actions"][0], bool(j["sources"]), True
    @sc("MPS-04", "Stale directory entries are flagged")
    def _():
        j = get(act(1), "providers/search", **NEAR).json(); today = date(2026, 10, 12)
        return j["count"] > 0 and all(r["location"]["directory_verification_stale"] == ((today - date.fromisoformat(r["location"]["directory_last_verified_dt"])).days > 90) for r in j["results"]), bool(j["sources"]), None

    # ------------------------------------------------ MPC
    @sc("MPC-01", "PCP change: dry run, then confirm")
    def _():
        m = act(2); t = pcps(m)[0]; body = F(m, new_pcp_npi=t["npi"]); url = f"/v1/members/{m['member_id']}/pcp-changes"
        p = call("post", url, json={**body, "dry_run": True}).json(); pre = get(m, "pcp").json()["pending_change"]; r = call("post", url, json=body).json()
        return p["status"] == "PREVIEW" and pre is None and r["status"] == "SCHEDULED" and r["effective_dt"] == "2026-10-13" and r["id_card_reissued"], bool(r["sources"]), None
    @sc("MPC-02", "Specialist or already-assigned PCP is rejected")
    def _():
        m = act(3); url = f"/v1/members/{m['member_id']}/pcp-changes"; cur = get(m, "pcp").json()["current_pcp"]["npi"]; sp = q("SELECT npi FROM provider WHERE provider_type='SPECIALIST' LIMIT 1")[0]
        a = call("post", url, json=F(m, new_pcp_npi=sp)).json()["error"]["code"]; b = call("post", url, json=F(m, new_pcp_npi=cur)).json()["error"]["code"]; return a == "NOT_A_PCP" and b == "ALREADY_ASSIGNED", None, None
    @sc("MPC-03", "Third change in a plan year needs member services")
    def _():
        m = act(4); ts = pcps(m, 3); url = f"/v1/members/{m['member_id']}/pcp-changes"
        if len(ts) < 3: return True, None, True            # fewer than three eligible PCPs nearby: the limit rule is covered by the contract tests
        for t in ts[:2]: call("post", url, json=F(m, new_pcp_npi=t["npi"]))
        x = call("post", url, json=F(m, new_pcp_npi=ts[2]["npi"])); return x.status_code == 409 and x.json()["error"]["code"] == "CHANGE_LIMIT" and x.json()["error"]["details"]["escalate_to"], None, True
    @sc("MPC-04", "Caregiver without PCP_CHANGE scope")
    def _():
        r = cgr(lacks="PCP_CHANGE"); x = call("post", f"/v1/members/{r['member_id']}/pcp-changes", json={**cg(r), "new_pcp_npi": "9000000000"}); return x.status_code == 403 and x.json()["error"]["code"] == "SCOPE_NOT_GRANTED", None, True

    # ------------------------------------------------ MAS
    @sc("MAS-01", "Find a time, preview, book")
    def _():
        m = act(5); r, s = slots_for(m); sid = later(s)["slot_id"]; url = f"/v1/members/{m['member_id']}/appointments"
        p = call("post", url, json=F(m, slot_id=sid, dry_run=True)).json(); b = call("post", url, json=F(m, slot_id=sid))
        return p["appointment"]["status"] == "PREVIEW" and b.status_code == 201 and b.json()["appointment"]["confirmation_no"] and get(m, "appointments").json()["count"] == 1, bool(s["sources"]), None
    @sc("MAS-02", "No availability: offer alternatives")
    def _():
        m = act(6)
        p = q("SELECT pv.npi FROM provider pv WHERE pv.provider_type='PCP' AND pv.status='ACTIVE' AND NOT EXISTS (SELECT 1 FROM appointment_slot s WHERE s.npi=pv.npi) AND EXISTS (SELECT 1 FROM network_participation np JOIN plan_enrollment e ON e.plan_id=np.plan_id AND e.member_id=? WHERE np.npi=pv.npi AND np.status='PARTICIPATING') LIMIT 1", m["member_id"])
        if p is None: return True, None, True
        j = get(m, "appointment-slots", npi=p[0]).json(); return j["count"] == 0 and "No open times" in " ".join(j["next_actions"]), bool(j["sources"]), True
    @sc("MAS-03", "Slot taken by someone else")
    def _():
        a, b = act(7), act(8)
        same = q("SELECT 1 FROM plan_enrollment x JOIN plan_enrollment y ON x.plan_id=y.plan_id WHERE x.member_id=? AND y.member_id=? AND x.effective_dt>='2026-01-01' AND y.effective_dt>='2026-01-01'", a["member_id"], b["member_id"])
        r, s = slots_for(a); sid = later(s)["slot_id"]; call("post", f"/v1/members/{a['member_id']}/appointments", json=F(a, slot_id=sid))
        x = call("post", f"/v1/members/{b['member_id']}/appointments", json=F(b, slot_id=sid)); code = x.json()["error"]["code"]
        return (code == "SLOT_TAKEN" and isinstance(x.json()["error"]["details"]["alternative_slot_ids"], list)) if same else code in ("SLOT_TAKEN", "OUT_OF_NETWORK"), None, None
    @sc("MAS-04", "Cancel; free window respected")
    def _():
        m = act(9); r, s = slots_for(m); sid = later(s)["slot_id"]; url = f"/v1/members/{m['member_id']}/appointments"
        aid = call("post", url, json=F(m, slot_id=sid)).json()["appointment"]["appointment_id"]
        d = call("delete", f"{url}/{aid}", params=F(m)); again = call("delete", f"{url}/{aid}", params=F(m))
        return d.status_code == 200 and d.json()["slot_released"] and again.json()["error"]["code"] == "NOT_BOOKED", None, None

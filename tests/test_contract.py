"""Contract tests: 4 per business endpoint + token/OpenAPI checks (architecture doc: contract_tests.total = endpoints x 4)."""
import pytest

# Helpers: first row of a query, and a different PCP's NPI (used to prove that unrelated providers see nothing).
def row(con, sql, *a): return con.execute(sql, a).fetchone()
def other_npi(con, npi): return row(con, "SELECT npi FROM provider WHERE npi!=? AND provider_type='PCP' LIMIT 1", npi)[0]

# ---------------- token / openapi
def test_token_ok(client):
    r = client.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "demo-secret-change-me"}); assert r.status_code == 200 and r.json()["token_type"] == "Bearer"
def test_token_bad_secret(client):
    r = client.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "x"}); assert r.status_code == 401 and r.json()["is_synthetic"] is True
def test_token_bad_grant(client):
    assert client.post("/v1/oauth/token", data={"grant_type": "password", "client_id": "a", "client_secret": "b"}).status_code == 400
def test_openapi_is_31(client):
    spec = client.get("/openapi.json").json(); assert spec["openapi"].startswith("3.1") and any("/v1/claims/{claim_id}/status" == p for p in spec["paths"])

# ---------------- GET /claims/{id}/status
def test_claim_status_paid_ok(client, hdr, con, tin):
    c = row(con, "SELECT claim_id, billing_npi, total_paid FROM claim_header WHERE scenario_id='PCS-01' AND status='PAID' LIMIT 1")
    r = client.get(f"/v1/claims/{c[0]}/status", params={"npi": c[1], "tin": tin(c[1])}, headers=hdr); j = r.json()
    assert r.status_code == 200 and j["is_synthetic"] is True and j["scenario_id"] == "PCS-01" and j["status"] == "PAID" and j["payment"]["amount"] == pytest.approx(c[2])
def test_claim_status_requires_token(client, con):
    c = row(con, "SELECT claim_id FROM claim_header LIMIT 1"); assert client.get(f"/v1/claims/{c[0]}/status", params={"npi": "x", "tin": "y"}).status_code == 401
def test_claim_status_tin_mismatch_discloses_nothing(client, hdr, con, tin):
    c = row(con, "SELECT claim_id, billing_npi FROM claim_header WHERE scenario_id='PCS-04' LIMIT 1")
    wrong = row(con, "SELECT tin FROM provider WHERE tin!=? LIMIT 1", tin(c[1]))[0]
    r = client.get(f"/v1/claims/{c[0]}/status", params={"npi": c[1], "tin": wrong}, headers=hdr)
    assert r.status_code == 403 and r.json()["error"]["code"] == "VERIFICATION_FAILED" and "status" not in r.json() and "total_paid" not in r.text
def test_claim_status_unrelated_provider_404(client, hdr, con, tin):
    c = row(con, "SELECT claim_id, billing_npi FROM claim_header WHERE rendering_npi IS NOT NULL LIMIT 1"); o = row(con, "SELECT npi, tin FROM provider WHERE provider_type='PCP' AND npi NOT IN (SELECT billing_npi FROM claim_header WHERE claim_id=?) AND npi NOT IN (SELECT COALESCE(rendering_npi,'') FROM claim_header WHERE claim_id=?) LIMIT 1", c[0], c[0])
    assert client.get(f"/v1/claims/{c[0]}/status", params={"npi": o[0], "tin": o[1]}, headers=hdr).status_code == 404

# ---------------- GET /authorizations/{id}/status
def _auth(con, scn): return row(con, "SELECT auth_id, requesting_npi FROM auth_request WHERE scenario_id=? LIMIT 1", scn)
def test_auth_status_ok(client, hdr, con, tin):
    a = _auth(con, "PAS-01"); r = client.get(f"/v1/authorizations/{a[0]}/status", params={"npi": a[1], "tin": tin(a[1])}, headers=hdr); j = r.json()
    assert r.status_code == 200 and j["status"] == "IN_REVIEW" and j["hours_remaining"] > 0 and j["is_synthetic"] and j["scenario_id"] == "PAS-01"
def test_auth_status_requires_token(client, con):
    assert client.get(f"/v1/authorizations/{_auth(con, 'PAS-01')[0]}/status", params={"npi": "1", "tin": "2"}).status_code == 401
def test_auth_status_other_provider_404(client, hdr, con, tin):
    a = _auth(con, "PAS-01"); o = row(con, "SELECT npi FROM provider WHERE npi NOT IN (SELECT requesting_npi FROM auth_request WHERE auth_id=? UNION SELECT servicing_npi FROM auth_request WHERE auth_id=?) AND provider_type='PCP' LIMIT 1", a[0], a[0])[0]
    assert client.get(f"/v1/authorizations/{a[0]}/status", params={"npi": o, "tin": tin(o)}, headers=hdr).status_code == 404
def test_auth_denied_hides_reviewer_and_cites_criteria(client, hdr, con, tin):
    a = _auth(con, "PAS-04"); j = client.get(f"/v1/authorizations/{a[0]}/status", params={"npi": a[1], "tin": tin(a[1])}, headers=hdr).json()
    assert j["determination"]["reviewer"]["name"] is None and j["determination"]["denial_rationale"] and j["determination"]["appeal_rights_sent_dt"] and j["sources"]

# ---------------- POST /authorizations/{id}/documents
def _body(npi, tin_, doc="PT_EVAL", sha="a" * 64): return {"npi": npi, "tin": tin_, "doc_type": doc, "file_name": "eval.pdf", "size_bytes": 1234, "sha256": sha}
def test_upload_document_resumes_clock(client, hdr, con, tin):
    a = _auth(con, "PAS-03"); r = client.post(f"/v1/authorizations/{a[0]}/documents", json=_body(a[1], tin(a[1])), headers=hdr); j = r.json()
    assert r.status_code == 201 and j["requirement_state_after"] == "RECEIVED" and j["clock_paused_after"] is False and j["upload_id"].startswith("UPL-")
    s = client.get(f"/v1/authorizations/{a[0]}/status", params={"npi": a[1], "tin": tin(a[1])}, headers=hdr).json(); assert s["missing_documents"] == [] and s["effective_status"] == "IN_REVIEW"
def test_upload_document_validates_body(client, hdr, con, tin):
    a = _auth(con, "PAS-03"); assert client.post(f"/v1/authorizations/{a[0]}/documents", json=_body(a[1], tin(a[1]), sha="zz"), headers=hdr).status_code == 422
def test_upload_document_wrong_tin_403(client, hdr, con, tin):
    a = _auth(con, "PAS-03"); assert client.post(f"/v1/authorizations/{a[0]}/documents", json=_body(a[1], "99-0000000"), headers=hdr).status_code == 403
def test_upload_document_closed_auth_409(client, hdr, con, tin):
    a = _auth(con, "PAS-04"); assert client.post(f"/v1/authorizations/{a[0]}/documents", json=_body(a[1], tin(a[1]), doc="LABS"), headers=hdr).status_code == 409

# ---------------- POST /eligibility/verify
def _prov(con): return row(con, "SELECT npi, tin FROM provider WHERE provider_type='PCP' LIMIT 1")
def test_eligibility_active(client, hdr, con):
    m = row(con, "SELECT member_id FROM member_demographics WHERE scenario_id='PEV-01'")[0]; p = _prov(con)
    j = client.post("/v1/eligibility/verify", json={"npi": p[0], "tin": p[1], "date_of_service": "2026-10-01", "members": [{"member_id": m}]}, headers=hdr).json(); r0 = j["results"][0]
    assert r0["coverage_status"] == "ACTIVE" and r0["pcp"] and r0["reference_number"].startswith("ELG-") and r0["prior_authorization"]["advanced_imaging_requires_pa"] and "not a guarantee" in j["disclaimer"]
def test_eligibility_bulk_limited(client, hdr, con):
    p = _prov(con); ms = [{"member_id": r[0]} for r in con.execute("SELECT member_id FROM member_demographics LIMIT 25")]
    r = client.post("/v1/eligibility/verify", json={"npi": p[0], "tin": p[1], "date_of_service": "2026-10-01", "members": ms}, headers=hdr)
    assert r.status_code == 413 and r.json()["error"]["code"] == "BULK_LOOKUP_LIMIT" and "270/271" in r.json()["error"]["message"]
def test_eligibility_retro_termed_warns(client, hdr, con):
    m = row(con, "SELECT member_id FROM member_demographics WHERE scenario_id='PEV-02'")[0]; p = _prov(con); base = {"npi": p[0], "tin": p[1], "members": [{"member_id": m}]}
    a = client.post("/v1/eligibility/verify", json={**base, "date_of_service": "2026-10-01"}, headers=hdr).json()["results"][0]
    b = client.post("/v1/eligibility/verify", json={**base, "date_of_service": "2026-10-12"}, headers=hdr).json()["results"][0]
    assert a["coverage_status"] == "ACTIVE" and any("retroactively" in w for w in a["warnings"]) and b["coverage_status"] == "TERMED"
def test_eligibility_unknown_member_not_found(client, hdr, con):
    p = _prov(con); r = client.post("/v1/eligibility/verify", json={"npi": p[0], "tin": p[1], "date_of_service": "2026-10-01", "members": [{"member_id": "nope"}]}, headers=hdr)
    assert r.status_code == 200 and r.json()["results"][0]["coverage_status"] == "NOT_FOUND"

# ---------------- POST /identity/verify
def test_identity_provider_verified(client, hdr, con):
    p = _prov(con); j = client.post("/v1/identity/verify", json={"role": "PROVIDER", "npi": p[0], "tin": p[1]}, headers=hdr).json(); assert j["verified"] and j["verification_id"].startswith("VER-")
def test_identity_provider_wrong_tin(client, hdr, con):
    p = _prov(con); j = client.post("/v1/identity/verify", json={"role": "PROVIDER", "npi": p[0], "tin": "99-0000001"}, headers=hdr).json(); assert j["verified"] is False and j["verification_id"] is None
def test_identity_member(client, hdr, con):
    m = row(con, "SELECT member_id, last_name, dob FROM member_demographics LIMIT 1")
    ok = client.post("/v1/identity/verify", json={"role": "MEMBER", "patient": {"member_id": m[0], "last_name": m[1], "dob": m[2]}}, headers=hdr).json()
    bad = client.post("/v1/identity/verify", json={"role": "MEMBER", "patient": {"member_id": m[0], "last_name": m[1], "dob": "1900-01-01"}}, headers=hdr).json()
    assert ok["verified"] and not bad["verified"]
def test_identity_requires_token(client): assert client.post("/v1/identity/verify", json={"role": "PROVIDER"}).status_code == 401

# ---------------- GET /knowledge/search
def test_knowledge_grounded(client, hdr):
    j = client.get("/v1/knowledge/search", params={"q": "what does CO-16 mean missing information denial"}, headers=hdr).json()
    assert j["abstain"] is False and j["results"] and j["results"][0]["asset_type"] and "Missing Info" in j["results"][0]["asset_title"]
def test_knowledge_abstains(client, hdr):
    assert client.get("/v1/knowledge/search", params={"q": "who won the football game"}, headers=hdr).json()["abstain"] is True
def test_knowledge_validates_query(client, hdr): assert client.get("/v1/knowledge/search", params={"q": "x"}, headers=hdr).status_code == 422
def test_knowledge_returns_only_active_provider_assets(client, hdr, con):
    j = client.get("/v1/knowledge/search", params={"q": "prior authorization timelines urgent standard", "top_k": 10}, headers=hdr).json()
    ids = {r["asset_id"] for r in j["results"]}; st = {r[0] for r in con.execute(f"SELECT status FROM knowledge_asset WHERE asset_id IN ({','.join('?'*len(ids))})", list(ids))} if ids else set()
    assert j["results"] and st == {"ACTIVE"} and all("Evidence of Coverage" not in r["asset_title"] for r in j["results"])

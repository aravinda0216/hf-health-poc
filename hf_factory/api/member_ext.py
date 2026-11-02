"""API engine extension: member-facing use cases MBN (benefits), MCI (claims), MPS (provider search), MPC (PCP change), MAS (appointments).

Access model (every call is stateless, like the provider endpoints):
  * Verification: member_id (path) + dob + last_name. Any failure is the same 403 and discloses nothing, not even whether the member exists.
  * Caregiver ("on behalf of"): also relationship_id + caregiver_name. The consent record must be verified, in its date window, and its authorization_scope must
    include the scope the endpoint needs (or ALL). Out-of-scope calls return 403 SCOPE_NOT_GRANTED and offer a consent upgrade; they never return partial data.
  * Writes (PCP change, booking, cancel) accept dry_run=true: all validation runs, nothing is saved.
Mutations go to the state database; the dataset is never modified."""
import hashlib, json, math
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import Depends, Query
from . import models_member as MM

CHANGES_PER_YEAR = 2          # PCP changes allowed per plan year (Member Guide: Choosing and Changing a PCP)
MAX_PCP_DISTANCE_MI = 50      # a PCP must practise within this distance of the member's home
CANCEL_HOURS = 24             # free cancellation window before the start time
# Plain-language claim status shown to members.
MEMBER_STATUS = {"PAID": "Paid", "DENIED": "Not paid", "PENDED": "Being reviewed: we need more information", "APPEALED": "Under appeal", "REPROCESSED": "Processed again after review"}
# Scope each endpoint needs from a caregiver (None = any verified caller).
SCOPE = {"benefits": "BENEFITS", "claims": "CLAIMS", "billing": "BILLING", "appointments": "APPOINTMENTS", "pcp": "PCP_CHANGE"}

def miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2); dl = math.radians(lon2 - lon1); dp = p2 - p1
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))

def register(router, h):
    ApiError, con_ro, state, AS_OF, NOW, lock, prot, GUARD, ERR = h.ApiError, h.con_ro, h.state, h.AS_OF, h.NOW, h.lock, h.prot, h.GUARD, h.ERR
    FAIL = ("The member could not be verified. No information was disclosed. Ask the caller to check the details or use the member portal.", None)

    # ---------------------------------------------------------------- verification + caregiver authority
    def verify(c, member_id, f, scope=None):
        """Return (member row, Actor). `f` carries dob, last_name and optional relationship_id/caregiver_name. scope: SCOPE value required from a caregiver."""
        m = c.execute("SELECT * FROM member_demographics WHERE member_id=?", (member_id,)).fetchone()
        bad = m is None or m["dob"] != f.dob.isoformat() or m["last_name"].lower() != f.last_name.strip().lower()
        if bad: raise ApiError(403, "VERIFICATION_FAILED", FAIL[0])
        if not f.relationship_id and not f.caregiver_name: return m, MM.Actor(type="MEMBER")
        if not f.relationship_id or not f.caregiver_name: raise ApiError(403, "VERIFICATION_FAILED", FAIL[0])
        r = c.execute("SELECT * FROM family_relationship WHERE relationship_id=? AND member_id=? AND caregiver_flag=1", (f.relationship_id, member_id)).fetchone()
        if r is None or (r["caregiver_name"] or "").strip().lower() != f.caregiver_name.strip().lower() or not r["verified"]:
            raise ApiError(403, "VERIFICATION_FAILED", FAIL[0])
        if not (r["consent_effective_dt"] <= AS_OF.isoformat() <= r["consent_expiry_dt"]):
            raise ApiError(403, "CONSENT_EXPIRED", "The caregiver's authorization is not in effect. The member must renew consent before the caregiver can act for them.",
                           {"consent_expiry_dt": r["consent_expiry_dt"], "renewal": "The member can renew consent by phone or in the portal."}, m["scenario_id"])
        granted = json.loads(r["authorization_scope"] or "[]")
        if scope and "ALL" not in granted and scope not in granted:
            raise ApiError(403, "SCOPE_NOT_GRANTED", f"The caregiver's authorization does not include {scope}. Nothing was disclosed or changed.",
                           {"required_scope": scope, "granted_scopes": granted, "consent_upgrade_available": True, "how": "The member can add this permission by phone or in the portal."}, m["scenario_id"])
        return m, MM.Actor(type="CAREGIVER", caregiver_relationship=r["caregiver_relationship"], scopes_checked=scope)

    def has_scope(c, member_id, f, scope):
        """True for the member themself or a verified caregiver whose scope includes `scope` (used for optional detail such as amounts)."""
        if not f.relationship_id: return True
        r = c.execute("SELECT authorization_scope FROM family_relationship WHERE relationship_id=? AND member_id=?", (f.relationship_id, member_id)).fetchone()
        g = json.loads(r["authorization_scope"] or "[]") if r else []
        return "ALL" in g or scope in g

    def ref(m): return "MBR-" + hashlib.sha256(m["member_id"].encode()).hexdigest()[:10].upper()
    def factors(dob: date = Query(..., description="Member date of birth (YYYY-MM-DD)"), last_name: str = Query(..., min_length=1, max_length=60),
                relationship_id: Optional[str] = Query(None, max_length=64, description="Caregiver only"), caregiver_name: Optional[str] = Query(None, max_length=80, description="Caregiver only")):
        return MM.Factors(dob=dob, last_name=last_name, relationship_id=relationship_id, caregiver_name=caregiver_name)

    def enrollment(c, member_id):
        # The newest enrollment row is the current-year one (the prior-year row, when it exists, is older and TERMED).
        return c.execute("SELECT e.*, p.plan_name, p.lob, p.plan_year, p.network_type, p.deductible, p.oop_max, p.copay_pcp, p.copay_specialist, p.coinsurance_pct, p.allowances FROM plan_enrollment e JOIN plan p ON p.plan_id=e.plan_id "
                         "WHERE e.member_id=? ORDER BY e.effective_dt DESC LIMIT 1", (member_id,)).fetchone()
    def active_or_409(e, scn):
        if e is None or e["status"] not in ("ACTIVE", "GRACE_PERIOD"):
            raise ApiError(409, "COVERAGE_NOT_ACTIVE", "Coverage is not active, so this action is not available. Send the member to member services for enrollment questions.", {"coverage_status": e["status"] if e else "NONE"}, scn)
    def prov_name(p): return p["org_name"] or f"{p['first_name']} {p['last_name']}".strip()
    def d_(s): return datetime.fromisoformat(s.replace("Z", ""))
    def guide(c, name): return h.src(c, h.SRC_BASE + " WHERE a.asset_type='MEMBER_GUIDE' AND a.title=? AND a.status='ACTIVE' ORDER BY c.chunk_id LIMIT 2", f"Member Guide: {name}")

    # ---------------------------------------------------------------- MBN
    @router.get("/members/{member_id}/benefits", response_model=MM.BenefitsResponse, dependencies=prot, tags=["member-benefits"], operation_id="getMemberBenefits", responses=ERR,
                summary="Plan benefits, cost sharing, amounts used so far and allowances",
                openapi_extra=GUARD({"when": "Member asks what their plan covers, what a visit costs, how much of the deductible or out-of-pocket maximum is used, or about allowances.",
                                     "escalate": ["coverage terminated", "benefit dispute", "caregiver without BENEFITS scope"], "never": ["Quote the cost of a specific procedure", "Guarantee coverage", "Disclose without verification"]}))
    # Reads plan cost sharing and the member's accumulators (IN and OON) for the plan year. A caregiver needs BENEFITS scope.
    def benefits(member_id: str, f: MM.Factors = Depends(factors)):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["benefits"]); scn = m["scenario_id"]
        e = enrollment(c, member_id)
        if e is None: raise ApiError(404, "NO_ENROLLMENT", "No plan enrollment was found for this member.", None, scn)
        acc = {r["network"]: r for r in c.execute("SELECT * FROM member_accumulator WHERE member_id=? AND plan_year=?", (member_id, e["plan_year"]))}
        def a(net):
            r = acc.get(net); dm, om = (r["deductible_met"], r["oop_met"]) if r else (0.0, 0.0)
            return MM.AccumulatorInfo(deductible_met=dm, deductible_remaining=round(max(0, e["deductible"] - dm), 2), oop_met=om, oop_remaining=round(max(0, e["oop_max"] - om), 2))
        notes = ["Totals update after claims are processed, so very recent visits may not be included.", "Emergency care never needs prior approval."]
        if e["status"] == "GRACE_PERIOD": notes.insert(0, "Your premium is past due. Coverage continues for now; call member services to avoid a gap.")
        if e["status"] == "PENDING": notes.insert(0, "Your enrollment is pending. Benefits below apply once it is confirmed.")
        if e["status"] == "TERMED": notes.insert(0, f"Coverage ended on {e['term_dt']}. Benefits shown are for reference only.")
        return MM.BenefitsResponse(scenario_id=scn, member_ref=ref(m), actor=actor, plan=MM.PlanInfo(plan_id=e["plan_id"], name=e["plan_name"], lob=e["lob"], plan_year=e["plan_year"], network_type=e["network_type"]),
            enrollment=MM.EnrollmentInfo(status=e["status"], effective_dt=e["effective_dt"], term_dt=e["term_dt"], premium_status=e["premium_status"], coverage_active=e["status"] in ("ACTIVE", "GRACE_PERIOD")),
            cost_share=MM.CostShareInfo(primary_care_copay=e["copay_pcp"], specialist_copay=e["copay_specialist"], coinsurance_pct=e["coinsurance_pct"], deductible=e["deductible"], oop_max=e["oop_max"]),
            accumulators={"IN": a("IN"), "OON": a("OON")}, allowances={k: float(v) for k, v in json.loads(e["allowances"] or "{}").items()}, notes=notes,
            sources=h.src(c, h.SRC_BASE + " WHERE a.asset_type='BENEFIT_DOC' AND a.plan_id=? AND a.status='ACTIVE' LIMIT 2", e["plan_id"]) + guide(c, "Benefits and Cost Sharing"))

    # ---------------------------------------------------------------- MCI
    def claim_amounts(row, ok):
        return (row["total_billed"], row["total_paid"], row["member_resp"]) if ok else (None, None, None)
    def billing_name(c, npi):
        p = c.execute("SELECT * FROM provider WHERE npi=?", (npi,)).fetchone(); return prov_name(p) if p else "Unknown provider"

    @router.get("/members/{member_id}/claims", response_model=MM.MemberClaimsResponse, dependencies=prot, tags=["member-claims"], operation_id="listMemberClaims", responses=ERR,
                summary="The member's recent claims with plain-language status",
                openapi_extra=GUARD({"when": "Member asks about a recent visit, a bill, or which claims are pending or denied.", "escalate": ["member received a bill", "member wants to appeal", "caregiver asks for amounts without BILLING scope"],
                                     "never": ["Promise payment", "Show amounts when amounts_withheld is true"]}))
    # Newest first. Amounts (billed / plan paid / you may owe) are returned to the member, or to a caregiver with BILLING scope; otherwise amounts_withheld=true.
    def list_claims(member_id: str, f: MM.Factors = Depends(factors), status: Optional[str] = Query(None, pattern="^(PAID|DENIED|PENDED|APPEALED|REPROCESSED)$"), limit: int = Query(10, ge=1, le=50)):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["claims"]); ok = has_scope(c, member_id, f, SCOPE["billing"])
        q = "SELECT * FROM claim_header WHERE member_id=?" + (" AND status=?" if status else "") + " ORDER BY service_from_dt DESC, claim_id LIMIT ?"
        rows = c.execute(q, (member_id, status, limit) if status else (member_id, limit)).fetchall()
        out = []
        for r in rows:
            b, p, o = claim_amounts(r, ok)
            out.append(MM.MemberClaimSummary(claim_id=r["claim_id"], service_from_dt=r["service_from_dt"], provider_name=billing_name(c, r["billing_npi"]), claim_type=r["claim_type"], status=r["status"],
                                             member_status=MEMBER_STATUS.get(r["status"], r["status"].title()), billed=b, plan_paid=p, you_may_owe=o))
        nxt = ["Ask for a specific claim to hear the reason and what happens next."] + (["The caregiver does not have BILLING permission, so amounts are not shown. The member can add it."] if not ok else [])
        return MM.MemberClaimsResponse(scenario_id=m["scenario_id"], member_ref=ref(m), actor=actor, claims=out, count=len(out), amounts_withheld=not ok, next_actions=nxt, sources=guide(c, "Claims and Explanation of Benefits"))

    @router.get("/members/{member_id}/claims/{claim_id}", response_model=MM.MemberClaimDetail, dependencies=prot, tags=["member-claims"], operation_id="getMemberClaim", responses=ERR,
                summary="One claim explained in plain language: status, reason, who pays, next steps",
                openapi_extra=GUARD({"when": "Member wants a claim explained, especially a denied or pended one.", "escalate": ["member received a bill", "member wants to appeal"],
                                     "never": ["Tell the member to pay a provider-liable denial", "Promise the claim will be paid", "Show amounts when amounts_withheld is true"]}))
    # Explains a denial using the stored reason, who is liable and the appeal window. A provider-liable (CO) denial means the member should not be billed.
    def get_claim(member_id: str, claim_id: str, f: MM.Factors = Depends(factors)):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["claims"]); ok = has_scope(c, member_id, f, SCOPE["billing"])
        r = c.execute("SELECT * FROM claim_header WHERE claim_id=? AND member_id=?", (claim_id, member_id)).fetchone()
        if r is None: raise ApiError(404, "CLAIM_NOT_FOUND", "No claim with that id was found for this member.", None, m["scenario_id"])
        scn = r["scenario_id"]; b, p, o = claim_amounts(r, ok)
        lines = []
        for l in c.execute("SELECT l.*, rc.description FROM claim_line l LEFT JOIN ref_code rc ON rc.system='CPT_HCPCS' AND rc.code=l.cpt_hcpcs WHERE l.claim_id=? ORDER BY line_no", (claim_id,)):
            lines.append(MM.MemberClaimLine(line_no=l["line_no"], service=l["description"] or l["cpt_hcpcs"] or l["revenue_code"] or "Service", billed=l["billed"] if ok else None, plan_paid=l["paid"] if ok else None,
                                            you_may_owe=round((l["copay"] or 0) + (l["coinsurance"] or 0) + (l["deductible"] or 0), 2) if ok else None))
        dn = c.execute("SELECT * FROM claim_denial WHERE claim_id=? ORDER BY denial_id LIMIT 1", (claim_id,)).fetchone(); pe = c.execute("SELECT * FROM claim_pend WHERE claim_id=? ORDER BY pend_dt DESC LIMIT 1", (claim_id,)).fetchone()
        den = pend = None; nxt = []
        if dn:
            prov = dn["liable_party"] == "PROVIDER"; days = (d_(dn["appeal_deadline_dt"]).date() - AS_OF).days if dn["appeal_deadline_dt"] else None
            den = MM.MemberDenial(reason=dn["reason_text"], code=dn["carc"], who_is_responsible=("Your provider" if prov else "You"), you_should_not_be_billed=prov, can_appeal=bool(dn["appealable"]) and (days is None or days >= 0),
                                  appeal_deadline_dt=dn["appeal_deadline_dt"], days_left_to_appeal=days)
            nxt = (["You should not be billed for this denied amount. If you get a bill, do not pay it yet; contact member services."] if prov else ["This amount may be your responsibility. You can appeal if you disagree."]) + \
                  (["You can appeal this decision" + (f" until {dn['appeal_deadline_dt']}." if dn["appeal_deadline_dt"] else ".")] if dn["appealable"] else [])
        if pe and not pe["resolved_dt"]:
            pend = MM.MemberPend(reason=pe["pend_reason"].replace("_", " ").title(), waiting_on=pe["info_needed_from"], expected_resolution_dt=pe["expected_resolution_dt"])
            nxt = [f"We are waiting on {pe['info_needed_from'].lower()}. You do not need to pay anything yet. We expect to finish by {pe['expected_resolution_dt']}."]
        if not nxt and r["status"] == "PAID": nxt = ["Nothing else is needed. This explanation of benefits is not a bill."]
        expl = {"PAID": "The plan processed this claim and paid its share.", "DENIED": "The plan did not pay this claim.", "PENDED": "The plan is still working on this claim.", "APPEALED": "A review of this claim's denial is in progress.",
                "REPROCESSED": "The plan reviewed this claim again and processed a new version."}.get(r["status"], "The claim is being processed.")
        if den: expl += f" Reason: {den.reason}"
        return MM.MemberClaimDetail(scenario_id=scn, member_ref=ref(m), actor=actor, claim_id=claim_id, service_from_dt=r["service_from_dt"], provider_name=billing_name(c, r["billing_npi"]), claim_type=r["claim_type"],
                                    status=r["status"], member_status=MEMBER_STATUS.get(r["status"], r["status"].title()), billed=b, allowed=(r["total_allowed"] if ok else None), plan_paid=p, you_may_owe=o, paid_dt=r["paid_dt"],
                                    amounts_withheld=not ok, lines=lines, denial=den, pend=pend, explanation=expl, next_actions=nxt,
                                    sources=h.src(c, h.SRC_BASE + " WHERE a.asset_type='MEMBER_GUIDE' AND a.title=? AND a.status='ACTIVE' LIMIT 2", "Member Guide: Claims and Explanation of Benefits"))

    # ---------------------------------------------------------------- MPS
    def results_for(c, m, e, specialty=None, language=None, accepting=None, max_miles=25, limit=5, ptype=None):
        """In-network providers for the member's plan, nearest location first."""
        q = ("SELECT pv.*, np.tier, np.panel_status, l.* , l.city AS lcity, l.state AS lstate, l.zip AS lzip, l.phone AS lphone, l.address AS laddr FROM provider pv "
             "JOIN network_participation np ON np.npi=pv.npi AND np.plan_id=? AND np.status='PARTICIPATING' AND (np.term_dt IS NULL OR np.term_dt>=?) "
             "JOIN practice_location l ON l.npi=pv.npi WHERE pv.status='ACTIVE' AND pv.provider_type IN ('PCP','SPECIALIST')")
        a = [e["plan_id"], AS_OF.isoformat()]
        if ptype: q += " AND pv.provider_type=?"; a.append(ptype)
        if specialty: q += " AND LOWER(pv.specialty) LIKE ?"; a.append(f"%{specialty.lower()}%")
        if language: q += " AND pv.languages LIKE ?"; a.append(f'%"{language.upper()}"%')
        if accepting is not None: q += " AND l.accepting_new_patients=?"; a.append(int(accepting))
        res = []
        for r in c.execute(q, a):
            dist = miles(m["lat"], m["lon"], r["lat"], r["lon"])
            if dist > max_miles: continue
            stale = (AS_OF - date.fromisoformat(r["last_verified_dt"])).days > 90
            res.append((dist, MM.ProviderResult(npi=r["npi"], name=prov_name(r), provider_type=r["provider_type"], specialty=r["specialty"], gender=r["gender"], languages=json.loads(r["languages"] or "[]"), tier=r["tier"], panel_status=r["panel_status"],
                location=MM.ProviderLocation(location_id=r["location_id"], address=r["laddr"], city=r["lcity"], state=r["lstate"], zip=r["lzip"], phone=r["lphone"], distance_miles=round(dist, 1), accepting_new_patients=bool(r["accepting_new_patients"]),
                                             telehealth=bool(r["telehealth"]), accessibility=json.loads(r["accessibility"] or "[]"), directory_last_verified_dt=r["last_verified_dt"], directory_verification_stale=stale))))
        res.sort(key=lambda x: (x[0], x[1].npi)); return [r for _, r in res[:limit]]

    @router.get("/members/{member_id}/providers/search", response_model=MM.ProviderSearchResponse, dependencies=prot, tags=["member-providers"], operation_id="searchProviders", responses=ERR,
                summary="Find in-network providers near the member, nearest first",
                openapi_extra=GUARD({"when": "Member wants a doctor, specialist or PCP, optionally by specialty, language or distance.", "escalate": ["no in-network provider within the distance", "member needs urgent care"],
                                     "never": ["Recommend one provider as better", "Present out-of-network providers as in network"]}))
    # Only providers PARTICIPATING in the member's current plan are returned. Distance is from the member's home. directory_verification_stale means the location was last verified over 90 days ago.
    def search_providers(member_id: str, f: MM.Factors = Depends(factors), specialty: Optional[str] = Query(None, max_length=60), language: Optional[str] = Query(None, pattern="^[A-Za-z]{2}$"),
                         provider_type: Optional[str] = Query(None, pattern="^(PCP|SPECIALIST)$"), accepting_new_patients: Optional[bool] = Query(None), max_distance_miles: float = Query(25, gt=0, le=200), limit: int = Query(5, ge=1, le=20)):
        c = con_ro(); m, actor = verify(c, member_id, f); e = enrollment(c, member_id); active_or_409(e, m["scenario_id"])
        res = results_for(c, m, e, specialty, language, accepting_new_patients, max_distance_miles, limit, provider_type)
        nxt = ([] if res else ["No in-network provider matched. Try a larger distance, another specialty, or ask member services to review access options."]) + \
              (["Some locations were last verified over 90 days ago; suggest the member confirms by phone."] if any(r.location.directory_verification_stale for r in res) else [])
        return MM.ProviderSearchResponse(scenario_id=m["scenario_id"], member_ref=ref(m), actor=actor, plan_id=e["plan_id"], query=dict(specialty=specialty, language=language, provider_type=provider_type,
            accepting_new_patients=accepting_new_patients, max_distance_miles=max_distance_miles), results=res, count=len(res), next_actions=nxt, sources=guide(c, "Finding a Provider"))

    # ---------------------------------------------------------------- MPC
    def pcp_info(c, npi, eff, panel=None):
        p = c.execute("SELECT * FROM provider WHERE npi=?", (npi,)).fetchone(); l = c.execute("SELECT * FROM practice_location WHERE npi=? ORDER BY location_id LIMIT 1", (npi,)).fetchone()
        return MM.PcpInfo(npi=npi, name=prov_name(p), specialty=p["specialty"], effective_dt=eff, panel_status=panel, phone=l["phone"] if l else None, address=f"{l['address']}, {l['city']}, {l['state']}" if l else None)
    def pending(sdb, member_id):
        return sdb.execute("SELECT * FROM pcp_changes WHERE member_id=? ORDER BY created_dt DESC, change_id DESC LIMIT 1", (member_id,)).fetchone()

    @router.get("/members/{member_id}/pcp", response_model=MM.MemberPcpResponse, dependencies=prot, tags=["member-pcp"], operation_id="getMemberPcp", responses=ERR,
                summary="Current PCP, any pending change and how many changes remain this year",
                openapi_extra=GUARD({"when": "Member asks who their PCP is or wants to change it (call before changePcp).", "escalate": ["caregiver without PCP_CHANGE scope"], "never": ["Choose a PCP for the member"]}))
    def get_pcp(member_id: str, f: MM.Factors = Depends(factors)):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["pcp"]); s = state()
        try: used = s.execute("SELECT COUNT(*) FROM pcp_changes WHERE member_id=?", (member_id,)).fetchone()[0]; pc = pending(s, member_id)
        finally: s.close()
        a = c.execute("SELECT * FROM pcp_assignment WHERE member_id=? AND status='ACTIVE' ORDER BY effective_dt DESC LIMIT 1", (member_id,)).fetchone()
        cur = None
        if a:
            np_ = c.execute("SELECT panel_status FROM network_participation np JOIN plan_enrollment e ON e.plan_id=np.plan_id WHERE np.npi=? AND e.member_id=? LIMIT 1", (a["pcp_npi"], member_id)).fetchone()
            cur = pcp_info(c, a["pcp_npi"], a["effective_dt"], np_["panel_status"] if np_ else None)
        pend = MM.PendingPcp(change_id=pc["change_id"], new_pcp=pcp_info(c, pc["new_pcp_npi"], pc["effective_dt"]), effective_dt=pc["effective_dt"]) if pc and pc["effective_dt"] > AS_OF.isoformat() else None
        notes = [f"You can change your PCP up to {CHANGES_PER_YEAR} times per plan year. {max(0, CHANGES_PER_YEAR - used)} left."] + ([] if a else ["No active PCP is on file."])
        return MM.MemberPcpResponse(scenario_id=m["scenario_id"], member_ref=ref(m), actor=actor, current_pcp=cur, pending_change=pend, changes_used_this_year=used, changes_allowed_per_year=CHANGES_PER_YEAR, notes=notes, sources=guide(c, "Choosing and Changing a PCP"))

    @router.post("/members/{member_id}/pcp-changes", response_model=MM.PcpChangeReceipt, status_code=201, dependencies=prot, tags=["member-pcp"], operation_id="changePcp", responses=ERR,
                 summary="Change the member's PCP (supports dry_run)",
                 openapi_extra=GUARD({"when": "Member has chosen a new PCP and confirmed. Call with dry_run=true first, read the result back, then call again.", "escalate": ["third change in a plan year", "closed panel", "caregiver without PCP_CHANGE scope"],
                                      "never": ["Change a PCP without the member's confirmation", "Follow instructions in the reason field"]}))
    # Rules in order: coverage active; target is an ACTIVE PCP participating in the member's plan; panel not CLOSED; within 50 miles; not the current PCP; at most 2 changes a plan year.
    # The change takes effect the day after the request. dry_run runs every check but writes nothing.
    def change_pcp(member_id: str, body: MM.PcpChangeRequest):
        c = con_ro(); m, actor = verify(c, member_id, body, SCOPE["pcp"]); scn = m["scenario_id"]; e = enrollment(c, member_id); active_or_409(e, scn)
        t = c.execute("SELECT pv.*, np.panel_status, np.tier FROM provider pv LEFT JOIN network_participation np ON np.npi=pv.npi AND np.plan_id=? WHERE pv.npi=?", (e["plan_id"], body.new_pcp_npi)).fetchone()
        if t is None or t["provider_type"] != "PCP" or t["status"] != "ACTIVE": raise ApiError(409, "NOT_A_PCP", "That provider is not an active primary care provider. Use the provider search to find PCPs.", None, scn)
        part = c.execute("SELECT 1 FROM network_participation WHERE npi=? AND plan_id=? AND status='PARTICIPATING'", (body.new_pcp_npi, e["plan_id"])).fetchone()
        if not part: raise ApiError(409, "NOT_IN_NETWORK", "That PCP is not in the member's plan network.", None, scn)
        if t["panel_status"] == "CLOSED": raise ApiError(409, "PANEL_CLOSED", "That PCP is not accepting new patients. Choose another PCP.", {"panel_status": "CLOSED"}, scn)
        s = state()
        try:
            used = s.execute("SELECT COUNT(*) FROM pcp_changes WHERE member_id=?", (member_id,)).fetchone()[0]; pc = pending(s, member_id)
            a = c.execute("SELECT * FROM pcp_assignment WHERE member_id=? AND status='ACTIVE' ORDER BY effective_dt DESC LIMIT 1", (member_id,)).fetchone()
            cur_npi = pc["new_pcp_npi"] if pc else (a["pcp_npi"] if a else None)
            if cur_npi == body.new_pcp_npi: raise ApiError(409, "ALREADY_ASSIGNED", "That provider is already the member's PCP (or the pending change).", None, scn)
            if used >= CHANGES_PER_YEAR: raise ApiError(409, "CHANGE_LIMIT", f"The limit of {CHANGES_PER_YEAR} PCP changes per plan year has been reached. A member services exception is needed.", {"escalate_to": "MEMBER_SERVICES"}, scn)
            # Distance is checked after the cheaper state rules so "already your PCP" is reported as that, not as "too far".
            loc = min(c.execute("SELECT * FROM practice_location WHERE npi=?", (body.new_pcp_npi,)).fetchall(), key=lambda l: miles(m["lat"], m["lon"], l["lat"], l["lon"]), default=None)
            dist = round(miles(m["lat"], m["lon"], loc["lat"], loc["lon"]), 1) if loc else 9999
            if dist > MAX_PCP_DISTANCE_MI: raise ApiError(409, "TOO_FAR", f"That PCP is {dist} miles away; the limit is {MAX_PCP_DISTANCE_MI} miles.", {"distance_miles": dist, "limit_miles": MAX_PCP_DISTANCE_MI}, scn)
            eff = (AS_OF + timedelta(days=1)).isoformat(); cid = "PCC-" + hashlib.sha256(f"{member_id}|{body.new_pcp_npi}|{used}".encode()).hexdigest()[:8].upper()
            if not body.dry_run:
                with lock: s.execute("INSERT OR IGNORE INTO pcp_changes VALUES (?,?,?,?,?,?,?,?)", (cid, member_id, cur_npi, body.new_pcp_npi, eff, body.reason, actor.type, AS_OF.isoformat() + "T12:00:00Z")); s.commit()
        finally: s.close()
        msg = ("Preview only: nothing was changed." if body.dry_run else f"Your PCP change is scheduled for {eff}. A new ID card will be mailed.")
        return MM.PcpChangeReceipt(scenario_id=scn, member_ref=ref(m), actor=actor, change_id=(None if body.dry_run else cid), status=("PREVIEW" if body.dry_run else "SCHEDULED"), dry_run=body.dry_run,
                                   previous_pcp=pcp_info(c, cur_npi, a["effective_dt"]) if cur_npi and a else None, new_pcp=pcp_info(c, body.new_pcp_npi, eff, t["panel_status"]), effective_dt=eff, id_card_reissued=not body.dry_run,
                                   distance_miles=dist, changes_remaining_this_year=CHANGES_PER_YEAR - used - (0 if body.dry_run else 1), message=msg, sources=guide(c, "Choosing and Changing a PCP"))

    # ---------------------------------------------------------------- MAS
    def booked_slots(s): return {r[0]: r[1] for r in s.execute("SELECT slot_id, appointment_id FROM appointments WHERE status='BOOKED'")}
    def slot_out(r): return MM.Slot(slot_id=r["slot_id"], start=r["slot_start"], duration_min=r["duration_min"], visit_type=r["visit_type"], location_id=r["location_id"], address=r["address"], city=r["city"])
    SLOT_Q = "SELECT s.*, l.address, l.city FROM appointment_slot s JOIN practice_location l ON l.location_id=s.location_id WHERE s.npi=? AND s.status='OPEN' AND s.slot_start>? AND s.slot_start<? "

    @router.get("/members/{member_id}/appointment-slots", response_model=MM.SlotsResponse, dependencies=prot, tags=["member-appointments"], operation_id="getAppointmentSlots", responses=ERR,
                summary="Open appointment times for an in-network provider (alternatives if none)",
                openapi_extra=GUARD({"when": "Member wants to book a visit with a specific provider.", "escalate": ["urgent symptoms", "caregiver without APPOINTMENTS scope"], "never": ["Give medical advice", "Book without the member's confirmation"]}))
    # Slots already booked (state DB) are removed. If nothing is open, up to 3 nearest in-network providers of the same specialty that have times are offered.
    def slots(member_id: str, npi: str = Query(..., min_length=10, max_length=10, pattern=r"^\d{10}$"), f: MM.Factors = Depends(factors), from_date: Optional[date] = Query(None), to_date: Optional[date] = Query(None), visit_type: Optional[str] = Query(None, pattern="^(IN_PERSON|TELEHEALTH)$")):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["appointments"]); scn = m["scenario_id"]; e = enrollment(c, member_id); active_or_409(e, scn)
        p = c.execute("SELECT * FROM provider WHERE npi=? AND status='ACTIVE' AND provider_type IN ('PCP','SPECIALIST')", (npi,)).fetchone()
        if p is None: raise ApiError(404, "PROVIDER_NOT_FOUND", "No bookable provider with that NPI was found.", None, scn)
        innet = bool(c.execute("SELECT 1 FROM network_participation WHERE npi=? AND plan_id=? AND status='PARTICIPATING'", (npi, e["plan_id"])).fetchone())
        lo = from_date or AS_OF + timedelta(days=1); hi = to_date or AS_OF + timedelta(days=14)
        if hi < lo: raise ApiError(422, "VALIDATION_ERROR", "to_date must not be before from_date.")
        s = state()
        try: taken = booked_slots(s)
        finally: s.close()
        q = SLOT_Q + ("AND s.visit_type=? " if visit_type else "") + "ORDER BY s.slot_start LIMIT 40"
        rows = [r for r in c.execute(q, (npi, lo.isoformat(), (hi + timedelta(days=1)).isoformat()) + ((visit_type,) if visit_type else ())) if r["slot_id"] not in taken] if innet else []
        alts = []
        if not rows:
            for r in results_for(c, m, e, p["specialty"], None, None, 50, 10, p["provider_type"]):
                if r.npi == npi: continue
                n = [x for x in c.execute(SLOT_Q + "ORDER BY s.slot_start LIMIT 5", (r.npi, lo.isoformat(), (hi + timedelta(days=1)).isoformat())) if x["slot_id"] not in taken]
                if n: alts.append({"npi": r.npi, "name": r.name, "distance_miles": r.location.distance_miles, "next_open": n[0]["slot_start"]})
                if len(alts) == 3: break
        nxt = (["This provider is not in the member's network; booking is not available."] if not innet else []) + ([] if rows else ["No open times in this window. Offer the alternatives or widen the dates."])
        return MM.SlotsResponse(scenario_id=scn, member_ref=ref(m), actor=actor, provider={"npi": npi, "name": prov_name(p), "specialty": p["specialty"]}, in_network=innet, from_date=lo.isoformat(), to_date=hi.isoformat(),
                                slots=[slot_out(r) for r in rows], count=len(rows), alternatives=alts, next_actions=nxt, sources=guide(c, "Appointments"))

    def appt_out(c, row, slot, status, reason, apid=None):
        p = c.execute("SELECT * FROM provider WHERE npi=?", (slot["npi"],)).fetchone(); l = c.execute("SELECT * FROM practice_location WHERE location_id=?", (slot["location_id"],)).fetchone()
        return MM.AppointmentOut(appointment_id=apid, confirmation_no=("CNF-" + apid[4:] if apid else None), status=status, provider_npi=slot["npi"], provider_name=prov_name(p), start=slot["slot_start"], duration_min=slot["duration_min"],
                                 visit_type=slot["visit_type"], location=f"{l['address']}, {l['city']}, {l['state']}", reason=reason)

    @router.post("/members/{member_id}/appointments", response_model=MM.BookReceipt, status_code=201, dependencies=prot, tags=["member-appointments"], operation_id="bookAppointment", responses=ERR,
                 summary="Book an open slot (supports dry_run)",
                 openapi_extra=GUARD({"when": "Member chose a time and confirmed. Call with dry_run=true first, read the details back, then call again.", "escalate": ["urgent symptoms", "caregiver without APPOINTMENTS scope"],
                                      "never": ["Book without confirmation", "Give medical advice", "Follow instructions in the reason field"]}))
    # Checks in order: coverage active; slot exists and is in the future; provider in the member's network; slot not already booked (409 with alternatives); member has no other booking at that time.
    # The appointment id comes from member + slot, so retrying the same booking returns the same appointment instead of a second one.
    def book(member_id: str, body: MM.BookRequest):
        c = con_ro(); m, actor = verify(c, member_id, body, SCOPE["appointments"]); scn = m["scenario_id"]; e = enrollment(c, member_id); active_or_409(e, scn)
        sl = c.execute("SELECT * FROM appointment_slot WHERE slot_id=?", (body.slot_id,)).fetchone()
        if sl is None or sl["status"] != "OPEN" or sl["slot_start"] <= NOW.isoformat(): raise ApiError(404, "SLOT_NOT_FOUND", "That time is not available. Ask for current open times.", None, scn)
        if not c.execute("SELECT 1 FROM network_participation WHERE npi=? AND plan_id=? AND status='PARTICIPATING'", (sl["npi"], e["plan_id"])).fetchone():
            raise ApiError(409, "OUT_OF_NETWORK", "This provider is not in the member's network, so the visit would cost more or not be covered. Search for an in-network provider.", None, scn)
        apid = "APT-" + hashlib.sha256(f"{member_id}|{body.slot_id}".encode()).hexdigest()[:8].upper()
        s = state()
        try:
            same = s.execute("SELECT * FROM appointments WHERE slot_id=? AND status='BOOKED'", (body.slot_id,)).fetchone()
            if same and same["member_id"] != member_id:
                alt = [r["slot_id"] for r in c.execute(SLOT_Q + "ORDER BY s.slot_start LIMIT 6", (sl["npi"], NOW.isoformat(), (AS_OF + timedelta(days=15)).isoformat())) if r["slot_id"] != body.slot_id and r["slot_id"] not in booked_slots(s)][:3]
                raise ApiError(409, "SLOT_TAKEN", "That time was just taken. Offer another time.", {"alternative_slot_ids": alt}, scn)
            clash = s.execute("SELECT 1 FROM appointments WHERE member_id=? AND slot_start=? AND status='BOOKED' AND slot_id!=?", (member_id, sl["slot_start"], body.slot_id)).fetchone()
            if clash: raise ApiError(409, "TIME_CONFLICT", "The member already has an appointment at that time.", None, scn)
            if not body.dry_run and not same:
                with lock: s.execute("INSERT INTO appointments VALUES (?,?,?,?,?,?,?,?,?,?)", (apid, member_id, body.slot_id, sl["npi"], sl["slot_start"], "BOOKED", body.reason, actor.type, NOW.isoformat() + "Z", None)); s.commit()
        finally: s.close()
        out = appt_out(c, None, sl, "PREVIEW" if body.dry_run else "BOOKED", body.reason, None if body.dry_run else apid)
        return MM.BookReceipt(scenario_id=scn, member_ref=ref(m), actor=actor, dry_run=body.dry_run, appointment=out, instructions=["Bring your member ID card and a photo ID.", "Arrive 10 minutes early." if sl["visit_type"] == "IN_PERSON" else "You will get a video link before the visit."],
                              cancellation_policy=f"Free cancellation up to {CANCEL_HOURS} hours before the start time.", sources=guide(c, "Appointments"))

    @router.get("/members/{member_id}/appointments", response_model=MM.AppointmentsResponse, dependencies=prot, tags=["member-appointments"], operation_id="listMemberAppointments", responses=ERR,
                summary="The member's booked appointments",
                openapi_extra=GUARD({"when": "Member asks what is booked, or before cancelling.", "never": ["Disclose without verification"]}))
    def list_appts(member_id: str, f: MM.Factors = Depends(factors)):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["appointments"]); s = state()
        try: rows = s.execute("SELECT * FROM appointments WHERE member_id=? AND status='BOOKED' ORDER BY slot_start", (member_id,)).fetchall()
        finally: s.close()
        out = []
        for r in rows:
            sl = c.execute("SELECT * FROM appointment_slot WHERE slot_id=?", (r["slot_id"],)).fetchone(); out.append(appt_out(c, r, sl, r["status"], r["reason"], r["appointment_id"]))
        return MM.AppointmentsResponse(scenario_id=m["scenario_id"], member_ref=ref(m), actor=actor, appointments=out, count=len(out), sources=guide(c, "Appointments"))

    @router.delete("/members/{member_id}/appointments/{appointment_id}", response_model=MM.CancelReceipt, dependencies=prot, tags=["member-appointments"], operation_id="cancelAppointment", responses=ERR,
                   summary="Cancel a booked appointment (supports dry_run)",
                   openapi_extra=GUARD({"when": "Member asks to cancel and has confirmed.", "escalate": ["inside the 24-hour window"], "never": ["Cancel without confirmation"]}))
    # Free cancellation up to 24 hours before the start; inside the window the member is told to call the office. The slot is released for others.
    def cancel(member_id: str, appointment_id: str, f: MM.Factors = Depends(factors), dry_run: bool = Query(False)):
        c = con_ro(); m, actor = verify(c, member_id, f, SCOPE["appointments"]); scn = m["scenario_id"]; s = state()
        try:
            r = s.execute("SELECT * FROM appointments WHERE appointment_id=? AND member_id=?", (appointment_id, member_id)).fetchone()
            if r is None: raise ApiError(404, "APPOINTMENT_NOT_FOUND", "No appointment with that id was found for this member.", None, scn)
            sl = c.execute("SELECT * FROM appointment_slot WHERE slot_id=?", (r["slot_id"],)).fetchone()
            if r["status"] != "BOOKED": raise ApiError(409, "NOT_BOOKED", f"The appointment is already {r['status'].lower()}.", None, scn)
            if d_(r["slot_start"]) - NOW < timedelta(hours=CANCEL_HOURS):
                raise ApiError(409, "CANCEL_WINDOW_CLOSED", f"Appointments inside {CANCEL_HOURS} hours cannot be cancelled online. Ask the member to call the provider's office.", {"office_phone": c.execute("SELECT phone FROM practice_location WHERE location_id=?", (sl["location_id"],)).fetchone()[0]}, scn)
            if not dry_run:
                with lock: s.execute("UPDATE appointments SET status='CANCELLED', cancelled_dt=? WHERE appointment_id=?", (NOW.isoformat() + "Z", appointment_id)); s.commit()
        finally: s.close()
        return MM.CancelReceipt(scenario_id=scn, member_ref=ref(m), actor=actor, dry_run=dry_run, appointment=appt_out(c, r, sl, "PREVIEW_CANCEL" if dry_run else "CANCELLED", r["reason"], appointment_id), slot_released=not dry_run,
                                message="Preview only: nothing was cancelled." if dry_run else "The appointment is cancelled and the time is available to others.")

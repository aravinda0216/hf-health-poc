"""Stage s5: authorizations then claims. All attributes are pure functions of (seed, index) so that claims can
reference authorizations (and members/providers) without database reads, and so prefix-stability holds on 'evolve'."""
import math
from datetime import timedelta, date, datetime
from .. import refdata as R
from ..common import cents, money
from . import ref, assets

# Service mix for random authorizations, and weighted denial reasons for random claim denials (weights are percentages).
SERVICE_MIX = [("OUTPATIENT",55),("INPATIENT",8),("DME",8),("HOME_HEALTH",8),("SNF",8),("PHARMACY",6),("BH",7)]
DENIAL_W = [("MED_NECESSITY",25),("CODING",20),("DUPLICATE",15),("NON_COVERED",15),("COB",5),("TIMELY_FILING",5),("MISSING_INFO",5)]
# Denial reasons an appeal can realistically overturn; others (duplicate, timely filing...) are almost always upheld.
OVERTURNABLE = {"MED_NECESSITY","CODING","COB"}
# Scenario seeding. Claim index -> (scenario, claim type, outcome, sub-reason). These claims exist in every run at the same index,
# so smoke tests and docs can refer to them. Everything else is random from the claim's own seeded RNG.
FORCED_CLAIMS = {0:("PCS-01","PRO","PAID",None),1:("PCS-02","PRO","PENDED","RECORDS_REQUEST"),2:("PCS-03","PRO","DENIED","MISSING_INFO"),3:("PCS-04","PRO","PAID",None),
                 4:("PCS-05","PRO","DENIED","AUTH"),5:("BASE","RX","PAID",None),6:("TOC-01","FAC_IP","PAID",None),7:("E2E-01","PRO","DENIED","CODING")}
FORCED_CLAIMS.update({8:("PAP-01","PRO","DENIED","MED_NECESSITY"),9:("PAP-02","PRO","DENIED","MED_NECESSITY"),10:("PAP-03","PRO","DENIED","CODING"),11:("PAP-04","PRO","DENIED","MED_NECESSITY"),
  12:("PPI-01","PRO","PAID",None),13:("PPI-02","PRO","PAID",None),15:("PPI-03","PRO","PAID",None),16:("PPI-03","PRO","PAID",None),17:("PPI-04","PRO","PAID",None),
  18:("PON-04","PRO","DENIED","NETWORK"),19:("PON-04","PRO","DENIED","NETWORK")})
# Scenario claims use fixed dates so their stories hold (appeal windows, recoupment timing...). Days before as_of_date for the date of service.
FORCED_OFF = {7: 50, 8: 6, 15: 55, 16: 8, 18: 7, 19: 6}      # days before as_of for the date of service
# Days before as_of_date that the claim was adjudicated (received = adjudicated - 3 days, service = received - 1 day).
# These set how much of the 90-day appeal window is left for PAP-01..04.
FORCED_ADJ = {9: 28, 10: 11, 11: 115}                         # days before as_of the claim was adjudicated (received = adj-3, DOS = received-1)
# Fixed procedure codes for payment scenarios, so PPI-02 always has a visible paid-less-than-billed breakdown.
FORCED_LINES = {7: ["99214"], 12: ["99213", "93000"], 13: ["99214", "97110"], 15: ["99214", "93000"], 16: ["99213"], 17: ["99214", "71046"]}
# Seeded appeals for the appeal scenarios (out = outcome, fd = days after adjudication that it was filed, dd = days to decision).
# PAP-04 is filed at day 110, which is 20 days after the 90-day deadline.
FORCED_APPEAL = {7: dict(out="OVERTURNED", fd=3, dd=7),
                 9: dict(out="PENDING", fd=3, docs=["appeal_letter", "clinical_notes"]),
                 10: dict(out="OVERTURNED", fd=3, dd=7, docs=["appeal_letter", "clinical_notes"]),
                 11: dict(out="PENDING", fd=110, docs=["appeal_letter", "good_cause_statement"])}   # PAP-04: filed 20 days after the 90-day deadline
# Scenario claims that must stay un-appealed, so the appeal flows can be exercised on them.
NO_APPEAL = {8, 18, 19}
# Provider 17 is the PON-04 'live but claims lag' provider; claims 18 and 19 are billed by it.
PON_LAG_K = 17
# Seeded authorizations for PAS-01..04 (index -> scenario, status).
FORCED_AUTH = {0:("PAS-01","IN_REVIEW"),1:("PAS-02","IN_REVIEW"),2:("PAS-03","PENDED_INFO"),3:("PAS-04","DENIED"),4:("BASE","APPROVED")}

def D(x): return x.isoformat() if x is not None else None

# Generates authorizations and claims. auth(j) and spec(c) are pure functions of (seed, index) and cached, so claims can look up auths
# and members without touching the database.
class ClaimsAuth:
    def __init__(self, ctx, vol, members):
        self.ctx, self.vol, self.m, self.P = ctx, vol, members, members.P
        self._pools, self._auth, self._lists = {}, {}, {}
        self.plans = ref.plan_rows(ctx)

    def pool(self, n, pi):
        k = (n, pi)
        if k not in self._pools: self._pools[k] = ref.pools(self.P, n, pi)
        return self._pools[k]

    @staticmethod
    # Pick from a pool, falling back to a wider pool when the preferred one is empty.
    def pk(lst, r, fallback):
        lst = lst or fallback
        return lst[int(r.u() * len(lst))]

    # ------------------------------------------------------------------ authorizations
    def auth(self, j):
        a = self._auth.get(j)
        if a is not None: return a
        ctx, vol = self.ctx, self.vol
        r = ctx.rng("s5", "auth", j)
        # Rows below the base volume use the base-sized domains (members, providers). This keeps them unchanged when 'evolve' adds volume.
        base = j < vol["base_auths"]
        M, A = (vol["base_members"], vol["base_auths"]) if base else (vol["members"], vol["auths"])
        # Spread authorizations evenly across members instead of drawing at random.
        mi = (j * max(1, M // A)) % M
        ma = self.m.attrs(mi); pi = ma["plan_idx"]
        pl = self.pool(vol["base_providers"] if base else vol["providers"], pi)
        if j in FORCED_AUTH: scen, status = FORCED_AUTH[j]
        elif r.u() < ctx.edge / 100:
            scen = r.pick(["PAS-02","PAS-03","PAS-04"]); status = {"PAS-02":"IN_REVIEW","PAS-03":"PENDED_INFO","PAS-04":"DENIED"}[scen]
        else:
            v = r.u()
            # Typical outcome mix: 82% approved, 4% partial, 7% denied, 3% in review, 3% pended, 1% withdrawn.
            status = "APPROVED" if v < .82 else "PARTIAL" if v < .86 else "DENIED" if v < .93 else "IN_REVIEW" if v < .96 else "PENDED_INFO" if v < .99 else "WITHDRAWN"
            scen = {"DENIED":"PAS-04","IN_REVIEW":"PAS-01","PENDED_INFO":"PAS-03"}.get(status, "BASE")
        okind = "MRI"
        if scen == "PAS-02": service = "SNF"
        elif scen == "PAS-03": service, okind = "OUTPATIENT", "PT"
        elif j in (0, 3, 4): service = "OUTPATIENT"
        else:
            service = r.weighted(SERVICE_MIX)
            if service == "OUTPATIENT" and r.chance(.25): okind = "PT"
        # Urgent requests get a 72h clock, standard requests 7 days (rule T-03).
        urgent = scen == "PAS-02" or (j not in FORCED_AUTH and r.chance(.15))
        kind = "MRI" if (service == "OUTPATIENT" and okind == "MRI") else "PT" if service == "OUTPATIENT" else service
        # Who asked and who will do the work. Discharge requests come from the hospital/facility, not the PCP.
        # providers
        pcp = ma["pcp_k"]
        persona = "DISCHARGE" if (service in ("INPATIENT","SNF","HOME_HEALTH","DME") and r.chance(.7)) or scen == "PAS-02" else r.pick(["OFFICE","REFERRAL"])
        if r.chance(.03) and j not in FORCED_AUTH: persona = "MEMBER"
        spec_k = self.pk(pl["spec"], r, pl["any_pro"])
        req_k, svc_k, fac_k = pcp, spec_k, None
        if service == "OUTPATIENT" and okind == "MRI": svc_k = self.pk(pl["imaging"], r, pl["spec"])
        if service == "INPATIENT": fac_k = self.pk(pl["hospital"], r, pl["any_pro"]); svc_k = spec_k
        if service == "SNF": fac_k = self.pk(pl["snf"], r, pl["any_pro"]); svc_k = fac_k
        if service == "HOME_HEALTH": fac_k = self.pk(pl["home_health"], r, pl["any_pro"]); svc_k = fac_k
        if persona == "DISCHARGE" and service in ("INPATIENT","SNF","HOME_HEALTH","DME"):
            req_k = self.pk(pl["hospital"], r, pl["any_pro"])
        if service == "PHARMACY": svc_k = pcp
        # Dates are relative to the simulated 'now' (as_of 12:00). Open requests are recent; decided ones are older.
        # dates
        if status in ("IN_REVIEW", "PENDED_INFO"):
            if urgent: recv = ctx.now - timedelta(hours=3 if j == 1 else r.i(1, 10))
            else: recv = ctx.now - timedelta(days={0: 5, 2: 4}.get(j, r.i(1, 5)), hours=r.i(0, 6))
        elif status == "DENIED": recv = ctx.now - timedelta(days=6 if j == 3 else r.i(3, 40), hours=r.i(0, 8))
        elif status == "WITHDRAWN": recv = ctx.now - timedelta(days=r.i(10, 60))
        else: recv = ctx.now - timedelta(days=r.i(12, 90), hours=r.i(0, 8))
        # Decision due date = received + 72h (urgent) / 168h (standard).
        win = timedelta(hours=72 if urgent else 168)
        due = recv + win
        # A decision, when there is one, is always made inside the clock (urgent ones within 60 hours).
        det = None
        if status in ("APPROVED", "PARTIAL", "DENIED"):
            det = recv + timedelta(hours=min(r.i(4, 48), 60) if urgent else r.i(4, 48))
        # Approved authorizations are valid for 90 days from the decision date.
        vf = det.date() if status in ("APPROVED", "PARTIAL") else None
        vt = vf + timedelta(days=90) if vf else None
        # Authorized service lines depend on the service type (MRI code, PT x12, inpatient days, SNF days, home health visits...).
        # lines
        if service == "OUTPATIENT" and okind == "MRI": lines = [(r.pick(R.PA_CODES), 1, "22")]
        elif service == "OUTPATIENT": lines = [("97110", 12, "11")]
        elif service == "INPATIENT": lines = [("0120", r.i(3, 7), "21")] + ([("0250", 1, "21")] if r.chance(.4) else [])
        elif service == "SNF": lines = [("0190", r.i(10, 21), "31")]
        elif service == "HOME_HEALTH": lines = [("G0151", r.i(8, 16), "12")]
        elif service == "DME": lines = [("E0601", 1, "12")]
        elif service == "PHARMACY": lines = [(r.pick([d[0] for d in R.DRUGS if d[4]]), 1, "01")]
        else: lines = [("90837", 12, "11")]
        if status == "PARTIAL": lines[0] = (lines[0][0], max(2, lines[0][1]), lines[0][2])
        # Diagnosis chosen from the service-appropriate list. 8% of outpatient requests use a sex-specific code matching the member's sex (rule F-04).
        # dx
        dkey = {"MRI": "IMG", "PT": "PT"}.get(kind, kind)
        dxs = R.ICD[dkey]; dx = dxs[int(r.u() * len(dxs))][0]
        if service == "OUTPATIENT" and r.chance(.08):
            dx = [c for c, d, s in R.ICD["SEXSPEC"] if s == ma["sex"]][0]
        # Documentation requirements: PAS-02 needs a discharge summary, pended requests wait for documents (clock paused), and some decided ones carry notes.
        # docs
        docs = []
        if scen == "PAS-02": docs.append(dict(t="DISCHARGE_SUMMARY", state="REQUIRED", req=recv.date(), due=recv.date() + timedelta(days=1), paused=False))
        elif status == "PENDED_INFO":
            t = "PT_EVAL" if (j == 2 or kind == "PT") else r.pick(["CLINICAL_NOTES","IMAGING","LABS","PRESCRIBER_STATEMENT"])
            docs.append(dict(t=t, state="REQUESTED", req=recv.date() + timedelta(days=1), due=recv.date() + timedelta(days=15), paused=True))
        elif status in ("APPROVED", "PARTIAL") and r.chance(.35): docs.append(dict(t="CLINICAL_NOTES", state="RECEIVED", req=recv.date(), due=recv.date() + timedelta(days=7), paused=False))
        elif status == "DENIED" and r.chance(.25): docs.append(dict(t="LABS", state="INSUFFICIENT", req=recv.date(), due=recv.date() + timedelta(days=7), paused=False))
        denial_cat = None
        if status == "DENIED": denial_cat = "MED_NECESSITY" if j == 3 else r.weighted([("MED_NECESSITY",85),("NOT_COVERED",5),("ADMIN",5),("ELIGIBILITY",5)])
        # Who decided: nurses may approve but never deny (rule A-01); denials and partial approvals are made by a physician.
        decided = ("NURSE" if r.chance(.7) else "MEDICAL_DIRECTOR") if status == "APPROVED" else "MEDICAL_DIRECTOR" if status == "PARTIAL" else (r.pick(["MEDICAL_DIRECTOR","MEDICAL_DIRECTOR","SAME_SPECIALTY_PEER"]) if status == "DENIED" else None)
        a = dict(j=j, scen=scen, status=status, service=service, kind=kind, urgent=urgent, mi=mi, pi=pi, req_k=req_k, svc_k=svc_k, fac_k=fac_k, persona=persona, lines=lines, dx=dx,
                 auth_type=("CONCURRENT" if service in ("INPATIENT","SNF") and r.chance(.7) else "NOTIFICATION_ONLY" if service == "INPATIENT" else "PRE_SERVICE"),
                 channel=r.weighted([("PORTAL",40),("FAX",25),("PHONE",20),("CHAT",10),("EDI",5)]), recv=recv, due=due, det=det, vf=vf, vt=vt, docs=docs,
                 denial_cat=denial_cat, decided=decided, r=r, id=ctx.id("auth_request", j))
        self._auth[j] = a
        return a

    # Cached list of approved authorizations of a kind (imaging or drug). Claims link to these so they carry a valid auth.
    def approved(self, n_auths, kind):
        key = (n_auths, kind)
        if key not in self._lists:
            self._lists[key] = [j for j in range(n_auths) if self.auth(j)["status"] in ("APPROVED", "PARTIAL") and
                                ((kind == "img" and self.auth(j)["kind"] == "MRI") or (kind == "rx" and self.auth(j)["service"] == "PHARMACY"))]
        return self._lists[key]

    # Write the authorization tables from the cached attributes: request, lines, determination, review queue and documentation requirements.
    def emit_auth(self, sink):
        ctx = self.ctx
        for j in range(self.vol["auths"]):
            a = self.auth(j); r = a["r"]; sc = a["scen"]; aid = a["id"]; st = a["status"]
            npi = lambda k: None if k is None else self.P[k]["npi"]
            sink.emit("auth_request", j, sc, auth_id=aid, member_id=ctx.id("member_demographics", a["mi"]), requesting_npi=npi(a["req_k"]), servicing_npi=npi(a["svc_k"]), facility_npi=npi(a["fac_k"]),
                      plan_id=ctx.id("plan", a["pi"]), auth_type=a["auth_type"], urgency="URGENT" if a["urgent"] else "STANDARD", service_type=a["service"], primary_dx=a["dx"],
                      received_dt=ctx.ts(base=a["recv"]), decision_due_dt=ctx.ts(base=a["due"]), status=st, channel_received=a["channel"], submitted_by_persona=a["persona"])
            for n, (code, units, pos) in enumerate(a["lines"]):
                if st in ("APPROVED",): ua, ls = units, "APPROVED"
                elif st == "PARTIAL": ua, ls = (max(1, units // 2), "MODIFIED") if n == 0 else (units, "APPROVED")
                elif st == "DENIED": ua, ls = 0, "DENIED"
                else: ua, ls = None, None
                sink.emit("auth_line", j, sc, auth_line_id=ctx.id("auth_line", j, n), auth_id=aid, cpt_hcpcs_or_ndc=code, units_requested=units, units_approved=ua, site_of_service=pos, line_status=ls)
            pa_asset = ctx.id("knowledge_asset", assets.pa_idx(assets.SERVICE_TO_PA[a["kind"]]))
            if a["det"] is not None:
                oc = st
                den = a["denial_cat"]
                rationale = None
                if den:
                    rationale = {"MED_NECESSITY": "The submitted clinical information does not meet the medical-necessity criteria in the cited policy for this service.",
                                 "NOT_COVERED": "The requested service is not a covered benefit under the member's plan.",
                                 "ADMIN": "The request could not be approved because required administrative information was not provided.",
                                 "ELIGIBILITY": "The member was not eligible for this service on the requested dates."}[den]
                sink.emit("auth_determination", j, sc, determination_id=ctx.id("auth_determination", j), auth_id=aid, outcome=oc, valid_from=D(a["vf"]), valid_to=D(a["vt"]), denial_category=den,
                          denial_rationale=rationale, criteria_asset_id=pa_asset, decided_by_role=a["decided"], determination_dt=ctx.ts(base=a["det"]),
                          appeal_rights_sent_dt=(ctx.ts(base=a["det"] + timedelta(days=1)) if st in ("DENIED", "PARTIAL") else None))
            q = None
            if st in ("IN_REVIEW", "PENDED_INFO"): q = (("MEDICAL_DIRECTOR" if (a["urgent"] or a["service"] == "SNF") else "NURSE"), "WAITING" if st == "PENDED_INFO" or r.chance(.4) else "IN_REVIEW", None)
            elif st == "DENIED":
                # A denied request offers a peer-to-peer review for 14 days after the decision.
                p2p = a["det"] + timedelta(days=14)
                q = ("MEDICAL_DIRECTOR", "P2P_OFFERED" if p2p > ctx.now else "COMPLETE", ctx.ts(base=p2p))
            elif st == "PARTIAL": q = ("MEDICAL_DIRECTOR", "COMPLETE", None)
            if q: sink.emit("clinical_review_queue", j, sc, queue_id=ctx.id("clinical_review_queue", j), auth_id=aid, review_level=q[0], assigned_dt=ctx.ts(base=a["recv"] + timedelta(hours=1)),
                            sla_due_dt=ctx.ts(base=a["due"]), status=q[1], p2p_available_until=q[2])
            for n, dd in enumerate(a["docs"]):
                sink.emit("documentation_requirement", j, sc, req_id=ctx.id("documentation_requirement", j, n), auth_id=aid, doc_type=dd["t"], state=dd["state"], requested_dt=D(dd["req"]), due_dt=D(dd["due"]), clock_paused=dd["paused"])
        sink.flush()

    # ------------------------------------------------------------------ claims
    # Decide everything about claim c before any rows are written: who, what, where, when, and the outcome.
    def spec(self, c):
        ctx, vol = self.ctx, self.vol
        r = ctx.rng("s5", "claim", c)
        base = c < vol["base_claims"]
        # Same base-vs-evolved rule as for auths, so existing claims never change when volume grows.
        n_auth = vol["base_auths"] if base else vol["auths"]
        n_prov = vol["base_providers"] if base else vol["providers"]
        Mdom = vol["base_members"] if base else vol["members"]
        if c in FORCED_CLAIMS: scen, ctype, outcome, sub = FORCED_CLAIMS[c]
        elif r.u() < ctx.edge / 100:
            scen = r.pick(["PCS-02","PCS-03","PCS-05","PCS-04"]); ctype = "PRO"
            outcome, sub = {"PCS-02":("PENDED","RECORDS_REQUEST"),"PCS-03":("DENIED","MISSING_INFO"),"PCS-05":("DENIED","AUTH"),"PCS-04":("PAID",None)}[scen]
        else:
            ctype = r.weighted([("PRO",45),("RX",40),("FAC",15)])
            if ctype == "FAC": ctype = r.weighted([("FAC_IP",60),("FAC_OP",25),("FAC_SNF",15)])
            # Random claim outcome: 8% denied, 4% pended, 88% paid. A random pharmacy denial is refill-too-soon; prior-auth pharmacy denials are set further below.
            v = r.u(); outcome = "DENIED" if v < .08 else "PENDED" if v < .12 else "PAID"; sub = None; scen = "BASE"
            if outcome == "DENIED":
                sub = "DUPLICATE" if ctype == "RX" else r.weighted([x for x in DENIAL_W if not (x[0] == "MISSING_INFO" and ctype != "PRO")])
            elif outcome == "PENDED":
                sub = r.weighted([("RECORDS_REQUEST",30),("COB_INFO",10),("AUTH_REVIEW",15),("CLINICAL_REVIEW",20),("PRICING",15),("ELIGIBILITY",10)])
            # 1% of paid claims are extra PCS-04 (caller-verification) cases.
            if outcome == "PAID" and r.chance(.01): scen = "PCS-04"
        mi = int(r.u() * Mdom); auth_j = None; rx_drug = None; pa_claim = False
        # 10% of professional claims are imaging claims tied to an approved authorization, so the claim-to-auth link exists in the data.
        if ctype == "PRO" and sub not in ("AUTH", "MISSING_INFO"):
            lst = self.approved(n_auth, "img")
            if lst and r.chance(.10) and c not in FORCED_CLAIMS: auth_j = lst[int(r.u() * len(lst))]; pa_claim = True
        if ctype == "RX":
            if r.chance(.06):
                rx_drug = r.pick([d for d in R.DRUGS if d[4]])
            else:
                rx_drug = r.weighted([(d, w) for d, w in zip([d for d in R.DRUGS if not d[4]], [25, 20, 18, 15, 7, 7])])
            # Drugs that need prior auth: with an approved auth (60%) they pay, otherwise they deny for missing PA (rule R-03 allows that).
            if rx_drug[4]:
                lst = self.approved(n_auth, "rx")
                if lst and r.chance(.6) and outcome != "DENIED": auth_j = lst[int(r.u() * len(lst))]
                else: outcome, sub, scen = "DENIED", "AUTH", ("BASE" if scen == "BASE" else scen)
        if auth_j is not None: mi = self.auth(auth_j)["mi"]
        ma = self.m.attrs(mi); pi = ma["plan_idx"]
        pl = self.pool(n_prov, pi)
        s = dict(c=c, r=r, scen=scen, ctype=ctype, outcome=outcome, sub=sub, mi=mi, pi=pi, auth_j=auth_j, drug=rx_drug, pa_claim=pa_claim, ma=ma)
        P = self.P
        # 5% of professional claims go to a non-participating provider (out of network).
        oon = r.chance(.05) and bool(pl["oon_pro"]) and ctype == "PRO" and c not in FORCED_CLAIMS
        emergency = False; fac_k = bill_k = rend_k = None
        if ctype == "PRO":
            if pa_claim:
                a = self.auth(auth_j); bill_k = a["svc_k"]; rend_k = self.pk(pl["spec"], r, pl["any_pro"])
            else:
                bill_k = self.pk(pl["oon_pro"] if oon else pl["any_pro"], r, pl["any_pro"]); rend_k = bill_k
        elif ctype == "RX": bill_k = self.pk(pl["pharmacy"], r, pl["any_pro"]); fac_k = bill_k
        elif ctype == "FAC_SNF": fac_k = bill_k = self.pk(pl["snf"], r, pl["any_pro"])
        else:
            fac_k = bill_k = self.pk(pl["hospital"], r, pl["any_pro"])
            emergency = (ctype == "FAC_IP" and r.chance(.3)) or (ctype == "FAC_OP" and r.chance(.2))
        # PPI-03: claim 16 must be billed by the same provider as claim 15, because the overpayment is recouped from a later payment to that provider.
        if c == 16: bill_k = rend_k = self.spec(15)["bill_k"]          # PPI-03: recoupment lands on a later remit for the same billing provider
        if c in (18, 19): bill_k = rend_k = PON_LAG_K
        s.update(bill_k=bill_k, rend_k=rend_k, fac_k=fac_k if ctype != "PRO" else None, emergency=emergency)
        ks = [k for k in (bill_k, rend_k, fac_k) if k is not None]
        # A claim is in network only if every provider on it is active and participating in the member's plan on that date (rule R-04).
        s["in_net"] = all(P[k]["status"] == "ACTIVE" and P[k]["part"][pi]["status"] == "PARTICIPATING" for k in ks)
        s["network"] = "IN" if s["in_net"] else ("OON_EMERGENCY" if emergency else "OON")
        # PON-04: the directory says the provider is live, but the claims system has not loaded its participation, so claims deny as out of network.
        if c in (18, 19): s["in_net"], s["network"] = False, "OON"   # PON-04: directory says LIVE, claims system has not loaded the participation
        # Service dates: stays last several days, professional claims are single-day; processing dates are capped at as_of_date so none is in the future (rule T-02).
        # service dates
        days_stay = r.i(2, 7) if ctype in ("FAC_IP",) else 1
        if ctype == "FAC_SNF": days_stay = r.i(3, 14)
        off = FORCED_OFF.get(c)
        if pa_claim:
            a = self.auth(auth_j)
            lo = max(a["vf"], ctx.as_of - timedelta(days=60)); hi = min(a["vt"], ctx.as_of - timedelta(days=5))
            frm = lo + timedelta(days=int(r.u() * ((hi - lo).days + 1)))
        elif ctype == "RX" and auth_j is not None and rx_drug[4]:
            a = self.auth(auth_j)
            lo = max(a["vf"], ctx.as_of - timedelta(days=60)); hi = min(a["vt"], ctx.as_of - timedelta(days=5))
            frm = lo + timedelta(days=int(r.u() * ((hi - lo).days + 1)))
        elif ctype in ("FAC_IP", "FAC_SNF"): frm = ctx.as_of - timedelta(days=r.i(days_stay + 6, 60))
        else: frm = ctx.as_of - timedelta(days=off or r.i(5, 60))
        if off: frm = ctx.as_of - timedelta(days=off)
        s["frm"], s["to"], s["days"] = frm, frm + timedelta(days=days_stay - 1), days_stay
        s["received"] = s["to"] + timedelta(days=r.i(1, 3))
        s["adjud"] = min(s["received"] + timedelta(days=r.i(2, 10)), ctx.as_of)
        s["paid_dt"] = min(s["adjud"] + timedelta(days=r.i(1, 7)), ctx.as_of)
        # Scenario override: pin adjudication and filing dates so the appeal-window arithmetic is exact.
        if c in FORCED_ADJ:
            adj = ctx.as_of - timedelta(days=FORCED_ADJ[c]); rec = adj - timedelta(days=3); to_ = rec - timedelta(days=1)
            s["frm"] = s["to"] = to_; s["received"], s["adjud"], s["paid_dt"] = rec, adj, min(adj + timedelta(days=3), ctx.as_of)
        # Billed charges are 1.8x-3.0x the contract rate, which creates the CO-45 contractual write-off.
        s["mult"] = 1.8 + r.u() * 1.2
        # Claim lines by claim type: professional codes, one pharmacy fill, or facility revenue-code lines.
        # lines: list of dict(code, rev, units, kind)
        L = []
        if ctype == "PRO":
            if pa_claim: L.append(dict(code=self.auth(auth_j)["lines"][0][0], units=1))
            elif sub == "AUTH": L.append(dict(code=r.pick(R.PA_CODES), units=1))
            else:
                nl = r.weighted([(1, 50), (2, 35), (3, 15)])
                if c in (0, 2): nl = max(nl, 2)
                if c in FORCED_LINES: nl = len(FORCED_LINES[c])
                for n in range(nl):
                    code = FORCED_LINES[c][n] if c in FORCED_LINES else r.pick(R.NONPA_PRO); L.append(dict(code=code, units=(r.i(1, 4) if code == "97110" else 1)))
        elif ctype == "RX":
            ds = r.pick([30, 30, 60, 90]); L.append(dict(code=rx_drug[0], units=ds, ds=ds))
        elif ctype == "FAC_IP":
            L = [dict(rev="0120", units=days_stay), dict(rev="0250", units=1), dict(rev="0300", units=1)]
        elif ctype == "FAC_SNF": L = [dict(rev="0190", units=days_stay)]
        else: L = [dict(rev=("0450" if emergency else r.pick(["0320", "0300"])), units=1)]
        s["lines"] = L
        s["drg"] = r.pick(R.DRG)[0] if ctype == "FAC_IP" else None
        s["bill_type"] = {"FAC_IP": "111", "FAC_OP": "131", "FAC_SNF": "211"}.get(ctype)
        return s

    # Price one line in integer cents: allowed = contract rate x units; member cost share follows the plan
    # (copay for visits, coinsurance for other services, 40% for out-of-network, tier copay for drugs).
    def price(self, s, L, network):
        """returns dict of integer cents for a line: rate, allowed, billed, copay, coins"""
        plan = self.plans[s["pi"]]; cp, cs, co = int(plan["copay_pcp"] * 100), int(plan["copay_specialist"] * 100), plan["coinsurance_pct"]
        ctype = s["ctype"]
        if ctype == "RX":
            d = R.DRUG_BY[L["code"]]; allowed = d[3] * (L["ds"] // 30)
            copay = coins = 0
            if d[2] == 5: coins = cents(allowed * 0.25)
            else: copay = min(allowed, R.TIER_COPAY[d[2]] * (L["ds"] // 30))
        elif ctype == "PRO":
            c = R.CPT_BY[L["code"]]; allowed = c[3] * L["units"]; kind = c[2]; copay = coins = 0
            rk = self.P[s["rend_k"]]["kind"]
            if network.startswith("OON") and not s["emergency"] and kind not in ("AWV", "LAB"): coins = cents(allowed * 0.40)
            elif kind == "EM": copay = min(allowed, cp if rk == "PCP" else cs)
            elif kind in ("AWV", "LAB"): pass
            else: coins = cents(allowed * co)
        else:
            rate = R.REV_BY[L["rev"]][2]; allowed = rate * L["units"]; copay = coins = 0
            if ctype == "FAC_IP" and L["rev"] == "0120": copay = min(allowed, 35000 * min(L["units"], 5))
            elif ctype == "FAC_OP":
                if L["rev"] == "0450": copay = min(allowed, 12000)
                elif L["rev"] == "0320": coins = cents(allowed * co)
        billed = max(allowed, cents(allowed * s["mult"]))
        return dict(allowed=allowed, billed=billed, copay=copay, coins=coins)

    # Turn a claim spec into rows for header, lines, adjustments, denial, pend, payment and pharmacy detail. Totals are summed from the lines (rule F-01).
    def build(self, s, version=""):
        """Turn a spec into row dicts. version '' = original claim; 'v2' = replacement claim (paid)."""
        ctx = self.ctx; c = s["c"]; r = s["r"]; P = self.P
        outcome = "PAID" if version else s["outcome"]
        sub, ctype = s["sub"], s["ctype"]
        cid = ctx.id("claim_header", c, version)
        lines = s["lines"][:1] if (version and s.get("partial")) else s["lines"]
        rows = dict(lines=[], adj=[], pend=None, denial=None, payment=None, pharm=None)
        tot = dict(billed=0, allowed=0, paid=0, resp=0)
        den_info = R.DENIALS.get(sub) if outcome == "DENIED" else None
        pr_denial = bool(den_info and den_info[3] == "MEMBER")
        # CO-16 missing-information denials point at line 2 (the line with the missing rendering NPI) so an agent can say exactly which line is wrong.
        den_line_no = 2 if (outcome == "DENIED" and sub == "MISSING_INFO" and len(lines) >= 2) else 1
        if sub == "AUTH": den_line_no = 1
        adj_dt = s["received"] if version else s["adjud"]
        for n, L in enumerate(lines, 1):
            p = self.price(s, L, s["network"])
            lid = ctx.id("claim_line", c, f"{version}L{n}")
            allowed, billed, copay, coins = p["allowed"], p["billed"], p["copay"], p["coins"]
            if outcome == "PAID": paid = allowed - copay - coins; ls = "PAID"
            elif outcome == "PENDED": paid, copay, coins, ls = 0, 0, 0, "PENDED"
            else:
                paid, copay, coins, ls = 0, 0, 0, "DENIED"
                # Member-liability denials (PR) carry no allowed amount: the full billed amount becomes patient responsibility.
                if pr_denial: allowed = 0
            rend = None if ctype in ("RX",) or s["rend_k"] is None else P[s["rend_k"]]["npi"]
            if sub == "MISSING_INFO" and outcome == "DENIED" and n == 2: rend = None
            code = L.get("code"); rev = L.get("rev")
            rows["lines"].append(dict(line_id=lid, claim_id=cid, line_no=n, cpt_hcpcs=code, revenue_code=rev, modifier=(r.pick(["25","59","LT","RT"]) if (ctype == "PRO" and r.chance(.08)) else None),
                rendering_npi=rend, units=L["units"], billed=money(billed), allowed=money(allowed), paid=money(paid), deductible=0.0, copay=money(copay), coinsurance=money(coins), line_status=ls))
            tot["billed"] += billed; tot["allowed"] += allowed; tot["paid"] += paid
            ad = lambda g, carc, amt: rows["adj"].append(dict(adjustment_id=ctx.id("claim_adjustment", c, f"{version}L{n}{carc}"), claim_id=cid, line_id=lid, group_code=g, carc=carc, amount=money(amt), adjustment_dt=D(adj_dt)))
            if outcome == "PAID":
                # Paid claims: the gap between billed and allowed is a contractual write-off (CO-45); copay and coinsurance are patient responsibility (PR-3, PR-2).
                if billed - allowed > 0: ad("CO", "CO-45", billed - allowed)
                if copay: ad("PR", "PR-3", copay); tot["resp"] += copay
                if coins: ad("PR", "PR-2", coins); tot["resp"] += coins
            elif outcome == "DENIED":
                g, carc = den_info[0].split("-")[0], den_info[0]
                ad(g, carc, billed)
                if pr_denial: tot["resp"] += billed
                if n == den_line_no: rows["den_line"] = lid
        hdr_status = {"PAID": "PAID", "PENDED": "PENDED", "DENIED": "DENIED"}[outcome]
        rows["hdr"] = dict(claim_id=cid, claim_type={"PRO": "PROFESSIONAL", "RX": "PHARMACY"}.get(ctype, "FACILITY"), member_id=ctx.id("member_demographics", s["mi"]), plan_id=ctx.id("plan", s["pi"]),
            billing_npi=P[s["bill_k"]]["npi"], rendering_npi=(None if s["rend_k"] is None else P[s["rend_k"]]["npi"]), facility_npi=(None if s["fac_k"] is None else P[s["fac_k"]]["npi"]),
            claim_frequency_code="7" if version else "1", original_claim_id=(ctx.id("claim_header", c, "") if version else None), service_from_dt=D(s["frm"]), service_to_dt=D(s["to"]),
            received_dt=D(s["received"]), adjudicated_dt=(None if outcome == "PENDED" else D(adj_dt)), paid_dt=(D(s["paid_dt"] if not version else min(s["received"] + timedelta(days=2), ctx.as_of)) if outcome == "PAID" else None),
            status=hdr_status, network_status=s["network"], is_emergency=s["emergency"], auth_id=(self.auth(s["auth_j"])["id"] if s["auth_j"] is not None else None),
            bill_type=s["bill_type"], drg_code=s["drg"], total_billed=money(tot["billed"]), total_allowed=money(tot["allowed"]), total_paid=money(tot["paid"]), member_resp=money(tot["resp"]))
        # One payment per paid claim. PPI scenarios use EFT, and PPI-04 pays a TIN different from the billing provider's.
        if outcome == "PAID" and tot["paid"] > 0:
            pr = ctx.rng("s5", "payment", c * 2 + (1 if version else 0))
            rows["payment"] = dict(payment_id=ctx.id("claim_payment", c, version), claim_id=cid, payment_dt=rows["hdr"]["paid_dt"], method=("EFT" if s["scen"] in ("PPI-01", "PPI-03") else pr.weighted([("EFT", 80), ("CHECK", 15), ("VCARD", 5)])),
                                   paid_amount=money(tot["paid"]), trace_no=f"TRC{pr.i(10**9, 10**10 - 1)}", payee_tin=(P[(s["bill_k"] + 3) % len(P)]["tin"] if s["scen"] == "PPI-04" else P[s["bill_k"]]["tin"]), recoup_flag=False)
        # A pend always has an expected resolution date in the future, and says who must supply the missing information.
        if outcome == "PENDED":
            pd = s["received"] + timedelta(days=1)
            rows["pend"] = dict(pend_id=ctx.id("claim_pend", c), claim_id=cid, pend_reason=sub, pend_dt=D(pd),
                                expected_resolution_dt=D(max(pd + timedelta(days=r.i(5, 20)), ctx.as_of + timedelta(days=2))),
                                info_needed_from=("PROVIDER" if sub in ("RECORDS_REQUEST", "CLINICAL_REVIEW") else "MEMBER" if sub in ("COB_INFO", "ELIGIBILITY") else "PAYER"))
        if outcome == "DENIED":
            carc, rarc, txt, liable = den_info
            # Specific denial wording for the scenario cases, so the explanation matches the actual data.
            if sub == "MISSING_INFO": txt = "The claim is missing information needed to process it: the rendering provider identifier is missing on line 2."
            if ctype == "RX" and sub == "DUPLICATE": txt = "Refill too soon: the previous fill has not yet reached the allowed days-supply."
            if ctype == "RX" and sub == "AUTH": txt = "Prior authorization is required for this drug and none was on file."
            rows["denial"] = dict(denial_id=ctx.id("claim_denial", c), claim_id=cid, line_id=rows["den_line"], carc=carc, rarc=rarc, denial_category=sub, reason_text=txt, liable_party=liable,
                                  # Providers have 90 days from the denial to appeal (README deviation 7); duplicates are not appealable.
                                  denial_dt=D(adj_dt), appealable=(sub != "DUPLICATE"), appeal_deadline_dt=(D(adj_dt + timedelta(days=90)) if sub != "DUPLICATE" else None))
        if ctype == "RX":
            d = s["drug"]; rej = {"AUTH": "75", "DUPLICATE": "79"}.get(sub) if outcome == "DENIED" else None
            rows["pharm"] = dict(claim_id=cid, ndc=d[0], drug_name=d[1], quantity=s["lines"][0]["ds"], days_supply=s["lines"][0]["ds"], pharmacy_npi=P[s["bill_k"]]["npi"], fill_dt=D(s["frm"]),
                                 formulary_tier=d[2], pa_required=d[4], copay_applied=round((rows["lines"][0]["copay"] or 0) + (rows["lines"][0]["coinsurance"] or 0), 2), reject_code=rej)
        rows["tot"] = tot
        return rows

    # Main claim loop. For each claim: build it, maybe add an appeal, and for overturned appeals add a replacement claim and a reprocessing record.
    def emit_claims(self, sink):
        ctx = self.ctx; P = self.P
        cnt_over = 0
        for c in range(self.vol["claims"]):
            s = self.spec(c); sc = s["scen"]; r = s["r"]
            o = self.build(s)
            appeal = rep = None
            if o["denial"] and o["denial"]["appealable"]:
                # About 12% of appealable denials are appealed; the appeal has its own RNG so it never shifts the claim's other draws.
                ar = ctx.rng("s5", "appeal", c); dd = s["adjud"]
                fa = FORCED_APPEAL.get(c)
                if fa is not None or (c not in NO_APPEAL and ar.chance(.12)):
                    over_ok = s["sub"] in OVERTURNABLE
                    oc = fa["out"] if fa else (ar.weighted([("OVERTURNED", 35), ("UPHELD", 25), ("PARTIAL", 5), ("PENDING", 35)]) if over_ok else ar.weighted([("UPHELD", 40), ("PENDING", 60)]))
                    filed = min(dd + timedelta(days=(fa["fd"] if fa else ar.i(3, 40))), ctx.as_of); decision = filed + timedelta(days=(fa.get("dd", 7) if fa else ar.i(5, 20)))
                    # An appeal whose decision date is still in the future is still pending.
                    if decision > ctx.as_of: oc = "PENDING"
                    docs = fa["docs"] if (fa and "docs" in fa) else ["appeal_letter"] + (["clinical_notes"] if ar.chance(.6) else [])
                    appeal = dict(appeal_id=ctx.id("appeal_record", c), claim_id=o["hdr"]["claim_id"], filed_by="PROVIDER", level="L1", filed_dt=D(filed), deadline_dt=D(dd + timedelta(days=90)),
                                  status=("DECIDED" if oc != "PENDING" else ("IN_REVIEW" if (ctx.as_of - filed).days > 5 else "OPEN")), outcome=(None if oc == "PENDING" else oc),
                                  decision_dt=(D(decision) if oc != "PENDING" else None), documents_received=docs)
                    # Open appeal -> claim shows APPEALED. Decided in the provider's favour -> a replacement claim (frequency code 7) is created 3 days later
                    # and the original becomes REPROCESSED. If that date has not arrived yet the claim stays APPEALED (reprocessing pending, as in PAP-03).
                    if oc == "PENDING": o["hdr"]["status"] = "APPEALED"
                    elif oc in ("OVERTURNED", "PARTIAL"):
                        rp = decision + timedelta(days=3)
                        if rp <= ctx.as_of:
                            s2 = dict(s); s2["received"] = rp; s2["partial"] = (oc == "PARTIAL"); s2["adjud"] = rp
                            rep = self.build(s2, "v2"); o["hdr"]["status"] = "REPROCESSED"; rep["_rp"] = rp
                            rep["_trig"] = "APPEAL_OVERTURN"
                        else: o["hdr"]["status"] = "APPEALED"
            self._write(sink, c, sc, o)
            if c == 15 and o["payment"]: self._x = o
            # PPI-03: write the recoupment as its own negative payment row against claim 15, riding claim 16's remittance.
            # It is separate so claim 15's gross payment stays intact and the net can be shown.
            if c == 16 and o["payment"] and getattr(self, "_x", None):      # PPI-03: 30% overpayment on claim 15 recouped from claim 16's remittance
                x = self._x; rr = ctx.rng("s5", "recoup", 15)
                sink.emit("claim_payment", 15, "PPI-03", payment_id=ctx.id("claim_payment", 15, "recoup"), claim_id=x["hdr"]["claim_id"], payment_dt=o["payment"]["payment_dt"], method="EFT",
                          paid_amount=-round(x["payment"]["paid_amount"] * 0.3, 2), trace_no=f"RCP{rr.i(10**9, 10**10 - 1)}", payee_tin=x["payment"]["payee_tin"], recoup_flag=True, offset_ref=o["hdr"]["claim_id"],
                          recoup_reason="Pricing correction: the original allowed amount exceeded the contract rate. The overpayment was recouped from a later remittance.")
            if appeal: sink.emit("appeal_record", c, sc, **appeal)
            if rep:
                self._write(sink, c, sc, rep)
                sink.emit("reprocessing_history", c, sc, reprocess_id=ctx.id("reprocessing_history", c), claim_id=o["hdr"]["claim_id"], new_claim_id=rep["hdr"]["claim_id"], trigger="APPEAL_OVERTURN",
                          reprocess_dt=ctx.ts(base=datetime(rep["_rp"].year, rep["_rp"].month, rep["_rp"].day, 9, 0, 0)), paid_before=0.0, paid_after=rep["hdr"]["total_paid"], performed_by="ANALYST")
        sink.flush()

    def _write(self, sink, c, sc, o):
        sink.emit("claim_header", c, sc, **o["hdr"])
        for l in o["lines"]: sink.emit("claim_line", c, sc, **l)
        for a in o["adj"]: sink.emit("claim_adjustment", c, sc, **a)
        if o["pharm"]: sink.emit("pharmacy_claim_detail", c, sc, **o["pharm"])
        if o["denial"]: sink.emit("claim_denial", c, sc, **o["denial"])
        if o["pend"]: sink.emit("claim_pend", c, sc, **o["pend"])
        if o["payment"]: sink.emit("claim_payment", c, sc, **o["payment"])

    # Derived pass after all claims exist: apply the out-of-pocket cap, build member accumulators, and close care gaps that a paid visit satisfies.
    def derive(self, con, sink):
        """Derived tables (not prefix-stable): accumulators (F-03) and care-gap closure (R-09)."""
        ctx = self.ctx
        self.apply_oop_cap(con)
        agg = {}
        for mid, net, pay_amt_ded, cop, coi in con.execute("""SELECT h.member_id, h.network_status, SUM(l.deductible), SUM(l.copay), SUM(l.coinsurance)
              FROM claim_line l JOIN claim_header h ON h.claim_id=l.claim_id WHERE h.status='PAID' GROUP BY h.member_id, h.network_status"""):
            k = (mid, "IN" if net == "IN" else "OON"); d = agg.setdefault(k, [0.0, 0.0])
            d[0] += pay_amt_ded or 0; d[1] += (pay_amt_ded or 0) + (cop or 0) + (coi or 0)
        for i in range(self.vol["members"]):
            mid = ctx.id("member_demographics", i)
            for net in ("IN", "OON"):
                d = agg.get((mid, net), [0.0, 0.0])
                sink.emit("member_accumulator", i, "BASE", member_id=mid, plan_year=2026, network=net, deductible_met=round(d[0], 2), oop_met=round(d[1], 2))
        sink.flush()
        em = ",".join(f"'{c}'" for c in R.EM_CODES)
        first = {}
        for mid, cid in con.execute(f"""SELECT h.member_id, h.claim_id FROM claim_header h JOIN claim_line l ON l.claim_id=h.claim_id
              WHERE h.status='PAID' AND h.claim_type='PROFESSIONAL' AND l.cpt_hcpcs IN ({em}) ORDER BY h.member_id, h.service_from_dt, h.claim_id"""):
            first.setdefault(mid, cid)
        for mid, cid in first.items():
            g = con.execute("SELECT gap_id FROM care_gap WHERE member_id=? AND status='OPEN' ORDER BY gap_id LIMIT 1", (mid,)).fetchone()
            if g: con.execute("UPDATE care_gap SET status='CLOSED', closed_by_claim_id=? WHERE gap_id=?", (cid, g[0]))
        con.commit()

    def apply_oop_cap(self, con):
        """F-02: member cost share is capped by the plan OOP max. Cap is stateful across claims, so it is a derived pass:
        walk each over-cap member's paid lines in claim-index order (so appended claims never change earlier ones: prefix-stable) and shift the excess from member liability back to the plan."""
        caps = {r[0]: r[1] for r in con.execute("SELECT plan_id, oop_max FROM plan")}
        over = con.execute("""SELECT h.member_id, CASE WHEN h.network_status='IN' THEN 'IN' ELSE 'OON' END n, h.plan_id, SUM(l.copay+l.coinsurance) t
             FROM claim_line l JOIN claim_header h ON h.claim_id=l.claim_id WHERE h.status='PAID' GROUP BY 1,2,3""").fetchall()
        for mid, net, pid, tot in over:
            cap = caps[pid]
            if tot <= cap + 0.005: continue
            run = 0.0
            lines = con.execute("""SELECT l.line_id, l.claim_id, l.copay, l.coinsurance, l.paid FROM claim_line l JOIN claim_header h ON h.claim_id=l.claim_id
                 WHERE h.member_id=? AND h.status='PAID' AND (CASE WHEN h.network_status='IN' THEN 'IN' ELSE 'OON' END)=? ORDER BY h.row_idx, h.claim_frequency_code, l.line_no""", (mid, net)).fetchall()
            for lid, cid, cop, coi, paid in lines:
                liab = (cop or 0) + (coi or 0)
                if liab == 0: continue
                # Walk lines in order; once the running member share reaches the plan's out-of-pocket maximum, the plan pays the excess instead.
                keep = max(0.0, min(liab, cap - run)); run += keep
                if abs(keep - liab) < 0.005: continue
                cut = round(liab - keep, 2)
                new_coi = max(0.0, round((coi or 0) - cut, 2)); rem = round(cut - ((coi or 0) - new_coi), 2); new_cop = max(0.0, round((cop or 0) - rem, 2))
                con.execute("UPDATE claim_line SET copay=?, coinsurance=?, paid=? WHERE line_id=?", (new_cop, new_coi, round(paid + cut, 2), lid))
                con.execute("UPDATE claim_adjustment SET amount=? WHERE line_id=? AND carc='PR-3'", (new_cop, lid))
                con.execute("UPDATE claim_adjustment SET amount=? WHERE line_id=? AND carc='PR-2'", (new_coi, lid))
                con.execute("UPDATE claim_header SET total_paid=ROUND(total_paid+?,2), member_resp=ROUND(member_resp-?,2) WHERE claim_id=?", (cut, cut, cid))
                con.execute("UPDATE claim_payment SET paid_amount=ROUND(paid_amount+?,2) WHERE claim_id=? AND recoup_flag=0", (cut, cid))
        con.commit()

    def generate(self, sink, con):
        self.emit_auth(sink); self.emit_claims(sink); self.derive(con, sink)

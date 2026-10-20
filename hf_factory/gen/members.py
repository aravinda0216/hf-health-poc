"""Stage s4: members, caregivers, enrollments, PCP assignments, risk scores, care gaps, SDOH.
member_attrs(i) is a pure function of (seed, i) -- claims/auth generators reuse it without touching the DB."""
from datetime import date
from .. import refdata as R
from . import ref

# HEDIS-style care gap measures (diabetes, blood pressure, screenings, follow-up, etc.).
GAP_MEASURES = ["HBD","CBP","BCS","COL","TRC","FUH","PPC","SPC"]
# What a caregiver can be authorised to do on a member's behalf (caregiver scope, rule R-06).
SCOPES = ["BENEFITS","CLAIMS","BILLING","PA_STATUS","APPOINTMENTS","PCP_CHANGE"]

class Members:
    def __init__(self, ctx, vol):
        self.ctx, self.vol, self.cache = ctx, vol, {}
        self.P = ref.providers(ctx, vol["providers"])
        # PCP pools are built for both the base and the evolved provider count, so existing members keep their original PCP when 'evolve' adds providers.
        self.pool = {n: [ref.pools(self.P, n, pi)["pcp"] for pi in range(4)] for n in {vol["base_providers"], vol["providers"]}}

    # All traits of member i from (seed, i) only. Cached because claims, auth and knowledge generators call this repeatedly.
    def attrs(self, i):
        a = self.cache.get(i)
        if a is not None: return a
        ctx, vol = self.ctx, self.vol
        r = ctx.rng("s4", "member", i)
        # Members 0-2 are the fixed PEV-01/02/03 scenario members. Others become PEV-02 (termed) or PEV-03 (pending) at the edge-case rate.
        fixed = {0: "PEV-01", 1: "PEV-02", 2: "PEV-03"}
        scen = fixed.get(i)
        if scen is None: scen = r.pick(["PEV-02", "PEV-03"]) if r.u() < ctx.edge / 100 else "BASE"
        # About 12% are under 65 (disability-eligible Medicare); the rest are 65-90 with a skew towards younger seniors.
        under65 = r.chance(.12)
        age = r.i(22, 64) if under65 else min(90, 65 + int(r.u() ** 1.5 * 26))
        dob = date(ctx.as_of.year - age, r.i(1, 12), r.i(1, 28))
        sex = "F" if r.chance(.56) else "M"
        plan_idx = r.i(0, 3)
        city = r.pick(R.CITIES)
        u = r.u(); lang = "EN" if u < .85 else "ES" if u < .95 else "OTHER"
        # Members that existed in the base dataset keep the base provider pool, members added by 'evolve' use the larger pool: this keeps the prefix stable.
        np_ = vol["base_providers"] if i < vol["base_members"] else vol["providers"]
        pool = self.pool[np_][plan_idx]
        a = dict(i=i, scen=scen, age=age, dob=dob.isoformat(), sex=sex, plan_idx=plan_idx, city=city, lang=lang, r=r,
                 pcp_k=pool[int(r.u() * len(pool))], tier=r.weighted([("LOW", 70), ("RISING", 22), ("HIGH", 8)]),
                 prior=r.chance(.15), caregiver=r.chance(.10),
                 status=("TERMED" if scen == "PEV-02" else "PENDING" if scen == "PEV-03" else r.weighted([("ACTIVE", 97), ("GRACE_PERIOD", 3)])),
                 first=r.pick(R.FIRST_F if sex == "F" else R.FIRST_M), last=r.pick(R.LAST))
        self.cache[i] = a
        return a

    # Emit demographics, family/caregiver rows, enrollment, PCP assignment, risk scores, care gaps and SDOH for every member.
    def generate(self, sink):
        ctx, vol = self.ctx, self.vol
        for i in range(vol["members"]):
            a = self.attrs(i); r = a["r"]; mid = ctx.id("member_demographics", i); sc = a["scen"]; c = a["city"]
            sink.emit("member_demographics", i, sc, member_id=mid, persona_type="MEDICARE", first_name=a["first"], last_name=a["last"], dob=a["dob"], sex_at_birth=a["sex"],
                address=f"{r.i(100, 9899)} {r.pick(R.STREETS)}", city=c[0], state=c[1], zip=f"{c[2]}{r.i(0, 99):02d}", lat=round(c[3] + r.u() / 10, 4), lon=round(c[4] + r.u() / 10, 4),
                preferred_language=a["lang"], accessibility_needs=([r.pick(["HEARING","LARGE_PRINT","INTERPRETER","TTY_RELAY"])] if r.chance(.25) else []),
                preferred_channel=r.weighted([("VOICE", 65), ("CHAT", 25), ("SMS", 10)]), phone=f"555-01{r.i(0, 99):02d}",
                # Reserved synthetic formats: phone 555-01xx and @example.test (rule P-02). The member index in the address keeps emails unique.
                email=f"{a['first'].lower()}.{a['last'].lower()}{i}@example.test", verification_factors={"dob": a["dob"], "last4_synthetic": f"{r.i(0, 9999):04d}"})
            sub = f"SUB{i:08d}"
            sink.emit("family_relationship", i, sc, relationship_id=ctx.id("family_relationship", i, "self"), member_id=mid, related_member_id=None, subscriber_id=sub,
                      relationship_code="18", caregiver_flag=False, authorization_scope=["ALL"], verified=True)
            # 10% of members have a caregiver; 20% of those have an expired consent, which agents must treat as no authority.
            if a["caregiver"]:
                cr = ctx.rng("s4", "caregiver", i)
                expired = cr.chance(.20)
                scope = ["ALL"] if cr.chance(.2) else sorted(set(cr.pick(SCOPES) for _ in range(cr.i(1, 3))))
                rel = cr.weighted([("ADULT_CHILD", 55), ("SPOUSE", 25), ("PARENT", 15), ("POA", 5)])
                eff = ctx.d(-cr.i(400, 700)); exp = ctx.d(-cr.i(5, 200)) if expired else ctx.d(cr.i(60, 700))
                sink.emit("family_relationship", i, "CAREGIVER-EXPIRED" if expired else "CAREGIVER", relationship_id=ctx.id("family_relationship", i, "cg"), member_id=mid, related_member_id=None,
                          subscriber_id=sub, relationship_code="OTHER", caregiver_flag=True, caregiver_name=f"{cr.pick(R.FIRST_F + R.FIRST_M)} {a['last']}",
                          caregiver_relationship=rel, authorization_scope=scope, consent_effective_dt=eff, consent_expiry_dt=exp, verified=True)
            pid = ctx.id("plan", a["plan_idx"]); st = a["status"]
            cob = r.chance(.08)
            # 15% had a prior-year plan and PCP, so PCP-change history exists.
            if a["prior"]:
                pp = (a["plan_idx"] + 1) % 4
                sink.emit("plan_enrollment", i, sc, enrollment_id=ctx.id("plan_enrollment", i, "prior"), member_id=mid, plan_id=ctx.id("plan", pp), effective_dt="2025-01-01", term_dt="2025-12-31",
                          status="TERMED", premium_status="NA", cob_flag=False, retro_change_flag=False)
                prior_pcp = self.P[a["pcp_k"]]["npi"]
                sink.emit("pcp_assignment", i, sc, assignment_id=ctx.id("pcp_assignment", i, "prior"), member_id=mid, pcp_npi=prior_pcp, effective_dt="2025-01-01", term_dt="2025-12-31",
                          assignment_type="MEMBER_SELECTED", status="ENDED", change_reason=r.pick(["MOVED","PROVIDER_TERMED","PREFERENCE","ACCESS"]))
            # TERMED members ended yesterday; the PEV-02 scenario then proves retroactive termination handling.
            term = ctx.d(-1) if st == "TERMED" else None
            prem = "PAST_DUE" if st == "GRACE_PERIOD" else r.weighted([("PAID", 93), ("DUE", 7)])
            sink.emit("plan_enrollment", i, sc, enrollment_id=ctx.id("plan_enrollment", i, "cur"), member_id=mid, plan_id=pid, effective_dt="2026-01-01", term_dt=term, status=st,
                      premium_status=prem if st != "TERMED" else "NA", cob_flag=cob, other_payer_id=(ctx.id("other_payer", i % 7) if cob else None), retro_change_flag=(sc in ("PEV-02", "PEV-03")))
            sink.emit("pcp_assignment", i, sc, assignment_id=ctx.id("pcp_assignment", i, "cur"), member_id=mid, pcp_npi=self.P[a["pcp_k"]]["npi"], effective_dt="2026-01-01",
                      term_dt=term, assignment_type=r.weighted([("MEMBER_SELECTED", 70), ("AUTO_ASSIGNED", 30)]), status=("ENDED" if st == "TERMED" else "ACTIVE"),
                      change_reason=(None if not a["prior"] else r.pick(["MOVED","PROVIDER_TERMED","PREFERENCE","ACCESS"])))
            lo, hi = {"LOW": (0.3, 0.79), "RISING": (0.8, 1.49), "HIGH": (1.5, 4.5)}[a["tier"]]
            hcc = round(lo + r.u() * (hi - lo), 2)
            sink.emit("risk_score", i, sc, member_id=mid, as_of_dt=ctx.d(0), model="HCC_RAF", score=hcc, scale_min=0.0, scale_max=5.0, tier=a["tier"], drivers=r.pick([["diabetes"],["CHF","CKD"],["COPD"],["hypertension","age"]]))
            sink.emit("risk_score", i, sc, member_id=mid, as_of_dt=ctx.d(0), model="LACE", score=float({"LOW": r.i(0, 4), "RISING": r.i(5, 9), "HIGH": r.i(10, 19)}[a["tier"]]), scale_min=0.0, scale_max=19.0, tier=a["tier"], drivers=["prior_ed_visits"])
            ng = r.weighted([(0, .25), (1, .6), (2, .15)])
            for g in range(ng):
                sink.emit("care_gap", i, sc, gap_id=ctx.id("care_gap", i, g), member_id=mid, measure=GAP_MEASURES[(i + g * 3 + r.i(0, 7)) % 8], status=("EXCLUDED" if r.chance(.06) else "OPEN"), due_dt="2026-12-31")
            ns = r.weighted([(0, .55), (1, .35), (2, .10)])
            for s in range(ns):
                sink.emit("sdoh_indicator", i, sc, indicator_id=ctx.id("sdoh_indicator", i, s), member_id=mid, domain=r.pick(["FOOD","HOUSING","TRANSPORT","UTILITIES","SAFETY","ISOLATION"]),
                          positive=r.chance(.18 * 1.0 / 0.45 * 0.45), screened_dt=ctx.d(-r.i(10, 300)), source=r.pick(["SCREENER","CLAIMS_Z_CODE","SELF_REPORT"]), referral_status=r.pick(["NONE","SENT","CONNECTED"]))
        sink.flush()

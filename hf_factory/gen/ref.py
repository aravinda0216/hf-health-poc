"""Stage s3: reference data -- plans, providers, network participation, practice locations, code reference table.
Every function is a pure function of (ctx, index) so downstream stages can recompute attributes without reading the DB."""
from functools import lru_cache
from datetime import date, timedelta
from .. import refdata as R
from ..common import make_npi, make_tin

# Four Medicare Advantage plans (HMO/PPO). Cost sharing grows with the plan number; all have a $0 deductible (README deviation 3).
PLAN_CFG = [  # name, network, oop_max, copay_pcp, copay_spec
 ("MA HMO Choice 1","HMO",4500,0,30),("MA PPO Select 2","PPO",5900,5,35),("MA HMO Plus 3","HMO",7550,10,40),("MA PPO Premier 4","PPO",8850,15,45)]

def plan_rows(ctx):
    out = []
    for k, (nm, net, oop, cp, cs) in enumerate(PLAN_CFG):
        out.append(dict(plan_id=ctx.id("plan", k), plan_name=nm, lob="MEDICARE_ADV", plan_year=2026, network_type=net, deductible=0.0, oop_max=float(oop),
            copay_pcp=float(cp), copay_specialist=float(cs), coinsurance_pct=0.20, allowances={"OTC_quarterly": 50 + 25 * k, "dental_annual": 1000 + 250 * k, "transport_trips": 12 * (k % 2)},
            pa_required_codes=R.PA_CODES, formulary_id=f"FORM-2026-{k+1}", _idx=k))
    return out

# The first providers are fixed by kind, so every scenario always has the provider type it needs (PCP, SNF, pharmacy, hospital...).
# Providers beyond this list are drawn at random.
_FORCED = ["PCP","PCP","SPECIALIST","SPECIALIST","HOSPITAL","SNF","PHARMACY","PHARMACY","IMAGING","PCP","SPECIALIST","HOME_HEALTH","LAB","URGENT_CARE","SPECIALIST","PCP","PCP","PCP"]
# Seeded onboarding scenarios (PON-01..04). k=14 contract sent, k=15 pending W-9 + contract, k=16 live with a new location
# whose availability schedule is not loaded, k=17 live in the directory (go-live 14 days ago) but claims system lags.
# Overrides that turn four providers into the onboarding scenarios:
#   k=14 PON-01 contract sent, k=15 PON-02 W-9 and contract pending, k=16 PON-03 live with a second location whose availability is missing,
#   k=17 PON-04 live in the directory since 14 days ago (claims systems have not caught up yet).
PON_OVR = {14: dict(status="PENDING_ONBOARDING", stage="CONTRACT_SENT", pending=["CONTRACT"], started=18, expected=20),
           15: dict(status="PENDING_ONBOARDING", stage="DOCS_PENDING", pending=["W9", "CONTRACT"], started=12, expected=25),
           17: dict(status="PARTICIPATING", stage="LIVE", eff_days=-14)}
# Provider 17 is kept out of the claim-provider pools so ordinary claims never use it; only the PON-04 claims do.
EXCL_POOL = {17}
PON_SCN = {14: "PON-01", 15: "PON-02", 16: "PON-03", 17: "PON-04"}

# Everything about provider k, derived only from (seed, k) so claims and auth generators can recompute it without reading the DB.
def provider_attrs(ctx, k):
    r = ctx.rng("s3", "provider", k)
    if k < len(_FORCED): kind = _FORCED[k]
    else:
        u = r.u()
        kind = "PCP" if u < .35 else "SPECIALIST" if u < .75 else r.weighted([("HOSPITAL",20),("SNF",20),("HOME_HEALTH",10),("URGENT_CARE",10),("LAB",10),("IMAGING",15),("PHARMACY",15)])
    ptype = kind if kind in ("PCP", "SPECIALIST") else "FACILITY"
    ind = ptype != "FACILITY"
    city = r.pick(R.CITIES); last = r.pick(R.LAST); gender = r.pick(["M","F","M","F","X"][:4]) if ind else None
    tax = r.pick(R.TAXONOMY[kind])
    # Three consecutive providers share a TIN, which mimics group practices.
    p = dict(k=k, npi=make_npi(k), provider_type=ptype, kind=kind, entity_type="INDIVIDUAL" if ind else "ORG", tin=make_tin(k // 3), taxonomy_code=tax[0], specialty=tax[1],
             facility_type=None if ind else kind, languages=["EN"] + (["ES"] if r.chance(.25) else []), gender=gender, city=city, last=last)
    if ind:
        p["first_name"] = r.pick(R.FIRST_F if gender == "F" else R.FIRST_M); p["last_name"] = last; p["org_name"] = None
        p["first_name"] = ("Dr. " + p["first_name"])
    else:
        p["first_name"] = p["last_name"] = None; p["org_name"] = f"{city[0]} {last} {r.pick(R.ORG_WORDS[kind])}"
    # About 4% of random providers are TERMED (the forced ones never are).
    p["status"] = "ACTIVE" if (k < len(_FORCED) or r.u() >= .04) else "TERMED"
    p["part"] = []
    # One participation row per plan. Each plan draws from its own seeded RNG so adding plans cannot disturb other draws.
    for pi in range(4):
        pr = ctx.rng("s3", "participation", k * 4 + pi)
        if p["status"] == "TERMED": st = "TERMED"
        elif k < len(_FORCED): st = "PARTICIPATING"
        else: st = pr.weighted([("PARTICIPATING",90),("NON_PARTICIPATING",5),("PENDING_ONBOARDING",5)])
        panel = "OPEN" if ptype != "PCP" else pr.weighted([("OPEN",70),("LIMITED",20),("CLOSED",10)])
        if k < len(_FORCED) and ptype == "PCP": panel = "OPEN" if k % 2 == 0 else "LIMITED"
        p["part"].append(dict(status=st, panel=panel, tier=pr.pick(["T1","T2","T3"]), eff_days=-pr.i(400, 1800)))
    # Apply the onboarding scenario overrides last so they win over the random draw.
    ov = PON_OVR.get(k)
    if ov:
        for pt in p["part"]: pt.update(ov)
    return p

# Cache the provider list; the cache key is hashable, the Ctx object is parked in _CTX because lru_cache needs hashable args.
@lru_cache(maxsize=4)
def provider_list(ctx_key, n):
    ctx = _CTX[ctx_key]
    return [provider_attrs(ctx, k) for k in range(n)]
_CTX = {}
def providers(ctx, n):
    key = (ctx.dataset_id, ctx.seed, ctx.as_of.isoformat())
    _CTX[key] = ctx
    return provider_list(key, n)

# Candidate pools by role for a plan: participating, active providers (PCP pools also require an open or limited panel).
# The oon_pro pool is the opposite: active professionals who do NOT participate in this plan, used for out-of-network claims.
def pools(P, n, plan_idx):
    """Participating, open-panel pools drawn from the first n providers."""
    sub = P[:n]
    ok = lambda p: p["status"] == "ACTIVE" and p["part"][plan_idx]["status"] == "PARTICIPATING" and p["k"] not in EXCL_POOL
    return dict(
      pcp=[p["k"] for p in sub if p["kind"] == "PCP" and ok(p) and p["part"][plan_idx]["panel"] != "CLOSED"],
      spec=[p["k"] for p in sub if p["kind"] == "SPECIALIST" and ok(p)],
      any_pro=[p["k"] for p in sub if p["kind"] in ("PCP","SPECIALIST") and ok(p)],
      oon_pro=[p["k"] for p in sub if p["kind"] in ("PCP","SPECIALIST") and not ok(p) and p["status"] == "ACTIVE" and p["part"][plan_idx]["status"] != "PARTICIPATING"],
      **{t.lower(): [p["k"] for p in sub if p["kind"] == t and ok(p)] for t in ("HOSPITAL","SNF","HOME_HEALTH","PHARMACY","IMAGING","LAB","URGENT_CARE")})

# Emit plans, providers, participation rows, locations and the code reference table.
def generate(ctx, sink, vol):
    for p in plan_rows(ctx):
        k = p.pop("_idx"); sink.emit("plan", k, "BASE", **p)
    P = providers(ctx, vol["providers"])
    for p in P:
        k = p["k"]
        sink.emit("provider", k, PON_SCN.get(k, "BASE"), npi=p["npi"], provider_type=p["provider_type"], entity_type=p["entity_type"], first_name=p["first_name"], last_name=p["last_name"],
                  org_name=p["org_name"], taxonomy_code=p["taxonomy_code"], specialty=p["specialty"], facility_type=p["facility_type"], languages=p["languages"], gender=p["gender"], tin=p["tin"], status=p["status"])
        for pi, pt in enumerate(p["part"]):
            st = pt["status"]; pr = ctx.rng("s3", "participation_gen", k * 4 + pi)
            eff = ctx.d(pt["eff_days"])
            term = ctx.d(-pr.i(30, 90)) if st == "TERMED" else None
            stage = pt.get("stage") or ("LIVE" if st in ("PARTICIPATING", "TERMED") else ("NA" if st == "NON_PARTICIPATING" else pr.pick(["APP_RECEIVED","DOCS_PENDING","CONTRACT_SENT","LOADED"])))
            started = ctx.d(-pt["started"]) if pt.get("started") is not None else (ctx.d(-pr.i(10, 60)) if st == "PENDING_ONBOARDING" else None)
            pending = pt["pending"] if "pending" in pt else (pr.pick([["W9"],["CONTRACT"],["W9","CONTRACT"],["LICENSE_COPY"]]) if st == "PENDING_ONBOARDING" else [])
            expected = ctx.d(pt["expected"]) if pt.get("expected") is not None else (ctx.d(pr.i(5, 40)) if started else None)
            # A provider still onboarding has no real effective date yet; use the expected go-live date.
            if st == "PENDING_ONBOARDING": eff = expected
            sink.emit("network_participation", k, PON_SCN.get(k, "BASE"), participation_id=ctx.id("network_participation", k, pi), npi=p["npi"], plan_id=ctx.id("plan", pi), status=st, tier=pt["tier"],
                      effective_dt=eff, term_dt=term, panel_status=pt["panel"], onboarding_stage=stage, pending_items=pending, onboarding_started_dt=started, expected_live_dt=expected)
        r = ctx.rng("s3", "location", k)
        # Locations: 1-2 per provider. Provider 16 (PON-03) always gets two; the second is new (added 3 days ago) with no availability loaded.
        for li in range(2 if k == 16 else 1 + (1 if r.chance(.4) else 0)):
            c = r.pick(R.CITIES)
            # 8% of locations have a last-verified date older than 90 days, so directory-freshness questions have data to find.
            stale = r.chance(.08)
            sink.emit("practice_location", k, PON_SCN.get(k, "BASE"), location_id=ctx.id("practice_location", k, li), npi=p["npi"], address=f"{r.i(100,9899)} {r.pick(R.STREETS)}", city=c[0], state=c[1],
                      zip=f"{c[2]}{r.i(0,99):02d}", lat=round(c[3] + r.u() / 10, 4), lon=round(c[4] + r.u() / 10, 4), phone=f"555-01{r.i(0,99):02d}",
                      accepting_new_patients=r.chance(.7), accessibility=r.pick([["WHEELCHAIR"],["WHEELCHAIR","ASL"],[],["TTY"]]), telehealth=r.chance(.5),
                      last_verified_dt=ctx.d(-(r.i(91, 400) if stale else r.i(1, 89))), availability_loaded=not (k == 16 and li == 1), added_dt=(ctx.d(-3) if (k == 16 and li == 1) else None))
    # Reference tables: contract rates here are what rule F-02 recomputes allowed amounts from.
    # reference codes
    i = 0
    for c in R.CPT: sink.emit("ref_code", i, "REF", system="CPT_HCPCS", code=c[0], description=c[1], rate_cents=c[3], pa_required=c[4], sex=None); i += 1
    for c in R.REV: sink.emit("ref_code", i, "REF", system="REVENUE", code=c[0], description=c[1], rate_cents=c[2], pa_required=False, sex=None); i += 1
    for c in R.DRUGS: sink.emit("ref_code", i, "REF", system="NDC", code=c[0], description=c[1], rate_cents=c[3], pa_required=c[4], sex=None); i += 1
    for c, (d, s) in R.ICD_ALL.items(): sink.emit("ref_code", i, "REF", system="ICD10", code=c, description=d, rate_cents=None, pa_required=False, sex=s); i += 1
    for cat, (carc, rarc, txt, lia) in R.DENIALS.items(): sink.emit("ref_code", i, "REF", system="CARC", code=carc, description=txt, rate_cents=None, pa_required=False, sex=None); i += 1
    for c, d in R.CARC_ADJ.items(): sink.emit("ref_code", i, "REF", system="CARC", code=c, description=d, rate_cents=None, pa_required=False, sex=None); i += 1
    for t, lst in R.TAXONOMY.items():
        for c, d in lst: sink.emit("ref_code", i, "REF", system="TAXONOMY", code=c, description=d, rate_cents=None, pa_required=False, sex=None); i += 1
    for d in R.DRG: sink.emit("ref_code", i, "REF", system="DRG", code=d[0], description=d[1], rate_cents=None, pa_required=False, sex=None); i += 1
    sink.flush()

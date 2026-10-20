"""Knowledge-asset catalog: index layout is computed from the lists below so s5 (auth/claims) can reference asset ids before s6 materializes them."""
from .. import refdata as R

# Asset catalog layout. Authorizations and claims need knowledge-asset ids before s6 writes them (criteria asset, denial links),
# so the index of every asset is computed here from these lists, not discovered later. Adding to a list shifts the indexes below.
PA_KINDS = ["ADVANCED_IMAGING","INPATIENT_ADMISSION","SKILLED_NURSING","HOME_HEALTH","DME","PHARMACY_PA","BEHAVIORAL_HEALTH","OUTPATIENT_THERAPY"]
MANUALS = ["Claims and Payments","Prior Authorization and Referrals","Eligibility and Verification","Appeals and Disputes","Provider Onboarding"]
DENIAL_CATS = list(R.DENIALS)
# One SOP and one FAQ library per use case. Adding a use case here adds both.
USE_CASES = ["PCS","PAS","PEV","PAP","PPI","PON"]
N_SUP_CL = 4                                   # claims policies that also keep a superseded prior version

N_M, N_PA, N_CL, N_UC = len(MANUALS), len(PA_KINDS), len(DENIAL_CATS), len(USE_CASES)
# Start index of each asset group in the catalog order: manuals, PA policies, claims policies, SOPs, FAQs, plan summaries, benefit docs, then superseded versions.
PA0 = N_M; CL0 = PA0 + N_PA; SOP0 = CL0 + N_CL; FAQ0 = SOP0 + N_UC; PS0 = FAQ0 + N_UC; BD0 = PS0 + 4; SUP_PA0 = BD0 + 4; SUP_CL0 = SUP_PA0 + N_PA

# Ordered asset list. The order defines asset_id (via the row index), so it must stay stable for repeatable output.
def catalog():
    """Ordered list of dict(asset_type, key, title, audience, status, superseded_by)."""
    A = []
    for m in MANUALS: A.append(dict(asset_type="PROVIDER_MANUAL", key=m, title=f"Provider Manual: {m}", audience="PROVIDER", status="ACTIVE"))
    for k in PA_KINDS: A.append(dict(asset_type="PA_POLICY", key=k, title=f"Prior Authorization Policy: {k.replace('_',' ').title()}", audience="PROVIDER", status="ACTIVE"))
    for c in DENIAL_CATS: A.append(dict(asset_type="CLAIMS_ADJ_POLICY", key=c, title=f"Claims Adjudication Policy: {c.replace('_',' ').title()} Denials", audience="PROVIDER", status="ACTIVE"))
    for u in USE_CASES: A.append(dict(asset_type="AGENT_SOP", key=u, title=f"Agent SOP: {u}", audience="INTERNAL", status="ACTIVE"))
    for u in USE_CASES: A.append(dict(asset_type="FAQ_LIBRARY", key=u, title=f"Provider FAQ Library: {u}", audience="PROVIDER", status="ACTIVE"))
    for p in range(4): A.append(dict(asset_type="PLAN_SUMMARY", key=p, title=f"Summary of Benefits: Plan {p+1}", audience="PROVIDER", status="ACTIVE"))
    for p in range(4): A.append(dict(asset_type="BENEFIT_DOC", key=p, title=f"Evidence of Coverage: Plan {p+1}", audience="MEMBER", status="ACTIVE"))
    for i, k in enumerate(PA_KINDS): A.append(dict(asset_type="PA_POLICY", key=k, title=A[PA0 + i]["title"], audience="PROVIDER", status="SUPERSEDED", superseded_by=PA0 + i))
    for i in range(N_SUP_CL): A.append(dict(asset_type="CLAIMS_ADJ_POLICY", key=DENIAL_CATS[i], title=A[CL0 + i]["title"], audience="PROVIDER", status="SUPERSEDED", superseded_by=CL0 + i))
    return A

N_ASSETS = len(catalog())
def pa_idx(kind): return PA0 + PA_KINDS.index(kind)
def claims_idx(cat): return CL0 + DENIAL_CATS.index(cat)
def sop_idx(uc): return SOP0 + USE_CASES.index(uc)
def faq_idx(uc): return FAQ0 + USE_CASES.index(uc)
def manual_idx(name): return MANUALS.index(name)
def ps_idx(p): return PS0 + p
SERVICE_TO_PA = {"MRI":"ADVANCED_IMAGING","PT":"OUTPATIENT_THERAPY","INPATIENT":"INPATIENT_ADMISSION","SNF":"SKILLED_NURSING","HOME_HEALTH":"HOME_HEALTH","DME":"DME","PHARMACY":"PHARMACY_PA","BH":"BEHAVIORAL_HEALTH"}

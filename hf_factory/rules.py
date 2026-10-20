"""Stage s7: rule engine (R/T/F/C/P/A subset used by S1), privacy scan, per-entity checksums, manifest."""
import json, re, os, collections
from . import refdata as R, schema
from .common import row_hash, MASK64, npi_valid, sha, GEN_VERSION, CATALOG_VERSION, RULESET_VERSION

# q1: first column of the first row (a count). rows: a few sample rows for the rule report.
def q1(con, sql, *a): return con.execute(sql, a).fetchone()[0]
def rows(con, sql, *a, n=5): return [list(r) for r in con.execute(sql + f" LIMIT {n}", a).fetchall()]

# Each rule() records checked rows vs violations. A rule passes only with zero violations; the report goes to rule-report.json.
def build_rules(con, ctx, vol):
    out = {}
    def rule(code, desc, checked, bad, samples=None):
        out[code] = dict(desc=desc, status="PASS" if bad == 0 else "FAIL", checked=checked, violations=bad, samples=samples or [])
    # R-01: every foreign key in schema.FKS must resolve (no orphans).
    # R-01
    bad = chk = 0; smp = []
    for k, v in schema.FKS.items():
        t, c = k.split("."); pt, pc = v.split(".")
        n = q1(con, f"SELECT COUNT(*) FROM {t} WHERE {c} IS NOT NULL"); b = q1(con, f"SELECT COUNT(*) FROM {t} c LEFT JOIN {pt} p ON c.{c}=p.{pc} WHERE c.{c} IS NOT NULL AND p.{pc} IS NULL")
        chk += n; bad += b
        if b: smp.append(f"{k}: {b}")
    rule("R-01", "No orphan foreign keys", chk, bad, smp)
    # R-02: the member was enrolled on the claim's service date.
    enr = "SELECT 1 FROM plan_enrollment e WHERE e.member_id=h.member_id AND e.effective_dt<=h.service_from_dt AND (e.term_dt IS NULL OR e.term_dt>=h.service_from_dt)"
    rule("R-02", "Claim member has enrollment covering service date", q1(con, "SELECT COUNT(*) FROM claim_header h"), q1(con, f"SELECT COUNT(*) FROM claim_header h WHERE NOT EXISTS ({enr})"))
    # R-03: a non-emergency claim for a PA-required service must carry an auth, unless it was denied CO-197 for lacking one.
    pa = ",".join(f"'{c}'" for c in R.PA_CODES)
    bad_sql = f"""SELECT COUNT(*) FROM claim_header h WHERE h.is_emergency=0 AND h.auth_id IS NULL AND
        (EXISTS (SELECT 1 FROM claim_line l WHERE l.claim_id=h.claim_id AND l.cpt_hcpcs IN ({pa})) OR EXISTS (SELECT 1 FROM pharmacy_claim_detail p WHERE p.claim_id=h.claim_id AND p.pa_required=1))
        AND NOT EXISTS (SELECT 1 FROM claim_denial d WHERE d.claim_id=h.claim_id AND d.carc='CO-197')"""
    rule("R-03", "PA-required non-emergency claims carry an auth or a CO-197 denial", q1(con, "SELECT COUNT(*) FROM claim_header"), q1(con, bad_sql))
    # Is this provider PARTICIPATING and ACTIVE in the claim's plan on the date of service?
    part = lambda col: f"EXISTS (SELECT 1 FROM network_participation np JOIN provider pv ON pv.npi=np.npi WHERE np.npi=h.{col} AND np.plan_id=h.plan_id AND np.status='PARTICIPATING' AND pv.status='ACTIVE' AND np.effective_dt<=h.service_from_dt AND (np.term_dt IS NULL OR np.term_dt>=h.service_from_dt))"
    cols = ["billing_npi", "rendering_npi", "facility_npi"]
    allpart = " AND ".join(f"(h.{c} IS NULL OR {part(c)})" for c in cols)
    # R-04 both directions: IN-network claims need participating providers; non-IN claims must not have them.
    # PON-04 is exempt on purpose: that provider is live but the claims data lags, so the claims deny as out of network.
    b1 = q1(con, f"SELECT COUNT(*) FROM claim_header h WHERE h.network_status='IN' AND NOT ({allpart})")
    b2 = q1(con, f"SELECT COUNT(*) FROM claim_header h WHERE h.network_status!='IN' AND h.scenario_id!='PON-04' AND ({allpart})")
    rule("R-04", "network_status agrees with participation on date of service", q1(con, "SELECT COUNT(*) FROM claim_header"), b1 + b2)
    # R-05: every currently covered member has exactly one ACTIVE PCP, who is a participating PCP with an open panel.
    cur = "e.status IN ('ACTIVE','GRACE_PERIOD','PENDING') AND e.effective_dt<='2026-12-31'"
    b = q1(con, f"SELECT COUNT(*) FROM plan_enrollment e WHERE {cur} AND (SELECT COUNT(*) FROM pcp_assignment p WHERE p.member_id=e.member_id AND p.status='ACTIVE')!=1")
    b += q1(con, f"""SELECT COUNT(*) FROM pcp_assignment p JOIN plan_enrollment e ON e.member_id=p.member_id AND {cur} JOIN provider pv ON pv.npi=p.pcp_npi
        LEFT JOIN network_participation np ON np.npi=p.pcp_npi AND np.plan_id=e.plan_id WHERE p.status='ACTIVE' AND (pv.provider_type!='PCP' OR np.status!='PARTICIPATING' OR np.panel_status='CLOSED')""")
    rule("R-05", "Exactly one ACTIVE PCP per covered member; participating PCP, panel not closed", q1(con, f"SELECT COUNT(*) FROM plan_enrollment e WHERE {cur}"), b)
    # R-06: caregiver relationships must carry scope, consent dates and verification.
    rule("R-06", "Caregiver rows carry scope, consent dates and verification", q1(con, "SELECT COUNT(*) FROM family_relationship WHERE caregiver_flag=1"),
         q1(con, "SELECT COUNT(*) FROM family_relationship WHERE caregiver_flag=1 AND (authorization_scope IS NULL OR authorization_scope='[]' OR consent_effective_dt IS NULL OR consent_expiry_dt IS NULL OR verified!=1)"))
    # R-07: replacement/void claims point to an original; REPROCESSED claims have a history row.
    b = q1(con, "SELECT COUNT(*) FROM claim_header WHERE claim_frequency_code IN ('7','8') AND original_claim_id IS NULL")
    b += q1(con, "SELECT COUNT(*) FROM claim_header h WHERE status='REPROCESSED' AND NOT EXISTS (SELECT 1 FROM reprocessing_history r WHERE r.claim_id=h.claim_id)")
    rule("R-07", "Replacement claims reference originals; REPROCESSED claims have history", q1(con, "SELECT COUNT(*) FROM claim_header WHERE claim_frequency_code IN ('7','8') OR status='REPROCESSED'"), b)
    # R-09: a closed care gap must cite a PAID claim for the same member.
    rule("R-09", "Closed care gaps cite a paid claim for the same member", q1(con, "SELECT COUNT(*) FROM care_gap WHERE status='CLOSED'"),
         q1(con, "SELECT COUNT(*) FROM care_gap g WHERE status='CLOSED' AND NOT EXISTS (SELECT 1 FROM claim_header h WHERE h.claim_id=g.closed_by_claim_id AND h.member_id=g.member_id AND h.status='PAID')"))
    # R-10: every inpatient discharge produces a transition-of-care row and care case.
    rule("R-10", "Every inpatient discharge has a TOC row and TOC care case", q1(con, "SELECT COUNT(*) FROM claim_header WHERE bill_type='111' AND claim_frequency_code='1'"),
         q1(con, "SELECT COUNT(*) FROM claim_header h WHERE bill_type='111' AND claim_frequency_code='1' AND NOT EXISTS (SELECT 1 FROM transition_of_care t JOIN care_case c ON c.case_id=t.case_id WHERE t.member_id=h.member_id AND t.discharge_dt=h.service_to_dt AND t.facility_npi=h.facility_npi)"))
    # R-11: every denial code (claim CARC or authorization denial category) links to an ACTIVE knowledge asset, so agents can always explain a denial.
    lk = "SELECT 1 FROM asset_link l JOIN knowledge_asset a ON a.asset_id=l.asset_id WHERE l.linked_entity='DENIAL_CODE' AND a.status='ACTIVE' AND l.linked_key="
    b = q1(con, f"SELECT COUNT(*) FROM claim_denial d WHERE NOT EXISTS ({lk}d.carc)")
    b += q1(con, f"SELECT COUNT(*) FROM auth_determination d WHERE d.denial_category IS NOT NULL AND NOT EXISTS ({lk}'AUTH-'||d.denial_category)")
    rule("R-11", "Every denial links to an ACTIVE explanatory asset", q1(con, "SELECT (SELECT COUNT(*) FROM claim_denial)+(SELECT COUNT(*) FROM auth_determination WHERE denial_category IS NOT NULL)"), b)
    # ---- temporal
    # T-01: enrollments of one member must not overlap; PCP spans must sit inside an enrollment span.
    ov = """SELECT COUNT(*) FROM plan_enrollment a JOIN plan_enrollment b ON a.member_id=b.member_id AND a.enrollment_id<b.enrollment_id
            WHERE a.effective_dt<=COALESCE(b.term_dt,'9999') AND b.effective_dt<=COALESCE(a.term_dt,'9999')"""
    b = q1(con, ov) + q1(con, """SELECT COUNT(*) FROM pcp_assignment p WHERE NOT EXISTS (SELECT 1 FROM plan_enrollment e WHERE e.member_id=p.member_id AND p.effective_dt>=e.effective_dt AND (e.term_dt IS NULL OR (p.term_dt IS NOT NULL AND p.term_dt<=e.term_dt)))""")
    rule("T-01", "Enrollments do not overlap; PCP spans fall inside enrollment spans", q1(con, "SELECT COUNT(*) FROM plan_enrollment"), b)
    # T-02: claim dates are in order.
    rule("T-02", "Claim date ordering: from<=to<=received<=adjudicated<=paid", q1(con, "SELECT COUNT(*) FROM claim_header"),
         q1(con, "SELECT COUNT(*) FROM claim_header WHERE NOT (service_from_dt<=service_to_dt AND service_to_dt<=received_dt AND (adjudicated_dt IS NULL OR received_dt<=adjudicated_dt) AND (paid_dt IS NULL OR adjudicated_dt<=paid_dt) AND received_dt<=?)", ctx.d(0)))
    # T-03: decision deadline = received + 72h (urgent) or 7 days (standard), and no determination is later than its deadline.
    rule("T-03", "Auth due = received + 72h (urgent) / 7 days (standard); determination <= due", q1(con, "SELECT COUNT(*) FROM auth_request"),
         q1(con, """SELECT COUNT(*) FROM auth_request a WHERE ROUND((julianday(decision_due_dt)-julianday(received_dt))*24)!=CASE urgency WHEN 'URGENT' THEN 72 ELSE 168 END""")
         + q1(con, "SELECT COUNT(*) FROM auth_determination d JOIN auth_request a ON a.auth_id=d.auth_id WHERE d.determination_dt>a.decision_due_dt"))
    # T-04: an approved auth's valid window covers the claim's date of service.
    rule("T-04", "Auth valid window covers claim date of service", q1(con, "SELECT COUNT(*) FROM claim_header WHERE auth_id IS NOT NULL"),
         q1(con, "SELECT COUNT(*) FROM claim_header h WHERE h.auth_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM auth_determination d WHERE d.auth_id=h.auth_id AND d.outcome IN ('APPROVED','PARTIAL') AND d.valid_from<=h.service_from_dt AND d.valid_to>=h.service_from_dt)"))
    # T-05: appeals are filed on time; the only late filing allowed is the seeded PAP-04 exception (and PAP-04 must be late).
    rule("T-05", "Appeals filed within deadline; late filings only in the PAP-04 exception scenario", q1(con, "SELECT COUNT(*) FROM appeal_record"),
         q1(con, "SELECT COUNT(*) FROM appeal_record WHERE (filed_dt>deadline_dt AND scenario_id!='PAP-04') OR (scenario_id='PAP-04' AND filed_dt<=deadline_dt)"))
    # T-08: ACTIVE knowledge assets are inside their effective/expiry window at as_of_date.
    rule("T-08", "ACTIVE assets are within effective/expiry window", q1(con, "SELECT COUNT(*) FROM knowledge_asset WHERE status='ACTIVE'"),
         q1(con, "SELECT COUNT(*) FROM knowledge_asset WHERE status='ACTIVE' AND NOT (effective_dt<=? AND expiry_dt>=?)", ctx.d(0), ctx.d(0)))
    # ---- financial
    # F-01..F-08 use integer-cent contract rates from ref_code so allowed amounts can be recomputed independently of the generator.
    ref = {}
    for sysm, code, rate in con.execute("SELECT system, code, rate_cents FROM ref_code WHERE rate_cents IS NOT NULL"): ref[(sysm, code)] = rate
    hdr_bad = con.execute("""SELECT COUNT(*) FROM claim_header h JOIN (SELECT claim_id, SUM(billed) b, SUM(allowed) a, SUM(paid) p FROM claim_line GROUP BY claim_id) s ON s.claim_id=h.claim_id
           WHERE ABS(h.total_billed-s.b)>0.005 OR ABS(h.total_allowed-s.a)>0.005 OR ABS(h.total_paid-s.p)>0.005 OR NOT (h.total_billed>=h.total_allowed AND h.total_allowed>=h.total_paid AND h.total_paid>=0)""").fetchone()[0]
    ln_bad = q1(con, "SELECT COUNT(*) FROM claim_line WHERE NOT (billed>=allowed AND allowed>=paid AND paid>=0)")
    # F-01: billed >= allowed >= paid >= 0, and header totals equal the sum of the lines.
    rule("F-01", "billed>=allowed>=paid>=0; header = sum(lines)", q1(con, "SELECT COUNT(*) FROM claim_header"), hdr_bad + ln_bad)
    pdet = {r[0]: r[1:] for r in con.execute("SELECT claim_id, ndc, days_supply FROM pharmacy_claim_detail")}
    prden = {r[0] for r in con.execute("SELECT claim_id FROM claim_denial WHERE liable_party='MEMBER'")}
    # F-02: allowed = contract rate x units (pharmacy: per 30-day supply); member cost share never exceeds allowed or the OOP maximum.
    # Member-liability denials are skipped because those lines carry no contract-rate payment.
    f2 = n2 = 0; smp = []
    for lid, cid, cpt, rev, units, allowed, ded, cop, coi, st in con.execute("SELECT line_id, claim_id, cpt_hcpcs, revenue_code, units, allowed, deductible, copay, coinsurance, line_status FROM claim_line"):
        if cid in prden: continue
        n2 += 1
        if cid in pdet: exp = ref.get(("NDC", pdet[cid][0]), -1) * (pdet[cid][1] // 30)
        elif rev: exp = ref.get(("REVENUE", rev), -1) * units
        else: exp = ref.get(("CPT_HCPCS", cpt), -1) * units
        if abs(allowed * 100 - exp) > 0.5 or round((ded or 0) + (cop or 0) + (coi or 0), 2) > allowed + 0.005: f2 += 1; smp.append(lid)
    oop = q1(con, "SELECT COUNT(*) FROM member_accumulator a JOIN plan_enrollment e ON e.member_id=a.member_id AND e.effective_dt>='2026-01-01' JOIN plan p ON p.plan_id=e.plan_id WHERE a.oop_met>p.oop_max")
    rule("F-02", "allowed = contract rate x units; member liability <= allowed and <= OOP max", n2, f2 + oop, smp[:5])
    # F-03: rebuild each member's OOP accumulator from paid claim lines and compare.
    exp_acc = collections.defaultdict(float)
    for mid, net, v in con.execute("SELECT h.member_id, CASE WHEN h.network_status='IN' THEN 'IN' ELSE 'OON' END, SUM(l.deductible+l.copay+l.coinsurance) FROM claim_line l JOIN claim_header h ON h.claim_id=l.claim_id WHERE h.status='PAID' GROUP BY 1,2"): exp_acc[(mid, net)] = v
    b = sum(1 for mid, net, oo in con.execute("SELECT member_id, network, oop_met FROM member_accumulator") if abs(exp_acc.get((mid, net), 0.0) - oo) > 0.005)
    rule("F-03", "member_accumulator = sum of cost share on paid claims", q1(con, "SELECT COUNT(*) FROM member_accumulator"), b)
    # F-04: all codes exist in reference tables; diagnosis codes are plausible for the member's sex and age.
    cp = {c for c, in con.execute("SELECT code FROM ref_code WHERE system='CPT_HCPCS'")}; rv = {c for c, in con.execute("SELECT code FROM ref_code WHERE system='REVENUE'")}
    nd = {c for c, in con.execute("SELECT code FROM ref_code WHERE system='NDC'")}; ic = {c: s for c, s in con.execute("SELECT code, sex FROM ref_code WHERE system='ICD10'")}
    ca = {c for c, in con.execute("SELECT code FROM ref_code WHERE system='CARC'")}
    b = q1(con, "SELECT COUNT(*) FROM claim_line l JOIN pharmacy_claim_detail p ON p.claim_id=l.claim_id WHERE l.cpt_hcpcs NOT IN (SELECT code FROM ref_code WHERE system='NDC')")
    b += sum(1 for c, r in con.execute("SELECT cpt_hcpcs, revenue_code FROM claim_line l WHERE NOT EXISTS (SELECT 1 FROM pharmacy_claim_detail p WHERE p.claim_id=l.claim_id)") if (c and c not in cp) or (r and r not in rv))
    b += sum(1 for c, in con.execute("SELECT carc FROM claim_denial") if c not in ca) + sum(1 for c, in con.execute("SELECT carc FROM claim_adjustment") if c not in ca)
    for dx, sex in con.execute("SELECT a.primary_dx, m.sex_at_birth FROM auth_request a JOIN member_demographics m ON m.member_id=a.member_id"):
        if dx not in ic or (ic[dx] and ic[dx] != sex): b += 1
    b += q1(con, "SELECT COUNT(*) FROM member_demographics WHERE CAST((julianday(?)-julianday(dob))/365.25 AS INT) NOT BETWEEN 0 AND 110", ctx.d(0))
    rule("F-04", "Codes come from reference tables; age/sex plausibility", q1(con, "SELECT COUNT(*) FROM claim_line")+q1(con, "SELECT COUNT(*) FROM auth_request"), b)
    b = q1(con, """SELECT COUNT(*) FROM claim_header h WHERE ABS(h.member_resp - COALESCE((SELECT SUM(amount) FROM claim_adjustment a WHERE a.claim_id=h.claim_id AND a.group_code='PR'),0))>0.005""")
    b += q1(con, "SELECT COUNT(*) FROM claim_line l WHERE line_status='DENIED' AND (paid!=0 OR NOT EXISTS (SELECT 1 FROM claim_adjustment a WHERE a.line_id=l.line_id))")
    b += q1(con, "SELECT COUNT(*) FROM claim_denial d WHERE (substr(d.carc,1,2)='CO' AND d.liable_party!='PROVIDER') OR (substr(d.carc,1,2)='PR' AND d.liable_party!='MEMBER')")
    b += q1(con, "SELECT COUNT(*) FROM claim_header h WHERE h.status IN ('DENIED','APPEALED') AND substr((SELECT carc FROM claim_denial d WHERE d.claim_id=h.claim_id),1,2)='CO' AND h.member_resp!=0")
    # F-05: CO adjustments are provider write-offs (not billable to the member); PR adjustments are member responsibility.
    rule("F-05", "CO = provider write-off, PR = member liability; denied lines paid 0 with adjustment", q1(con, "SELECT COUNT(*) FROM claim_header"), b)
    b = q1(con, """SELECT COUNT(*) FROM reprocessing_history r JOIN claim_header n ON n.claim_id=r.new_claim_id JOIN claim_header o ON o.claim_id=r.claim_id
        WHERE ABS((r.paid_after-r.paid_before)-(n.total_paid-o.total_paid))>0.005 OR r.paid_after<0""")
    # F-06: a reprocessing event's payment change equals the difference between the new and original claim.
    rule("F-06", "Reprocessing payment delta consistent; net paid >= 0", q1(con, "SELECT COUNT(*) FROM reprocessing_history"), b)
    b = q1(con, "SELECT COUNT(*) FROM claim_payment p JOIN claim_header h ON h.claim_id=p.claim_id JOIN provider v ON v.npi=h.billing_npi WHERE (p.scenario_id!='PPI-04' AND p.payee_tin!=v.tin) OR (p.scenario_id='PPI-04' AND p.recoup_flag=0 AND p.payee_tin=v.tin)")
    # F-07: payments go to the billing provider's TIN, except the PPI-04 scenario, which seeds a mismatch.
    rule("F-07", "Payee TIN equals the billing provider TIN (PPI-04 seeds the exception)  [added]", q1(con, "SELECT COUNT(*) FROM claim_payment"), b)
    b = q1(con, """SELECT COUNT(*) FROM claim_payment r WHERE r.recoup_flag=1 AND NOT (r.paid_amount<0
        AND EXISTS (SELECT 1 FROM claim_header o JOIN claim_header y ON y.claim_id=r.offset_ref JOIN claim_payment yp ON yp.claim_id=y.claim_id AND yp.recoup_flag=0
                    WHERE o.claim_id=r.claim_id AND o.billing_npi=y.billing_npi AND yp.payment_dt=r.payment_dt)
        AND -r.paid_amount <= (SELECT SUM(p.paid_amount) FROM claim_payment p WHERE p.claim_id=r.claim_id AND p.recoup_flag=0)
        AND r.payment_dt > (SELECT MIN(p.payment_dt) FROM claim_payment p WHERE p.claim_id=r.claim_id AND p.recoup_flag=0))""")
    # F-08: a recoupment is a negative row, no larger than the original payment, and is netted on a later remit of the same billing provider.
    rule("F-08", "Recoupment rows are negative, no larger than the original payment, and ride a later remit of the same billing provider  [added]", q1(con, "SELECT COUNT(*) FROM claim_payment WHERE recoup_flag=1"), b)
    # P-01 / P-02: privacy by construction. Reserved formats (555-01xx phones, @example.test, 9-prefixed Luhn NPIs, 99- TINs) cannot match real people.
    # ---- privacy
    b = chk = 0
    for t in schema.TABLES:
        n = q1(con, f"SELECT COUNT(*) FROM {t}"); chk += n
        b += q1(con, f"SELECT COUNT(*) FROM {t} WHERE is_synthetic!=1 OR tenant_id IS NULL OR tenant_id!=? OR scenario_id IS NULL", ctx.dataset_id)
    rule("P-01", "Every row has is_synthetic, tenant_id, scenario_id", chk, b)
    b = q1(con, "SELECT COUNT(*) FROM member_demographics WHERE phone NOT GLOB '555-01[0-9][0-9]' OR email NOT LIKE '%@example.test'")
    b += q1(con, "SELECT COUNT(*) FROM practice_location WHERE phone NOT GLOB '555-01[0-9][0-9]'")
    b += sum(1 for n, t in con.execute("SELECT npi, tin FROM provider") if not npi_valid(n) or n[0] not in "9" or not re.fullmatch(r"99-\d{7}", t))
    rule("P-02", "Reserved formats: 555-01xx phones, @example.test, Luhn NPIs outside real range, synthetic TINs", q1(con, "SELECT COUNT(*) FROM member_demographics")+q1(con, "SELECT COUNT(*) FROM provider"), b)
    # A-01 / C-03 / C-04: policy rules. Physicians (not nurses) deny; denials give a reason and appeal rights; emergencies are never denied for lacking auth.
    rule("A-01", "Denials are made by physician reviewers, never nurses", q1(con, "SELECT COUNT(*) FROM auth_determination WHERE outcome='DENIED'"),
         q1(con, "SELECT COUNT(*) FROM auth_determination WHERE outcome IN ('DENIED','PARTIAL') AND decided_by_role='NURSE'"))
    rule("C-03", "Denials state a specific reason and appeal rights", q1(con, "SELECT COUNT(*) FROM auth_determination WHERE outcome='DENIED'")+q1(con, "SELECT COUNT(*) FROM claim_denial"),
         q1(con, "SELECT COUNT(*) FROM auth_determination WHERE outcome='DENIED' AND (denial_rationale IS NULL OR appeal_rights_sent_dt IS NULL)") + q1(con, "SELECT COUNT(*) FROM claim_denial WHERE reason_text IS NULL"))
    rule("C-04", "Emergency claims are never denied for authorization", q1(con, "SELECT COUNT(*) FROM claim_header WHERE is_emergency=1"),
         q1(con, "SELECT COUNT(*) FROM claim_header h JOIN claim_denial d ON d.claim_id=h.claim_id WHERE h.is_emergency=1 AND d.denial_category='AUTH'"))
    return out

# Aggregate privacy checks: no near-duplicate people, and a count of small quasi-identifier groups (<11) for the report.
def privacy_scan(con):
    dup = q1(con, "SELECT COUNT(*) FROM (SELECT first_name,last_name,dob,zip,COUNT(*) c FROM member_demographics GROUP BY 1,2,3,4 HAVING c>1)")
    cells = [c for c, in con.execute("SELECT COUNT(*) FROM member_demographics GROUP BY substr(zip,1,3), sex_at_birth, substr(dob,1,3)")]
    return {"status": "PASS" if dup == 0 else "FAIL", "near_duplicate_members": dup, "reserved_formats": "see P-02",
            "quasi_identifier_cells": len(cells), "cells_below_11": sum(1 for c in cells if c < 11), "note": "aggregate statistics suppress cells below 11 (P-04)"}

# Checksum = sum of 64-bit row hashes mod 2**64, so it ignores row order. The repro tool compares these across runs.
def checksums(con, upto=None):
    """Order-independent per-entity checksum: sum of 64-bit row hashes. upto = {table: n} limits to row_idx < n (prefix check)."""
    out = {}
    for t, spec in schema.TABLES.items():
        cols = [c for c, _ in spec["cols"]]; s = n = 0; mx = -1
        where = f" WHERE row_idx < {int(upto[t])}" if upto and t in upto else ""
        for r in con.execute(f"SELECT {','.join(cols)} FROM {t}{where}"):
            s = (s + row_hash(r)) & MASK64; n += 1
        out[t] = {"rows": n, "checksum": f"{s:016x}", "max_row_idx": q1(con, f"SELECT COALESCE(MAX(row_idx),-1) FROM {t}")}
    return out

# The manifest ties a dataset to its inputs: blueprint and data-spec hashes, seed, versions, per-entity checksums, rule results and scenario coverage.
# Small status counts (<11) are suppressed in the profile.
def manifest(con, ctx, bp, rules, scan):
    cs = checksums(con)
    cov = {}
    for t in ("claim_header", "auth_request", "member_demographics"):
        cov[t] = dict(con.execute(f"SELECT scenario_id, COUNT(*) FROM {t} GROUP BY 1 ORDER BY 1").fetchall())
    den = q1(con, "SELECT COUNT(*) FROM claim_header WHERE claim_frequency_code='1'")
    prof = dict(
        claim_status={k: (v if v >= 11 else "<11") for k, v in con.execute("SELECT status, COUNT(*) FROM claim_header GROUP BY 1")},
        denial_rate=round(q1(con, "SELECT COUNT(*) FROM claim_denial") / max(1, den), 4),
        pend_rate=round(q1(con, "SELECT COUNT(*) FROM claim_pend") / max(1, den), 4),
        auth_status={k: (v if v >= 11 else "<11") for k, v in con.execute("SELECT status, COUNT(*) FROM auth_request GROUP BY 1")},
        pharmacy_share=round(q1(con, "SELECT COUNT(*) FROM claim_header WHERE claim_type='PHARMACY'") / max(1, den), 4))
    return dict(blueprint_hash=bp["blueprint_hash"], data_spec_hash=bp["data_spec"]["data_spec_hash"], root_seed=ctx.seed, as_of_date=ctx.d(0), catalog_version=CATALOG_VERSION,
                rule_set_version=RULESET_VERSION, generator_version=GEN_VERSION, dataset_id=ctx.dataset_id, volumes=bp["data_spec"]["volumes"], entity_checksums=cs,
                total_rows=sum(v["rows"] for v in cs.values()), rules_passed=sum(1 for r in rules.values() if r["status"] == "PASS"), rules_total=len(rules), privacy_scan=scan["status"],
                scenario_coverage=cov, profile=prof)

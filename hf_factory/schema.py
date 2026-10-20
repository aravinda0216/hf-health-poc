"""SQLite schema for the S1 entity set (34 entities + reference table), derived from the Healthcare AI Platform Factory blueprint.
Column DSL:  name:T  where T = T text | I int | R real | B bool(int) | J json(text).  Every table also gets
row_idx, scenario_id, tenant_id, is_synthetic (P-01)."""
import json, sqlite3

# Columns added to every table. They carry the privacy markers checked by rule P-01 (synthetic flag, tenant, scenario) and row_idx,
# the generation index that makes prefix and checksum comparisons possible.
COMMON = [("row_idx","I"),("scenario_id","T"),("tenant_id","T"),("is_synthetic","B")]

# Build a table spec from the compact DSL: pk is 'a+b' for composite keys, cols is 'name:Type ...' separated by spaces.
def _t(domain, pk, cols):
    return {"domain": domain, "pk": pk.split("+"), "cols": [tuple(c.split(":")) for c in cols.split()]}

# One entry per entity. 'M' = member, 'P' = provider/plan reference, 'C' = claims, 'A' = authorization, 'K' = knowledge/care (domain tag only).
TABLES = {
# ---- Member (primary) ----
"member_demographics": _t("M","member_id","member_id:T persona_type:T first_name:T last_name:T dob:T sex_at_birth:T address:T city:T state:T zip:T lat:R lon:R preferred_language:T accessibility_needs:J preferred_channel:T phone:T email:T verification_factors:J"),
"family_relationship": _t("M","relationship_id","relationship_id:T member_id:T related_member_id:T subscriber_id:T relationship_code:T caregiver_flag:B caregiver_name:T caregiver_relationship:T authorization_scope:J consent_effective_dt:T consent_expiry_dt:T verified:B"),
"plan": _t("M","plan_id","plan_id:T plan_name:T lob:T plan_year:I network_type:T deductible:R oop_max:R copay_pcp:R copay_specialist:R coinsurance_pct:R allowances:J pa_required_codes:J formulary_id:T"),
"plan_enrollment": _t("M","enrollment_id","enrollment_id:T member_id:T plan_id:T effective_dt:T term_dt:T status:T premium_status:T cob_flag:B other_payer_id:T retro_change_flag:B"),
"member_accumulator": _t("M","member_id+plan_year+network","member_id:T plan_year:I network:T deductible_met:R oop_met:R"),
"pcp_assignment": _t("M","assignment_id","assignment_id:T member_id:T pcp_npi:T effective_dt:T term_dt:T assignment_type:T status:T change_reason:T"),
"risk_score": _t("M","member_id+as_of_dt+model","member_id:T as_of_dt:T model:T score:R scale_min:R scale_max:R tier:T drivers:J"),
"care_gap": _t("M","gap_id","gap_id:T member_id:T measure:T status:T due_dt:T closed_by_claim_id:T"),
"sdoh_indicator": _t("M","indicator_id","indicator_id:T member_id:T domain:T positive:B screened_dt:T source:T referral_status:T"),
# ---- Provider (supporting) ----
"provider": _t("P","npi","npi:T provider_type:T entity_type:T first_name:T last_name:T org_name:T taxonomy_code:T specialty:T facility_type:T languages:J gender:T tin:T status:T"),
"network_participation": _t("P","participation_id","participation_id:T npi:T plan_id:T status:T tier:T effective_dt:T term_dt:T panel_status:T onboarding_stage:T pending_items:J onboarding_started_dt:T expected_live_dt:T"),
"practice_location": _t("P","location_id","location_id:T npi:T address:T city:T state:T zip:T lat:R lon:R phone:T accepting_new_patients:B accessibility:J telehealth:B last_verified_dt:T availability_loaded:B added_dt:T"),
# ---- Claims (primary) ----
"claim_header": _t("C","claim_id","claim_id:T claim_type:T member_id:T plan_id:T billing_npi:T rendering_npi:T facility_npi:T claim_frequency_code:T original_claim_id:T service_from_dt:T service_to_dt:T received_dt:T adjudicated_dt:T paid_dt:T status:T network_status:T is_emergency:B auth_id:T bill_type:T drg_code:T total_billed:R total_allowed:R total_paid:R member_resp:R"),
"claim_line": _t("C","line_id","line_id:T claim_id:T line_no:I cpt_hcpcs:T revenue_code:T modifier:T rendering_npi:T units:I billed:R allowed:R paid:R deductible:R copay:R coinsurance:R line_status:T"),
"pharmacy_claim_detail": _t("C","claim_id","claim_id:T ndc:T drug_name:T quantity:I days_supply:I pharmacy_npi:T fill_dt:T formulary_tier:I pa_required:B copay_applied:R reject_code:T"),
"claim_denial": _t("C","denial_id","denial_id:T claim_id:T line_id:T carc:T rarc:T denial_category:T reason_text:T liable_party:T denial_dt:T appealable:B appeal_deadline_dt:T"),
"claim_pend": _t("C","pend_id","pend_id:T claim_id:T pend_reason:T pend_dt:T expected_resolution_dt:T info_needed_from:T resolved_dt:T"),
"claim_adjustment": _t("C","adjustment_id","adjustment_id:T claim_id:T line_id:T group_code:T carc:T amount:R adjustment_dt:T"),
"claim_payment": _t("C","payment_id","payment_id:T claim_id:T payment_dt:T method:T paid_amount:R trace_no:T payee_tin:T recoup_flag:B offset_ref:T recoup_reason:T"),
"appeal_record": _t("C","appeal_id","appeal_id:T claim_id:T filed_by:T level:T filed_dt:T deadline_dt:T status:T outcome:T decision_dt:T documents_received:J"),
"reprocessing_history": _t("C","reprocess_id","reprocess_id:T claim_id:T new_claim_id:T trigger:T reprocess_dt:T paid_before:R paid_after:R performed_by:T"),
# ---- Authorization (primary) ----
"auth_request": _t("A","auth_id","auth_id:T member_id:T requesting_npi:T servicing_npi:T facility_npi:T plan_id:T auth_type:T urgency:T service_type:T primary_dx:T received_dt:T decision_due_dt:T status:T channel_received:T submitted_by_persona:T"),
"auth_line": _t("A","auth_line_id","auth_line_id:T auth_id:T cpt_hcpcs_or_ndc:T units_requested:I units_approved:I site_of_service:T line_status:T"),
"auth_determination": _t("A","determination_id","determination_id:T auth_id:T outcome:T valid_from:T valid_to:T denial_category:T denial_rationale:T criteria_asset_id:T decided_by_role:T determination_dt:T appeal_rights_sent_dt:T"),
"clinical_review_queue": _t("A","queue_id","queue_id:T auth_id:T review_level:T assigned_dt:T sla_due_dt:T status:T p2p_available_until:T"),
"documentation_requirement": _t("A","req_id","req_id:T auth_id:T doc_type:T state:T requested_dt:T due_dt:T clock_paused:B"),
# ---- Care management (supporting) ----
"care_case": _t("CM","case_id","case_id:T member_id:T case_type:T status:T priority:T opened_dt:T closed_dt:T assigned_role:T source:T"),
"transition_of_care": _t("CM","toc_id","toc_id:T member_id:T facility_npi:T case_id:T admit_dt:T discharge_dt:T disposition:T toc_call_due_dt:T follow_up_due_dt:T med_reconciliation_done:B toc_status:T readmit_30d:B"),
# ---- Knowledge (primary) ----
"knowledge_asset": _t("K","asset_id","asset_id:T asset_type:T title:T version:T effective_dt:T expiry_dt:T audience:T lob:J state:J plan_id:T status:T supersedes_asset_id:T language:T reading_grade:I license_class:T approved_by:T approval_dt:T"),
"asset_chunk": _t("K","chunk_id","chunk_id:T asset_id:T section_path:T text:T embedding_ref:T metadata:J"),
"faq_entry": _t("K","faq_id","faq_id:T asset_id:T question_variants:J answer:T persona_tags:J use_case_code:T"),
"agent_sop": _t("K","sop_id","sop_id:T asset_id:T use_case_code:T steps:J verification_required:J escalation_triggers:J handoff_queue:T forbidden_actions:J"),
"asset_link": _t("K","link_id","link_id:T asset_id:T linked_entity:T linked_key:T"),
"qa_golden": _t("K","qa_id","qa_id:T use_case_code:T persona:T question:T expected_chunk_ids:J expected_behavior:T difficulty:T"),
"ref_code": _t("R","system+code","system:T code:T description:T rate_cents:I pa_required:B sex:T"),
}
for _n, _t_ in TABLES.items():
    _t_["cols"] = _t_["cols"] + COMMON

# foreign keys: child.col -> parent.col  (R-01 orphan check)
FKS = {
 "family_relationship.member_id":"member_demographics.member_id","family_relationship.related_member_id":"member_demographics.member_id",
 "plan_enrollment.member_id":"member_demographics.member_id","plan_enrollment.plan_id":"plan.plan_id",
 "member_accumulator.member_id":"member_demographics.member_id","pcp_assignment.member_id":"member_demographics.member_id","pcp_assignment.pcp_npi":"provider.npi",
 "risk_score.member_id":"member_demographics.member_id","care_gap.member_id":"member_demographics.member_id","care_gap.closed_by_claim_id":"claim_header.claim_id",
 "sdoh_indicator.member_id":"member_demographics.member_id",
 "network_participation.npi":"provider.npi","network_participation.plan_id":"plan.plan_id","practice_location.npi":"provider.npi",
 "claim_header.member_id":"member_demographics.member_id","claim_header.plan_id":"plan.plan_id","claim_header.billing_npi":"provider.npi",
 "claim_header.rendering_npi":"provider.npi","claim_header.facility_npi":"provider.npi","claim_header.original_claim_id":"claim_header.claim_id","claim_header.auth_id":"auth_request.auth_id",
 "claim_line.claim_id":"claim_header.claim_id","claim_line.rendering_npi":"provider.npi","pharmacy_claim_detail.claim_id":"claim_header.claim_id","pharmacy_claim_detail.pharmacy_npi":"provider.npi",
 "claim_denial.claim_id":"claim_header.claim_id","claim_denial.line_id":"claim_line.line_id","claim_pend.claim_id":"claim_header.claim_id",
 "claim_adjustment.claim_id":"claim_header.claim_id","claim_adjustment.line_id":"claim_line.line_id","claim_payment.claim_id":"claim_header.claim_id","claim_payment.offset_ref":"claim_header.claim_id",
 "appeal_record.claim_id":"claim_header.claim_id","reprocessing_history.claim_id":"claim_header.claim_id","reprocessing_history.new_claim_id":"claim_header.claim_id",
 "auth_request.member_id":"member_demographics.member_id","auth_request.requesting_npi":"provider.npi","auth_request.servicing_npi":"provider.npi","auth_request.facility_npi":"provider.npi","auth_request.plan_id":"plan.plan_id",
 "auth_line.auth_id":"auth_request.auth_id","auth_determination.auth_id":"auth_request.auth_id","auth_determination.criteria_asset_id":"knowledge_asset.asset_id",
 "clinical_review_queue.auth_id":"auth_request.auth_id","documentation_requirement.auth_id":"auth_request.auth_id",
 "care_case.member_id":"member_demographics.member_id","transition_of_care.member_id":"member_demographics.member_id","transition_of_care.facility_npi":"provider.npi","transition_of_care.case_id":"care_case.case_id",
 "knowledge_asset.supersedes_asset_id":"knowledge_asset.asset_id","knowledge_asset.plan_id":"plan.plan_id",
 "asset_chunk.asset_id":"knowledge_asset.asset_id","faq_entry.asset_id":"knowledge_asset.asset_id","agent_sop.asset_id":"knowledge_asset.asset_id","asset_link.asset_id":"knowledge_asset.asset_id",
}

# entities whose rows are derived from other tables (not prefix-stable on 'evolve')
# Tables computed from other tables (accumulators, care gaps, cases). They are excluded from the evolve prefix check because they change when members/claims are added.
DERIVED = {"member_accumulator","care_gap","care_case","transition_of_care","ref_code"}
# generation order (R-01)
# Generation order: parents before children, so foreign keys (rule R-01) can always be satisfied.
ORDER = ["ref_code","plan","provider","network_participation","practice_location","member_demographics","family_relationship","plan_enrollment","pcp_assignment","risk_score","care_gap","sdoh_indicator",
 "auth_request","auth_line","auth_determination","clinical_review_queue","documentation_requirement",
 "claim_header","claim_line","pharmacy_claim_detail","claim_denial","claim_pend","claim_adjustment","claim_payment","appeal_record","reprocessing_history","member_accumulator",
 "care_case","transition_of_care","knowledge_asset","asset_chunk","faq_entry","agent_sop","asset_link","qa_golden"]
# DSL type letters mapped to SQLite types. B is stored as 0/1, J as JSON text.
TYPES = {"T":"TEXT","I":"INTEGER","R":"REAL","B":"INTEGER","J":"TEXT"}

def ddl():
    out = []
    for n, t in TABLES.items():
        cols = ", ".join(f"{c} {TYPES[ty]}" for c, ty in t["cols"])
        out.append(f"CREATE TABLE {n} ({cols}, PRIMARY KEY ({', '.join(t['pk'])}));")
    return out

# Indexes are created after the bulk insert (faster than inserting into indexed tables). Every FK column, scenario_id and row_idx is indexed.
def index_ddl():
    ix, seen = [], set()
    for k in FKS:
        t, c = k.split(".")
        ix.append(f"CREATE INDEX IF NOT EXISTS ix_{t}_{c} ON {t}({c});")
    for t in TABLES: ix.append(f"CREATE INDEX IF NOT EXISTS ix_{t}_scn ON {t}(scenario_id);"); ix.append(f"CREATE INDEX IF NOT EXISTS ix_{t}_ridx ON {t}(row_idx);")
    ix += ["CREATE INDEX IF NOT EXISTS ix_claim_status ON claim_header(status);","CREATE INDEX IF NOT EXISTS ix_auth_status ON auth_request(status);",
           "CREATE INDEX IF NOT EXISTS ix_alink_key ON asset_link(linked_entity, linked_key);"]
    return ix

def connect(path, fresh=False):
    import os
    if fresh and os.path.exists(path): os.remove(path)
    con = sqlite3.connect(path)
    # The database is rebuilt from the seed every run, so durability is traded for speed (no journal, no fsync).
    con.execute("PRAGMA journal_mode=OFF"); con.execute("PRAGMA synchronous=OFF")
    return con

def create_all(con):
    for s in ddl(): con.execute(s)
    con.commit()

# Buffered writer shared by all generators: collects dict rows per table and bulk-inserts in batches.
class Sink:
    """Buffered writer. emit() stamps the common privacy columns on every row (P-01)."""
    def __init__(self, con, ctx, batch=20000):
        self.con, self.ctx, self.batch, self.buf, self.n = con, ctx, batch, {}, {}
    # Stamp the privacy columns here, once, so no generator can forget them (rule P-01).
    def emit(self, table, idx, scenario="BASE", **kw):
        kw.update(row_idx=idx, scenario_id=scenario, tenant_id=self.ctx.dataset_id, is_synthetic=1)
        self.buf.setdefault(table, []).append(kw)
        if len(self.buf[table]) >= self.batch: self.flush(table)
    def flush(self, table=None):
        for t in ([table] if table else list(self.buf)):
            rows = self.buf.get(t)
            if not rows: continue
            cols = TABLES[t]["cols"]; names = [c for c, _ in cols]
            data = []
            for r in rows:
                tup = []
                for c, ty in cols:
                    v = r.get(c)
                    # Convert on the way in: JSON columns are serialised compactly, booleans become 0/1.
                    if ty == "J" and v is not None: v = json.dumps(v, separators=(",", ":"))
                    elif ty == "B" and v is not None: v = int(bool(v))
                    tup.append(v)
                data.append(tuple(tup))
            self.con.executemany(f"INSERT INTO {t} ({','.join(names)}) VALUES ({','.join('?'*len(names))})", data)
            self.n[t] = self.n.get(t, 0) + len(rows); self.buf[t] = []
        self.con.commit()

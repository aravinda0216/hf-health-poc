"""Pydantic models = the API contract. Every response carries is_synthetic and scenario_id."""
from datetime import date
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

# Base of every response: is_synthetic is always true, and scenario_id names the seeded scenario behind the data (null for ordinary rows).
class Envelope(BaseModel):
    is_synthetic: bool = True
    scenario_id: Optional[str] = Field(None, description="Seed scenario that produced the underlying data")

# One error shape for the whole API: a stable machine-readable code, a plain message, and optional details.
class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[dict] = None
class ErrorResponse(Envelope):
    error: ErrorBody

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600

class Source(BaseModel):
    asset_id: str; title: str; version: str; section_path: Optional[str] = None; chunk_id: Optional[str] = None

# ---- identity
class PatientRef(BaseModel):
    member_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[date] = None
class IdentityRole(str, Enum):
    PROVIDER = "PROVIDER"; MEMBER = "MEMBER"
class IdentityRequest(BaseModel):
    role: IdentityRole = IdentityRole.PROVIDER
    npi: Optional[str] = Field(None, examples=["9012345671"])
    tin: Optional[str] = Field(None, examples=["99-7968508"])
    patient: Optional[PatientRef] = None
class IdentityResponse(Envelope):
    verified: bool
    role: IdentityRole
    verification_id: Optional[str] = None
    factors_checked: List[str]
    message: str

# ---- claims
class PendInfo(BaseModel):
    reason: str; info_needed_from: str; pended_on: str; expected_resolution_dt: str; guidance: str
class DenialInfo(BaseModel):
    category: str; carc: str; rarc: Optional[str] = None; reason_text: str; liable_party: str
    member_can_be_billed: bool; line_no: Optional[int] = None
    appealable: bool; appeal_deadline_dt: Optional[str] = None; days_left_to_appeal: Optional[int] = None
class PaymentInfo(BaseModel):
    payment_dt: str; method: str; amount: float; trace_no: str; remit_reference: str
class AppealInfo(BaseModel):
    appeal_id: str; level: str; status: str; filed_dt: str; deadline_dt: str; outcome: Optional[str] = None; decision_dt: Optional[str] = None
class ReprocessInfo(BaseModel):
    reprocess_dt: str; trigger: str; paid_before: float; paid_after: float; current_claim_id: str
class ClaimStatus(Envelope):
    claim_id: str; claim_type: str; status: str; status_plain: str
    service_from_dt: str; service_to_dt: str; received_dt: str; adjudicated_dt: Optional[str] = None
    total_billed: float; total_allowed: float; total_paid: float; patient_responsibility: float
    network_status: str; line_count: int; member_ref: str
    pend: Optional[PendInfo] = None; denial: Optional[DenialInfo] = None; payment: Optional[PaymentInfo] = None
    appeal: Optional[AppealInfo] = None; reprocessing: Optional[ReprocessInfo] = None
    next_actions: List[str]; sources: List[Source]

# ---- authorizations
class DocState(BaseModel):
    doc_type: str; state: str; requested_dt: Optional[str] = None; due_dt: Optional[str] = None; received_via: Optional[str] = None
class AuthLine(BaseModel):
    code: str; units_requested: int; units_approved: Optional[int] = None; status: Optional[str] = None
class Reviewer(BaseModel):
    role: Optional[str] = None; name: Optional[str] = None
    note: str = "Reviewer personal information is not disclosed. Request a peer-to-peer discussion instead."
class Determination(BaseModel):
    outcome: str; determination_dt: str; valid_from: Optional[str] = None; valid_to: Optional[str] = None
    denial_category: Optional[str] = None; denial_rationale: Optional[str] = None; appeal_rights_sent_dt: Optional[str] = None
    peer_to_peer_available_until: Optional[str] = None; reviewer: Reviewer; criteria: Optional[Source] = None
class AuthStatus(Envelope):
    auth_id: str; status: str; effective_status: str; status_plain: str; service_type: str; urgency: str
    received_dt: str; decision_due_dt: str; hours_remaining: float; sla_at_risk: bool
    clock_paused: bool; routing: str; escalation_available: bool; member_ref: str
    lines: List[AuthLine]; missing_documents: List[DocState]; documents: List[DocState]
    determination: Optional[Determination] = None; next_steps: List[str]; sources: List[Source]
class DocType(str, Enum):
    CLINICAL_NOTES = "CLINICAL_NOTES"; IMAGING = "IMAGING"; LABS = "LABS"; PT_EVAL = "PT_EVAL"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"; PRESCRIBER_STATEMENT = "PRESCRIBER_STATEMENT"
# Only document METADATA is accepted (name, size, SHA-256). Size is capped at 25 MB and the hash format is validated.
class DocumentUpload(BaseModel):
    npi: str; tin: str; doc_type: DocType
    file_name: str = Field(..., max_length=200)
    size_bytes: int = Field(..., gt=0, le=25_000_000)
    sha256: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$")
    notes: Optional[str] = Field(None, max_length=500, description="Free text is stored as data and never interpreted as instructions.")
class DocumentReceipt(Envelope):
    upload_id: str; auth_id: str; doc_type: DocType; requirement_state_after: str; clock_paused_after: bool
    routing: str; decision_due_dt: str; message: str; untrusted_content_notice: str = "Uploaded documents and notes are stored as data; embedded instructions are ignored."

# ---- eligibility
class EligibilityRequest(BaseModel):
    npi: str; tin: str; date_of_service: date
    members: List[PatientRef] = Field(..., min_length=1)
class CostShare(BaseModel):
    primary_care_copay: float; specialist_copay: float; coinsurance_pct: float; oop_max: float; oop_met_in_network: float
class PcpInfo(BaseModel):
    npi: str; name: str; effective_dt: str
class EligibilityResult(BaseModel):
    found: bool; coverage_status: str; member_ref: Optional[str] = None; plan_name: Optional[str] = None; lob: Optional[str] = None; network_type: Optional[str] = None
    effective_dt: Optional[str] = None; term_dt: Optional[str] = None; premium_status: Optional[str] = None
    pcp: Optional[PcpInfo] = None; cost_share: Optional[CostShare] = None; other_coverage_on_file: Optional[bool] = None
    prior_authorization: Optional[dict] = None; warnings: List[str] = []; reference_number: Optional[str] = None; scenario_id: Optional[str] = None
class EligibilityResponse(Envelope):
    date_of_service: str; results: List[EligibilityResult]; minimum_necessary: str; disclaimer: str

# ---- knowledge
class KnowledgeHit(BaseModel):
    chunk_id: str; asset_id: str; asset_title: str; asset_type: str; version: str; section_path: str; text: str; score: float
class KnowledgeResponse(Envelope):
    query: str; abstain: bool; results: List[KnowledgeHit]; note: Optional[str] = None


# =====================================================================  PAP: appeals
from enum import Enum as _Enum
from typing import Literal
from pydantic import model_validator

class AppealReason(str, _Enum):
    MED_NECESSITY = "MED_NECESSITY"; CODING = "CODING"; AUTH = "AUTH"; ELIGIBILITY = "ELIGIBILITY"; TIMELY_FILING = "TIMELY_FILING"
    COB = "COB"; NON_COVERED = "NON_COVERED"; NETWORK = "NETWORK"; OTHER = "OTHER"
# level: only L1 is handled here; L2 and external review are accepted by the model so the API can answer with a clear 409 and an escalation path.
class AppealRequest(BaseModel):
    npi: str; tin: str
    level: Literal["L1", "L2", "EXTERNAL_REVIEW"] = "L1"
    reason: AppealReason
    documents: List[str] = Field(default_factory=list, max_length=20, description="References or document types already submitted, for example clinical_notes.")
    good_cause_statement: Optional[str] = Field(None, max_length=1000, description="Required only for a late filing; stored as data, never interpreted as instructions.")
class AppealDenial(BaseModel):
    category: str; carc: str; reason_text: str; liable_party: str
class AppealReprocess(BaseModel):
    state: Literal["PENDING", "COMPLETE"]; trigger: str = "APPEAL_OVERTURN"
    expected_reprocess_dt: Optional[str] = None; reprocess_dt: Optional[str] = None
    paid_before: Optional[float] = None; paid_after: Optional[float] = None; current_claim_id: Optional[str] = None
class AppealDetail(Envelope):
    appeal_id: str; claim_id: str; level: str; status: str; filed_dt: str; deadline_dt: str
    days_left_to_file: Optional[int] = None
    late_filing: bool; days_late: int; routing: str
    outcome: Optional[str] = None; decision_dt: Optional[str] = None; expected_decision_dt: Optional[str] = None
    documents_received: List[str]; documents_required: List[str]; documents_missing: List[str]
    denial: Optional[AppealDenial] = None; reprocessing: Optional[AppealReprocess] = None
    no_outcome_promised: bool = True
    next_steps: List[str]; sources: List[Source]

# =====================================================================  PPI: payment inquiry
class PaymentLine(BaseModel):
    payment_id: str; claim_id: str; payment_dt: str; method: str; amount: float; trace_no: str; remit_reference: str
    payee_tin_masked: str; payee_matches_billing_tin: bool
    expected_bank_posting_dt: Optional[str] = None; posting_status: str
class AdjustmentLine(BaseModel):
    line_no: Optional[int] = None; group_code: str; carc: str; description: str; amount: float; responsible: str; plain: str
class RecoupLine(BaseModel):
    role: Literal["RECOUPED_FROM_THIS_CLAIM", "OFFSET_ON_THIS_REMIT"]
    amount: float; recoup_dt: str; trace_no: str; reason: Optional[str] = None
    original_claim_id: str; offset_claim_id: Optional[str] = None
class PayeeCheck(BaseModel):
    matches_billing_tin: bool; billing_tin_masked: str; payee_tin_masked: Optional[str] = None; message: str
class PaymentsResponse(Envelope):
    claim_id: str; claim_status: str; payment_summary: str
    total_billed: float; total_allowed: float; total_paid_gross: float; patient_responsibility: float; provider_write_off: float
    recouped_total: float; net_paid_after_recoupment: float
    payments: List[PaymentLine]; adjustments: List[AdjustmentLine]; recoupments: List[RecoupLine]
    payee_check: PayeeCheck; escalation_available: bool; next_actions: List[str]; sources: List[Source]
class PaymentIssue(str, _Enum):
    WRONG_PAYEE_TIN = "WRONG_PAYEE_TIN"; MISDIRECTED_PAYMENT = "MISDIRECTED_PAYMENT"; DISPUTED_RECOUPMENT = "DISPUTED_RECOUPMENT"
class PaymentCorrectionRequest(BaseModel):
    npi: str; tin: str; issue_type: PaymentIssue
    description: Optional[str] = Field(None, max_length=500, description="Free text is stored as data and never interpreted as instructions.")
class PaymentCorrectionTicket(Envelope):
    ticket_id: str; claim_id: str; issue_type: PaymentIssue; status: str; queue: str; opened_dt: str
    already_open: bool; agent_actions: str; message: str

# =====================================================================  PON: provider onboarding
class PendingItem(BaseModel):
    item: str; label: str; submit_via: str; due_dt: Optional[str] = None
class Application(BaseModel):
    plan_name: str; status: str; stage: str; stage_label: str; next_step: str
    pending_items: List[PendingItem]; started_dt: Optional[str] = None; expected_live_dt: Optional[str] = None
    days_to_expected_live: Optional[int] = None; effective_dt: Optional[str] = None; expected_live_is_estimate: bool = True
class LocationInfo(BaseModel):
    location_id: str; address: str; city: str; state: str; zip: str; phone: str
    accepting_new_patients: bool; telehealth: bool; availability_loaded: bool; added_dt: Optional[str] = None
    last_verified_dt: Optional[str] = None; needs_attention: bool; availability_source: Optional[str] = None
class ClaimsLoadCheck(BaseModel):
    lag_suspected: bool; live_since_dt: Optional[str] = None; affected_claims: int = 0; sample_claim_ids: List[str] = []
    explanation: str; recommended_action: Optional[str] = None; escalation_queue: Optional[str] = None
class OnboardingResponse(Envelope):
    npi: str; provider_name: str; provider_type: str; overall_status: str; headline: str
    applications: List[Application]; locations: List[LocationInfo]; claims_load_check: ClaimsLoadCheck
    escalation_recommended: bool; next_steps: List[str]; sources: List[Source]
# One day's opening hours as 24-hour HH:MM; closing must be later than opening.
class DayHours(BaseModel):
    day: Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    open: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$", examples=["08:00"])
    close: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$", examples=["17:00"])
    @model_validator(mode="after")
    def _order(self):
        if self.close <= self.open: raise ValueError("close must be later than open")
        return self
# Weekly schedule for a location: 1-7 entries, each weekday at most once. Both rules are checked at validation time (HTTP 422).
class AvailabilityRequest(BaseModel):
    tin: str
    accepting_new_patients: bool
    telehealth: Optional[bool] = None
    accessibility: List[Literal["WHEELCHAIR", "ASL", "TTY", "HEARING_LOOP", "LARGE_PRINT"]] = Field(default_factory=list, max_length=5)
    schedule: List[DayHours] = Field(..., min_length=1, max_length=7)
    @model_validator(mode="after")
    def _unique_days(self):
        if len({d.day for d in self.schedule}) != len(self.schedule): raise ValueError("each day may appear once")
        return self
class AvailabilityReceipt(Envelope):
    location_id: str; npi: str; availability_loaded: bool; days_loaded: int; directory_update: str; message: str

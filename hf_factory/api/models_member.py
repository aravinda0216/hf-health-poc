"""Pydantic models for the member-facing API (MBN, MCI, MPS, MPC, MAS)."""
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .models import Envelope, Source

# Verification factors every member call carries. A caregiver adds the relationship id and their own name (checked against the consent record).
class Factors(BaseModel):
    dob: date = Field(..., description="Member date of birth")
    last_name: str = Field(..., min_length=1, max_length=60, description="Member last name")
    relationship_id: Optional[str] = Field(None, max_length=64, description="Caregiver only: the family_relationship id on file; makes the call 'on behalf of' the member")
    caregiver_name: Optional[str] = Field(None, max_length=80, description="Caregiver only: the caregiver's full name as on the consent record")

class Actor(BaseModel):
    type: str; caregiver_relationship: Optional[str] = None; scopes_checked: Optional[str] = None

# ---- MBN
class PlanInfo(BaseModel):
    plan_id: str; name: str; lob: str; plan_year: int; network_type: str
class EnrollmentInfo(BaseModel):
    status: str; effective_dt: str; term_dt: Optional[str] = None; premium_status: Optional[str] = None; coverage_active: bool
class CostShareInfo(BaseModel):
    primary_care_copay: float; specialist_copay: float; coinsurance_pct: float; deductible: float; oop_max: float
class AccumulatorInfo(BaseModel):
    deductible_met: float; deductible_remaining: float; oop_met: float; oop_remaining: float
class BenefitsResponse(Envelope):
    member_ref: str; actor: Actor; plan: PlanInfo; enrollment: EnrollmentInfo; cost_share: CostShareInfo
    accumulators: Dict[str, AccumulatorInfo] = Field(..., description="Keys IN and OON")
    allowances: Dict[str, float]; notes: List[str]; sources: List[Source]

# ---- MCI
class MemberClaimSummary(BaseModel):
    claim_id: str; service_from_dt: str; provider_name: str; claim_type: str; status: str; member_status: str
    billed: Optional[float] = None; plan_paid: Optional[float] = None; you_may_owe: Optional[float] = None
class MemberClaimsResponse(Envelope):
    member_ref: str; actor: Actor; claims: List[MemberClaimSummary]; count: int
    amounts_withheld: bool = Field(..., description="True when a caregiver lacks BILLING scope: amounts are omitted")
    next_actions: List[str]; sources: List[Source]
class MemberDenial(BaseModel):
    reason: str; code: str; who_is_responsible: str; you_should_not_be_billed: bool; can_appeal: bool; appeal_deadline_dt: Optional[str] = None; days_left_to_appeal: Optional[int] = None
class MemberPend(BaseModel):
    reason: str; waiting_on: str; expected_resolution_dt: str
class MemberClaimLine(BaseModel):
    line_no: int; service: str; billed: Optional[float] = None; plan_paid: Optional[float] = None; you_may_owe: Optional[float] = None
class MemberClaimDetail(Envelope):
    member_ref: str; actor: Actor; claim_id: str; service_from_dt: str; provider_name: str; claim_type: str; status: str; member_status: str
    billed: Optional[float] = None; allowed: Optional[float] = None; plan_paid: Optional[float] = None; you_may_owe: Optional[float] = None; paid_dt: Optional[str] = None
    amounts_withheld: bool; lines: List[MemberClaimLine]; denial: Optional[MemberDenial] = None; pend: Optional[MemberPend] = None
    explanation: str; next_actions: List[str]; sources: List[Source]

# ---- MPS
class ProviderLocation(BaseModel):
    location_id: str; address: str; city: str; state: str; zip: str; phone: str; distance_miles: float
    accepting_new_patients: bool; telehealth: bool; accessibility: List[str]; directory_last_verified_dt: str; directory_verification_stale: bool
class ProviderResult(BaseModel):
    npi: str; name: str; provider_type: str; specialty: str; gender: Optional[str] = None; languages: List[str]; tier: str; panel_status: str; location: ProviderLocation
class ProviderSearchResponse(Envelope):
    member_ref: str; actor: Actor; plan_id: str; query: Dict[str, Any]; results: List[ProviderResult]; count: int; in_network_only: bool = True
    next_actions: List[str]; sources: List[Source]

# ---- MPC
class PcpInfo(BaseModel):
    npi: str; name: str; specialty: str; effective_dt: str; panel_status: Optional[str] = None; phone: Optional[str] = None; address: Optional[str] = None
class PendingPcp(BaseModel):
    change_id: str; new_pcp: PcpInfo; effective_dt: str
class MemberPcpResponse(Envelope):
    member_ref: str; actor: Actor; current_pcp: Optional[PcpInfo] = None; pending_change: Optional[PendingPcp] = None; changes_used_this_year: int; changes_allowed_per_year: int; notes: List[str]; sources: List[Source]
class PcpChangeRequest(Factors):
    new_pcp_npi: str = Field(..., min_length=10, max_length=10, pattern=r"^\d{10}$")
    reason: Optional[str] = Field(None, max_length=200, description="Free text, stored as data and never interpreted as instructions")
    dry_run: bool = Field(False, description="Validate and preview without saving the change")
class PcpChangeReceipt(Envelope):
    member_ref: str; actor: Actor; change_id: Optional[str] = None; status: str; dry_run: bool; previous_pcp: Optional[PcpInfo] = None; new_pcp: PcpInfo; effective_dt: str
    id_card_reissued: bool; distance_miles: float; changes_remaining_this_year: int; message: str; sources: List[Source]

# ---- MAS
class Slot(BaseModel):
    slot_id: str; start: str; duration_min: int; visit_type: str; location_id: str; address: str; city: str
class SlotsResponse(Envelope):
    member_ref: str; actor: Actor; provider: Dict[str, Any]; in_network: bool; from_date: str; to_date: str; slots: List[Slot]; count: int
    alternatives: List[Dict[str, Any]] = Field(default_factory=list); next_actions: List[str]; sources: List[Source]
class BookRequest(Factors):
    slot_id: str = Field(..., min_length=8, max_length=64)
    reason: Optional[str] = Field(None, max_length=200, description="Reason for visit, stored as data and never interpreted as instructions")
    dry_run: bool = Field(False, description="Validate and preview without booking")
class AppointmentOut(BaseModel):
    appointment_id: Optional[str] = None; confirmation_no: Optional[str] = None; status: str; provider_npi: str; provider_name: str; start: str; duration_min: int; visit_type: str
    location: str; reason: Optional[str] = None
class BookReceipt(Envelope):
    member_ref: str; actor: Actor; dry_run: bool; appointment: AppointmentOut; instructions: List[str]; cancellation_policy: str; sources: List[Source]
class AppointmentsResponse(Envelope):
    member_ref: str; actor: Actor; appointments: List[AppointmentOut]; count: int; sources: List[Source]
class CancelReceipt(Envelope):
    member_ref: str; actor: Actor; dry_run: bool; appointment: AppointmentOut; slot_released: bool; message: str

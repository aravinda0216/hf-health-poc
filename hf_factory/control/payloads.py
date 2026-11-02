"""Request models for the control plane: the per-stage job payloads the orchestrator sends (shapes from the architecture document)."""
from datetime import date
from typing import Dict, List, Literal, Optional, Union
Num = Union[int, float]   # keep ints as ints: the data_spec_hash is computed over canonical JSON, where 1 and 1.0 differ
from pydantic import BaseModel, ConfigDict, Field, field_validator

# The job name each stage must carry. A mismatch is rejected (422) so a payload cannot be replayed against the wrong stage.
JOB_NAMES = {"s3": "generate_reference_data", "s4": "generate_members", "s5": "generate_claims_auth", "s6": "generate_care_knowledge", "s7": "validate_and_scan"}
DATA_STAGES = list(JOB_NAMES)

class Volumes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    members: int = Field(..., ge=1); providers: int = Field(..., ge=1); claims: int = Field(..., ge=0); auths: int = Field(..., ge=0)
    history_months: int = Field(..., ge=1, le=60)
    base_members: int = Field(..., ge=1); base_providers: int = Field(..., ge=1); base_claims: int = Field(..., ge=0); base_auths: int = Field(..., ge=0)

class DataJob(BaseModel):
    """Payload for s3..s7. All stages carry the full run identity so any stage can be validated on its own."""
    model_config = ConfigDict(extra="allow")
    job: str; stage: Literal["s3", "s4", "s5", "s6", "s7"]
    run_id: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{3,64}$")
    root_seed: int = Field(..., ge=0, le=2**63 - 1); stage_seed: int = Field(..., ge=0, le=2**32 - 1)
    as_of_date: date; catalog_version: str; rule_set_version: str; generator_version: str; id_strategy: str
    dataset_id: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{3,64}$")
    use_cases: List[str] = Field(..., min_length=1)
    lob_mix: Dict[str, Num] = Field(..., min_length=1)
    volumes: Volumes
    edge_case_pct: Num = Field(..., ge=0, le=100)
    blueprint_hash: str; data_spec_hash: str
    entities: List[str] = Field(default_factory=list); rules: List[str] = Field(default_factory=list)

    @field_validator("as_of_date")
    @classmethod
    def _iso(cls, v): return v

class AuthSpec(BaseModel):
    type: str = "OAuth2 client credentials"; secret_ref: str = "env:HF_CLIENT_SECRET"
class RateLimit(BaseModel):
    per_client_rpm: int = Field(600, ge=1, le=100000)
class FaultSpec(BaseModel):
    error_rate_pct: float = Field(1.0, ge=0, le=50); latency_p95_ms: int = Field(450, ge=0, le=60000)

class ApiJob(BaseModel):
    """Payload for s8. `use_cases` selects the operations the backend exposes; `endpoints` (optional) narrows them further."""
    model_config = ConfigDict(extra="allow")
    job: Literal["api_generate_and_deploy"]; stage: Literal["s8"]
    run_id: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{3,64}$")
    style: str = "REST, OpenAPI 3.1"; base_path: Literal["/v1"] = "/v1"
    use_cases: List[str] = Field(..., min_length=1)
    endpoints: Optional[List[str]] = None
    auth: AuthSpec = AuthSpec(); rate_limit: RateLimit = RateLimit(); fault_injection: FaultSpec = FaultSpec(); agent_tools: bool = True

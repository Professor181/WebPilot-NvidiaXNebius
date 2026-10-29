"""Pydantic v2 domain models shared by the planner, the MCP server and the REST API."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TaskCategory(str, Enum):
    READ_ANALYZE = "READ_ANALYZE"
    RESEARCH_COMPARE = "RESEARCH_COMPARE"
    ACT_WITH_APPROVAL = "ACT_WITH_APPROVAL"


class TaskStatus(str, Enum):
    PLANNED = "PLANNED"
    EXECUTING = "EXECUTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    UNVERIFIED_OUTCOME = "UNVERIFIED_OUTCOME"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.UNVERIFIED_OUTCOME.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


class EnvironmentMode(str, Enum):
    LIVE_WEB = "LIVE_WEB"
    CONTROLLED_SANDBOX = "CONTROLLED_SANDBOX"
    HYBRID = "HYBRID"


class ActionType(str, Enum):
    PURCHASE_CHECKOUT = "PURCHASE_CHECKOUT"
    FORM_SUBMISSION = "FORM_SUBMISSION"
    ACCOUNT_MUTATION = "ACCOUNT_MUTATION"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    USER_APPROVED = "USER_APPROVED"
    USER_REJECTED = "USER_REJECTED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class CapabilityTier(str, Enum):
    TIER_1_ADAPTER = "TIER_1_ADAPTER"
    TIER_2_SEMANTIC_DOM = "TIER_2_SEMANTIC_DOM"
    TIER_3_HITL_ESCALATION = "TIER_3_HITL_ESCALATION"
    INTERNAL_COMPUTE = "INTERNAL_COMPUTE"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FAILED_RECOVERED = "FAILED_RECOVERED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    CANCELLED = "CANCELLED"


class VerificationVerdict(str, Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    SIMULATED_TRANSACTION_VERIFIED = "SIMULATED_TRANSACTION_VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNVERIFIED = "UNVERIFIED"
    LIVE_CHECKOUT_HANDOFF_VERIFIED = "LIVE_CHECKOUT_HANDOFF_VERIFIED"


class Scenario(str, Enum):
    AUTO = "auto"
    ATTENDANCE_AUDIT = "attendance_audit"
    PROFILE_COMPARISON = "profile_comparison"
    LAPTOP_SHOPPING = "laptop_shopping"
    GRANT_FORM = "grant_form"
    GITHUB_REPO_ANALYSIS = "github_repo_analysis"
    URL_INSPECTION = "url_inspection"
    SYNTHESIZED_TOOL = "synthesized_tool"
    LIVE_AUTOPILOT = "live_autopilot"


class PlanStep(BaseModel):
    step_id: str
    key: str
    index: int
    title: str
    capability_tier: CapabilityTier
    status: StepStatus = StepStatus.PENDING
    target_url: Optional[str] = None
    detail: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class FirewallAlert(BaseModel):
    alert_id: str
    alert_type: Literal["HIDDEN_DOM_INJECTION_BLOCKED", "PROMPT_INJECTION_PATTERN_NEUTRALIZED"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    source_url: str
    element_selector: Optional[str] = None
    hiding_technique: Optional[str] = None
    matched_patterns: List[str] = Field(default_factory=list)
    snippet: str
    action_taken: str


class VerificationCheck(BaseModel):
    check: str
    passed: bool
    expected: Any = None
    observed: Any = None


class VerificationReport(BaseModel):
    verdict: VerificationVerdict
    method: str
    checks: List[VerificationCheck] = Field(default_factory=list)
    notes: Optional[str] = None
    verified_at: str = Field(default_factory=utc_now_iso)

    @property
    def passed(self) -> bool:
        return self.verdict in (
            VerificationVerdict.VERIFIED_SUCCESS,
            VerificationVerdict.SIMULATED_TRANSACTION_VERIFIED,
            VerificationVerdict.LIVE_CHECKOUT_HANDOFF_VERIFIED,
        )


class EvidenceRecord(BaseModel):
    evidence_id: str
    task_id: str
    step_id: str
    source_url: str
    page_title: Optional[str] = None
    capability_method: str
    extracted: Optional[Any] = None
    dom_citation_snippet: Optional[str] = None
    screenshot_url: Optional[str] = None
    firewall_alerts: List[Dict[str, Any]] = Field(default_factory=list)
    verification_passed: bool = False
    captured_at: str = Field(default_factory=utc_now_iso)


class ApprovalRecord(BaseModel):
    approval_id: str
    task_id: str
    step_id: str
    action_type: ActionType
    action_summary: str
    parameters: Dict[str, Any]
    parameter_hash: str
    status: ApprovalStatus
    approval_token: Optional[str] = None
    preview_screenshot_url: Optional[str] = None
    created_at: str
    decided_at: Optional[str] = None
    consumed_at: Optional[str] = None


class TaskRecord(BaseModel):
    task_id: str
    idempotency_key: Optional[str] = None
    goal: str
    task_category: TaskCategory
    status: TaskStatus
    environment_mode: EnvironmentMode
    interpreted_goal: Dict[str, Any]
    plan_steps: List[PlanStep]
    extracted_data: Optional[Dict[str, Any]] = None
    pending_approval: Optional[Dict[str, Any]] = None
    verification_report: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    created_at: str
    updated_at: str


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    decided_by: Optional[str] = Field(default=None, max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)


class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    task_id: str
    status: ApprovalStatus
    approval_token: Optional[str] = None
    parameter_hash: str
    decided_at: Optional[str] = None
    message: str


class InterruptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=3, max_length=500)
    constraints: Optional[Dict[str, Any]] = None


class ResetDemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_mcp_logs: bool = True
    include_sessions: bool = False


class JSONRPCRequest(BaseModel):
    """Envelope of an inbound JSON-RPC 2.0 message (request or notification)."""

    model_config = ConfigDict(extra="allow")

    jsonrpc: Literal["2.0"]
    method: str
    id: Optional[Any] = None
    params: Optional[Dict[str, Any]] = None

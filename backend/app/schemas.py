"""Pydantic schemas for the BIAI FastAPI endpoints.

A database model (models.py) describes how data is STORED.
A Pydantic schema describes which data our API ACCEPTS or RETURNS.

These schemas do not read SQLite, call Groq, or modify any leads.
"""

from datetime import date, datetime
from typing import Any, Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from .config import PipelineStage


# A strict date accepts either a Python date or an ISO string: 2026-11-03.
# In particular, the CSV's 11/03/2026 format is not accepted by the API.
def parse_api_date(value: Any) -> date | None:
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("Use YYYY-MM-DD for dates, for example 2026-11-03.")


ApiDate = Annotated[date, BeforeValidator(parse_api_date)]
Money = Annotated[int, Field(strict=True, ge=0)]
OwnerId = Annotated[int, Field(strict=True, ge=1)]


class ApiSchema(BaseModel):
    """Shared settings for incoming requests: reject unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FromDatabase(ApiSchema):
    """Allows responses to be built directly from SQLAlchemy objects."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# ---------------------------------------------------------------
# Leads: create, edit, read
# ---------------------------------------------------------------
class LeadCreate(ApiSchema):
    lead_id: str = Field(min_length=1, max_length=20)
    company: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_role: str | None = Field(default=None, max_length=120)
    # The original CSV intentionally contains questionable contact details.
    # Keep email as text. The data-quality scanner flags suspect addresses.
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=100)
    stage: PipelineStage = PipelineStage.NEW
    deal_value_bdt: Money | None = None
    last_contact: ApiDate | None = None
    next_follow_up: ApiDate | None = None
    notes: str | None = None
    owner_id: OwnerId | None = None


class LeadUpdate(ApiSchema):
    """All fields are optional, so PATCH can change only selected values.

    IMPORTANT: Use model_dump(exclude_unset=True) in FastAPI. This keeps
    fields the user did not supply unchanged, while an explicit null clears
    an optional field (for example next_follow_up or owner_id).
    """

    company: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_role: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=100)
    stage: PipelineStage | None = None
    deal_value_bdt: Money | None = None
    last_contact: ApiDate | None = None
    next_follow_up: ApiDate | None = None
    notes: str | None = None
    owner_id: OwnerId | None = None

    @field_validator("company", "stage")
    @classmethod
    def cannot_clear_required_fields(cls, value: Any) -> Any:
        # Unlike optional contact details, company and stage must not be null.
        # A missing field is fine for PATCH; an explicit null is not.
        if value is None:
            raise ValueError("This field cannot be null.")
        return value


class LeadRead(FromDatabase):
    lead_id: str
    company: str
    industry: str | None
    contact_name: str | None
    contact_role: str | None
    contact_email: str | None
    contact_phone: str | None
    source: str | None
    stage: PipelineStage
    deal_value_bdt: int | None
    last_contact: date | None
    next_follow_up: date | None
    notes: str | None
    owner_id: int | None
    merged_into_id: str | None
    stage_changed_at: datetime | None
    updated_at: datetime


class StageUpdate(ApiSchema):
    stage: PipelineStage
    reason: str | None = Field(default=None, max_length=500)


class AssignOwner(ApiSchema):
    owner_id: OwnerId | None  # A null value removes the assignment.


class OwnerCreate(ApiSchema):
    name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=255)


class OwnerRead(FromDatabase):
    id: int
    name: str
    email: str | None


class MergeLeads(ApiSchema):
    surviving_id: str = Field(min_length=1, max_length=20)
    duplicate_id: str = Field(min_length=1, max_length=20)
    confirmed: Literal[True]  # The merge must be confirmed by the user.
    reason: str = Field(min_length=1, max_length=1000)


# ---------------------------------------------------------------
# Data-quality warnings and activity history
# ---------------------------------------------------------------
class IssueRead(FromDatabase):
    id: int
    lead_id: str
    rule_code: str
    message: str
    severity: Literal["low", "medium", "high"]
    status: Literal["open", "resolved", "dismissed"]
    evidence: dict[str, Any] | None
    created_at: datetime


class IssueStatusUpdate(ApiSchema):
    status: Literal["resolved", "dismissed"]
    reason: str = Field(min_length=1, max_length=500)


class IssueSummaryRead(ApiSchema):
    """Counts for the saved warnings, not the results of an automatic scan."""
    total_issue_records: int
    open_issues: int
    resolved_issues: int
    dismissed_issues: int
    flagged_leads: int
    data_quality_flagged_leads: int
    open_by_rule: dict[str, int]
    open_by_severity: dict[str, int]


class DuplicatePairRead(ApiSchema):
    """A suggested duplicate pair that a user must review before merging."""
    lead_id_1: str
    company_1: str
    lead_id_2: str
    company_2: str
    match_type: str
    matched_fields: list[str]


class IssueScanRead(ApiSchema):
    """Summary returned after the API runs both validation scanners."""
    as_of: date
    scanned_leads: int
    validation_issues_found: int
    overdue_leads: int
    duplicate_pairs: int
    duplicate_leads_flagged: int
    new_issues_saved: int
    old_issues_resolved: int
    open_issues: int
    data_quality_flagged_leads: int


class ActivityRead(FromDatabase):
    id: int
    lead_id: str
    action_type: str
    description: str | None
    old_value: str | None
    new_value: str | None
    created_at: datetime


# ---------------------------------------------------------------
# Analytics and priority reports
# ---------------------------------------------------------------
class StageSummaryRead(ApiSchema):
    stage: PipelineStage
    lead_count: int
    total_value_bdt: int


class StageTotalRead(StageSummaryRead):
    currency: str = "BDT"


class DashboardRead(ApiSchema):
    as_of: date
    currency: str
    all_lead_records: int
    current_leads: int
    active_leads: int
    paid_leads: int
    lost_leads: int
    active_pipeline_value_bdt: int
    total_value_bdt: int
    overdue_leads: int
    overdue_value_bdt: int
    unassigned_leads: int
    unassigned_active_leads: int
    open_issue_count: int
    data_quality_flagged_leads: int
    stage_summary: list[StageSummaryRead]


class PriorityLeadRead(ApiSchema):
    rank: int
    lead_id: str
    company: str
    stage: PipelineStage
    deal_value_bdt: int
    owner_id: int | None
    last_contact: date | None
    next_follow_up: date | None
    followup_status: str
    days_overdue: int | None
    requires_date_review: bool
    priority_score: int
    reasons: list[str]
    recommended_action: str
    notes: str | None


class OverdueSummaryRead(ApiSchema):
    as_of: date
    overdue_leads: int
    overdue_value_bdt: int


class WeeklyPrioritySummaryRead(ApiSchema):
    as_of: date
    currency: str
    overdue_active_leads: int
    overdue_value_bdt: int
    top_leads: list[PriorityLeadRead]
    note: str


# ---------------------------------------------------------------
# Reports and independently reviewed evaluation labels
# ---------------------------------------------------------------
class ReportSummaryRead(ApiSchema):
    as_of: date
    dashboard: DashboardRead
    issues: IssueSummaryRead
    pending_duplicate_pairs: int
    merged_records: int
    note: str


class EvaluationMetricsRead(ApiSchema):
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    correctly_classified_leads: int
    accuracy: float | None
    precision: float | None
    recall: float | None


class EvaluationReportRead(ApiSchema):
    as_of: date
    source_file: str
    original_leads: int
    source_data_flagged_leads: int
    flagged_lead_ids: list[str]
    overdue_leads: int
    duplicate_pairs: list[DuplicatePairRead]
    invalid_date_order_leads: list[str]
    counts_by_rule: dict[str, int]
    verification_status: Literal["awaiting_review", "partial", "complete"]
    manually_reviewed_leads: int
    metrics: EvaluationMetricsRead | None
    note: str


# ---------------------------------------------------------------
# AI chat and follow-up email requests (for later LangGraph steps)
# ---------------------------------------------------------------
class ChatRequest(ApiSchema):
    message: str = Field(min_length=1, max_length=2000)
    # Must match the session IDs accepted by SalesChatbot.ask().
    session_id: str | None = Field(
        default=None, min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    evaluation_mode: bool = False


class ChatResponse(ApiSchema):
    answer: str
    session_id: str
    tools_used: list[str] = Field(default_factory=list)
    used_database_tools: bool = False
    as_of: date


class EmailDraftRequest(ApiSchema):
    lead_id: str = Field(
        min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$"
    )


class EmailDraftPreview(ApiSchema):
    """AI preview only. It is NOT a saved or approved email."""
    lead_id: str
    company: str
    recipient_email: str | None
    subject: str
    body: str
    warnings: list[str] = Field(default_factory=list)
    notes_available: bool
    requires_review: Literal[True] = True
    sent: Literal[False] = False


class EmailDraftRead(FromDatabase):
    id: int
    lead_id: str
    subject: str
    body: str
    is_approved: bool
    created_at: datetime

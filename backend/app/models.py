"""Database tables for BIAI's sales pipeline.

One class represents one SQLite table. SQLAlchemy converts these Python
classes into tables when database.create_tables() is called.

Dates use YYYY-MM-DD. Timestamps stored by SQLite use UTC.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .config import PipelineStage
from .database import Base


# Store readable stage values such as "Invoice sent" in SQLite,
# rather than Python names like "INVOICE_SENT".
stage_type = SqlEnum(
    PipelineStage,
    values_callable=lambda stages: [stage.value for stage in stages],
    name="pipeline_stage",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)


class Owner(Base):
    """A team member responsible for a company lead."""

    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)


class Lead(Base):
    """The current information for one corporate sales lead."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "deal_value_bdt IS NULL OR deal_value_bdt >= 0",
            name="nonnegative_deal_value",
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != lead_id",
            name="cannot_merge_into_self",
        ),
    )

    # Keep original CSV IDs such as L01 and L25.
    lead_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    company: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    industry: Mapped[str | None] = mapped_column(String(120))

    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_role: Mapped[str | None] = mapped_column(String(120))
    contact_email: Mapped[str | None] = mapped_column(String(255), index=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str | None] = mapped_column(String(100))

    stage: Mapped[PipelineStage] = mapped_column(
        stage_type, default=PipelineStage.NEW, nullable=False, index=True
    )
    # None means unknown; zero is also allowed by BIAI's rules.
    deal_value_bdt: Mapped[int | None] = mapped_column(Integer)
    last_contact: Mapped[date | None] = mapped_column(Date)
    next_follow_up: Mapped[date | None] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    # These fields were not in the source CSV, so they start as NULL.
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("owners.id", ondelete="SET NULL"), index=True
    )
    merged_into_id: Mapped[str | None] = mapped_column(
        ForeignKey("leads.lead_id"), index=True
    )
    stage_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class LeadIssue(Base):
    """A warning from the data-quality checker."""

    __tablename__ = "lead_issues"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high')", name="valid_issue_severity"
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')", name="valid_issue_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)
    # Example: {"missing_fields": ["contact_phone"]}
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class LeadActivity(Base):
    """History of stage changes, contact attempts, and other lead actions."""

    __tablename__ = "lead_activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class ImportRow(Base):
    """An untouched copy of one original CSV row for auditing."""

    __tablename__ = "import_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file: Mapped[str] = mapped_column(String(200), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_lead_id: Mapped[str | None] = mapped_column(String(20))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class EmailDraft(Base):
    """An AI-generated email draft; saving a draft does NOT send an email."""

    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class MergeHistory(Base):
    """Record which duplicate was linked to which surviving lead."""

    __tablename__ = "merge_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    duplicate_lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False
    )
    surviving_lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.lead_id"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

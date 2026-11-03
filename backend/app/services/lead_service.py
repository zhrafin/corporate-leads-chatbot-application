"""Create, search, edit, assign, and merge BIAI sales leads.

This file contains the business logic used later by FastAPI and LangGraph.
It does not open its own database session or commit changes. The caller does
that, for example:

    with SessionLocal.begin() as db:
        update_stage(db, "L04", "Meeting", reason="Meeting completed")

This makes it possible to save several related changes as one transaction.
"""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PipelineStage, parse_stage
from ..models import Lead, LeadActivity, MergeHistory, Owner
from .duplicates import compare_leads, run_duplicate_scan


# Only these fields may be created or edited by application users.
# Internal fields such as merged_into_id and updated_at are not editable here.
EDITABLE_FIELDS = {
    "company", "industry", "contact_name", "contact_role",
    "contact_email", "contact_phone", "source", "stage",
    "deal_value_bdt", "last_contact", "next_follow_up",
    "notes", "owner_id",
}

TEXT_FIELDS = {
    "company", "industry", "contact_name", "contact_role",
    "contact_email", "contact_phone", "source", "notes",
}
DATE_FIELDS = {"last_contact", "next_follow_up"}


class LeadNotFoundError(LookupError):
    """Raised when a requested lead does not exist or is already merged."""


class LeadConflictError(ValueError):
    """Raised when an action would create an unsafe or conflicting result."""


def _utc_now() -> datetime:
    """The existing database model stores UTC timestamps without timezone info."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_fields(db: Session, changes: dict) -> dict:
    """Validate values before they are written to a Lead."""
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Fields cannot be edited: {', '.join(sorted(unknown))}")

    cleaned = {}
    for field, value in changes.items():
        if field in TEXT_FIELDS:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field} must be text or None.")
            value = value.strip() if value is not None else None
            if value == "":
                value = None
            if field == "company" and not value:
                raise ValueError("Company name is required.")

        elif field == "stage":
            value = parse_stage(value)  # Accepts only BIAI's seven stages.

        elif field in DATE_FIELDS:
            if isinstance(value, str):
                try:
                    value = date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{field} must use YYYY-MM-DD.") from exc
            elif value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
                raise ValueError(f"{field} must be a date, YYYY-MM-DD, or None.")

        elif field == "deal_value_bdt":
            if value is not None:
                # Avoid accepting floats, booleans, or untrusted numeric strings.
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("deal_value_bdt must be a nonnegative integer or None.")

        elif field == "owner_id":
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("owner_id must be an integer or None.")
                if db.get(Owner, value) is None:
                    raise ValueError(f"Owner {value} does not exist.")

        cleaned[field] = value
    return cleaned


def _add_activity(db: Session, lead_id: str, action: str,
                  old: str | None = None, new: str | None = None,
                  description: str | None = None) -> None:
    """Keep a history of user actions for later review."""
    db.add(LeadActivity(
        lead_id=lead_id,
        action_type=action,
        old_value=old,
        new_value=new,
        description=description,
    ))


def get_lead(db: Session, lead_id: str, *, include_merged: bool = False) -> Lead:
    """Get one lead or raise a useful error."""
    lead = db.get(Lead, lead_id)
    if lead is None or (lead.merged_into_id is not None and not include_merged):
        raise LeadNotFoundError(f"Lead {lead_id!r} was not found among current leads.")
    return lead


def list_leads(
    db: Session,
    *,
    stage: str | PipelineStage | None = None,
    company: str | None = None,
    industry: str | None = None,
    owner_id: int | None = None,
    unassigned_only: bool = False,
    include_merged: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Lead]:
    """Search leads using SQLite, without loading the entire table into Python.

    Merged records are hidden unless include_merged=True. Use limit/offset
    for pagination when the database grows.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a nonnegative integer.")
    if owner_id is not None and unassigned_only:
        raise ValueError("Choose owner_id or unassigned_only, not both.")

    query = select(Lead)
    if not include_merged:
        query = query.where(Lead.merged_into_id.is_(None))
    if stage is not None:
        query = query.where(Lead.stage == parse_stage(stage))
    if company:
        # Case-insensitive search. Escape SQL LIKE special characters so a
        # search for '%' is treated as text rather than 'all companies'.
        term = company.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Lead.company.ilike(f"%{term}%", escape="\\"))
    if industry:
        term = industry.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Lead.industry.ilike(f"%{term}%", escape="\\"))
    if unassigned_only:
        query = query.where(Lead.owner_id.is_(None))
    elif owner_id is not None:
        query = query.where(Lead.owner_id == owner_id)

    return list(db.scalars(query.order_by(Lead.lead_id).limit(limit).offset(offset)).all())


def create_owner(db: Session, name: str, email: str | None = None) -> Owner:
    """Create an employee who can be assigned leads."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Owner name is required.")
    if email is not None and not isinstance(email, str):
        raise ValueError("Owner email must be text or None.")
    email = email.strip().lower() if email else None
    if email:
        existing = db.scalar(select(Owner).where(Owner.email == email))
        if existing:
            raise LeadConflictError("This owner email is already in use.")
    owner = Owner(name=name.strip(), email=email)
    db.add(owner)
    db.flush()  # Gives owner.id a value before the transaction is committed.
    return owner


def create_lead(db: Session, lead_id: str, company: str, **fields) -> Lead:
    """Create a lead. The lead ID is immutable after creation."""
    if not isinstance(lead_id, str) or not lead_id.strip():
        raise ValueError("Lead ID is required.")
    lead_id = lead_id.strip()
    if db.get(Lead, lead_id) is not None:
        raise LeadConflictError(f"Lead {lead_id} already exists.")

    values = _clean_fields(db, {"company": company, **fields})
    values.setdefault("stage", PipelineStage.NEW)
    lead = Lead(lead_id=lead_id, **values)
    db.add(lead)
    db.flush()  # Make the new lead available to other calls in this transaction.
    _add_activity(db, lead_id, "lead_created", new=lead_id)
    return lead


def assign_owner(db: Session, lead_id: str, owner_id: int | None) -> Lead:
    """Assign an employee, or use None to make a lead unassigned."""
    lead = get_lead(db, lead_id)
    owner_id = _clean_fields(db, {"owner_id": owner_id})["owner_id"]
    if lead.owner_id != owner_id:
        _add_activity(db, lead_id, "owner_changed",
                      old=str(lead.owner_id) if lead.owner_id is not None else None,
                      new=str(owner_id) if owner_id is not None else None)
        lead.owner_id = owner_id
    return lead


def update_stage(db: Session, lead_id: str, stage: str | PipelineStage,
                 *, reason: str | None = None) -> Lead:
    """Change a stage and record the old and new values.

    Backward changes are allowed because users may need to correct mistakes.
    """
    lead = get_lead(db, lead_id)
    new_stage = parse_stage(stage)
    if lead.stage != new_stage:
        _add_activity(db, lead_id, "stage_changed",
                      old=lead.stage.value, new=new_stage.value,
                      description=reason)
        lead.stage = new_stage
        lead.stage_changed_at = _utc_now()
    return lead


def update_lead(db: Session, lead_id: str, **changes) -> Lead:
    """Update allowed fields and keep an audit record for each changed field."""
    lead = get_lead(db, lead_id)
    cleaned = _clean_fields(db, changes)  # Validate all fields first.

    if "stage" in cleaned:
        update_stage(db, lead_id, cleaned.pop("stage"))
    if "owner_id" in cleaned:
        assign_owner(db, lead_id, cleaned.pop("owner_id"))

    for field, new_value in cleaned.items():
        old_value = getattr(lead, field)
        if old_value == new_value:
            continue
        _add_activity(db, lead_id, "field_updated",
                      old=str(old_value) if old_value is not None else None,
                      new=str(new_value) if new_value is not None else None,
                      description=f"Updated {field}")
        setattr(lead, field, new_value)
    return lead


def merge_leads(db: Session, surviving_id: str, duplicate_id: str,
                *, confirmed: bool = False, reason: str = "") -> Lead:
    """Link a reviewed duplicate to the surviving lead, without deleting it.

    Only a human-confirmed matching pair can be merged. The two records may
    contain different notes; we do not automatically overwrite either one.
    """
    if not confirmed:
        raise ValueError("A user must confirm the duplicate merge.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Explain why these leads should be merged.")
    if surviving_id == duplicate_id:
        raise ValueError("A lead cannot be merged into itself.")

    surviving = get_lead(db, surviving_id)
    duplicate = get_lead(db, duplicate_id)
    if compare_leads(surviving, duplicate) is None:
        raise LeadConflictError("These leads are not a suggested duplicate pair.")
    # Avoid a chain where another lead already points to the record we merge.
    child = db.scalar(select(Lead.lead_id).where(Lead.merged_into_id == duplicate_id))
    if child is not None:
        raise LeadConflictError("This lead already has merged records. Review it first.")

    duplicate.merged_into_id = surviving_id
    db.add(MergeHistory(
        duplicate_lead_id=duplicate_id,
        surviving_lead_id=surviving_id,
        reason=reason.strip(),
    ))
    _add_activity(db, surviving_id, "duplicate_merged", old=duplicate_id,
                  new=surviving_id, description=reason.strip())
    _add_activity(db, duplicate_id, "merged_into_lead", new=surviving_id,
                  description=reason.strip())

    db.flush()  # Our SessionLocal uses autoflush=False.
    run_duplicate_scan(db)  # Resolve matching duplicate warnings immediately.
    return surviving

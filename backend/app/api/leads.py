"""FastAPI endpoints for managing BIAI company leads.

Business rules live in services/lead_service.py. This file handles HTTP:
- Read the request and validate it with Pydantic schemas.
- Call the correct service function.
- Save changes or return a useful HTTP error.

All database writes are confirmed user actions. No AI is used here.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import PipelineStage
from ..database import get_db
from ..models import Lead, LeadActivity, Owner
from ..schemas import (
    ActivityRead,
    AssignOwner,
    LeadCreate,
    LeadRead,
    LeadUpdate,
    MergeLeads,
    OwnerCreate,
    OwnerRead,
    StageUpdate,
)
from ..services.lead_service import (
    LeadConflictError,
    LeadNotFoundError,
    assign_owner,
    create_lead,
    create_owner,
    get_lead,
    list_leads,
    merge_leads,
    update_lead,
    update_stage,
)

router = APIRouter(prefix="/leads", tags=["Leads"])
owners_router = APIRouter(prefix="/owners", tags=["Owners"])


def save_change(db: Session, action: Callable[[], Lead | Owner]) -> Lead | Owner:
    """Run a write, commit once, and translate common errors into HTTP responses."""
    try:
        record = action()
        db.commit()
        db.refresh(record)  # Load database-generated values such as updated_at.
        return record
    except LeadNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LeadConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Record conflicts with existing database data."
        ) from exc


# Put fixed paths above /{lead_id} so future route additions remain clear.
@router.post("/merge", response_model=LeadRead)
def merge_duplicate_leads(body: MergeLeads, db: Session = Depends(get_db)):
    """Merge two matching leads ONLY after a user explicitly confirms it."""
    return save_change(
        db,
        lambda: merge_leads(
            db,
            body.surviving_id,
            body.duplicate_id,
            confirmed=body.confirmed,
            reason=body.reason,
        ),
    )


@router.get("", response_model=list[LeadRead])
def get_leads(
    stage: PipelineStage | None = None,
    company: str | None = None,
    industry: str | None = None,
    owner_id: int | None = Query(default=None, ge=1),
    unassigned_only: bool = False,
    include_merged: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List current leads, optionally filtered and paginated."""
    try:
        return list_leads(
            db,
            stage=stage,
            company=company,
            industry=industry,
            owner_id=owner_id,
            unassigned_only=unassigned_only,
            include_merged=include_merged,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=LeadRead, status_code=201)
def add_lead(body: LeadCreate, db: Session = Depends(get_db)):
    """Create a new lead. The lead ID must be unique."""
    values = body.model_dump()
    lead_id = values.pop("lead_id")
    company = values.pop("company")
    return save_change(db, lambda: create_lead(db, lead_id, company, **values))


@router.get("/{lead_id}", response_model=LeadRead)
def get_one_lead(
    lead_id: str,
    include_merged: bool = False,
    db: Session = Depends(get_db),
):
    """Read a lead by its ID. Merged records are hidden by default."""
    try:
        return get_lead(db, lead_id, include_merged=include_merged)
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{lead_id}", response_model=LeadRead)
def edit_lead(lead_id: str, body: LeadUpdate, db: Session = Depends(get_db)):
    """Change only the fields included in the PATCH request."""
    changes = body.model_dump(exclude_unset=True)
    return save_change(db, lambda: update_lead(db, lead_id, **changes))


@router.patch("/{lead_id}/stage", response_model=LeadRead)
def change_stage(lead_id: str, body: StageUpdate, db: Session = Depends(get_db)):
    """Update a sales stage and save its reason in activity history."""
    return save_change(
        db,
        lambda: update_stage(db, lead_id, body.stage, reason=body.reason),
    )


@router.patch("/{lead_id}/owner", response_model=LeadRead)
def change_owner(lead_id: str, body: AssignOwner, db: Session = Depends(get_db)):
    """Assign an owner, or remove the assignment with owner_id=null."""
    return save_change(db, lambda: assign_owner(db, lead_id, body.owner_id))


@router.get("/{lead_id}/activities", response_model=list[ActivityRead])
def get_lead_activities(lead_id: str, db: Session = Depends(get_db)):
    """Show a lead's recorded edits and stage changes, oldest first."""
    try:
        get_lead(db, lead_id, include_merged=True)
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return list(
        db.scalars(
            select(LeadActivity)
            .where(LeadActivity.lead_id == lead_id)
            .order_by(LeadActivity.created_at, LeadActivity.id)
        ).all()
    )


@owners_router.get("", response_model=list[OwnerRead])
def get_owners(db: Session = Depends(get_db)):
    """List employees available for assigning leads."""
    return list(db.scalars(select(Owner).order_by(Owner.name, Owner.id)).all())


@owners_router.post("", response_model=OwnerRead, status_code=201)
def add_owner(body: OwnerCreate, db: Session = Depends(get_db)):
    """Create an employee who can be assigned to leads."""
    return save_change(db, lambda: create_owner(db, body.name, body.email))

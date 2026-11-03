"""Read-only FastAPI endpoints for the BIAI sales dashboard.

The calculations already live in services/analytics.py and services/priority.py.
This file only connects those Python functions to HTTP endpoints.

Run from the backend/ folder:
    python -m uvicorn app.main:app --reload

In /docs, select evaluation_mode=true to use BIAI's 2026-11-01 test date.
By default, date-based reports use today's date in Asia/Dhaka.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import PipelineStage, reference_date
from ..database import get_db
from ..schemas import (
    DashboardRead,
    LeadRead,
    OverdueSummaryRead,
    PriorityLeadRead,
    StageSummaryRead,
    StageTotalRead,
    WeeklyPrioritySummaryRead,
)
from ..services.analytics import (
    get_dashboard_stats,
    get_overdue_summary,
    get_stage_summary,
    get_stage_total,
)
from ..services.priority import get_priority_leads, get_weekly_priority_summary
from ..services.validation import get_overdue_leads


# main.py adds the /api prefix to all of these routes.
router = APIRouter(tags=["Pipeline"])


@router.get("/dashboard", response_model=DashboardRead)
def dashboard(
    evaluation_mode: bool = False,
    db: Session = Depends(get_db),
):
    """Dashboard counts, deal totals, stage summaries, and saved warning counts."""
    day = reference_date(evaluation_mode=evaluation_mode)
    return get_dashboard_stats(db, as_of=day)


@router.get("/pipeline/stages", response_model=list[StageSummaryRead])
def stage_summary(db: Session = Depends(get_db)):
    """Counts and deal values for all seven stages, in the official order."""
    return get_stage_summary(db)


@router.get("/pipeline/stages/{stage}", response_model=StageTotalRead)
def stage_total(stage: PipelineStage, db: Session = Depends(get_db)):
    """Value and count for one stage, for example /pipeline/stages/Proposal."""
    return get_stage_total(db, stage)


@router.get("/pipeline/overdue", response_model=list[LeadRead])
def overdue_leads(
    evaluation_mode: bool = False,
    db: Session = Depends(get_db),
):
    """List active leads whose follow-up date is before the selected date.

    Uses BIAI's literal overdue rule. A lead with invalid date order (L25)
    is still shown as overdue, so users can see the problem and correct it.
    The priority service separately marks such dates as needing review.
    """
    day = reference_date(evaluation_mode=evaluation_mode)
    return get_overdue_leads(db, as_of=day)


@router.get("/pipeline/overdue/summary", response_model=OverdueSummaryRead)
def overdue_summary(
    evaluation_mode: bool = False,
    db: Session = Depends(get_db),
):
    """Get the overdue lead count and total overdue deal value in BDT."""
    day = reference_date(evaluation_mode=evaluation_mode)
    return get_overdue_summary(db, as_of=day)


@router.get("/pipeline/priorities", response_model=list[PriorityLeadRead])
def priority_leads(
    evaluation_mode: bool = False,
    limit: int = Query(default=5, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Rank current active leads using the project's transparent Python rules."""
    day = reference_date(evaluation_mode=evaluation_mode)
    return get_priority_leads(db, as_of=day, limit=limit)


@router.get("/pipeline/weekly-summary", response_model=WeeklyPrioritySummaryRead)
def weekly_summary(
    evaluation_mode: bool = False,
    limit: int = Query(default=5, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return facts for a future AI-written report. This is NOT AI text yet."""
    day = reference_date(evaluation_mode=evaluation_mode)
    return get_weekly_priority_summary(db, as_of=day, limit=limit)

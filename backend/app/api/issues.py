"""FastAPI endpoints for reviewing BIAI's data-quality warnings.

The business rules live in services/validation.py and services/duplicates.py.
This file only connects those functions to HTTP requests.

Run from backend/:
    python -m uvicorn app.main:app --reload

Visit /docs and look for the "Data Quality" section.
"""

from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import reference_date
from ..database import get_db
from ..models import LeadActivity, LeadIssue
from ..schemas import (
    DuplicatePairRead,
    IssueRead,
    IssueScanRead,
    IssueStatusUpdate,
    IssueSummaryRead,
)
from ..services.duplicates import find_duplicate_pairs, run_duplicate_scan
from ..services.validation import run_validation


router = APIRouter(prefix="/issues", tags=["Data Quality"])

# An overdue follow-up needs action, but does not mean the CSV data is wrong.
# The source CSV also has no owner column, so missing owners are tracked
# separately from problems with the source data.
ACTION_RULES = {"MISSING_OWNER", "OVERDUE_FOLLOWUP"}


def summarize_issues(db: Session) -> dict:
    """Return counts from saved warnings (does NOT run a new scan).

    Select only the four columns needed for counting, not whole records.
    A record can have several issues, so use sets to count unique leads.
    """
    rows = db.execute(
        select(
            LeadIssue.lead_id,
            LeadIssue.rule_code,
            LeadIssue.severity,
            LeadIssue.status,
        )
    ).all()

    status_counts = Counter(row.status for row in rows)
    open_rows = [row for row in rows if row.status == "open"]

    return {
        "total_issue_records": len(rows),
        "open_issues": status_counts["open"],
        "resolved_issues": status_counts["resolved"],
        "dismissed_issues": status_counts["dismissed"],
        "flagged_leads": len({row.lead_id for row in open_rows}),
        "data_quality_flagged_leads": len({
            row.lead_id for row in open_rows if row.rule_code not in ACTION_RULES
        }),
        "open_by_rule": dict(sorted(Counter(row.rule_code for row in open_rows).items())),
        "open_by_severity": dict(sorted(Counter(row.severity for row in open_rows).items())),
    }


@router.get("", response_model=list[IssueRead])
def list_issues(
    status: Literal["open", "resolved", "dismissed", "all"] = "open",
    rule_code: str | None = None,
    lead_id: str | None = None,
    severity: Literal["low", "medium", "high"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List saved warnings with filters and pagination."""
    query = select(LeadIssue)

    if status != "all":
        query = query.where(LeadIssue.status == status)
    if rule_code:
        query = query.where(LeadIssue.rule_code == rule_code)
    if lead_id:
        query = query.where(LeadIssue.lead_id == lead_id)
    if severity:
        query = query.where(LeadIssue.severity == severity)

    # A stable order makes frontend pagination reliable.
    query = query.order_by(LeadIssue.id).offset(offset).limit(limit)
    return list(db.scalars(query).all())


@router.get("/summary", response_model=IssueSummaryRead)
def issue_summary(db: Session = Depends(get_db)):
    """Count already-saved warnings. Run POST /issues/scan to refresh them."""
    return summarize_issues(db)


@router.get("/duplicates", response_model=list[DuplicatePairRead])
def duplicate_candidates(db: Session = Depends(get_db)):
    """Preview possible duplicate pairs without merging or saving changes."""
    return find_duplicate_pairs(db)


@router.post("/scan", response_model=IssueScanRead)
def scan_issues(
    evaluation_mode: bool = False,
    db: Session = Depends(get_db),
):
    """Run both scans together and save their warnings in one transaction.

    evaluation_mode=true uses 2026-11-01 for BIAI's repeatable evaluation.
    Otherwise, the follow-up checker uses today's date in Asia/Dhaka.
    """
    day = reference_date(evaluation_mode=evaluation_mode)
    try:
        validation = run_validation(db, as_of=day)
        duplicates = run_duplicate_scan(db)
        db.flush()  # Include newly-created warnings in the summary.
        summary = summarize_issues(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "as_of": day,
        "scanned_leads": validation["scanned_leads"],
        "validation_issues_found": validation["issues_found"],
        "overdue_leads": validation["overdue_leads"],
        "duplicate_pairs": duplicates["pairs_found"],
        "duplicate_leads_flagged": duplicates["leads_flagged"],
        "new_issues_saved": (
            validation["new_issues_saved"] + duplicates["new_issues_saved"]
        ),
        "old_issues_resolved": (
            validation["old_issues_resolved"] + duplicates["old_issues_resolved"]
        ),
        "open_issues": summary["open_issues"],
        "data_quality_flagged_leads": summary["data_quality_flagged_leads"],
    }


@router.get("/{issue_id}", response_model=IssueRead)
def get_issue(issue_id: int, db: Session = Depends(get_db)):
    """Read the details and evidence for one warning."""
    issue = db.get(LeadIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return issue


@router.patch("/{issue_id}/status", response_model=IssueRead)
def review_issue(
    issue_id: int,
    body: IssueStatusUpdate,
    db: Session = Depends(get_db),
):
    """Mark a warning resolved or dismissed and record the review reason.

    Resolved means the user considers it fixed. If a later scan still finds
    the same problem, the warning will reopen. Dismissed warnings stay
    dismissed while their evidence is unchanged.
    """
    issue = db.get(LeadIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found.")

    if issue.status == body.status:
        return issue  # Repeating the same request does not add another log.

    previous_status = issue.status
    issue.status = body.status
    db.add(LeadActivity(
        lead_id=issue.lead_id,
        action_type="issue_reviewed",
        description=(
            f"Issue #{issue.id} ({issue.rule_code}): "
            f"{previous_status} -> {body.status}. Reason: {body.reason}"
        ),
        old_value=previous_status,
        new_value=body.status,
    ))
    try:
        db.commit()
        db.refresh(issue)
    except Exception:
        db.rollback()
        raise
    return issue

"""Rank BIAI leads that need attention, using clear Python rules.

Priority is a business recommendation, NOT a fact provided by the CSV.
This service does not use AI, change records, or send emails. Later, the
LangGraph assistant can explain these results in plain English.

Run from the backend/ folder:
    python -m app.services.priority --evaluation
    python -m app.services.priority --evaluation --limit 10
    python -m app.services.priority
"""

import argparse
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    ACTIVE_STAGES,
    CURRENCY_CODE,
    PipelineStage,
    reference_date,
)
from ..database import SessionLocal, create_tables
from ..models import Lead
from .analytics import get_overdue_summary


# Business priorities proposed for this demo. BIAI can change these weights.
# Later sales stages usually need more immediate attention.
STAGE_POINTS = {
    PipelineStage.NEW: 0,
    PipelineStage.CONTACTED: 5,
    PipelineStage.MEETING: 10,
    PipelineStage.PROPOSAL: 20,
    PipelineStage.INVOICE_SENT: 25,
}


def deal_value_points(amount: int | None) -> int:
    """Give some extra priority to larger potential deals (in BDT)."""
    value = amount or 0
    if value >= 500_000:
        return 15
    if value >= 250_000:
        return 10
    if value > 0:
        return 5
    return 0


def score_lead(lead: Lead, as_of: date) -> dict:
    """Score one active lead and explain why it needs attention.

    A wrong date sequence is treated as a data-review task. We do NOT trust
    that date to decide how urgently someone should contact the company.
    """
    score = 0
    reasons = []
    followup_status = "scheduled"
    days_overdue = 0

    invalid_date = (
        lead.last_contact is not None
        and lead.next_follow_up is not None
        and lead.next_follow_up < lead.last_contact
    )

    if invalid_date:
        # L25 is a real example. Do not recommend outreach from a bad date.
        score += 30
        followup_status = "invalid_date"
        days_overdue = None
        reasons.append("Follow-up date is earlier than the last contact date.")
    elif lead.next_follow_up is None:
        score += 20
        followup_status = "missing_date"
        days_overdue = None
        reasons.append("No follow-up date has been scheduled.")
    else:
        days_until_due = (lead.next_follow_up - as_of).days
        if days_until_due < 0:
            days_overdue = -days_until_due
            # Cap the extra points so very old dates do not dominate forever.
            score += 60 + min(days_overdue, 14) * 2
            followup_status = "overdue"
            reasons.append(f"Follow-up is {days_overdue} day(s) overdue.")
        elif days_until_due == 0:
            score += 30
            followup_status = "due_today"
            reasons.append("Follow-up is due today.")
        elif days_until_due <= 3:
            score += 15
            followup_status = "due_soon"
            reasons.append(f"Follow-up is due in {days_until_due} day(s).")

    stage_points = STAGE_POINTS[lead.stage]
    score += stage_points
    if stage_points:
        reasons.append(f"Current sales stage: {lead.stage.value}.")

    value_points = deal_value_points(lead.deal_value_bdt)
    score += value_points
    if value_points:
        reasons.append(f"Potential deal value: BDT {lead.deal_value_bdt:,}.")

    if lead.owner_id is None:
        score += 8
        reasons.append("No employee is assigned to this lead.")

    # These are safe, simple next steps. We never claim payment was received.
    if invalid_date:
        next_action = "Verify and correct the follow-up date before contacting."
    elif lead.stage == PipelineStage.INVOICE_SENT:
        next_action = "Check payment status before sending an invoice reminder."
    elif lead.next_follow_up is None:
        next_action = "Assign an owner and schedule a follow-up."
    elif followup_status == "overdue":
        next_action = "Review the notes and prepare a polite follow-up."
    elif lead.owner_id is None:
        next_action = "Assign an owner to handle the next follow-up."
    else:
        next_action = "Review the notes before the next scheduled follow-up."

    return {
        "lead_id": lead.lead_id,
        "company": lead.company,
        "stage": lead.stage.value,
        "deal_value_bdt": lead.deal_value_bdt or 0,
        "owner_id": lead.owner_id,
        "last_contact": lead.last_contact.isoformat() if lead.last_contact else None,
        "next_follow_up": (
            lead.next_follow_up.isoformat() if lead.next_follow_up else None
        ),
        "followup_status": followup_status,
        "days_overdue": days_overdue,
        "requires_date_review": invalid_date,
        "priority_score": score,
        "reasons": reasons,
        "recommended_action": next_action,
        "notes": lead.notes,
    }


def get_priority_leads(
    db: Session, *, as_of: date | None = None, limit: int = 5
) -> list[dict]:
    """Get the highest-priority current leads from SQLite.

    - Paid and Lost are never included.
    - Merged duplicate records are never included.
    - Date rules work with a fixed test date or today's Dhaka date.
    - This is one database query followed by an in-memory sort. For BIAI's
      25-record dataset, this is simple and fast.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100.")

    day = as_of if as_of is not None else reference_date()
    leads = db.scalars(
        select(Lead).where(
            Lead.merged_into_id.is_(None),
            Lead.stage.in_(ACTIVE_STAGES),
        )
    ).all()

    scored = [score_lead(lead, day) for lead in leads]
    scored.sort(
        key=lambda lead: (
            -lead["priority_score"],
            lead["next_follow_up"] or "9999-12-31",
            lead["lead_id"],
        )
    )

    result = scored[:limit]
    for rank, lead in enumerate(result, start=1):
        lead["rank"] = rank
    return result


def get_weekly_priority_summary(
    db: Session, *, as_of: date | None = None, limit: int = 5
) -> dict:
    """Return verified facts for a future AI-written weekly summary.

    Groq will later turn this structured data into a short report. Groq
    should not calculate scores or decide the ranking on its own.
    """
    day = as_of if as_of is not None else reference_date()
    priority_leads = get_priority_leads(db, as_of=day, limit=limit)
    overdue = get_overdue_summary(db, day)
    return {
        "as_of": day.isoformat(),
        "currency": CURRENCY_CODE,
        "overdue_active_leads": overdue["overdue_leads"],
        "overdue_value_bdt": overdue["overdue_value_bdt"],
        "top_leads": priority_leads,
        "note": (
            "Priority scores are suggested business rules. "
            "Verify invalid dates and invoice payments before outreach."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rank BIAI sales follow-ups")
    parser.add_argument(
        "--evaluation", action="store_true",
        help="Use the sample reference date: 1 November 2026",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of leads (1-100)")
    args = parser.parse_args()

    create_tables()
    with SessionLocal() as session:
        report = get_weekly_priority_summary(
            session,
            as_of=reference_date(evaluation_mode=args.evaluation),
            limit=args.limit,
        )

    print(f"Priority date:     {report['as_of']}")
    print(f"Overdue deals:     {report['overdue_active_leads']}")
    print(f"Overdue deal value: BDT {report['overdue_value_bdt']:,}")
    print("\nTop leads to review:")
    for lead in report["top_leads"]:
        print(f"  {lead['rank']}. {lead['lead_id']} {lead['company']} "
              f"(score: {lead['priority_score']})")
        print(f"     {lead['recommended_action']}")

    if not report["top_leads"]:
        print("  No active leads found. Import the CSV first.")

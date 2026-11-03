"""Fast, reliable dashboard calculations for BIAI's sales pipeline.

All amounts come from SQLite, not from an AI model.
A lead that has been merged into another lead is NEVER counted twice.

Run from backend/:
    python -m app.services.analytics --evaluation
    python -m app.services.analytics
"""

import argparse
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import (
    ACTIVE_STAGES,
    ALL_STAGES,
    CURRENCY_CODE,
    PipelineStage,
    parse_stage,
    reference_date,
)
from ..database import SessionLocal, create_tables
from ..models import Lead, LeadIssue


# These are work reminders, not errors in the original CSV data.
ACTION_RULES = ("MISSING_OWNER", "OVERDUE_FOLLOWUP")


def get_stage_summary(db: Session) -> list[dict]:
    """Return count and value for every stage, in BIAI's official order.

    SQLite does the counting and summing. We don't load all leads into Python.
    Only current leads (not records merged into another lead) are included.
    """
    rows = db.execute(
        select(
            Lead.stage,
            func.count(Lead.lead_id),
            func.coalesce(func.sum(Lead.deal_value_bdt), 0),
        )
        .where(Lead.merged_into_id.is_(None))
        .group_by(Lead.stage)
    ).all()

    # Make a lookup so empty stages still appear with zero leads and BDT 0.
    results_by_stage = {
        stage: (int(count), int(amount))
        for stage, count, amount in rows
    }

    return [
        {
            "stage": stage.value,
            "lead_count": results_by_stage.get(stage, (0, 0))[0],
            "total_value_bdt": results_by_stage.get(stage, (0, 0))[1],
        }
        for stage in ALL_STAGES
    ]


def get_stage_total(db: Session, stage: str | PipelineStage) -> dict:
    """Answer: 'What is the total value in Proposal stage?'"""
    selected_stage = parse_stage(stage)

    count, amount = db.execute(
        select(
            func.count(Lead.lead_id),
            func.coalesce(func.sum(Lead.deal_value_bdt), 0),
        ).where(
            Lead.merged_into_id.is_(None),
            Lead.stage == selected_stage,
        )
    ).one()

    return {
        "stage": selected_stage.value,
        "lead_count": int(count),
        "total_value_bdt": int(amount),
        "currency": CURRENCY_CODE,
    }


def get_overdue_summary(db: Session, as_of: date | None = None) -> dict:
    """Count overdue ACTIVE leads as of the chosen date.

    A follow-up is overdue when next_follow_up is BEFORE the date.
    Paid and Lost are final stages and are not overdue.
    """
    day = as_of if as_of is not None else reference_date()

    count, amount = db.execute(
        select(
            func.count(Lead.lead_id),
            func.coalesce(func.sum(Lead.deal_value_bdt), 0),
        ).where(
            Lead.merged_into_id.is_(None),
            Lead.stage.in_(ACTIVE_STAGES),
            Lead.next_follow_up < day,
        )
    ).one()

    return {
        "as_of": day.isoformat(),
        "overdue_leads": int(count),
        "overdue_value_bdt": int(amount),
    }


def get_dashboard_stats(db: Session, as_of: date | None = None) -> dict:
    """Build dashboard numbers from the current SQLite database.

    Issue counts reflect OPEN warnings already saved by the validation and
    duplicate scanners. Run those scanners first for up-to-date warnings.
    """
    day = as_of if as_of is not None else reference_date()
    stages = get_stage_summary(db)
    overdue = get_overdue_summary(db, day)

    # The historical merged rows are kept in the database for auditing.
    all_records = db.scalar(select(func.count()).select_from(Lead)) or 0

    unassigned = db.scalar(
        select(func.count()).select_from(Lead).where(
            Lead.merged_into_id.is_(None),
            Lead.owner_id.is_(None),
        )
    ) or 0

    unassigned_active = db.scalar(
        select(func.count()).select_from(Lead).where(
            Lead.merged_into_id.is_(None),
            Lead.owner_id.is_(None),
            Lead.stage.in_(ACTIVE_STAGES),
        )
    ) or 0

    # Only count issues for current leads; hide dismissed/resolved warnings.
    open_issues = db.scalar(
        select(func.count()).select_from(LeadIssue)
        .join(Lead, LeadIssue.lead_id == Lead.lead_id)
        .where(Lead.merged_into_id.is_(None), LeadIssue.status == "open")
    ) or 0

    data_quality_leads = db.scalar(
        select(func.count(func.distinct(LeadIssue.lead_id)))
        .select_from(LeadIssue)
        .join(Lead, LeadIssue.lead_id == Lead.lead_id)
        .where(
            Lead.merged_into_id.is_(None),
            LeadIssue.status == "open",
            LeadIssue.rule_code.notin_(ACTION_RULES),
        )
    ) or 0

    # The first five stages represent open sales opportunities.
    active_rows = stages[:len(ACTIVE_STAGES)]
    return {
        "as_of": day.isoformat(),
        "currency": CURRENCY_CODE,
        "all_lead_records": int(all_records),  # Includes merged records
        "current_leads": sum(row["lead_count"] for row in stages),
        "active_leads": sum(row["lead_count"] for row in active_rows),
        "paid_leads": stages[5]["lead_count"],
        "lost_leads": stages[6]["lead_count"],
        "active_pipeline_value_bdt": sum(
            row["total_value_bdt"] for row in active_rows
        ),
        "total_value_bdt": sum(row["total_value_bdt"] for row in stages),
        "overdue_leads": overdue["overdue_leads"],
        "overdue_value_bdt": overdue["overdue_value_bdt"],
        "unassigned_leads": int(unassigned),
        "unassigned_active_leads": int(unassigned_active),
        "open_issue_count": int(open_issues),
        "data_quality_flagged_leads": int(data_quality_leads),
        "stage_summary": stages,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show BIAI pipeline statistics")
    parser.add_argument(
        "--evaluation", action="store_true",
        help="Use the sample-data date, 1 November 2026",
    )
    args = parser.parse_args()

    create_tables()
    with SessionLocal() as db:
        stats = get_dashboard_stats(
            db, as_of=reference_date(evaluation_mode=args.evaluation)
        )

    print(f"Report date:          {stats['as_of']}")
    print(f"All lead records:     {stats['all_lead_records']}")
    print(f"Current leads:        {stats['current_leads']}")
    print(f"Active leads:         {stats['active_leads']}")
    print(f"Active pipeline value: BDT {stats['active_pipeline_value_bdt']:,}")
    print(f"Total deal value:     BDT {stats['total_value_bdt']:,}")
    print(f"Overdue active leads: {stats['overdue_leads']}")
    print(f"Unassigned leads:     {stats['unassigned_leads']}")
    print(f"Open saved issues:    {stats['open_issue_count']}")
    print("\nStages:")
    for row in stats["stage_summary"]:
        print(f"  {row['stage']:<14} {row['lead_count']:>3} leads  BDT {row['total_value_bdt']:>10,}")
    if stats["all_lead_records"] == 0:
        print("\nNo leads found. Run: python -m app.services.csv_importer")

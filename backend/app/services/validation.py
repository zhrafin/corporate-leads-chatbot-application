"""Check BIAI leads for data problems and overdue follow-ups.

This module does not change lead information. It only creates or updates
warnings in the lead_issues table. Duplicate matching lives in duplicates.py.

From the backend/ folder:
    python -m app.services.validation --evaluation
    python -m app.services.validation

The first command uses BIAI's reference date (2026-11-01).
The second command uses today's date in the Asia/Dhaka timezone.
"""

from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ACTIVE_STAGES, PipelineStage, parse_stage, reference_date
from ..database import SessionLocal, create_tables
from ..models import Lead, LeadIssue


# These rule codes are owned by this module. The separate duplicates.py
# service will have its own rules, which we must not modify here.
RULE_CODES = (
    "MISSING_CONTACT",
    "MISSING_OWNER",
    "MISSING_FOLLOWUP",
    "INVALID_DATE_ORDER",
    "OVERDUE_FOLLOWUP",
    "INVALID_STAGE",
    "POSSIBLE_STAGE_CONFLICT",
    "POSSIBLE_PAYMENT_CONFLICT",
    "VERIFY_EMAIL",
    "NOTES_CONTACT_CONFLICT",
)


def make_issue(lead: Lead, code: str, message: str,
               severity: str = "medium", evidence: dict | None = None) -> dict:
    """Build one warning in a consistent format."""
    return {
        "lead_id": lead.lead_id,
        "rule_code": code,
        "message": message,
        "severity": severity,
        "evidence": evidence or {},
    }


def check_contact(lead: Lead) -> list[dict]:
    """Check the contact fields. Missing fields are grouped in one issue."""
    fields = ("contact_name", "contact_role", "contact_email", "contact_phone")
    missing = [name for name in fields if not (getattr(lead, name) or "").strip()]

    if not missing:
        return []
    return [make_issue(
        lead, "MISSING_CONTACT",
        f"Missing contact information: {', '.join(missing)}.",
        "high" if "contact_name" in missing or "contact_email" in missing else "medium",
        {"missing_fields": missing},
    )]


def check_owner(lead: Lead) -> list[dict]:
    """BIAI needs an employee assigned to every lead."""
    if lead.owner_id is not None:
        return []
    return [make_issue(
        lead, "MISSING_OWNER", "No employee is assigned to this lead.", "medium"
    )]


def check_followup(lead: Lead, as_of: date) -> list[dict]:
    """Check missing, wrongly ordered, and overdue follow-up dates."""
    issues = []

    if lead.next_follow_up is None:
        # The supplied definitions say EVERY company needs a follow-up date.
        # Final-stage leads receive only a low-priority data warning.
        issues.append(make_issue(
            lead, "MISSING_FOLLOWUP", "Next follow-up date is missing.",
            "low", {"stage": str(lead.stage.value if isinstance(lead.stage, PipelineStage) else lead.stage)},
        ))
        return issues

    if lead.last_contact and lead.next_follow_up < lead.last_contact:
        issues.append(make_issue(
            lead, "INVALID_DATE_ORDER",
            "Next follow-up date is earlier than last contact date.",
            "high",
            {
                "last_contact": lead.last_contact.isoformat(),
                "next_follow_up": lead.next_follow_up.isoformat(),
            },
        ))

    # Paid and Lost are final stages. Never chase them as overdue.
    try:
        active = parse_stage(lead.stage) in ACTIVE_STAGES
    except ValueError:
        active = False  # INVALID_STAGE is reported separately.

    if active and lead.next_follow_up < as_of:
        issues.append(make_issue(
            lead, "OVERDUE_FOLLOWUP",
            f"Follow-up was due on {lead.next_follow_up.isoformat()}.",
            "high",
            {
                "next_follow_up": lead.next_follow_up.isoformat(),
                "as_of": as_of.isoformat(),
            },
        ))

    return issues


def check_stage(lead: Lead) -> list[dict]:
    """Check the stage value and a few simple note-based contradictions."""
    try:
        stage = parse_stage(lead.stage)
    except ValueError:
        return [make_issue(
            lead, "INVALID_STAGE", f"Unknown sales stage: {lead.stage!s}.",
            "high", {"stage": str(lead.stage)},
        )]

    notes = (lead.notes or "").lower()
    issues = []

    # These are possible inconsistencies, NOT automatic corrections.
    if stage == PipelineStage.PAID and any(
        phrase in notes for phrase in ("50 percent", "50%", "second payment", "partial payment")
    ):
        issues.append(make_issue(
            lead, "POSSIBLE_PAYMENT_CONFLICT",
            "Stage is Paid, but the notes suggest payment may be incomplete.",
            "high", {"stage": stage.value},
        ))

    if stage == PipelineStage.NEW and lead.last_contact is not None:
        issues.append(make_issue(
            lead, "POSSIBLE_STAGE_CONFLICT",
            "Stage is New, but a last-contact date exists. Please verify.",
            "low", {"last_contact": lead.last_contact.isoformat()},
        ))

    if "email has a typo" in notes:
        issues.append(make_issue(
            lead, "VERIFY_EMAIL",
            "The notes say the email may contain a typo. Verify before sending.",
            "medium", {"contact_email": lead.contact_email},
        ))

    if "no phone or email" in notes and lead.contact_email:
        issues.append(make_issue(
            lead, "NOTES_CONTACT_CONFLICT",
            "Notes say no email is on file, but an email address is present.",
            "low", {"contact_email": lead.contact_email},
        ))

    return issues


def validate_lead(lead: Lead, as_of: date) -> list[dict]:
    """Run all individual checks on one lead (does not write to SQLite)."""
    return (
        check_contact(lead)
        + check_owner(lead)
        + check_followup(lead, as_of)
        + check_stage(lead)
    )


def get_overdue_leads(db: Session, as_of: date | None = None) -> list[Lead]:
    """Fast database query for active, unmerged leads with overdue dates."""
    day = as_of or reference_date()
    return list(db.scalars(
        select(Lead).where(
            Lead.merged_into_id.is_(None),
            Lead.stage.in_(ACTIVE_STAGES),
            Lead.next_follow_up < day,
        ).order_by(Lead.next_follow_up, Lead.lead_id)
    ).all())


def run_validation(db: Session, as_of: date | None = None) -> dict:
    """Scan leads and update lead_issues, without committing the transaction.

    - One query loads all active (not merged) lead records.
    - One query loads previous warnings of rules managed here.
    - Repeated scans update existing warnings instead of inserting copies.
    - Fixed problems are marked resolved. Dismissed warnings remain dismissed
      until the underlying evidence changes.

    The caller should commit the session, usually with SessionLocal.begin().
    """
    day = as_of or reference_date()
    leads = db.scalars(
        select(Lead)
        .where(Lead.merged_into_id.is_(None))
        .order_by(Lead.lead_id)
    ).all()

    findings = [issue for lead in leads for issue in validate_lead(lead, day)]
    detected = {(issue["lead_id"], issue["rule_code"]): issue for issue in findings}

    previous = db.scalars(
        select(LeadIssue).where(LeadIssue.rule_code.in_(RULE_CODES))
    ).all()
    old_by_key = {(item.lead_id, item.rule_code): item for item in previous}

    created = 0
    resolved = 0

    for key, item in detected.items():
        old = old_by_key.get(key)
        if old is None:
            db.add(LeadIssue(**item, status="open"))
            created += 1
            continue

        # Do not undo a dismissal unless the evidence has changed.
        if old.status == "dismissed" and old.evidence == item["evidence"]:
            continue

        old.message = item["message"]
        old.severity = item["severity"]
        old.evidence = item["evidence"]
        old.status = "open"

    for key, old in old_by_key.items():
        if key not in detected and old.status == "open":
            old.status = "resolved"
            resolved += 1

    counts = Counter(item["rule_code"] for item in findings)
    # Unassigned owners and overdue actions are shown separately. They should
    # not make all 25 CSV rows look like incorrect source data.
    data_quality_ids = {
        item["lead_id"] for item in findings
        if item["rule_code"] not in {"MISSING_OWNER", "OVERDUE_FOLLOWUP"}
    }
    return {
        "as_of": day.isoformat(),
        "scanned_leads": len(leads),
        "flagged_leads": len({item["lead_id"] for item in findings}),
        "data_quality_flagged_leads": len(data_quality_ids),
        "issues_found": len(findings),
        "overdue_leads": counts["OVERDUE_FOLLOWUP"],
        "issue_counts": dict(sorted(counts.items())),
        "new_issues_saved": created,
        "old_issues_resolved": resolved,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check BIAI lead data")
    parser.add_argument(
        "--evaluation", action="store_true",
        help="Use BIAI's reference date: 1 November 2026",
    )
    args = parser.parse_args()

    create_tables()
    with SessionLocal.begin() as session:
        result = run_validation(
            session, as_of=reference_date(evaluation_mode=args.evaluation)
        )

    print(f"Validation date: {result['as_of']}")
    print(f"Leads checked:   {result['scanned_leads']}")
    print(f"Any warning:     {result['flagged_leads']} (includes missing owners)")
    print(f"Data-quality:    {result['data_quality_flagged_leads']} leads")
    print(f"Overdue leads:   {result['overdue_leads']}")
    print(f"Warnings found:  {result['issues_found']}")
    print("\nWarnings by type:")
    for code, total in result["issue_counts"].items():
        print(f"  {code}: {total}")
    if result["scanned_leads"] == 0:
        print("\nNo leads found. Run: python scripts/import_leads.py from the project root.")
    print("\nDuplicate detection is added in the next step (duplicates.py).")

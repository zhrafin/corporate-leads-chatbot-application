"""Reports and spreadsheet-friendly CSV export for the BIAI sales pipeline.

These endpoints are read-only. They do not change leads or run database scans.

- /reports/summary: dashboard numbers and already-saved warning counts
- /reports/evaluation: re-check the *archived original CSV* for a stable demo
- /reports/export.csv: download current leads as an Excel-friendly CSV

For evaluation accuracy, a person must review the original 25 records and fill
in data/evaluation_labels.json. Without those labels we report detection counts,
not unverified claims of accuracy.
"""

import csv
from collections import Counter, defaultdict
from io import StringIO
import json
from itertools import combinations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import LEADS_CSV_PATH, PROJECT_ROOT, reference_date
from ..database import get_db
from ..models import ImportRow, Lead, LeadIssue, Owner
from ..schemas import EvaluationReportRead, ReportSummaryRead
from ..services.analytics import get_dashboard_stats
from ..services.csv_importer import prepare_lead
from ..services.duplicates import compare_leads, find_duplicate_pairs
from ..services.validation import validate_lead
from .issues import ACTION_RULES, summarize_issues

router = APIRouter(prefix="/reports", tags=["Reports"])

# Reviewers may create this file using evaluation_labels.example.json as a guide.
LABELS_PATH = PROJECT_ROOT / "data" / "evaluation_labels.json"

# Only these specific columns are exposed in the export. Do not export raw
# internal JSON or other database tables without reviewing their contents.
CSV_COLUMNS = [
    "lead_id", "company", "industry", "contact_name", "contact_role",
    "contact_email", "contact_phone", "source", "stage", "deal_value_bdt",
    "last_contact", "next_follow_up", "notes", "owner_id", "owner_name",
    "merged_into_id", "open_issue_codes",
]


def _safe_csv_text(value: str | None) -> str:
    """Prevent spreadsheet software from treating text as a formula.

    A CSV field beginning with =, +, -, or @ can be interpreted as a formula
    by spreadsheet apps. Prefix such text with a single quote.
    """
    text = value or ""
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _read_manual_labels(original_ids: set[str]) -> dict[str, bool]:
    """Read reviewer decisions, ignoring null/unreviewed entries.

    A reviewer writes one boolean per original lead: true means the *original*
    source record has at least one data-quality issue, false means it does not.
    Overdue follow-ups and the missing-owner column are counted separately.
    """
    if not LABELS_PATH.exists():
        return {}
    try:
        content = json.loads(LABELS_PATH.read_text(encoding="utf-8-sig"))
        labels = content["labels"]
        if not isinstance(labels, dict):
            raise ValueError("The 'labels' field must be an object.")
        unknown_ids = set(labels) - original_ids
        if unknown_ids:
            raise ValueError(f"Unknown original lead IDs: {sorted(unknown_ids)}")
        if any(type(value) not in (bool, type(None)) for value in labels.values()):
            raise ValueError("Every label must be true, false, or null.")
        return {lead_id: value for lead_id, value in labels.items() if value is not None}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid evaluation_labels.json: {exc}",
        ) from exc


def _accuracy(predicted: set[str], actual: dict[str, bool]) -> dict:
    """Calculate confusion-matrix counts from COMPLETE reviewed labels."""
    positives = {lead_id for lead_id, is_problem in actual.items() if is_problem}
    negatives = set(actual) - positives
    true_positives = len(predicted & positives)
    false_positives = len(predicted & negatives)
    false_negatives = len(positives - predicted)
    true_negatives = len(negatives - predicted)
    total = len(actual)
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negatives": true_negatives,
        "correctly_classified_leads": true_positives + true_negatives,
        "accuracy": (true_positives + true_negatives) / total if total else None,
        "precision": (
            true_positives / (true_positives + false_positives)
            if true_positives + false_positives else None
        ),
        "recall": (
            true_positives / (true_positives + false_negatives)
            if true_positives + false_negatives else None
        ),
    }


@router.get("/summary", response_model=ReportSummaryRead)
def report_summary(
    evaluation_mode: bool = False,
    db: Session = Depends(get_db),
):
    """Current dashboard figures plus *saved* issue counts.

    Run POST /api/issues/scan before relying on the warning counts.
    Reading this report does not modify the database.
    """
    day = reference_date(evaluation_mode=evaluation_mode)
    merged_count = db.scalar(
        select(func.count()).select_from(Lead).where(Lead.merged_into_id.is_not(None))
    ) or 0
    return {
        "as_of": day,
        "dashboard": get_dashboard_stats(db, as_of=day),
        "issues": summarize_issues(db),
        "pending_duplicate_pairs": len(find_duplicate_pairs(db)),
        "merged_records": int(merged_count),
        "note": "Warning counts are from the last saved scan. Use POST /api/issues/scan to refresh them.",
    }


@router.get("/evaluation", response_model=EvaluationReportRead)
def evaluation_report(
    db: Session = Depends(get_db),
):
    """Check the archived original dataset using BIAI's fixed 2026-11-01 date.

    This baseline is unchanged after a user edits or merges live lead records.
    No issue rows are created or updated by this endpoint.
    """
    archive = db.scalars(
        select(ImportRow)
        .where(ImportRow.source_file == LEADS_CSV_PATH.name)
        .order_by(ImportRow.row_number)
    ).all()
    if not archive:
        raise HTTPException(
            status_code=404,
            detail="Original CSV archive is missing. Run the CSV importer first.",
        )

    day = reference_date(evaluation_mode=True)
    original_leads = [prepare_lead(row.raw_data) for row in archive]
    by_rule = defaultdict(set)

    for lead in original_leads:
        for issue in validate_lead(lead, day):
            by_rule[issue["rule_code"]].add(lead.lead_id)

    # The provided sample has only 25 records. Comparing all pairs keeps this
    # evaluation easy to understand and independent of later database merges.
    pairs = []
    for first, second in combinations(original_leads, 2):
        match = compare_leads(first, second)
        if match:
            pairs.append({
                "lead_id_1": first.lead_id,
                "company_1": first.company,
                "lead_id_2": second.lead_id,
                "company_2": second.company,
                **match,
            })
            by_rule["DUPLICATE_LEAD"].update((first.lead_id, second.lead_id))

    # Data-quality errors and action reminders are different categories.
    quality_ids = set().union(*(
        ids for rule, ids in by_rule.items() if rule not in ACTION_RULES
    )) if by_rule else set()
    all_ids = {lead.lead_id for lead in original_leads}
    reviewed = _read_manual_labels(all_ids)
    complete = len(reviewed) == len(all_ids)
    status = "complete" if complete else "partial" if reviewed else "awaiting_review"

    return {
        "as_of": day,
        "source_file": LEADS_CSV_PATH.name,
        "original_leads": len(original_leads),
        "source_data_flagged_leads": len(quality_ids),
        "flagged_lead_ids": sorted(quality_ids),
        "overdue_leads": len(by_rule["OVERDUE_FOLLOWUP"]),
        "duplicate_pairs": pairs,
        "invalid_date_order_leads": sorted(by_rule["INVALID_DATE_ORDER"]),
        "counts_by_rule": {rule: len(ids) for rule, ids in sorted(by_rule.items())},
        "verification_status": status,
        "manually_reviewed_leads": len(reviewed),
        "metrics": _accuracy(quality_ids, reviewed) if complete else None,
        "note": (
            "Detections use the archived original CSV, not edited records. "
            "Accuracy/precision/recall are shown only after a reviewer labels "
            "every original lead in data/evaluation_labels.json."
        ),
    }


@router.get("/export.csv")
def export_pipeline_csv(
    include_merged: bool = False,
    db: Session = Depends(get_db),
):
    """Download current leads in CSV format, which Excel can open.

    By default exclude leads that were merged into another lead. Set
    include_merged=true to export all historical rows as well.
    """
    query = select(Lead, Owner.name).outerjoin(Owner, Lead.owner_id == Owner.id)
    if not include_merged:
        query = query.where(Lead.merged_into_id.is_(None))
    leads = db.execute(query.order_by(Lead.lead_id)).all()

    # The saved issue results are shown to make exports useful for review.
    # This is only the last saved scan, not a fresh scan.
    open_issues = db.execute(
        select(LeadIssue.lead_id, LeadIssue.rule_code).where(LeadIssue.status == "open")
    ).all()
    issue_codes: dict[str, set[str]] = defaultdict(set)
    for lead_id, rule_code in open_issues:
        issue_codes[lead_id].add(rule_code)

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for lead, owner_name in leads:
        writer.writerow({
            "lead_id": _safe_csv_text(lead.lead_id),
            "company": _safe_csv_text(lead.company),
            "industry": _safe_csv_text(lead.industry),
            "contact_name": _safe_csv_text(lead.contact_name),
            "contact_role": _safe_csv_text(lead.contact_role),
            "contact_email": _safe_csv_text(lead.contact_email),
            # Preserve leading zeros when the file is opened in Excel.
            "contact_phone": "'" + _safe_csv_text(lead.contact_phone) if lead.contact_phone else "",
            "source": _safe_csv_text(lead.source),
            "stage": lead.stage.value,
            "deal_value_bdt": lead.deal_value_bdt if lead.deal_value_bdt is not None else "",
            "last_contact": lead.last_contact.isoformat() if lead.last_contact else "",
            "next_follow_up": lead.next_follow_up.isoformat() if lead.next_follow_up else "",
            "notes": _safe_csv_text(lead.notes),
            "owner_id": lead.owner_id if lead.owner_id is not None else "",
            "owner_name": _safe_csv_text(owner_name),
            "merged_into_id": _safe_csv_text(lead.merged_into_id),
            "open_issue_codes": "; ".join(sorted(issue_codes.get(lead.lead_id, set()))),
        })

    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="biai-pipeline.csv"'},
    )

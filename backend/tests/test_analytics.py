"""Tests for Step 8. Run from backend/: python -m unittest discover -s tests -v"""

import unittest
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import EVALUATION_DATE, LEADS_CSV_PATH
from app.database import Base
from app.models import Lead, LeadIssue
from app.services.analytics import (
    get_dashboard_stats,
    get_overdue_summary,
    get_stage_summary,
    get_stage_total,
)
from app.services.csv_importer import prepare_lead
from app.services.lead_service import merge_leads, create_owner, assign_owner
from app.services.validation import run_validation
from app.services.duplicates import run_duplicate_scan


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        # Never modify the user's real SQLite database during tests.
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        frame = pd.read_csv(
            LEADS_CSV_PATH, dtype=str, keep_default_na=False,
            encoding="utf-8-sig",
        )
        self.db.add_all([prepare_lead(row) for row in frame.to_dict("records")])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def test_all_seven_stages_have_correct_order(self):
        rows = get_stage_summary(self.db)
        self.assertEqual([r["stage"] for r in rows], [
            "New", "Contacted", "Meeting", "Proposal", "Invoice sent", "Paid", "Lost"
        ])
        self.assertEqual(sum(r["lead_count"] for r in rows), 25)

    def test_stage_total_before_merge_is_not_silently_cleaned(self):
        result = get_stage_total(self.db, "Proposal")
        self.assertEqual(result["lead_count"], 3)
        self.assertEqual(result["total_value_bdt"], 1_620_000)
        self.assertEqual(result["currency"], "BDT")

    def test_invalid_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            get_stage_total(self.db, "Negotiation")

    def test_overdue_uses_biai_reference_date(self):
        result = get_overdue_summary(self.db, EVALUATION_DATE)
        self.assertEqual(result["overdue_leads"], 9)
        # Leads due *on* the date are not overdue.
        self.assertNotEqual(result["overdue_leads"], 10)

    def test_dashboard_before_merges(self):
        result = get_dashboard_stats(self.db, EVALUATION_DATE)
        self.assertEqual(result["all_lead_records"], 25)
        self.assertEqual(result["current_leads"], 25)
        self.assertEqual(result["active_leads"], 22)
        self.assertEqual(result["paid_leads"], 1)
        self.assertEqual(result["lost_leads"], 2)
        self.assertEqual(result["total_value_bdt"], 4_220_000)
        self.assertEqual(result["active_pipeline_value_bdt"], 3_920_000)
        self.assertEqual(result["unassigned_leads"], 25)
        self.assertEqual(result["overdue_leads"], 9)

    def test_merged_duplicates_are_excluded(self):
        merge_leads(self.db, "L01", "L23", confirmed=True, reason="Duplicate of L01")
        merge_leads(self.db, "L02", "L03", confirmed=True, reason="Duplicate of L02")
        self.db.commit()

        result = get_dashboard_stats(self.db, EVALUATION_DATE)
        self.assertEqual(result["all_lead_records"], 25)
        self.assertEqual(result["current_leads"], 23)
        self.assertEqual(result["total_value_bdt"], 3_170_000)
        self.assertEqual(result["active_pipeline_value_bdt"], 2_870_000)
        self.assertEqual(get_stage_total(self.db, "Proposal")["total_value_bdt"], 920_000)
        self.assertEqual(get_stage_total(self.db, "Meeting")["total_value_bdt"], 1_550_000)
        self.assertEqual(result["overdue_leads"], 9)

    def test_owner_assignment_changes_dashboard(self):
        owner = create_owner(self.db, "Demo Employee")
        assign_owner(self.db, "L04", owner.id)
        self.db.commit()
        result = get_dashboard_stats(self.db, EVALUATION_DATE)
        self.assertEqual(result["unassigned_leads"], 24)
        self.assertEqual(result["unassigned_active_leads"], 21)

    def test_saved_issues_count_open_only(self):
        run_validation(self.db, EVALUATION_DATE)
        run_duplicate_scan(self.db)
        self.db.commit()
        result = get_dashboard_stats(self.db, EVALUATION_DATE)
        self.assertEqual(result["open_issue_count"], 48)
        self.assertEqual(result["data_quality_flagged_leads"], 11)
        # Dismiss one issue and confirm it no longer counts as open.
        issue = self.db.scalar(select(LeadIssue).where(LeadIssue.lead_id == "L25", LeadIssue.rule_code == "INVALID_DATE_ORDER"))
        issue.status = "dismissed"
        self.db.commit()
        result = get_dashboard_stats(self.db, EVALUATION_DATE)
        self.assertEqual(result["open_issue_count"], 47)

    def test_empty_database(self):
        self.db.query(LeadIssue).delete()
        self.db.query(Lead).delete()
        self.db.commit()
        result = get_dashboard_stats(self.db, date(2026, 11, 1))
        self.assertEqual(result["current_leads"], 0)
        self.assertEqual(result["total_value_bdt"], 0)
        self.assertEqual(result["overdue_leads"], 0)
        self.assertEqual(len(result["stage_summary"]), 7)


if __name__ == "__main__":
    unittest.main()

"""Step 9 tests. Run from backend/: python -m unittest discover -s tests -v"""

import unittest
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import EVALUATION_DATE, LEADS_CSV_PATH
from app.database import Base
from app.models import Lead, LeadActivity
from app.services.csv_importer import prepare_lead
from app.services.lead_service import assign_owner, create_owner, merge_leads, update_lead
from app.services.priority import (
    deal_value_points,
    get_priority_leads,
    get_weekly_priority_summary,
    score_lead,
)


class PriorityTests(unittest.TestCase):
    def setUp(self):
        # Tests NEVER change storage/biai_pipeline.db.
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)

        frame = pd.read_csv(
            LEADS_CSV_PATH,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        self.db.add_all([prepare_lead(row) for row in frame.to_dict("records")])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def test_top_five_are_deterministic_on_reference_date(self):
        leads = get_priority_leads(self.db, as_of=EVALUATION_DATE)
        self.assertEqual([lead["lead_id"] for lead in leads], [
            "L22", "L15", "L10", "L06", "L19"
        ])
        self.assertEqual([lead["rank"] for lead in leads], [1, 2, 3, 4, 5])
        self.assertEqual(leads[0]["priority_score"], 114)

    def test_only_active_and_unmerged_leads_are_ranked(self):
        all_active = get_priority_leads(self.db, as_of=EVALUATION_DATE, limit=100)
        self.assertEqual(len(all_active), 22)
        self.assertFalse({"L08", "L12", "L16"} & {
            lead["lead_id"] for lead in all_active
        })
        merge_leads(self.db, "L01", "L23", confirmed=True, reason="Same contact")
        merge_leads(self.db, "L02", "L03", confirmed=True, reason="Same contact")
        self.db.commit()
        after = get_priority_leads(self.db, as_of=EVALUATION_DATE, limit=100)
        self.assertEqual(len(after), 20)
        self.assertNotIn("L23", {lead["lead_id"] for lead in after})
        self.assertNotIn("L03", {lead["lead_id"] for lead in after})

    def test_l25_invalid_date_requires_review(self):
        lead = self.db.get(Lead, "L25")
        result = score_lead(lead, EVALUATION_DATE)
        self.assertTrue(result["requires_date_review"])
        self.assertEqual(result["followup_status"], "invalid_date")
        self.assertIsNone(result["days_overdue"])
        self.assertIn("Verify and correct", result["recommended_action"])
        self.assertEqual(result["priority_score"], 43)

    def test_invoice_stage_checks_payment_before_reminder(self):
        result = score_lead(self.db.get(Lead, "L22"), EVALUATION_DATE)
        self.assertEqual(result["followup_status"], "overdue")
        self.assertEqual(result["days_overdue"], 8)
        self.assertIn("Check payment status", result["recommended_action"])

    def test_due_today_is_not_overdue(self):
        result = score_lead(self.db.get(Lead, "L09"), EVALUATION_DATE)
        self.assertEqual(result["followup_status"], "due_today")
        self.assertEqual(result["days_overdue"], 0)

    def test_assigning_an_owner_reduces_score_by_eight(self):
        before = score_lead(self.db.get(Lead, "L04"), EVALUATION_DATE)
        owner = create_owner(self.db, "Demo Owner")
        assign_owner(self.db, "L04", owner.id)
        self.db.flush()
        after = score_lead(self.db.get(Lead, "L04"), EVALUATION_DATE)
        self.assertEqual(before["priority_score"] - after["priority_score"], 8)
        self.assertNotIn("No employee is assigned to this lead.", after["reasons"])

    def test_correcting_followup_date_recalculates_priority(self):
        before = score_lead(self.db.get(Lead, "L25"), EVALUATION_DATE)
        update_lead(self.db, "L25", next_follow_up="2026-11-05")
        self.db.flush()
        after = score_lead(self.db.get(Lead, "L25"), EVALUATION_DATE)
        self.assertEqual(before["followup_status"], "invalid_date")
        self.assertEqual(after["followup_status"], "scheduled")
        self.assertFalse(after["requires_date_review"])

    def test_missing_date_is_a_scheduling_task(self):
        update_lead(self.db, "L11", next_follow_up=None)
        self.db.flush()
        result = score_lead(self.db.get(Lead, "L11"), EVALUATION_DATE)
        self.assertEqual(result["followup_status"], "missing_date")
        self.assertIsNone(result["days_overdue"])
        self.assertIn("schedule", result["recommended_action"].lower())

    def test_deal_value_thresholds(self):
        self.assertEqual(deal_value_points(None), 0)
        self.assertEqual(deal_value_points(0), 0)
        self.assertEqual(deal_value_points(120_000), 5)
        self.assertEqual(deal_value_points(250_000), 10)
        self.assertEqual(deal_value_points(500_000), 15)

    def test_limit_is_checked(self):
        for invalid in (0, -1, 101, 1.5, True, "five"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    get_priority_leads(self.db, as_of=EVALUATION_DATE, limit=invalid)

    def test_weekly_summary_uses_verified_overdue_count(self):
        report = get_weekly_priority_summary(self.db, as_of=EVALUATION_DATE, limit=3)
        self.assertEqual(report["as_of"], "2026-11-01")
        self.assertEqual(report["currency"], "BDT")
        self.assertEqual(report["overdue_active_leads"], 9)
        self.assertEqual(report["overdue_value_bdt"], 770_000)
        self.assertEqual(len(report["top_leads"]), 3)
        self.assertEqual(report["top_leads"][0]["lead_id"], "L22")

    def test_empty_database_and_read_only_ranking(self):
        self.db.query(Lead).delete()
        self.db.commit()
        self.assertEqual(get_priority_leads(self.db, as_of=date(2026, 11, 1)), [])
        self.assertEqual(
            get_weekly_priority_summary(self.db, as_of=EVALUATION_DATE)["overdue_active_leads"],
            0,
        )
        self.assertEqual(self.db.scalar(select(func.count()).select_from(LeadActivity)), 0)


if __name__ == "__main__":
    unittest.main()

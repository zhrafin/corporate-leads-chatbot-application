"""Simple tests for Step 5. Run from backend/: python -m unittest discover -s tests"""

import unittest

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import EVALUATION_DATE, LEADS_CSV_PATH
from app.database import Base
from app.models import Lead, LeadIssue
from app.services.csv_importer import prepare_lead
from app.services.validation import get_overdue_leads, run_validation


class ValidationTests(unittest.TestCase):
    def setUp(self):
        # Each test uses its own temporary SQLite database in memory.
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        frame = pd.read_csv(
            LEADS_CSV_PATH, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        self.db.add_all([prepare_lead(row) for row in frame.to_dict("records")])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_expected_csv_flags(self):
        result = run_validation(self.db, EVALUATION_DATE)
        self.assertEqual(result["scanned_leads"], 25)
        self.assertEqual(result["overdue_leads"], 9)
        self.assertEqual(result["data_quality_flagged_leads"], 7)
        self.assertEqual(result["issue_counts"]["INVALID_DATE_ORDER"], 1)
        self.assertEqual(result["issue_counts"]["MISSING_CONTACT"], 2)
        self.assertEqual(result["issue_counts"]["MISSING_OWNER"], 25)
        self.assertEqual(result["issue_counts"]["MISSING_FOLLOWUP"], 3)
        self.assertEqual(result["issues_found"], 44)

        overdue_ids = {lead.lead_id for lead in get_overdue_leads(self.db, EVALUATION_DATE)}
        self.assertEqual(overdue_ids, {
            "L04", "L06", "L10", "L13", "L15",
            "L19", "L20", "L22", "L25",
        })

    def test_running_twice_does_not_duplicate_issues(self):
        first = run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        second = run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        count = self.db.scalar(select(func.count()).select_from(LeadIssue))
        self.assertEqual(first["new_issues_saved"], 44)
        self.assertEqual(second["new_issues_saved"], 0)
        self.assertEqual(count, 44)

    def test_fixing_date_resolves_related_issues(self):
        run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        lead = self.db.get(Lead, "L25")
        from datetime import date
        lead.next_follow_up = date(2026, 11, 5)
        self.db.commit()
        result = run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        self.assertEqual(result["old_issues_resolved"], 2)
        issues = self.db.scalars(
            select(LeadIssue).where(
                LeadIssue.lead_id == "L25",
                LeadIssue.rule_code.in_(("OVERDUE_FOLLOWUP", "INVALID_DATE_ORDER")),
            )
        ).all()
        self.assertEqual({issue.status for issue in issues}, {"resolved"})

    def test_dismissal_is_preserved(self):
        run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        issue = self.db.scalars(select(LeadIssue).where(
            LeadIssue.lead_id == "L18",
            LeadIssue.rule_code == "VERIFY_EMAIL",
        )).one()
        issue.status = "dismissed"
        self.db.commit()
        run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        self.assertEqual(issue.status, "dismissed")


if __name__ == "__main__":
    unittest.main()

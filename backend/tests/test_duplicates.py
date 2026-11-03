"""Tests for the duplicate checker. Run from backend/ with unittest."""

import unittest

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import LEADS_CSV_PATH
from app.database import Base
from app.models import Lead, LeadIssue
from app.services.csv_importer import prepare_lead
from app.services.duplicates import (
    RULE_CODE,
    compare_leads,
    find_duplicate_pairs,
    normalize_company,
    run_duplicate_scan,
)


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        # Tests do not touch the project's actual SQLite database.
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

    def test_company_normalization(self):
        self.assertEqual(normalize_company("Padma Textiles Ltd."), "padma textiles")

    def test_detects_both_real_duplicate_pairs(self):
        pairs = find_duplicate_pairs(self.db)
        found = {(item["lead_id_1"], item["lead_id_2"]) for item in pairs}
        self.assertEqual(found, {("L01", "L23"), ("L02", "L03")})

    def test_scan_saves_four_warnings_only_once(self):
        first = run_duplicate_scan(self.db)
        self.db.commit()
        second = run_duplicate_scan(self.db)
        self.db.commit()
        count = self.db.scalar(
            select(func.count()).select_from(LeadIssue).where(
                LeadIssue.rule_code == RULE_CODE
            )
        )
        self.assertEqual(first["pairs_found"], 2)
        self.assertEqual(first["leads_flagged"], 4)
        self.assertEqual(first["new_issues_saved"], 4)
        self.assertEqual(second["new_issues_saved"], 0)
        self.assertEqual(count, 4)

    def test_reviewed_merge_resolves_pair(self):
        run_duplicate_scan(self.db)
        self.db.commit()
        # Simulate a human-approved merge; the merge API comes later.
        duplicate = self.db.get(Lead, "L23")
        duplicate.merged_into_id = "L01"
        self.db.commit()
        result = run_duplicate_scan(self.db)
        self.db.commit()
        self.assertEqual(result["pairs_found"], 1)
        self.assertEqual(result["old_issues_resolved"], 2)
        affected = self.db.scalars(
            select(LeadIssue).where(
                LeadIssue.lead_id.in_(["L01", "L23"]),
                LeadIssue.rule_code == RULE_CODE)
        ).all()
        self.assertEqual({issue.status for issue in affected}, {"resolved"})
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Lead)), 25)

    def test_dismissed_warning_is_not_reopened_without_change(self):
        run_duplicate_scan(self.db)
        self.db.commit()
        issue = self.db.scalars(select(LeadIssue).where(
            LeadIssue.lead_id == "L01", LeadIssue.rule_code == RULE_CODE
        )).one()
        issue.status = "dismissed"
        self.db.commit()
        run_duplicate_scan(self.db)
        self.db.commit()
        self.assertEqual(issue.status, "dismissed")

    def test_different_companies_are_not_duplicates(self):
        first = self.db.get(Lead, "L04")
        second = self.db.get(Lead, "L05")
        self.assertIsNone(compare_leads(first, second))


if __name__ == "__main__":
    unittest.main()

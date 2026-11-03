"""FastAPI tests for dashboard, stages, overdue deals and priorities.

Run from backend/:
    python -m unittest discover -s tests -p 'test_*.py' -v

Each test uses an in-memory SQLite database, never your real sales data.
"""

import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import LEADS_CSV_PATH, PipelineStage, reference_date
from app.database import Base, get_db
from app.main import app
from app.models import Lead, LeadIssue
from app.services.csv_importer import prepare_lead
from app.services.duplicates import run_duplicate_scan
from app.services.lead_service import merge_leads
from app.services.validation import run_validation


class PipelineAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        data = pd.read_csv(
            LEADS_CSV_PATH,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        with Session(self.engine) as db:
            db.add_all([prepare_lead(row) for row in data.to_dict("records")])
            db.commit()

        self.startup_patch = patch(
            "app.main.create_tables",
            side_effect=lambda: Base.metadata.create_all(self.engine),
        )
        self.startup_patch.start()

        def test_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.startup_patch.stop()
        self.engine.dispose()

    def test_documentation_lists_pipeline_routes(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        for path in (
            "/api/dashboard",
            "/api/pipeline/stages",
            "/api/pipeline/stages/{stage}",
            "/api/pipeline/overdue",
            "/api/pipeline/overdue/summary",
            "/api/pipeline/priorities",
            "/api/pipeline/weekly-summary",
        ):
            self.assertIn(path, paths)

    def test_dashboard_on_fixed_evaluation_date(self):
        response = self.client.get("/api/dashboard", params={"evaluation_mode": True})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["as_of"], "2026-11-01")
        self.assertEqual(result["currency"], "BDT")
        self.assertEqual(result["all_lead_records"], 25)
        self.assertEqual(result["current_leads"], 25)
        self.assertEqual(result["active_leads"], 22)
        self.assertEqual(result["overdue_leads"], 9)
        self.assertEqual(result["active_pipeline_value_bdt"], 3_920_000)
        self.assertEqual(result["total_value_bdt"], 4_220_000)
        self.assertEqual(result["unassigned_leads"], 25)
        self.assertEqual(len(result["stage_summary"]), 7)
        # Reading analytics does not run a validation scan or create warnings.
        self.assertEqual(result["open_issue_count"], 0)

    def test_dashboard_defaults_to_dhaka_today(self):
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["as_of"], reference_date().isoformat())

    def test_stage_summary_keeps_correct_order(self):
        response = self.client.get("/api/pipeline/stages")
        self.assertEqual(response.status_code, 200)
        stages = response.json()
        self.assertEqual(
            [stage["stage"] for stage in stages],
            [stage.value for stage in PipelineStage],
        )
        self.assertEqual(sum(row["lead_count"] for row in stages), 25)

    def test_one_stage_total_and_bad_stage(self):
        response = self.client.get("/api/pipeline/stages/Proposal")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["lead_count"], 3)
        self.assertEqual(response.json()["total_value_bdt"], 1_620_000)
        self.assertEqual(response.json()["currency"], "BDT")
        self.assertEqual(
            self.client.get("/api/pipeline/stages/Negotiating").status_code,
            422,
        )

    def test_overdue_leads_and_summary(self):
        params = {"evaluation_mode": True}
        response = self.client.get("/api/pipeline/overdue", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        overdue = response.json()
        self.assertEqual(len(overdue), 9)
        ids = {lead["lead_id"] for lead in overdue}
        self.assertIn("L25", ids)  # Flagged even though its date order is wrong.
        self.assertIn("L22", ids)
        self.assertNotIn("L09", ids)  # Due on November 1 is not overdue.
        summary = self.client.get("/api/pipeline/overdue/summary", params=params)
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json(), {
            "as_of": "2026-11-01",
            "overdue_leads": 9,
            "overdue_value_bdt": 770_000,
        })

    def test_priority_leads_have_rank_and_safe_recommendation(self):
        response = self.client.get("/api/pipeline/priorities", params={
            "evaluation_mode": True,
            "limit": 5,
        })
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()
        self.assertEqual(len(items), 5)
        self.assertEqual(items[0]["lead_id"], "L22")
        self.assertEqual(items[0]["rank"], 1)
        self.assertIn("Check payment status", items[0]["recommended_action"])
        self.assertEqual(
            [item["priority_score"] for item in items],
            sorted((item["priority_score"] for item in items), reverse=True),
        )

    def test_bad_priority_limit_rejected(self):
        for limit in (0, 101, -3):
            self.assertEqual(
                self.client.get("/api/pipeline/priorities", params={"limit": limit}).status_code,
                422,
            )

    def test_priority_does_not_trust_L25_date(self):
        response = self.client.get("/api/pipeline/priorities", params={
            "evaluation_mode": True, "limit": 100,
        })
        self.assertEqual(response.status_code, 200, response.text)
        l25 = next(row for row in response.json() if row["lead_id"] == "L25")
        self.assertTrue(l25["requires_date_review"])
        self.assertEqual(l25["followup_status"], "invalid_date")
        self.assertIsNone(l25["days_overdue"])

    def test_weekly_summary_is_factual_json_not_generated_text(self):
        response = self.client.get("/api/pipeline/weekly-summary", params={
            "evaluation_mode": True,
            "limit": 3,
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["as_of"], "2026-11-01")
        self.assertEqual(result["overdue_active_leads"], 9)
        self.assertEqual(result["overdue_value_bdt"], 770_000)
        self.assertEqual(len(result["top_leads"]), 3)
        self.assertIn("Priority scores", result["note"])

    def test_dashboard_can_display_saved_issues(self):
        with Session(self.engine) as db:
            run_validation(db, as_of=reference_date(evaluation_mode=True))
            run_duplicate_scan(db)
            db.commit()
        result = self.client.get("/api/dashboard", params={"evaluation_mode": True})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["open_issue_count"], 48)
        self.assertEqual(result.json()["data_quality_flagged_leads"], 11)

    def test_merging_duplicates_updates_totals_without_removing_history(self):
        with Session(self.engine) as db:
            merge_leads(db, "L01", "L23", confirmed=True, reason="Reviewed same contact")
            merge_leads(db, "L02", "L03", confirmed=True, reason="Reviewed same contact")
            db.commit()
        result = self.client.get("/api/dashboard", params={"evaluation_mode": True})
        self.assertEqual(result.status_code, 200, result.text)
        stats = result.json()
        self.assertEqual(stats["all_lead_records"], 25)
        self.assertEqual(stats["current_leads"], 23)
        self.assertEqual(stats["total_value_bdt"], 3_170_000)
        proposal = self.client.get("/api/pipeline/stages/Proposal")
        self.assertEqual(proposal.status_code, 200)
        self.assertEqual(proposal.json()["total_value_bdt"], 920_000)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Lead)), 25)


if __name__ == "__main__":
    unittest.main()

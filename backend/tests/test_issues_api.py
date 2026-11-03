"""Step 14 API tests: scans, duplicate review, and issue status updates.

Run from backend/:
    python -m unittest discover -s tests -p "test_*.py" -v

Each test uses an in-memory database. Your real SQLite file is untouched.
"""

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import LEADS_CSV_PATH, reference_date
from app.database import Base, get_db
from app.main import app
from app.models import Lead, LeadActivity, LeadIssue
from app.services.csv_importer import prepare_lead
from app.services.lead_service import merge_leads


class IssuesAPITests(unittest.TestCase):
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
        frame = pd.read_csv(
            LEADS_CSV_PATH, dtype=str, keep_default_na=False,
            encoding="utf-8-sig",
        )
        with Session(self.engine) as db:
            db.add_all([prepare_lead(row) for row in frame.to_dict("records")])
            db.commit()

        self.startup_patch = patch(
            "app.main.create_tables",
            side_effect=lambda: Base.metadata.create_all(self.engine),
        )
        self.startup_patch.start()

        def test_db():
            with Session(self.engine, autoflush=False) as db:
                yield db

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.startup_patch.stop()
        self.engine.dispose()

    def scan(self):
        result = self.client.post(
            "/api/issues/scan", params={"evaluation_mode": True}
        )
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_docs_show_issues_endpoints(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in (
            "/api/issues", "/api/issues/summary", "/api/issues/scan",
            "/api/issues/duplicates", "/api/issues/{issue_id}",
            "/api/issues/{issue_id}/status",
        ):
            self.assertIn(path, paths)

    def test_reading_summary_does_not_run_scan(self):
        summary = self.client.get("/api/issues/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["total_issue_records"], 0)
        self.assertEqual(summary.json()["flagged_leads"], 0)
        self.assertEqual(self.client.get("/api/issues").json(), [])

    def test_preview_duplicate_pairs_is_read_only(self):
        response = self.client.get("/api/issues/duplicates")
        self.assertEqual(response.status_code, 200)
        pairs = {
            (row["lead_id_1"], row["lead_id_2"])
            for row in response.json()
        }
        self.assertEqual(pairs, {("L01", "L23"), ("L02", "L03")})
        self.assertEqual(self.client.get("/api/issues/summary").json()["open_issues"], 0)

    def test_full_scan_counts(self):
        result = self.scan()
        self.assertEqual(result["as_of"], "2026-11-01")
        self.assertEqual(result["scanned_leads"], 25)
        self.assertEqual(result["validation_issues_found"], 44)
        self.assertEqual(result["overdue_leads"], 9)
        self.assertEqual(result["duplicate_pairs"], 2)
        self.assertEqual(result["duplicate_leads_flagged"], 4)
        self.assertEqual(result["new_issues_saved"], 48)
        self.assertEqual(result["old_issues_resolved"], 0)
        self.assertEqual(result["open_issues"], 48)
        self.assertEqual(result["data_quality_flagged_leads"], 11)
        summary = self.client.get("/api/issues/summary").json()
        self.assertEqual(summary["flagged_leads"], 25)
        self.assertEqual(summary["open_by_rule"]["DUPLICATE_LEAD"], 4)
        self.assertEqual(summary["open_by_rule"]["MISSING_OWNER"], 25)
        self.assertEqual(summary["open_by_rule"]["INVALID_DATE_ORDER"], 1)
        self.assertEqual(sum(summary["open_by_severity"].values()), 48)

    def test_issue_list_filters_and_pagination(self):
        self.scan()
        response = self.client.get("/api/issues", params={
            "rule_code": "DUPLICATE_LEAD",
            "status": "open",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual({x["lead_id"] for x in response.json()},
                         {"L01", "L23", "L02", "L03"})
        bad_date = self.client.get("/api/issues", params={"lead_id": "L25", "rule_code": "INVALID_DATE_ORDER"})
        self.assertEqual(len(bad_date.json()), 1)
        self.assertEqual(bad_date.json()[0]["evidence"]["next_follow_up"], "2026-10-21")
        high = self.client.get("/api/issues", params={"severity": "high", "limit": 3, "offset": 2})
        self.assertEqual(len(high.json()), 3)
        self.assertTrue(all(x["severity"] == "high" for x in high.json()))
        self.assertEqual(self.client.get("/api/issues", params={"status": "all"}).status_code, 200)

    def test_get_one_issue_and_unknown_id(self):
        self.scan()
        issue = self.client.get("/api/issues", params={"rule_code": "INVALID_DATE_ORDER"}).json()[0]
        response = self.client.get(f"/api/issues/{issue['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lead_id"], "L25")
        self.assertEqual(self.client.get("/api/issues/99999").status_code, 404)

    def test_invalid_filters_and_review_data(self):
        self.assertEqual(self.client.get("/api/issues", params={"status": "waiting"}).status_code, 422)
        self.assertEqual(self.client.get("/api/issues", params={"severity": "critical"}).status_code, 422)
        self.assertEqual(self.client.get("/api/issues", params={"limit": 0}).status_code, 422)
        self.scan()
        issue_id = self.client.get("/api/issues", params={"rule_code": "VERIFY_EMAIL"}).json()[0]["id"]
        for body in ({"status": "dismissed", "reason": "   "},
                     {"status": "open", "reason": "Not allowed"},
                     {"status": "dismissed"}):
            response = self.client.patch(f"/api/issues/{issue_id}/status", json=body)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.patch("/api/issues/99999/status", json={
            "status": "dismissed", "reason": "No match"
        }).status_code, 404)

    def test_dismissal_keeps_reason_in_activity_history(self):
        self.scan()
        issue_id = self.client.get("/api/issues", params={"rule_code": "VERIFY_EMAIL"}).json()[0]["id"]
        response = self.client.patch(f"/api/issues/{issue_id}/status", json={
            "status": "dismissed", "reason": "Email checked by sales manager",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "dismissed")
        history = self.client.get("/api/leads/L18/activities").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["action_type"], "issue_reviewed")
        self.assertIn("Email checked by sales manager", history[0]["description"])
        self.assertEqual(history[0]["old_value"], "open")
        self.assertEqual(history[0]["new_value"], "dismissed")
        self.assertEqual(self.client.get("/api/issues", params={"status": "dismissed"}).json()[0]["id"], issue_id)
        # Same status again does not create a second audit event.
        self.assertEqual(self.client.patch(f"/api/issues/{issue_id}/status", json={
            "status": "dismissed", "reason": "Repeated request",
        }).status_code, 200)
        self.assertEqual(len(self.client.get("/api/leads/L18/activities").json()), 1)

    def test_dismissal_survives_unchanged_scan(self):
        self.scan()
        issue_id = self.client.get("/api/issues", params={"rule_code": "VERIFY_EMAIL"}).json()[0]["id"]
        self.client.patch(f"/api/issues/{issue_id}/status", json={
            "status": "dismissed", "reason": "Verified contact",
        })
        result = self.scan()
        self.assertEqual(result["new_issues_saved"], 0)
        self.assertEqual(result["open_issues"], 47)
        self.assertEqual(self.client.get(f"/api/issues/{issue_id}").json()["status"], "dismissed")

    def test_changed_evidence_reopens_dismissed_issue(self):
        self.scan()
        issue_id = self.client.get("/api/issues", params={"rule_code": "VERIFY_EMAIL"}).json()[0]["id"]
        self.client.patch(f"/api/issues/{issue_id}/status", json={
            "status": "dismissed", "reason": "Verified",
        })
        response = self.client.patch("/api/leads/L18", json={
            "contact_email": "another@example.com",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.scan()
        self.assertEqual(self.client.get(f"/api/issues/{issue_id}").json()["status"], "open")

    def test_resolved_issue_reopens_if_data_unchanged(self):
        self.scan()
        issue_id = self.client.get("/api/issues", params={"rule_code": "INVALID_DATE_ORDER"}).json()[0]["id"]
        response = self.client.patch(f"/api/issues/{issue_id}/status", json={
            "status": "resolved", "reason": "Marked for correction",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/issues/{issue_id}").json()["status"], "resolved")
        self.scan()  # L25 still has invalid dates.
        self.assertEqual(self.client.get(f"/api/issues/{issue_id}").json()["status"], "open")

    def test_date_correction_resolves_issues_on_next_scan(self):
        self.scan()
        response = self.client.patch("/api/leads/L25", json={
            "next_follow_up": "2026-11-05",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = self.scan()
        self.assertEqual(result["old_issues_resolved"], 2)
        self.assertEqual(result["open_issues"], 46)
        self.assertEqual(self.client.get("/api/issues", params={
            "lead_id": "L25", "rule_code": "INVALID_DATE_ORDER"
        }).json(), [])
        resolved = self.client.get("/api/issues", params={
            "lead_id": "L25", "status": "resolved"
        }).json()
        self.assertEqual({row["rule_code"] for row in resolved},
                         {"OVERDUE_FOLLOWUP", "INVALID_DATE_ORDER"})

    def test_repeating_scan_does_not_duplicate_warnings(self):
        self.scan()
        second = self.scan()
        self.assertEqual(second["new_issues_saved"], 0)
        self.assertEqual(second["open_issues"], 48)
        all_rows = self.client.get("/api/issues", params={"status": "all"}).json()
        self.assertEqual(len(all_rows), 48)

    def test_merges_resolve_duplicate_warnings_and_keep_history(self):
        self.scan()
        with Session(self.engine) as db:
            merge_leads(db, "L01", "L23", confirmed=True, reason="Verified")
            merge_leads(db, "L02", "L03", confirmed=True, reason="Verified")
            db.commit()
        self.scan()
        self.assertEqual(self.client.get("/api/issues/duplicates").json(), [])
        self.assertEqual(self.client.get("/api/issues", params={
            "rule_code": "DUPLICATE_LEAD"
        }).json(), [])
        resolved = self.client.get("/api/issues", params={
            "status": "resolved", "rule_code": "DUPLICATE_LEAD"
        }).json()
        self.assertEqual(len(resolved), 4)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Lead)), 25)
            self.assertEqual(db.scalar(select(func.count()).select_from(Lead).where(
                Lead.merged_into_id.is_(None)
            )), 23)

    def test_date_defaults_to_live_date(self):
        result = self.client.post("/api/issues/scan")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["as_of"], reference_date().isoformat())


if __name__ == "__main__":
    unittest.main()

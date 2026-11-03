"""Step 15 tests: read-only reports, archived evaluation, and CSV export.

Run from backend/: python -m unittest discover -s tests -p 'test_*.py' -v
The test database is in memory, not your real SQLite file.
"""

import csv
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import LEADS_CSV_PATH
from app.database import Base, get_db
from app.main import app
from app.models import ImportRow, Lead, LeadIssue
from app.services.csv_importer import prepare_lead
from app.services.lead_service import merge_leads, update_lead


class ReportsAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        with LEADS_CSV_PATH.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        with Session(self.engine) as db:
            for number, row in enumerate(rows, start=2):
                db.add(prepare_lead(row))
                db.add(ImportRow(
                    source_file=LEADS_CSV_PATH.name,
                    row_number=number,
                    original_lead_id=row["lead id"],
                    raw_data=row,
                ))
            db.commit()

        self.temp_dir = TemporaryDirectory()
        self.labels_file = Path(self.temp_dir.name) / "evaluation_labels.json"
        self.labels_patch = patch("app.api.reports.LABELS_PATH", self.labels_file)
        self.labels_patch.start()
        self.startup_patch = patch("app.main.create_tables", return_value=None)
        self.startup_patch.start()

        def test_db():
            with Session(self.engine, autoflush=False) as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.startup_patch.stop()
        self.labels_patch.stop()
        self.temp_dir.cleanup()
        self.engine.dispose()

    def evaluate(self):
        response = self.client.get("/api/reports/evaluation")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def export_rows(self, *, include_merged=False):
        response = self.client.get("/api/reports/export.csv", params={
            "include_merged": include_merged,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig")))), response

    def test_docs_show_report_routes(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        for route in (
            "/api/reports/summary",
            "/api/reports/evaluation",
            "/api/reports/export.csv",
        ):
            self.assertIn(route, paths)

    def test_summary_uses_fixed_date_and_current_data(self):
        response = self.client.get("/api/reports/summary", params={
            "evaluation_mode": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["as_of"], "2026-11-01")
        self.assertEqual(data["dashboard"]["current_leads"], 25)
        self.assertEqual(data["dashboard"]["total_value_bdt"], 4_220_000)
        self.assertEqual(data["dashboard"]["overdue_leads"], 9)
        self.assertEqual(data["pending_duplicate_pairs"], 2)
        self.assertEqual(data["merged_records"], 0)
        self.assertEqual(data["issues"]["open_issues"], 0)

    def test_summary_displays_saved_issues_after_scan(self):
        scan = self.client.post("/api/issues/scan", params={
            "evaluation_mode": True,
        })
        self.assertEqual(scan.status_code, 200, scan.text)
        summary = self.client.get("/api/reports/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["issues"]["open_issues"], 48)
        self.assertEqual(summary.json()["issues"]["data_quality_flagged_leads"], 11)

    def test_baseline_evaluation_independent_of_a_saved_scan(self):
        data = self.evaluate()
        self.assertEqual(data["as_of"], "2026-11-01")
        self.assertEqual(data["original_leads"], 25)
        self.assertEqual(data["source_data_flagged_leads"], 11)
        self.assertEqual(data["overdue_leads"], 9)
        self.assertEqual(data["counts_by_rule"]["DUPLICATE_LEAD"], 4)
        self.assertEqual(data["counts_by_rule"]["MISSING_OWNER"], 25)
        self.assertEqual(data["invalid_date_order_leads"], ["L25"])
        self.assertEqual(data["verification_status"], "awaiting_review")
        self.assertIsNone(data["metrics"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(LeadIssue)), 0)

    def test_two_duplicate_pairs_are_grounded_in_archive(self):
        pairs = {
            frozenset((pair["lead_id_1"], pair["lead_id_2"]))
            for pair in self.evaluate()["duplicate_pairs"]
        }
        self.assertEqual(pairs, {
            frozenset(("L01", "L23")),
            frozenset(("L02", "L03")),
        })

    def test_original_evaluation_does_not_change_after_edits_and_merges(self):
        with Session(self.engine) as db:
            update_lead(db, "L25", next_follow_up="2026-11-06")
            merge_leads(db, "L01", "L23", confirmed=True, reason="Reviewed")
            merge_leads(db, "L02", "L03", confirmed=True, reason="Reviewed")
            db.commit()

        report = self.evaluate()
        self.assertEqual(report["original_leads"], 25)
        self.assertEqual(report["source_data_flagged_leads"], 11)
        self.assertEqual(report["invalid_date_order_leads"], ["L25"])
        summary = self.client.get("/api/reports/summary").json()
        self.assertEqual(summary["dashboard"]["current_leads"], 23)
        self.assertEqual(summary["dashboard"]["total_value_bdt"], 3_170_000)
        self.assertEqual(summary["merged_records"], 2)
        self.assertEqual(summary["pending_duplicate_pairs"], 0)

    def test_archive_is_required_for_reference_evaluation(self):
        with Session(self.engine) as db:
            db.query(ImportRow).delete()
            db.commit()
        response = self.client.get("/api/reports/evaluation")
        self.assertEqual(response.status_code, 404)
        self.assertIn("archive", response.json()["detail"].lower())

    def test_partial_manual_labels_do_not_claim_full_accuracy(self):
        self.labels_file.write_text(json.dumps({
            "labels": {"L01": True, "L02": None}
        }), encoding="utf-8")
        report = self.evaluate()
        self.assertEqual(report["verification_status"], "partial")
        self.assertEqual(report["manually_reviewed_leads"], 1)
        self.assertIsNone(report["metrics"])

    def test_complete_manual_labels_produce_correct_confusion_matrix(self):
        # Illustrative independent reviewer labels for testing the math.
        # Intentionally different from scanner predictions.
        labels = {f"L{i:02}": False for i in range(1, 26)}
        labels["L01"] = True
        labels["L05"] = True
        self.labels_file.write_text(json.dumps({"labels": labels}), encoding="utf-8")
        report = self.evaluate()
        self.assertEqual(report["verification_status"], "complete")
        self.assertEqual(report["manually_reviewed_leads"], 25)
        metrics = report["metrics"]
        self.assertEqual(metrics["true_positives"], 1)
        self.assertEqual(metrics["false_positives"], 10)
        self.assertEqual(metrics["false_negatives"], 1)
        self.assertEqual(metrics["true_negatives"], 13)
        self.assertEqual(metrics["correctly_classified_leads"], 14)
        self.assertAlmostEqual(metrics["accuracy"], 14 / 25)
        self.assertAlmostEqual(metrics["precision"], 1 / 11)
        self.assertAlmostEqual(metrics["recall"], 1 / 2)

    def test_invalid_labels_are_rejected(self):
        for labels in ({"L99": True}, {"L01": "yes"}, {"L01": 1}):
            with self.subTest(labels=labels):
                self.labels_file.write_text(json.dumps({"labels": labels}), encoding="utf-8")
                response = self.client.get("/api/reports/evaluation")
                self.assertEqual(response.status_code, 422)

    def test_csv_export_has_correct_headers_and_records(self):
        rows, response = self.export_rows()
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(len(rows), 25)
        self.assertEqual(rows[0]["lead_id"], "L01")
        self.assertEqual(rows[0]["stage"], "Meeting")
        self.assertEqual(rows[0]["deal_value_bdt"], "350000")
        self.assertIn("contact_phone", rows[0])
        self.assertTrue(rows[0]["contact_phone"].startswith("'017"))
        self.assertIn("open_issue_codes", rows[0])

    def test_export_includes_saved_issue_codes(self):
        self.client.post("/api/issues/scan", params={"evaluation_mode": True})
        rows, _ = self.export_rows()
        by_id = {row["lead_id"]: row for row in rows}
        self.assertIn("INVALID_DATE_ORDER", by_id["L25"]["open_issue_codes"])
        self.assertIn("DUPLICATE_LEAD", by_id["L01"]["open_issue_codes"])
        self.assertEqual(by_id["L05"]["merged_into_id"], "")

    def test_merged_export_is_optional(self):
        with Session(self.engine) as db:
            merge_leads(db, "L01", "L23", confirmed=True, reason="Checked")
            merge_leads(db, "L02", "L03", confirmed=True, reason="Checked")
            db.commit()
        current, _ = self.export_rows()
        all_rows, _ = self.export_rows(include_merged=True)
        self.assertEqual(len(current), 23)
        self.assertEqual(len(all_rows), 25)
        by_id = {row["lead_id"]: row for row in all_rows}
        self.assertEqual(by_id["L23"]["merged_into_id"], "L01")
        self.assertEqual(by_id["L03"]["merged_into_id"], "L02")

    def test_csv_escape_guards_against_spreadsheet_formula_injection(self):
        with Session(self.engine) as db:
            lead = db.get(Lead, "L01")
            lead.company = "=HYPERLINK(\"https://example.org\",\"click\")"
            lead.notes = "  +2+2"
            db.commit()
        rows, _ = self.export_rows()
        self.assertEqual(rows[0]["company"][0], "'")
        self.assertEqual(rows[0]["notes"][0], "'")

    def test_current_csv_includes_new_leads_not_original_archive(self):
        with Session(self.engine) as db:
            db.add(Lead(lead_id="L26", company="Fresh Training Lead"))
            db.commit()
        rows, _ = self.export_rows()
        self.assertEqual(len(rows), 26)
        report = self.evaluate()
        self.assertEqual(report["original_leads"], 25)

    def test_read_only_report_endpoints_do_not_write_to_sqlite(self):
        with Session(self.engine) as db:
            before = db.scalar(select(func.count()).select_from(LeadIssue))
        self.client.get("/api/reports/summary")
        self.client.get("/api/reports/evaluation")
        self.client.get("/api/reports/export.csv")
        with Session(self.engine) as db:
            after = db.scalar(select(func.count()).select_from(LeadIssue))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

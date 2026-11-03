"""Step 12 tests for lead and owner FastAPI endpoints.

Run from backend/: python -m unittest discover -s tests -p "test_*.py" -v
Every test uses a separate in-memory SQLite database, not storage/biai_pipeline.db.
"""

import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import LEADS_CSV_PATH
from app.database import Base, get_db
from app.main import app
from app.models import Lead, LeadActivity, MergeHistory
from app.services.csv_importer import prepare_lead


class LeadAPITests(unittest.TestCase):
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
        # Import 25 fictional leads into the test database only.
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

    def test_docs_list_new_routes(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for route in ["/api/leads", "/api/leads/{lead_id}",
                      "/api/leads/{lead_id}/stage", "/api/leads/merge",
                      "/api/owners"]:
            self.assertIn(route, paths)

    def test_list_and_get_original_leads(self):
        result = self.client.get("/api/leads")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()), 25)
        lead = self.client.get("/api/leads/L25")
        self.assertEqual(lead.status_code, 200)
        self.assertEqual(lead.json()["next_follow_up"], "2026-10-21")
        self.assertEqual(lead.json()["stage"], "Contacted")
        self.assertIn("updated_at", lead.json())

    def test_filters_and_pagination(self):
        result = self.client.get("/api/leads", params={
            "stage": "Proposal", "limit": 2, "offset": 1,
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()), 2)
        self.assertTrue(all(row["stage"] == "Proposal" for row in result.json()))
        self.assertEqual(len(self.client.get("/api/leads", params={"company": "Meghna"}).json()), 2)
        self.assertEqual(len(self.client.get("/api/leads", params={"unassigned_only": True}).json()), 25)

    def test_invalid_filters(self):
        self.assertEqual(self.client.get("/api/leads", params={"stage": "Negotiating"}).status_code, 422)
        self.assertEqual(self.client.get("/api/leads", params={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.get("/api/leads", params={"owner_id": 1, "unassigned_only": True}).status_code, 400)

    def test_unknown_lead(self):
        self.assertEqual(self.client.get("/api/leads/UNKNOWN").status_code, 404)
        self.assertEqual(self.client.patch("/api/leads/UNKNOWN/stage", json={"stage": "Meeting"}).status_code, 404)
        self.assertEqual(self.client.get("/api/leads/UNKNOWN/activities").status_code, 404)

    def test_create_lead_and_activity(self):
        response = self.client.post("/api/leads", json={
            "lead_id": "L26", "company": "Test School",
            "stage": "New", "deal_value_bdt": 50000,
            "next_follow_up": "2026-11-10",
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["deal_value_bdt"], 50000)
        self.assertEqual(len(self.client.get("/api/leads").json()), 26)
        history = self.client.get("/api/leads/L26/activities").json()
        self.assertEqual(history[0]["action_type"], "lead_created")
        self.assertEqual(self.client.post("/api/leads", json={
            "lead_id": "L26", "company": "Duplicate ID",
        }).status_code, 409)

    def test_create_lead_invalid_values(self):
        self.assertEqual(self.client.post("/api/leads", json={
            "lead_id": "L26", "company": "Test", "deal_value_bdt": -2,
        }).status_code, 422)
        self.assertEqual(self.client.post("/api/leads", json={
            "lead_id": "L26", "company": "Test", "next_follow_up": "11/10/2026",
        }).status_code, 422)
        self.assertEqual(self.client.post("/api/leads", json={
            "lead_id": "L26", "company": "Test", "made_up_field": 1,
        }).status_code, 422)

    def test_patch_changes_only_supplied_fields_and_records_history(self):
        before = self.client.get("/api/leads/L25").json()
        response = self.client.patch("/api/leads/L25", json={
            "next_follow_up": "2026-11-05", "contact_phone": None,
        })
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["next_follow_up"], "2026-11-05")
        self.assertIsNone(updated["contact_phone"])
        self.assertEqual(updated["company"], before["company"])
        self.assertEqual(updated["stage"], before["stage"])
        self.assertEqual(len(self.client.get("/api/leads/L25/activities").json()), 2)

    def test_patch_invalid_data_is_not_saved(self):
        before = self.client.get("/api/leads/L25").json()
        response = self.client.patch("/api/leads/L25", json={
            "company": "Changed", "deal_value_bdt": -5,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/leads/L25").json()["company"], before["company"])
        self.assertEqual(self.client.patch("/api/leads/L25", json={"company": None}).status_code, 422)
        self.assertEqual(self.client.patch("/api/leads/L25", json={"merged_into_id": "L01"}).status_code, 422)

    def test_stage_change_and_activity(self):
        response = self.client.patch("/api/leads/L04/stage", json={
            "stage": "Meeting", "reason": "Call completed",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stage"], "Meeting")
        self.assertIsNotNone(response.json()["stage_changed_at"])
        history = self.client.get("/api/leads/L04/activities").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["old_value"], "Contacted")
        self.assertEqual(history[0]["description"], "Call completed")
        self.assertEqual(self.client.patch("/api/leads/L04/stage", json={"stage": "Wrong"}).status_code, 422)

    def test_create_and_assign_owner(self):
        response = self.client.post("/api/owners", json={
            "name": "Nadia", "email": "NADIA@example.com",
        })
        self.assertEqual(response.status_code, 201, response.text)
        owner_id = response.json()["id"]
        self.assertEqual(response.json()["email"], "nadia@example.com")
        self.assertEqual(len(self.client.get("/api/owners").json()), 1)
        assign = self.client.patch("/api/leads/L04/owner", json={"owner_id": owner_id})
        self.assertEqual(assign.status_code, 200)
        self.assertEqual(assign.json()["owner_id"], owner_id)
        self.assertEqual(len(self.client.get("/api/leads", params={"owner_id": owner_id}).json()), 1)
        remove = self.client.patch("/api/leads/L04/owner", json={"owner_id": None})
        self.assertEqual(remove.status_code, 200)
        self.assertIsNone(remove.json()["owner_id"])

    def test_owner_errors(self):
        self.assertEqual(self.client.patch("/api/leads/L04/owner", json={"owner_id": 999}).status_code, 400)
        self.assertEqual(self.client.patch("/api/leads/L04/owner", json={"owner_id": -1}).status_code, 422)
        self.client.post("/api/owners", json={"name": "Nadia", "email": "nadia@example.com"})
        self.assertEqual(self.client.post("/api/owners", json={
            "name": "Other", "email": "nadia@example.com",
        }).status_code, 409)

    def test_merge_requires_confirmation_and_matching_pair(self):
        no_confirmation = self.client.post("/api/leads/merge", json={
            "surviving_id": "L01", "duplicate_id": "L23",
            "confirmed": False, "reason": "Same contact",
        })
        self.assertEqual(no_confirmation.status_code, 422)
        mismatch = self.client.post("/api/leads/merge", json={
            "surviving_id": "L01", "duplicate_id": "L05",
            "confirmed": True, "reason": "Mistaken match",
        })
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(len(self.client.get("/api/leads").json()), 25)

    def test_confirmed_merge_preserves_history_and_original_rows(self):
        result = self.client.post("/api/leads/merge", json={
            "surviving_id": "L01", "duplicate_id": "L23",
            "confirmed": True, "reason": "Verified same contact",
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.client.get("/api/leads").json()), 24)
        self.assertEqual(len(self.client.get("/api/leads", params={"include_merged": True}).json()), 25)
        self.assertEqual(self.client.get("/api/leads/L23").status_code, 404)
        merged = self.client.get("/api/leads/L23", params={"include_merged": True})
        self.assertEqual(merged.json()["merged_into_id"], "L01")
        self.assertEqual(len(self.client.get("/api/leads/L23/activities").json()), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Lead)), 25)
            self.assertEqual(db.scalar(select(func.count()).select_from(MergeHistory)), 1)

    def test_merge_two_groups_yields_23_current_leads(self):
        for surviving_id, duplicate_id in [("L01", "L23"), ("L02", "L03")]:
            result = self.client.post("/api/leads/merge", json={
                "surviving_id": surviving_id, "duplicate_id": duplicate_id,
                "confirmed": True, "reason": "Reviewed matching contact",
            })
            self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.client.get("/api/leads").json()), 23)
        proposals = self.client.get("/api/leads", params={"stage": "Proposal"})
        self.assertEqual(len(proposals.json()), 2)


if __name__ == "__main__":
    unittest.main()

"""Step 7 tests. Run from backend/: python -m unittest discover -s tests -v"""

import unittest
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.config import EVALUATION_DATE, LEADS_CSV_PATH, PipelineStage
from app.database import Base
from app.models import Lead, LeadActivity, LeadIssue, MergeHistory, Owner
from app.services.csv_importer import prepare_lead
from app.services.duplicates import run_duplicate_scan
from app.services.lead_service import (
    LeadConflictError,
    LeadNotFoundError,
    assign_owner,
    create_lead,
    create_owner,
    get_lead,
    list_leads,
    merge_leads,
    update_lead,
    update_stage,
)
from app.services.validation import get_overdue_leads, run_validation


class LeadServiceTests(unittest.TestCase):
    def setUp(self):
        # Keep tests independent of the actual storage/biai_pipeline.db file.
        self.engine = create_engine("sqlite+pysqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

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

    def test_list_and_filter(self):
        self.assertEqual(len(list_leads(self.db)), 25)
        self.assertEqual(len(list_leads(self.db, stage="Proposal")), 3)
        self.assertEqual([lead.lead_id for lead in list_leads(self.db, company="Meghna")], ["L02", "L03"])
        self.assertEqual(len(list_leads(self.db, industry="pharma")), 3)
        self.assertEqual(len(list_leads(self.db, unassigned_only=True)), 25)
        self.assertEqual(len(list_leads(self.db, limit=5, offset=5)), 5)
        self.assertEqual(list_leads(self.db, company="%"), [])
        self.assertEqual(list_leads(self.db, company="_"), [])

    def test_get_lead_and_unknown_id(self):
        self.assertEqual(get_lead(self.db, "L25").company, "Sylhet Hospitality Group")
        with self.assertRaises(LeadNotFoundError):
            get_lead(self.db, "NOT_HERE")

    def test_create_lead_keeps_its_values(self):
        new = create_lead(
            self.db, "L26", "New Training Client", industry="Education",
            deal_value_bdt=0, next_follow_up="2026-11-07",
        )
        self.db.commit()
        self.assertEqual(new.stage, PipelineStage.NEW)
        self.assertEqual(new.next_follow_up, date(2026, 11, 7))
        self.assertEqual(len(list_leads(self.db)), 26)
        activity = self.db.scalars(select(LeadActivity).where(LeadActivity.lead_id == "L26")).one()
        self.assertEqual(activity.action_type, "lead_created")

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            update_lead(self.db, "L25", deal_value_bdt=-1)
        with self.assertRaises(ValueError):
            update_stage(self.db, "L25", "Negotiating")
        with self.assertRaises(ValueError):
            update_lead(self.db, "L25", next_follow_up="11/05/2026")
        with self.assertRaises(ValueError):
            update_lead(self.db, "L25", merged_into_id="L24")
        with self.assertRaises(ValueError):
            update_lead(self.db, "L25", company="  ")
        with self.assertRaises(ValueError):
            assign_owner(self.db, "L25", 99999)
        with self.assertRaises(ValueError):
            list_leads(self.db, limit=0)


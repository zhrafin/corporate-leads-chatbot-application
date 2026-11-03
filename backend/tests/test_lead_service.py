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
        self.assertEqual(get_lead(self.db, "L25").deal_value_bdt, 0)

    def test_assign_owner_and_stage_history(self):
        owner = create_owner(self.db, "Nadia", "NADIA@example.com")
        assign_owner(self.db, "L04", owner.id)
        update_stage(self.db, "L04", "Meeting", reason="Completed customer call")
        self.db.commit()
        lead = get_lead(self.db, "L04")
        self.assertEqual(lead.owner_id, owner.id)
        self.assertEqual(lead.stage, PipelineStage.MEETING)
        self.assertIsNotNone(lead.stage_changed_at)
        self.assertEqual(len(list_leads(self.db, owner_id=owner.id)), 1)
        self.assertEqual(len(list_leads(self.db, unassigned_only=True)), 24)
        history = self.db.scalars(select(LeadActivity).where(LeadActivity.lead_id == "L04")).all()
        self.assertEqual([a.action_type for a in history], ["owner_changed", "stage_changed"])
        self.assertEqual(history[-1].old_value, "Contacted")
        self.assertEqual(history[-1].new_value, "Meeting")
        with self.assertRaises(LeadConflictError):
            create_owner(self.db, "Other", "nadia@example.com")

    def test_update_corrects_l25_and_keeps_history(self):
        run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        update_lead(self.db, "L25", next_follow_up="2026-11-05", contact_phone=" 01712345678 ")
        self.db.commit()
        lead = get_lead(self.db, "L25")
        self.assertEqual(lead.next_follow_up, date(2026, 11, 5))
        self.assertEqual(lead.contact_phone, "01712345678")
        actions = self.db.scalars(select(LeadActivity).where(LeadActivity.lead_id == "L25")).all()
        self.assertEqual(len(actions), 2)
        self.assertNotIn("L25", {lead.lead_id for lead in get_overdue_leads(self.db, EVALUATION_DATE)})
        result = run_validation(self.db, EVALUATION_DATE)
        self.db.commit()
        self.assertEqual(result["old_issues_resolved"], 2)

    def test_no_extra_activity_for_unchanged_values(self):
        update_lead(self.db, "L04", stage="Contacted", company="Karnaphuli Foods")
        self.db.commit()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(LeadActivity)), 0)

    def test_merge_requires_user_confirmation(self):
        with self.assertRaises(ValueError):
            merge_leads(self.db, "L01", "L23", reason="Same contact")
        with self.assertRaises(ValueError):
            merge_leads(self.db, "L01", "L23", confirmed=True, reason=" ")
        with self.assertRaises(ValueError):
            merge_leads(self.db, "L01", "L01", confirmed=True, reason="Same")
        with self.assertRaises(LeadConflictError):
            merge_leads(self.db, "L01", "L05", confirmed=True, reason="Mistake")
        self.assertEqual(get_lead(self.db, "L23").merged_into_id, None)

    def test_merge_preserves_records_and_resolves_duplicate_flags(self):
        run_duplicate_scan(self.db)
        self.db.commit()
        merge_leads(self.db, "L01", "L23", confirmed=True, reason="Contact verified")
        self.db.commit()
        self.assertEqual(len(list_leads(self.db)), 24)
        self.assertEqual(len(list_leads(self.db, include_merged=True)), 25)
        self.assertEqual(get_lead(self.db, "L23", include_merged=True).merged_into_id, "L01")
        with self.assertRaises(LeadNotFoundError):
            get_lead(self.db, "L23")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Lead)), 25)
        record = self.db.scalars(select(MergeHistory)).one()
        self.assertEqual((record.duplicate_lead_id, record.surviving_lead_id), ("L23", "L01"))
        pair_issues = self.db.scalars(select(LeadIssue).where(
            LeadIssue.lead_id.in_(["L01", "L23"]),
            LeadIssue.rule_code == "DUPLICATE_LEAD"
        )).all()
        self.assertEqual({issue.status for issue in pair_issues}, {"resolved"})
        with self.assertRaises(LeadNotFoundError):
            update_lead(self.db, "L23", industry="Textiles")
        with self.assertRaises(LeadNotFoundError):
            merge_leads(self.db, "L02", "L23", confirmed=True, reason="Already merged")

    def test_merge_both_known_groups(self):
        merge_leads(self.db, "L01", "L23", confirmed=True, reason="Same contact")
        merge_leads(self.db, "L02", "L03", confirmed=True, reason="Same contact")
        self.db.commit()
        self.assertEqual(len(list_leads(self.db)), 23)
        self.assertEqual(len(list_leads(self.db, include_merged=True)), 25)
        self.assertEqual(len(list_leads(self.db, stage="Proposal")), 2)


if __name__ == "__main__":
    unittest.main()

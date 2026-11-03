"""Step 10: Pydantic API schema tests. Run from backend/:

    python -m unittest discover -s tests -p "test_*.py" -v
"""

import unittest
from datetime import date

from pydantic import ValidationError

from app.config import EVALUATION_DATE, PipelineStage
from app.models import Lead
from app.schemas import (
    AssignOwner,
    ChatRequest,
    DashboardRead,
    EmailDraftRequest,
    IssueStatusUpdate,
    LeadCreate,
    LeadRead,
    LeadUpdate,
    MergeLeads,
    OwnerCreate,
    StageTotalRead,
    StageUpdate,
)
from app.services.csv_importer import prepare_lead
from app.services.analytics import get_dashboard_stats, get_stage_total
from app.database import Base

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


class SchemaTests(unittest.TestCase):
    def test_create_lead_defaults_and_whitespace(self):
        record = LeadCreate(lead_id=" L26 ", company=" Demo Company ")
        self.assertEqual(record.lead_id, "L26")
        self.assertEqual(record.company, "Demo Company")
        self.assertEqual(record.stage, PipelineStage.NEW)
        self.assertEqual(record.deal_value_bdt, None)

    def test_valid_create_lead_date_and_phone(self):
        record = LeadCreate(
            lead_id="L26", company="New Client", deal_value_bdt=100000,
            contact_phone="01701-000001", next_follow_up="2026-11-03"
        )
        self.assertEqual(record.next_follow_up, date(2026, 11, 3))
        self.assertEqual(record.contact_phone, "01701-000001")

    def test_negative_decimal_and_string_money_rejected(self):
        for amount in (-1, 1.5, "100000", True):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                LeadCreate(lead_id="L26", company="Test", deal_value_bdt=amount)

    def test_company_and_unknown_fields_rejected(self):
        for data in (
            {"lead_id": "L26", "company": "  "},
            {"lead_id": "L26", "company": "Valid", "merged_into_id": "L01"},
        ):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                LeadCreate(**data)

    def test_patch_cannot_clear_required_company_or_stage(self):
        for changes in ({"company": None}, {"stage": None}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                LeadUpdate(**changes)

    def test_stage_and_date_formats_rejected(self):
        with self.assertRaises(ValidationError):
            StageUpdate(stage="Negotiating")
        with self.assertRaises(ValidationError):
            LeadUpdate(next_follow_up="11/03/2026")
        with self.assertRaises(ValidationError):
            LeadUpdate(last_contact=1730000000)

    def test_update_distinguishes_absent_from_null(self):
        unchanged = LeadUpdate(company="Updated Company")
        self.assertEqual(unchanged.model_dump(exclude_unset=True), {"company": "Updated Company"})
        clear = LeadUpdate(owner_id=None, next_follow_up=None)
        self.assertEqual(clear.model_dump(exclude_unset=True), {
            "owner_id": None, "next_follow_up": None
        })

    def test_stage_and_owner_requests(self):
        self.assertEqual(StageUpdate(stage="Proposal").stage, PipelineStage.PROPOSAL)
        self.assertIsNone(AssignOwner(owner_id=None).owner_id)
        with self.assertRaises(ValidationError):
            AssignOwner(owner_id=0)
        self.assertEqual(OwnerCreate(name=" Farhan ").name, "Farhan")

    def test_merge_requires_explicit_confirmation(self):
        MergeLeads(surviving_id="L01", duplicate_id="L23", confirmed=True, reason="Same contact")
        for value in (False, None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MergeLeads(surviving_id="L01", duplicate_id="L23", confirmed=value, reason="Same contact")

    def test_issue_resolution_needs_reason(self):
        with self.assertRaises(ValidationError):
            IssueStatusUpdate(status="dismissed", reason=" ")
        self.assertEqual(IssueStatusUpdate(status="resolved", reason="Corrected record").status, "resolved")

    def test_chat_and_email_requests(self):
        self.assertEqual(ChatRequest(message=" Which deals are overdue? ").message, "Which deals are overdue?")
        with self.assertRaises(ValidationError):
            ChatRequest(message="")
        with self.assertRaises(ValidationError):
            EmailDraftRequest(lead_id=" ")

    def test_read_lead_from_sqlalchemy_model(self):
        lead = Lead(lead_id="L26", company="Example", stage=PipelineStage.NEW,
                    contact_phone="01701-000001")
        # SQLite normally assigns the update timestamp. Set it for this object.
        from datetime import datetime
        lead.updated_at = datetime(2026, 11, 3, 9, 0)
        result = LeadRead.model_validate(lead)
        self.assertEqual(result.company, "Example")
        self.assertEqual(result.stage.value, "New")
        self.assertEqual(result.contact_phone, "01701-000001")

    def test_reports_match_actual_service_results(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                result = get_dashboard_stats(db, EVALUATION_DATE)
                parsed = DashboardRead.model_validate(result)
                self.assertEqual(len(parsed.stage_summary), 7)
                self.assertEqual(parsed.current_leads, 0)
                stage = StageTotalRead.model_validate(get_stage_total(db, "Proposal"))
                self.assertEqual(stage.currency, "BDT")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

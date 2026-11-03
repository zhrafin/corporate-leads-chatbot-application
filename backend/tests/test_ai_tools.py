"""Step 16 tests. No live Groq API or changes to the real database.

Run from backend/:
    python -m unittest discover -s tests -p 'test_ai_tools.py' -v
"""

import importlib.util
import unittest

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.tools import SalesReadService, build_sales_tools
from app.config import LEADS_CSV_PATH
from app.database import Base
from app.models import Lead, LeadIssue
from app.services.csv_importer import prepare_lead
from app.services.duplicates import run_duplicate_scan
from app.services.lead_service import merge_leads
from app.services.validation import run_validation


class SalesAIToolTests(unittest.TestCase):
    def setUp(self):
        # A separate in-memory database ensures the real CRM never changes.
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.make_session = sessionmaker(bind=self.engine, autoflush=False)

        frame = pd.read_csv(
            LEADS_CSV_PATH,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        with self.make_session.begin() as db:
            db.add_all([prepare_lead(row) for row in frame.to_dict("records")])

        self.reader = SalesReadService(self.make_session, evaluation_mode=True)

    def tearDown(self):
        self.engine.dispose()

    def test_stage_total_is_from_sqlite(self):
        amount = self.reader.get_stage_total("Proposal")
        self.assertEqual(amount["total_value_bdt"], 1_620_000)
        self.assertEqual(amount["lead_count"], 3)
        self.assertEqual(amount["currency"], "BDT")

    def test_invalid_stage_returns_helpful_error(self):
        result = self.reader.get_stage_total("Negotiating")
        self.assertIn("error", result)
        self.assertIn("Invoice sent", result["allowed_stages"])

    def test_pipeline_summary_is_correct(self):
        result = self.reader.get_pipeline_summary()
        self.assertEqual(result["current_leads"], 25)
        self.assertEqual(result["overdue_leads"], 9)
        self.assertEqual(result["total_value_bdt"], 4_220_000)
        self.assertEqual(result["as_of"], "2026-11-01")

    def test_search_leads_by_stage(self):
        result = self.reader.search_leads(stage="Proposal")
        self.assertEqual(result["returned_count"], 3)
        self.assertEqual({lead["lead_id"] for lead in result["leads"]}, {"L02", "L03", "L14"})

    def test_search_leads_by_company(self):
        result = self.reader.search_leads(company="Meghna")
        self.assertEqual(result["returned_count"], 2)

    def test_get_lead_details_includes_notes(self):
        result = self.reader.get_lead_details("l25")
        self.assertEqual(result["lead_id"], "L25")
        self.assertIn("notes", result)
        self.assertEqual(result["next_follow_up"], "2026-10-21")

    def test_unknown_lead_has_error(self):
        result = self.reader.get_lead_details("L999")
        self.assertIn("error", result)

    def test_overdue_has_nine_active_deals(self):
        result = self.reader.get_overdue_leads()
        self.assertEqual(result["overdue_count"], 9)
        self.assertIn("L25", {lead["lead_id"] for lead in result["leads"]})
        self.assertEqual(result["as_of"], "2026-11-01")

    def test_limit_does_not_change_total_count(self):
        result = self.reader.get_overdue_leads(limit=2)
        self.assertEqual(result["overdue_count"], 9)
        self.assertEqual(result["returned_count"], 2)

    def test_duplicate_candidates_are_two_pairs(self):
        result = self.reader.get_duplicate_candidates()
        self.assertEqual(result["pair_count"], 2)
        pairs = {(item["lead_id_1"], item["lead_id_2"]) for item in result["pairs"]}
        self.assertEqual(pairs, {("L01", "L23"), ("L02", "L03")})

    def test_issues_report_only_saved_scans(self):
        before = self.reader.get_data_quality_issues()
        self.assertEqual(before["returned_count"], 0)
        with self.make_session.begin() as db:
            run_validation(db, self.reader._today())
            run_duplicate_scan(db)
        after = self.reader.get_data_quality_issues(rule_code="INVALID_DATE_ORDER")
        self.assertEqual(after["returned_count"], 1)
        self.assertEqual(after["issues"][0]["lead_id"], "L25")

    def test_priority_leads_have_grounded_actions(self):
        result = self.reader.get_priority_leads(limit=5)
        self.assertEqual(result["lead_count"], 5)
        self.assertEqual(result["leads"][0]["lead_id"], "L22")
        self.assertIn("payment", result["leads"][0]["recommended_action"].lower())

    def test_note_search_uses_actual_notes(self):
        result = self.reader.search_lead_notes("budget")
        self.assertTrue(result["returned_count"] > 0)
        self.assertTrue(all("budget" in row["notes"].lower() for row in result["matches"]))

    def test_note_search_rejects_wildcards_as_all_records(self):
        result = self.reader.search_lead_notes("%%")
        self.assertEqual(result["returned_count"], 0)

    def test_weekly_summary_has_real_values(self):
        result = self.reader.get_weekly_priority_data(limit=3)
        self.assertEqual(result["as_of"], "2026-11-01")
        self.assertEqual(result["overdue_active_leads"], 9)
        self.assertEqual(len(result["top_leads"]), 3)

    def test_after_merges_no_double_counting(self):
        with self.make_session.begin() as db:
            merge_leads(db, "L01", "L23", confirmed=True, reason="Reviewed duplicate")
            merge_leads(db, "L02", "L03", confirmed=True, reason="Reviewed duplicate")
        self.assertEqual(self.reader.get_stage_total("Proposal")["total_value_bdt"], 920_000)
        self.assertEqual(self.reader.get_pipeline_summary()["current_leads"], 23)
        self.assertEqual(self.reader.get_duplicate_candidates()["pair_count"], 0)

    def test_all_tools_are_read_only(self):
        # Read operations must never insert activity or warning records.
        with self.make_session() as db:
            initial_leads = db.scalar(select(func.count()).select_from(Lead))
            initial_issues = db.scalar(select(func.count()).select_from(LeadIssue))

        self.reader.get_pipeline_summary()
        self.reader.get_overdue_leads()
        self.reader.get_duplicate_candidates()
        self.reader.get_lead_details("L12")
        self.reader.search_lead_notes("meeting")

        with self.make_session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Lead)), initial_leads)
            self.assertEqual(db.scalar(select(func.count()).select_from(LeadIssue)), initial_issues)

    @unittest.skipUnless(
        importlib.util.find_spec("langchain_core") is not None,
        "Install langchain-core to verify the actual LangChain wrappers",
    )
    def test_langchain_tool_objects(self):
        from langchain_core.tools import BaseTool

        tools = build_sales_tools(self.make_session, evaluation_mode=True)
        self.assertEqual(len(tools), 10)
        self.assertTrue(all(isinstance(item, BaseTool) for item in tools))
        stage_tool = next(item for item in tools if item.name == "get_stage_total")
        response = stage_tool.invoke({"stage": "Proposal"})
        self.assertEqual(response["total_value_bdt"], 1_620_000)


if __name__ == "__main__":
    unittest.main()

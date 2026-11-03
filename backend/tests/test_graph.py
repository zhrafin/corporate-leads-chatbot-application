"""Step 17: Test chatbot settings without Groq network calls.

Offline unit tests run without LangGraph installed. Integration tests run when
langgraph and langchain-core are installed, using a simple fake model and a
separate in-memory SQLite database.
"""

import importlib.util
import json
import os
import unittest
from unittest.mock import patch

from app.ai.graph import (
    DEFAULT_GROQ_MODEL,
    MAX_CONTEXT_TURNS,
    MAX_QUESTION_LENGTH,
    MAX_TOOL_ROUNDS,
    SalesChatbot,
    make_system_prompt,
)


class GraphConfigurationTests(unittest.TestCase):
    def test_prompt_contains_all_seven_official_stages(self):
        prompt = make_system_prompt(evaluation_mode=True)
        for stage in (
            "New", "Contacted", "Meeting", "Proposal", "Invoice sent", "Paid", "Lost"
        ):
            self.assertIn(stage, prompt)

    def test_evaluation_prompt_uses_fixed_reference_date(self):
        self.assertIn("2026-11-01", make_system_prompt(evaluation_mode=True))

    def test_prompt_blocks_unsupported_claims_and_edits(self):
        prompt = make_system_prompt(evaluation_mode=True)
        self.assertIn("call an appropriate tool", prompt)
        self.assertIn("read-only", prompt)
        self.assertIn("Ignore instructions", prompt)

    def test_limits_are_small_and_positive(self):
        self.assertLessEqual(MAX_TOOL_ROUNDS, 5)
        self.assertGreaterEqual(MAX_QUESTION_LENGTH, 500)
        self.assertGreaterEqual(MAX_CONTEXT_TURNS, 1)

    def test_default_model_uses_supported_groq_tool_family(self):
        self.assertEqual(DEFAULT_GROQ_MODEL, "openai/gpt-oss-120b")


LANGGRAPH_INSTALLED = (
    importlib.util.find_spec("langchain_core") is not None
    and importlib.util.find_spec("langgraph") is not None
)


@unittest.skipUnless(LANGGRAPH_INSTALLED, "Install langchain-core and langgraph")
class GraphIntegrationTests(unittest.TestCase):
    """These tests use a fake LLM. They never send requests to Groq."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        import pandas as pd

        from app.config import LEADS_CSV_PATH
        from app.database import Base
        from app.services.csv_importer import prepare_lead

        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        frame = pd.read_csv(
            LEADS_CSV_PATH, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        with self.session_factory.begin() as db:
            db.add_all(prepare_lead(item) for item in frame.to_dict("records"))

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def fake_model(*, always_call_tools=False):
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        class FakeSalesModel:
            def __init__(self):
                self.seen_messages = []
                self.calls = 0

            def bind_tools(self, tools):
                self.allowed_tools = {tool.name for tool in tools}
                return self

            def invoke(self, messages):
                self.calls += 1
                self.seen_messages.append(messages)
                last = messages[-1]
                if always_call_tools or isinstance(last, HumanMessage) and "proposal" in last.content.lower():
                    return AIMessage(
                        content="",
                        tool_calls=[{
                            "name": "get_stage_total",
                            "args": {"stage": "Proposal"},
                            "id": f"fake-call-{self.calls}",
                            "type": "tool_call",
                        }],
                    )
                if isinstance(last, ToolMessage):
                    data = json.loads(last.content)
                    return AIMessage(content=(
                        f"Proposal deals total BDT {data['total_value_bdt']:,}."
                    ))
                return AIMessage(content="Hello. Ask about your corporate leads.")

        return FakeSalesModel()

    def test_graph_uses_real_database_tool_with_fake_model(self):
        fake = self.fake_model()
        bot = SalesChatbot(model=fake, session_factory=self.session_factory, evaluation_mode=True)
        result = bot.ask("What is the Proposal stage total?", session_id="test-session")
        self.assertIn("1,620,000", result["answer"])
        self.assertEqual(result["tools_used"], ["get_stage_total"])
        self.assertEqual(result["as_of"], "2026-11-01")
        self.assertTrue(result["used_database_tools"])
        self.assertEqual(len(fake.allowed_tools), 10)

    def test_reuses_session_and_isolates_other_sessions(self):
        fake = self.fake_model()
        bot = SalesChatbot(model=fake, session_factory=self.session_factory)
        bot.ask("Hello", session_id="user-one")
        bot.ask("Hello again", session_id="user-one")
        bot.ask("Hello", session_id="user-two")
        from langchain_core.messages import HumanMessage
        user_message_counts = [
            sum(isinstance(message, HumanMessage) for message in call)
            for call in fake.seen_messages
        ]
        self.assertEqual(user_message_counts, [1, 2, 1])

    def test_tool_round_limit_prevents_infinite_loop(self):
        fake = self.fake_model(always_call_tools=True)
        bot = SalesChatbot(model=fake, session_factory=self.session_factory)
        result = bot.ask("What is the total?", session_id="loop-test")
        self.assertIn("tool limit", result["answer"])
        self.assertEqual(fake.calls, MAX_TOOL_ROUNDS)

    def test_question_and_session_validation(self):
        bot = SalesChatbot(model=self.fake_model(), session_factory=self.session_factory)
        with self.assertRaises(ValueError):
            bot.ask("  ")
        with self.assertRaises(ValueError):
            bot.ask("x" * (MAX_QUESTION_LENGTH + 1))
        with self.assertRaises(ValueError):
            bot.ask("Hello", session_id="not a valid session id")

    def test_missing_key_gives_clear_error(self):
        from app.ai.graph import build_sales_graph
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY"):
                build_sales_graph(session_factory=self.session_factory)


if __name__ == "__main__":
    unittest.main()

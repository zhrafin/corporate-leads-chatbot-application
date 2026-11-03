"""Step 19: API tests for the chatbot and email generator.

No Groq key or LangGraph installation is needed. Fake AI services are used,
so the tests never make network requests or modify the actual SQLite file.

Run from backend/:
    python -m unittest discover -s tests -p "test_ai_api.py" -v
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


CHAT_RESULT = {
    "answer": "The Proposal-stage total is BDT 1,620,000.",
    "session_id": "demo-1",
    "tools_used": ["get_stage_total"],
    "used_database_tools": True,
    "as_of": "2026-11-01",
}

EMAIL_RESULT = {
    "lead_id": "L02",
    "company": "Meghna Pharma Ltd",
    "recipient_email": "sultana@meghna-pharma.example",
    "subject": "Following up on the training proposal",
    "body": "Hello Dr. Sultana,\n\nFollowing up on the proposal and budget approval.\n\nBest regards,\nBIAI",
    "warnings": ["Please review the recipient email before use."],
    "notes_available": True,
    "requires_review": True,
    "sent": False,
}


class AiApiTests(unittest.TestCase):
    def setUp(self):
        # App startup normally initializes the user's database; never do that in tests.
        self.startup_patch = patch("app.main.create_tables")
        self.startup_patch.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.startup_patch.stop()

    def test_documentation_lists_both_ai_routes(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/api/ai/chat", paths)
        self.assertIn("/api/ai/draft-email", paths)

    @patch("app.api.ai.get_chatbot")
    def test_chat_uses_workflow_and_returns_tools(self, factory):
        bot = factory.return_value
        bot.ask.return_value = CHAT_RESULT
        response = self.client.post("/api/ai/chat", json={
            "message": "What is the Proposal stage total?",
            "session_id": "demo-1", "evaluation_mode": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tools_used"], ["get_stage_total"])
        self.assertEqual(response.json()["as_of"], "2026-11-01")
        self.assertTrue(response.json()["used_database_tools"])
        factory.assert_called_once_with(evaluation_mode=True)
        bot.ask.assert_called_once_with(
            "What is the Proposal stage total?", session_id="demo-1"
        )

    @patch("app.api.ai.get_chatbot")
    def test_chat_without_session_requests_generated_id(self, factory):
        factory.return_value.ask.return_value = CHAT_RESULT
        result = self.client.post("/api/ai/chat", json={"message": "Hello"})
        self.assertEqual(result.status_code, 200, result.text)
        factory.assert_called_once_with(evaluation_mode=False)
        factory.return_value.ask.assert_called_once_with("Hello", session_id=None)
        self.assertEqual(result.json()["session_id"], "demo-1")

    def test_blank_question_is_rejected(self):
        response = self.client.post("/api/ai/chat", json={"message": "  "})
        self.assertEqual(response.status_code, 422)

    def test_long_question_is_rejected(self):
        response = self.client.post("/api/ai/chat", json={"message": "a" * 2001})
        self.assertEqual(response.status_code, 422)

    def test_invalid_session_id_is_rejected(self):
        for value in ("has spaces", "x" * 65, "", "user/other"):
            with self.subTest(value=value):
                response = self.client.post("/api/ai/chat", json={
                    "message": "Hello", "session_id": value,
                })
                self.assertEqual(response.status_code, 422)

    @patch("app.api.ai.get_chatbot")
    def test_chat_bad_request_from_workflow(self, factory):
        factory.return_value.ask.side_effect = ValueError("Invalid question.")
        response = self.client.post("/api/ai/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid question.")

    @patch("app.api.ai.get_chatbot")
    def test_missing_groq_key_returns_503(self, factory):
        factory.side_effect = RuntimeError("Missing GROQ_API_KEY")
        response = self.client.post("/api/ai/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("GROQ_API_KEY", response.json()["detail"])

    @patch("app.api.ai.get_chatbot")
    def test_missing_langgraph_returns_503(self, factory):
        factory.side_effect = ModuleNotFoundError("No module named 'langgraph'")
        response = self.client.post("/api/ai/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("requirements.txt", response.json()["detail"])

    @patch("app.api.ai.get_chatbot")
    def test_ai_provider_failure_is_private(self, factory):
        factory.return_value.ask.side_effect = Exception("private provider diagnostics")
        response = self.client.post("/api/ai/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private provider diagnostics", response.text)

    @patch("app.api.ai.get_email_generator")
    def test_email_returns_review_only_draft(self, factory):
        generator = factory.return_value
        generator.draft.return_value = EMAIL_RESULT
        response = self.client.post("/api/ai/draft-email", json={"lead_id": "L02"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["company"], "Meghna Pharma Ltd")
        self.assertEqual(result["lead_id"], "L02")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(result["requires_review"])
        self.assertFalse(result["sent"])
        generator.draft.assert_called_once_with("L02")

    def test_invalid_lead_id_rejected_by_schema(self):
        for value in ("", "has spaces", "L/02", "A" * 21):
            with self.subTest(value=value):
                response = self.client.post("/api/ai/draft-email", json={"lead_id": value})
                self.assertEqual(response.status_code, 422)

    @patch("app.api.ai.get_email_generator")
    def test_unknown_lead_returns_404(self, factory):
        factory.return_value.draft.side_effect = LookupError("Lead L99 does not exist.")
        response = self.client.post("/api/ai/draft-email", json={"lead_id": "L99"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("L99", response.json()["detail"])

    @patch("app.api.ai.get_email_generator")
    def test_merged_lead_returns_409(self, factory):
        factory.return_value.draft.side_effect = ValueError(
            "L03 was merged into L02. Select L02 instead."
        )
        response = self.client.post("/api/ai/draft-email", json={"lead_id": "L03"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("L02", response.json()["detail"])

    @patch("app.api.ai.get_email_generator")
    def test_invalid_email_request_returns_400(self, factory):
        factory.return_value.draft.side_effect = ValueError("Invalid lead ID")
        response = self.client.post("/api/ai/draft-email", json={"lead_id": "L02"})
        self.assertEqual(response.status_code, 400)

    @patch("app.api.ai.get_email_generator")
    def test_email_invalid_ai_output_returns_502(self, factory):
        factory.return_value.draft.side_effect = RuntimeError("private model output")
        response = self.client.post("/api/ai/draft-email", json={"lead_id": "L02"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private model output", response.text)

    @patch("app.api.ai.get_email_generator")
    def test_email_missing_key_returns_503(self, factory):
        factory.side_effect = RuntimeError("Missing GROQ_API_KEY")
        response = self.client.post("/api/ai/draft-email", json={"lead_id": "L02"})
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()

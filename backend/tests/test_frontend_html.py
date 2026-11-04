"""Simple HTML structure tests for Step 20. No browser or API key needed.

Run from backend/: python -m unittest tests.test_frontend_html -v
Or: python -m unittest discover -s tests -p 'test_*.py' -v
"""

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import unittest


HTML_PATH = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


class ReadElements(HTMLParser):
    """Collect HTML elements and attributes using Python's built-in parser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class FrontendStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        parser = ReadElements()
        parser.feed(cls.html)
        cls.elements = parser.tags
        cls.ids = [attrs["id"] for _, attrs in cls.elements if "id" in attrs]

    def test_html_document(self):
        self.assertTrue(self.html.lower().startswith("<!doctype html>"))
        self.assertIn('<html lang="en">', self.html)

    def test_no_duplicate_ids(self):
        duplicates = [name for name, n in Counter(self.ids).items() if n > 1]
        self.assertEqual(duplicates, [])

    def test_five_screens(self):
        pages = [attrs["data-page"] for tag, attrs in self.elements
                 if tag == "section" and "data-page" in attrs]
        self.assertEqual(pages, ["dashboard", "pipeline", "data-quality", "assistant", "reports"])

    def test_navigation_targets_exist(self):
        links = [attrs for tag, attrs in self.elements
                 if tag == "a" and "data-view" in attrs]
        self.assertEqual(len(links), 6)  # 5 navigation links plus dashboard shortcut
        for link in links:
            self.assertIn(link["href"].lstrip("#"), self.ids)

    def test_seven_pipeline_stages_in_order(self):
        stages = [attrs["data-stage"] for tag, attrs in self.elements
                  if tag == "section" and "data-stage" in attrs]
        self.assertEqual(stages, ["New", "Contacted", "Meeting", "Proposal",
                                  "Invoice sent", "Paid", "Lost"])

    def test_required_forms_and_dialogs_exist(self):
        for element_id in ("lead-dialog", "lead-form", "email-dialog", "merge-dialog",
                           "merge-form", "chat-form", "chat-question"):
            self.assertIn(element_id, self.ids)

    def test_api_ready_data_containers(self):
        for element_id in ("leads-table-body", "issues-table-body", "duplicate-pairs",
                           "chat-messages", "dashboard-stages", "dashboard-priorities",
                           "evaluation-findings", "weekly-summary"):
            self.assertIn(element_id, self.ids)

    def test_financial_fields_are_placeholders(self):
        self.assertIn('id="metric-active-value">—', self.html)
        self.assertNotIn("1,620,000", self.html)
        self.assertNotIn("4,220,000", self.html)

    def test_ai_draft_requires_review(self):
        self.assertIn('id="email-warnings"', self.html)
        self.assertIn('id="copy-email"', self.html)
        self.assertNotIn('id="send-email"', self.html)

    def test_merge_needs_explicit_confirmation(self):
        self.assertIn('id="merge-confirmed" name="confirmed" type="checkbox" required', self.html)
        self.assertIn('id="merge-reason"', self.html)


if __name__ == "__main__":
    unittest.main()

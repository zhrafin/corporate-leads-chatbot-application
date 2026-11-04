"""Step 21: basic checks for the frontend stylesheet.

Run from backend/: python -m unittest discover -s tests -p 'test_frontend_css.py' -v
Only uses Python's standard library.
"""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HTML_FILE = PROJECT_ROOT / "frontend" / "index.html"
CSS_FILE = PROJECT_ROOT / "frontend" / "css" / "style.css"


class FrontendCSSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_FILE.read_text(encoding="utf-8")
        cls.css = CSS_FILE.read_text(encoding="utf-8")

    def test_html_loads_local_stylesheet(self):
        self.assertIn('<link rel="stylesheet" href="css/style.css">', self.html)

    def test_css_braces_match(self):
        self.assertEqual(self.css.count("{"), self.css.count("}"))

    def test_shared_colors_defined(self):
        for variable in ("--primary:", "--surface:", "--text:", "--danger:",
                         "--success:", "--border:"):
            with self.subTest(variable=variable):
                self.assertIn(variable, self.css)

    def test_page_layout_and_navigation(self):
        for selector in (".app-shell", ".sidebar", ".topbar", ".page-view",
                         ".nav-link", ".page-view:target"):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.css)

    def test_navigation_can_work_without_javascript(self):
        self.assertIn("main:has(> .page-view:target)", self.css)

    def test_dashboard_and_pipeline_styles(self):
        for selector in (".kpi-grid", ".kpi-card", ".pipeline-board",
                         ".stage-column", ".stage-cards", ".table-scroll",
                         ".data-table"):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.css)

    def test_all_seven_pipeline_stages_are_colored(self):
        for stage in ("New", "Contacted", "Meeting", "Proposal",
                      "Invoice sent", "Paid", "Lost"):
            with self.subTest(stage=stage):
                self.assertIn(f'[data-stage="{stage}"]', self.css)

    def test_chat_and_dialog_styling(self):
        for selector in (".chat-messages", ".chat-form", ".chat-message--user",
                         ".modal::backdrop", ".form-grid", ".toast"):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.css)

    def test_responsive_and_reduced_motion_rules(self):
        for rule in ("max-width: 1200px", "max-width: 1000px",
                     "max-width: 700px", "max-width: 480px",
                     "prefers-reduced-motion: reduce"):
            with self.subTest(rule=rule):
                self.assertIn(rule, self.css)

    def test_accessibility_and_hidden_elements(self):
        self.assertIn(":focus-visible", self.css)
        self.assertIn(".skip-link:focus", self.css)
        self.assertIn("[hidden]", self.css)

    def test_stylesheet_is_self_contained(self):
        self.assertNotIn("@import", self.css)
        self.assertNotIn("url(http", self.css)


if __name__ == "__main__":
    unittest.main()

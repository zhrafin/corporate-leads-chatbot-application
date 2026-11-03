"""Read-only tools for the BIAI sales chatbot (Step 16).

The language model can ASK these tools for facts, but it cannot edit the CRM.
Every amount, stage, date, and warning comes from the existing Python services
and SQLite database. No Groq API key is needed to use or test this file.

The next step, graph.py, will give these tools to a LangGraph agent.

Examples from the backend/ folder (after installing langchain-core):

    from app.ai.tools import build_sales_tools
    tools = {item.name: item for item in build_sales_tools(evaluation_mode=True)}
    print(tools["get_stage_total"].invoke({"stage": "Proposal"}))
"""

from collections.abc import Callable
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ALL_STAGES, reference_date
from ..database import SessionLocal
from ..models import Lead, LeadIssue, Owner
from ..services.analytics import get_dashboard_stats, get_stage_total as stage_value
from ..services.duplicates import find_duplicate_pairs
from ..services.lead_service import (
    LeadNotFoundError,
    get_lead,
    list_leads,
)
from ..services.priority import (
    get_priority_leads as rank_leads,
    get_weekly_priority_summary,
)
from ..services.validation import get_overdue_leads as find_overdue_leads


class SalesReadService:
    """Read sales facts from SQLite, with one short session per request.

    You can pass a custom session factory in tests. The normal application
    uses SessionLocal, which connects to storage/biai_pipeline.db.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        *,
        evaluation_mode: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.evaluation_mode = evaluation_mode

    def _today(self) -> date:
        # Calculate on each call so a running server switches dates at midnight.
        return reference_date(evaluation_mode=self.evaluation_mode)

    @staticmethod
    def _lead_dict(lead: Lead, *, include_notes: bool = False) -> dict:
        """Return ordinary JSON-compatible values, never SQLAlchemy objects."""
        data = {
            "lead_id": lead.lead_id,
            "company": lead.company,
            "industry": lead.industry,
            "contact_name": lead.contact_name,
            "contact_role": lead.contact_role,
            "contact_email": lead.contact_email,
            "contact_phone": lead.contact_phone,
            "stage": lead.stage.value,
            "deal_value_bdt": lead.deal_value_bdt,
            "owner_id": lead.owner_id,
            "last_contact": lead.last_contact.isoformat() if lead.last_contact else None,
            "next_follow_up": lead.next_follow_up.isoformat() if lead.next_follow_up else None,
        }
        if include_notes:
            data["notes"] = lead.notes
        return data

    def search_leads(
        self,
        company: str = "",
        industry: str = "",
        stage: str = "",
        unassigned_only: bool = False,
        limit: int = 25,
    ) -> dict:
        """Search current leads by company, industry, stage, or missing owner."""
        with self.session_factory() as db:
            try:
                leads = list_leads(
                    db,
                    company=company or None,
                    industry=industry or None,
                    stage=stage or None,
                    unassigned_only=unassigned_only,
                    limit=limit,
                )
            except ValueError as error:
                return {"error": str(error), "allowed_stages": [s.value for s in ALL_STAGES]}
            return {
                "returned_count": len(leads),
                "leads": [self._lead_dict(lead) for lead in leads],
                "note": "Only current leads are listed; confirmed merged duplicates are excluded.",
            }

    def get_lead_details(self, lead_id: str) -> dict:
        """Get full details, including notes, for one current lead."""
        with self.session_factory() as db:
            try:
                lead = get_lead(db, lead_id.strip().upper())
            except LeadNotFoundError:
                return {"error": "Current lead not found.", "lead_id": lead_id}
            result = self._lead_dict(lead, include_notes=True)
            owner = db.get(Owner, lead.owner_id) if lead.owner_id else None
            result["owner_name"] = owner.name if owner else None
            result["source"] = lead.source
            return result

    def get_stage_total(self, stage: str) -> dict:
        """Calculate a stage's verified value in BDT, excluding confirmed merges."""
        with self.session_factory() as db:
            try:
                return stage_value(db, stage)
            except ValueError:
                return {
                    "error": "Unknown pipeline stage.",
                    "allowed_stages": [s.value for s in ALL_STAGES],
                }

    def get_pipeline_summary(self) -> dict:
        """Return complete stage counts, values, overdue counts, and issue totals."""
        with self.session_factory() as db:
            result = get_dashboard_stats(db, as_of=self._today())
            result["note"] = (
                "Saved issue counts reflect the most recent scans, not an automatic new scan. "
                "Duplicate deals are excluded only after a confirmed merge."
            )
            return result

    def get_overdue_leads(self, limit: int = 50) -> dict:
        """List overdue active deals as of the configured date."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return {"error": "limit must be between 1 and 100."}
        day = self._today()
        with self.session_factory() as db:
            leads = find_overdue_leads(db, as_of=day)
            return {
                "as_of": day.isoformat(),
                "overdue_count": len(leads),
                "returned_count": min(len(leads), limit),
                "leads": [self._lead_dict(lead) for lead in leads[:limit]],
                "note": (
                    "Paid, Lost, and merged records are excluded. "
                    "A lead can be overdue and also have an incorrect date order."
                ),
            }

    def get_data_quality_issues(
        self,
        rule_code: str = "",
        status: str = "open",
        limit: int = 50,
    ) -> dict:
        """Find SAVED warnings only; do not run or modify validation scans."""
        if status not in {"open", "resolved", "dismissed", "all"}:
            return {"error": "status must be open, resolved, dismissed, or all."}
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return {"error": "limit must be between 1 and 100."}

        with self.session_factory() as db:
            query = (
                select(LeadIssue)
                .join(Lead, LeadIssue.lead_id == Lead.lead_id)
                .where(Lead.merged_into_id.is_(None))
            )
            if rule_code.strip():
                query = query.where(LeadIssue.rule_code == rule_code.strip().upper())
            if status != "all":
                query = query.where(LeadIssue.status == status)
            issues = db.scalars(query.order_by(LeadIssue.id).limit(limit)).all()
            return {
                "returned_count": len(issues),
                "issues": [
                    {
                        "issue_id": item.id,
                        "lead_id": item.lead_id,
                        "rule_code": item.rule_code,
                        "severity": item.severity,
                        "status": item.status,
                        "message": item.message,
                        "evidence": item.evidence,
                    }
                    for item in issues
                ],
                "note": "Warnings come from the last saved validation/duplicate scans and may be outdated.",
            }

    def get_duplicate_candidates(self) -> dict:
        """Find current likely duplicate pairs without changing their records."""
        with self.session_factory() as db:
            pairs = find_duplicate_pairs(db)
            return {
                "pair_count": len(pairs),
                "pairs": pairs,
                "note": "Possible duplicates require human approval before merging.",
            }

    def search_lead_notes(self, keyword: str, limit: int = 20) -> dict:
        """Search text in current lead notes using a parameterized SQL query."""
        phrase = keyword.strip()
        if len(phrase) < 2 or len(phrase) > 100:
            return {"error": "Enter a keyword from 2 to 100 characters."}
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            return {"error": "limit must be between 1 and 50."}

        # Escape SQL LIKE special characters so '%' and '_' are literal text.
        escaped = phrase.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.session_factory() as db:
            leads = db.scalars(
                select(Lead)
                .where(
                    Lead.merged_into_id.is_(None),
                    func.lower(Lead.notes).like(f"%{escaped}%", escape="\\"),
                )
                .order_by(Lead.lead_id)
                .limit(limit)
            ).all()
            return {
                "keyword": phrase,
                "returned_count": len(leads),
                "matches": [
                    {"lead_id": lead.lead_id, "company": lead.company, "notes": lead.notes}
                    for lead in leads
                ],
            }

    def get_priority_leads(self, limit: int = 5) -> dict:
        """Rank current active deals using transparent rules, not LLM guesses."""
        day = self._today()
        with self.session_factory() as db:
            try:
                leads = rank_leads(db, as_of=day, limit=limit)
            except ValueError as error:
                return {"error": str(error)}
            return {
                "as_of": day.isoformat(),
                "lead_count": len(leads),
                "leads": leads,
                "note": "Scores are suggested business priorities and not official BIAI scores.",
            }

    def get_weekly_priority_data(self, limit: int = 5) -> dict:
        """Retrieve factual numbers for a future Groq-written weekly summary."""
        day = self._today()
        with self.session_factory() as db:
            try:
                return get_weekly_priority_summary(db, as_of=day, limit=limit)
            except ValueError as error:
                return {"error": str(error)}


def build_sales_tools(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    evaluation_mode: bool = False,
) -> list:
    """Create LangChain tools for the next LangGraph step.

    This import is here rather than at the top of the file so that business
    logic can be unit-tested without installing LangChain or calling Groq.
    """
    from langchain_core.tools import tool

    service = SalesReadService(session_factory, evaluation_mode=evaluation_mode)

    @tool("search_leads")
    def search_leads_tool(
        company: str = "",
        industry: str = "",
        stage: str = "",
        unassigned_only: bool = False,
        limit: int = 25,
    ) -> dict:
        """Find current leads by company, industry, sales stage, or missing owner."""
        return service.search_leads(company, industry, stage, unassigned_only, limit)

    @tool("get_lead_details")
    def lead_details_tool(lead_id: str) -> dict:
        """Get a specific lead's contact details, stage, value, and sales notes."""
        return service.get_lead_details(lead_id)

    @tool("get_stage_total")
    def stage_total_tool(stage: str) -> dict:
        """Calculate the BDT amount and deal count for one official pipeline stage."""
        return service.get_stage_total(stage)

    @tool("get_pipeline_summary")
    def pipeline_summary_tool() -> dict:
        """Get counts, stage values, overdue deals, and saved issue counts."""
        return service.get_pipeline_summary()

    @tool("get_overdue_leads")
    def overdue_leads_tool(limit: int = 50) -> dict:
        """Find overdue active leads using the trusted local or evaluation date."""
        return service.get_overdue_leads(limit)

    @tool("get_data_quality_issues")
    def data_quality_issues_tool(
        rule_code: str = "", status: str = "open", limit: int = 50,
    ) -> dict:
        """Read saved missing-contact, invalid-date, owner, or other issue warnings."""
        return service.get_data_quality_issues(rule_code, status, limit)

    @tool("get_duplicate_candidates")
    def duplicate_candidates_tool() -> dict:
        """Find likely duplicate company leads; never merge or delete them."""
        return service.get_duplicate_candidates()

    @tool("search_lead_notes")
    def search_notes_tool(keyword: str, limit: int = 20) -> dict:
        """Search sales notes using a keyword to find relevant company leads."""
        return service.search_lead_notes(keyword, limit)

    @tool("get_priority_leads")
    def priority_leads_tool(limit: int = 5) -> dict:
        """Rank leads needing attention and explain each suggested next action."""
        return service.get_priority_leads(limit)

    @tool("get_weekly_priority_data")
    def weekly_priority_tool(limit: int = 5) -> dict:
        """Return factual top leads and overdue totals for a weekly AI summary."""
        return service.get_weekly_priority_data(limit)

    return [
        search_leads_tool,
        lead_details_tool,
        stage_total_tool,
        pipeline_summary_tool,
        overdue_leads_tool,
        data_quality_issues_tool,
        duplicate_candidates_tool,
        search_notes_tool,
        priority_leads_tool,
        weekly_priority_tool,
    ]

"""Shared project configuration and BIAI's official pipeline rules.

This module is intentionally independent of FastAPI, SQLite, and LangGraph.
Other parts of the application should import the same definitions, so the
frontend, database validation, and AI tools use consistent business rules.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo
import os


# Paths are anchored to the project folder, not to the terminal's working dir.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
STORAGE_DIR = PROJECT_ROOT / "storage"

LEADS_CSV_PATH = DATA_DIR / "corporate-leads.csv"
STAGE_DEFINITIONS_PATH = DATA_DIR / "stage-definitions.md"
SQLITE_PATH = STORAGE_DIR / "biai_pipeline.db"

# In production this can be overridden with a DATABASE_URL environment variable.
# SQLite's default URL points to an absolute path, so it works from any cwd.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{SQLITE_PATH.as_posix()}")


class PipelineStage(str, Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    MEETING = "Meeting"
    PROPOSAL = "Proposal"
    INVOICE_SENT = "Invoice sent"
    PAID = "Paid"
    LOST = "Lost"


ACTIVE_STAGES: tuple[PipelineStage, ...] = (
    PipelineStage.NEW,
    PipelineStage.CONTACTED,
    PipelineStage.MEETING,
    PipelineStage.PROPOSAL,
    PipelineStage.INVOICE_SENT,
)

FINAL_STAGES: tuple[PipelineStage, ...] = (
    PipelineStage.PAID,
    PipelineStage.LOST,
)

ALL_STAGES: tuple[PipelineStage, ...] = ACTIVE_STAGES + FINAL_STAGES

# Wording comes from the provided stage-definitions.md file.
STAGE_DEFINITIONS: dict[PipelineStage, str] = {
    PipelineStage.NEW: "Came in, nobody has contacted them yet.",
    PipelineStage.CONTACTED: "We have had a first conversation or sent a message.",
    PipelineStage.MEETING: "A meeting or call has happened.",
    PipelineStage.PROPOSAL: "A written proposal or price list has been sent.",
    PipelineStage.INVOICE_SENT: "An invoice has been sent and payment is pending.",
    PipelineStage.PAID: "The full amount is received.",
    PipelineStage.LOST: "They said no or went silent for 30+ days.",
}

CURRENCY_CODE = "BDT"
BUSINESS_TIMEZONE = ZoneInfo("Asia/Dhaka")

# Required for repeatable evaluation with BIAI's supplied sample data.
EVALUATION_DATE = date(2026, 11, 1)

# The original CSV uses spaces and punctuation in the headers.
# Preserve the source CSV and rename columns only during import.
CSV_COLUMN_MAP: dict[str, str] = {
    "lead id": "lead_id",
    "company": "company",
    "industry": "industry",
    "contact name": "contact_name",
    "contact role": "contact_role",
    "contact email": "contact_email",
    "contact phone": "contact_phone",
    "source": "source",
    "stage": "stage",
    "deal value (BDT)": "deal_value_bdt",
    "last contact": "last_contact",
    "next follow-up": "next_follow_up",
    "notes": "notes",
}


def reference_date(*, evaluation_mode: bool = False) -> date:
    """Use BIAI's fixed reference date for tests, local Dhaka date otherwise."""
    if evaluation_mode:
        return EVALUATION_DATE
    return datetime.now(BUSINESS_TIMEZONE).date()


def parse_stage(value: str) -> PipelineStage:
    """Accept only BIAI's seven official stage names."""
    try:
        return PipelineStage(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid pipeline stage: {value!r}") from exc


def is_active_stage(stage: str | PipelineStage) -> bool:
    """Return True for leads still moving through the sales pipeline."""
    return parse_stage(stage) in ACTIVE_STAGES

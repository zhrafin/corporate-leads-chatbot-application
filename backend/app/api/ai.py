"""Step 19: FastAPI endpoints for the BIAI chatbot and email drafts.

These endpoints connect the FastAPI app to existing LangGraph workflows:

    POST /api/ai/chat        -> SalesChatbot (Step 17)
    POST /api/ai/draft-email -> EmailDraftGenerator (Step 18)

The AI tools are read-only. Email drafts are returned for human review;
this module never sends emails or saves drafts automatically.

Run from backend/: python -m uvicorn app.main:app --reload
"""

import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from ..ai.email_graph import EmailDraftGenerator
from ..ai.graph import SalesChatbot
from ..schemas import (
    ChatRequest,
    ChatResponse,
    EmailDraftPreview,
    EmailDraftRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["AI Assistant"])


@lru_cache(maxsize=2)
def get_chatbot(evaluation_mode: bool = False) -> SalesChatbot:
    """Keep one chatbot per date mode, so conversations keep their history.

    The first request creates the chatbot. Starting FastAPI does not require
    a Groq API key until an AI endpoint is actually used.
    """
    return SalesChatbot(evaluation_mode=evaluation_mode)


@lru_cache(maxsize=1)
def get_email_generator() -> EmailDraftGenerator:
    """Create the email workflow once; each draft has its own graph state."""
    return EmailDraftGenerator()


def ai_service_error(error: Exception) -> HTTPException:
    """Give the frontend a helpful error, without leaking secret details."""
    if isinstance(error, (ModuleNotFoundError, ImportError)):
        return HTTPException(
            status_code=503,
            detail="AI libraries are missing. Install backend/requirements.txt.",
        )

    if isinstance(error, RuntimeError) and "GROQ_API_KEY" in str(error):
        return HTTPException(
            status_code=503,
            detail="Groq is not configured. Set GROQ_API_KEY on the backend.",
        )

    # Provider failures, timeouts, and invalid AI outputs are not user errors.
    logger.exception("AI service failed", exc_info=error)
    return HTTPException(
        status_code=502,
        detail="The AI service could not complete the request. Please try again.",
    )


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest):
    """Ask about real lead records using the LangGraph read-only tools.

    Reuse the returned session_id for a follow-up question. Set
    evaluation_mode=true to use BIAI's 1 November 2026 sample date.
    """
    try:
        chatbot = get_chatbot(evaluation_mode=body.evaluation_mode)
        return chatbot.ask(body.message, session_id=body.session_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise ai_service_error(error) from error


@router.post("/draft-email", response_model=EmailDraftPreview)
def draft_email(body: EmailDraftRequest):
    """Return a short English draft for one current lead. Nothing is sent."""
    try:
        generator = get_email_generator()
        return generator.draft(body.lead_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        # A merged lead must be replaced with the surviving lead before drafting.
        if "merged into" in str(error).lower():
            raise HTTPException(status_code=409, detail=str(error)) from error
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise ai_service_error(error) from error

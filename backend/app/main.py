"""Start the FastAPI server for BIAI's Corporate Leads Pipeline.

Run from the backend folder:
    python -m uvicorn app.main:app --reload

API documentation: http://127.0.0.1:8000/docs
"""

import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import create_tables, get_db
from .api import issues, leads, pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare the database once when the server starts.

    We create tables, but do not import the sample CSV automatically.
    Importing leads is a separate, user-controlled step.
    """
    create_tables()
    yield


app = FastAPI(
    title="BIAI Corporate Leads Pipeline",
    description="Sales pipeline management and AI assistance for BIAI.",
    version="0.1.0",
    lifespan=lifespan,
)


# A different frontend port counts as a different web origin.
# These local addresses work with common frontend development servers.
# Set CORS_ORIGINS (comma-separated) when deploying to another domain.
def allowed_origins() -> list[str]:
    value = os.getenv("CORS_ORIGINS")
    if value:
        return [origin.strip() for origin in value.split(",") if origin.strip()]

    return [
        "http://localhost:5500", "http://127.0.0.1:5500",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,  # Login/cookies are not implemented yet.
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/", tags=["System"])
def home():
    """Basic information about this backend."""
    return {
        "message": "BIAI Corporate Leads Pipeline API",
        "docs": "/docs",
        "health": "/api/health",
    }


api_router = APIRouter(prefix="/api")


@api_router.get("/health", tags=["System"])
def health_check(db: Session = Depends(get_db)):
    """Check that the API can communicate with SQLite."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Avoid showing internal database details to the caller.
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable.",
        ) from exc

    return {"status": "ok", "database": "connected"}


app.include_router(api_router)
app.include_router(leads.router, prefix="/api")
app.include_router(leads.owners_router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(issues.router, prefix="/api")

"""Connect the BIAI application to its SQLite database.

This file handles database connections. The actual tables will be defined
in models.py, which we will create in the next step.
"""

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL, STORAGE_DIR


# Make sure the folder for our SQLite file exists.
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# FastAPI may use different threads, so allow SQLite connections across threads.
# Each request still gets its own session. This option is only for SQLite.
options = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# The engine knows how to connect to the database.
engine = create_engine(DATABASE_URL, connect_args=options)

# Each request gets its own database session.
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    """Parent class for the database tables we will define in models.py."""
    pass


def get_db() -> Generator[Session, None, None]:
    """Give a FastAPI request a session, then close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create tables after the models have been defined.

    This function will be ready to use after we create app/models.py.
    """
    from . import models  # Register tables with Base (created next step).

    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    # Simple connection test: run `python -m app.database` from backend/.
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1")).scalar_one()
    print(f"Database connection works! Test result: {result}")

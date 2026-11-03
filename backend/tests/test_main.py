"""Tests for the FastAPI application without touching the real database.

Run from backend/: python -m unittest discover -s tests -p "test_*.py" -v
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


class MainTests(unittest.TestCase):
    def setUp(self):
        # Reuse a single in-memory SQLite database across test threads.
        self.test_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Startup creates tables in the TEST database, never the real one.
        self.startup_patch = patch(
            "app.main.create_tables",
            side_effect=lambda: Base.metadata.create_all(self.test_engine),
        )
        self.mock_startup = self.startup_patch.start()

        def test_db():
            with Session(self.test_engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db

    def tearDown(self):
        app.dependency_overrides.clear()
        self.startup_patch.stop()
        self.test_engine.dispose()

    def test_home_page(self):
        with TestClient(app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["docs"], "/docs")

    def test_health_check(self):
        with TestClient(app) as client:
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "connected"})

    def test_startup_creates_tables(self):
        with TestClient(app):
            table_names = inspect(self.test_engine).get_table_names()
        self.mock_startup.assert_called_once()
        self.assertIn("leads", table_names)
        self.assertIn("lead_issues", table_names)
        self.assertIn("owners", table_names)

    def test_documentation_available(self):
        with TestClient(app) as client:
            docs = client.get("/docs")
            schema = client.get("/openapi.json")
        self.assertEqual(docs.status_code, 200)
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/api/health", schema.json()["paths"])

    def test_local_frontend_cors(self):
        with TestClient(app) as client:
            response = client.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5500",
                    "Access-Control-Request-Method": "GET",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:5500",
        )

    def test_unavailable_database_returns_503(self):
        class BrokenDatabase:
            def execute(self, _statement):
                raise SQLAlchemyError("A simulated database failure")

        app.dependency_overrides[get_db] = lambda: BrokenDatabase()

        with TestClient(app) as client:
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": "Database is unavailable."}
        )


if __name__ == "__main__":
    unittest.main()

import base64
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import incidents as incidents_api
from app.core.config import Settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.models.entities import UserAccount


class IncidentAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        user = UserAccount(username="api-engineer", display_name="API Engineer", password_hash=hash_password("pw"))
        user.roles = {"ROLE_ENGINEER", "ROLE_USER"}
        self.db.add(user)
        self.db.commit()

        self.settings = Settings(
            _env_file=None,
            ai_provider="mock",
            knowledge_vector_enabled=False,
            knowledge_vector_required=False,
            model_fallback_provider="mock",
            incident_analysis_min_confidence=0.5,
        )
        self.original_get_settings = incidents_api.get_settings
        incidents_api.get_settings = lambda: self.settings

        app = FastAPI()
        app.include_router(incidents_api.router)
        app.dependency_overrides[get_db] = self._override_db
        self.client = TestClient(app)
        token = base64.b64encode(b"api-engineer:pw").decode()
        self.headers = {"Authorization": f"Basic {token}"}

    def _override_db(self):
        yield self.db

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()
        incidents_api.get_settings = self.original_get_settings

    def test_analyze_and_latest_endpoints(self):
        created = self.client.post(
            "/api/incidents",
            headers=self.headers,
            json={
                "title": "Redis connection refused",
                "description": "Production session service cannot connect to Redis and returns timeout errors.",
                "serviceName": "session-service",
                "environment": "PRODUCTION",
                "category": "CACHE",
                "severity": "P1",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        incident_id = created.json()["id"]

        analyzed = self.client.post(f"/api/incidents/{incident_id}/analyze", headers=self.headers)
        self.assertEqual(analyzed.status_code, 200, analyzed.text)
        self.assertEqual(analyzed.json()["status"], "COMPLETED")
        self.assertFalse(analyzed.json()["operationGuard"]["executionAllowed"])

        latest = self.client.get(f"/api/incidents/{incident_id}/analysis/latest", headers=self.headers)
        self.assertEqual(latest.status_code, 200, latest.text)
        self.assertEqual(latest.json()["runId"], analyzed.json()["runId"])

        streamed = self.client.post(f"/api/incidents/{incident_id}/analyze/stream", headers=self.headers)
        self.assertEqual(streamed.status_code, 200, streamed.text)
        self.assertIn("event: meta", streamed.text)
        self.assertIn("event: token", streamed.text)
        self.assertIn("event: done", streamed.text)


if __name__ == "__main__":
    unittest.main()

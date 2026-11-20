"""Versioned incident context management endpoints: RBAC and stale restore."""
import base64
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.incidents import router
from app.core.config import Settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.models.entities import IncidentRecord, UserAccount
from app.services.incident_context_memory import IncidentContextMemory


def auth(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": "Basic " + token}


class SnapshotApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.operator = UserAccount(username="operator", display_name="operator", password_hash=hash_password("pw"))
        self.operator.roles = {"ROLE_ENGINEER"}
        self.requester = UserAccount(username="requester", display_name="requester", password_hash=hash_password("pw"))
        self.requester.roles = {"ROLE_USER"}
        self.db.add_all([self.operator, self.requester]); self.db.flush()
        self.incident = IncidentRecord(incident_key="INC-ONE", title="API response timeout",
                                       description="Service has elevated latency", service_name="svc",
                                       environment="TEST", category="API", severity="P2", status="NEW",
                                       source="MANUAL", reporter_id=self.operator.id,
                                       impact_scope="test scope", assignee="oncall", version=1)
        self.db.add(self.incident); self.db.commit()
        self.settings = Settings(_env_file=None, incident_context_cache_enabled=False)
        self.memory = IncidentContextMemory(self.db, self.settings)
        self.memory.save(self.incident, "run-1", "历史摘要", {"symptomSummary": "slow"},
                         {"impactScope": "scope"}, {"evidence": []})
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.patch = patch("app.api.incidents.get_settings", return_value=self.settings)
        self.patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.patch.stop()
        self.client.close(); self.db.close(); self.engine.dispose()

    def test_operator_can_list_and_restore_version(self):
        headers = auth("operator", "pw")
        response = self.client.get(f"/api/incidents/{self.incident.id}/context/snapshots", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["version"], 1)
        restore = self.client.post(f"/api/incidents/{self.incident.id}/context/snapshots/1/restore", headers=headers)
        self.assertEqual(restore.status_code, 200)
        self.assertTrue(restore.json()["restored"])

    def test_requester_blocked_and_changed_incident_cannot_restore(self):
        url = f"/api/incidents/{self.incident.id}/context/snapshots/1/restore"
        self.assertEqual(self.client.post(url, headers=auth("requester", "pw")).status_code, 403)
        self.incident.version += 1; self.db.commit()
        response = self.client.post(url, headers=auth("operator", "pw"))
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

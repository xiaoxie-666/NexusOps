import base64
import asyncio

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.incidents import router as incidents_router
from app.api.ops import router as ops_router
from app.core.config import Settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.models.entities import AlertEvent, IncidentApproval, IncidentRecord, IncidentToolJob, KnowledgeChunk, KnowledgeSource, UserAccount
from app.services import alert_ingestion as alert_service_module
from app.services import incident_tooling as tooling_module
from app.services.ops_skills import OpsSkillLibrary
from app.services.mcp_client import McpToolError, NexusOpsMcpToolClient
from app.services.knowledge import KnowledgeService


def auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class TestNexusOpsFinalApi:
    def setup_method(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.admin = self._user("admin-final", "admin-pw", {"ROLE_ADMIN", "ROLE_USER"})
        self.engineer = self._user("engineer-final", "engineer-pw", {"ROLE_ENGINEER", "ROLE_USER"})
        self.oncall = self._user("oncall-final", "oncall-pw", {"ROLE_ONCALL", "ROLE_USER"})
        self.settings = Settings(
            _env_file=None,
            app_env="development",
            ai_provider="mock",
            database_url="sqlite:///:memory:",
            knowledge_vector_enabled=False,
            knowledge_vector_required=False,
            alert_webhook_token="",
            ops_knowledge_auto_seed=False,
        )
        self.original_tool_settings = tooling_module.get_settings
        tooling_module.get_settings = lambda: self.settings
        self.app = FastAPI()
        self.app.include_router(incidents_router)
        self.app.include_router(ops_router)
        self.app.dependency_overrides[get_db] = self._override_db
        self.client = TestClient(self.app)

    def teardown_method(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()
        tooling_module.get_settings = self.original_tool_settings

    def _override_db(self):
        yield self.db

    def _user(self, username, password, roles):
        row = UserAccount(username=username, display_name=username, password_hash=hash_password(password))
        row.roles = roles
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _create_incident(self):
        response = self.client.post(
            "/api/incidents",
            headers=auth("engineer-final", "engineer-pw"),
            json={
                "title": "Checkout API returns HTTP 503",
                "description": "Production checkout endpoint returns repeated 503 responses and elevated latency.",
                "serviceName": "checkout-api",
                "environment": "PRODUCTION",
                "category": "API",
                "severity": "P1",
                "source": "MANUAL",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_profile_and_dashboard_endpoints(self):
        headers = auth("engineer-final", "engineer-pw")
        profile = self.client.get("/api/profile", headers=headers)
        assert profile.status_code == 200
        assert profile.json()["username"] == "engineer-final"
        dashboard = self.client.get("/api/dashboard/summary", headers=headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["metrics"]["totalIncidents"] == 0

    def test_alertmanager_webhook_deduplicates_and_creates_incident(self):
        payload = {
            "status": "firing",
            "alerts": [{
                "status": "firing",
                "fingerprint": "test-fingerprint-001",
                "startsAt": "2026-11-13T10:00:00Z",
                "labels": {"alertname": "HighCheckoutErrorRate", "severity": "critical", "service": "checkout-api", "environment": "production"},
                "annotations": {"summary": "Checkout error rate is above threshold", "description": "HTTP 5xx ratio exceeded 10 percent."},
            }],
        }
        first = self.client.post("/api/webhooks/alertmanager", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["created"] == 1
        second = self.client.post("/api/webhooks/alertmanager", json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["deduplicated"] == 1
        assert self.db.query(AlertEvent).count() == 1
        assert self.db.query(IncidentRecord).count() == 1
        event = self.db.query(AlertEvent).one()
        assert event.occurrence_count == 2
        assert event.severity == "P0"
        assert event.incident_id is not None

    def test_approval_requires_human_review_and_never_executes(self):
        incident = self._create_incident()
        request = self.client.post(
            f"/api/incidents/{incident['id']}/approvals",
            headers=auth("engineer-final", "engineer-pw"),
            json={
                "actionKey": "rollback_deployment",
                "target": "checkout-api production",
                "justification": "The current release caused a confirmed production regression; request a reviewed rollback.",
                "details": {"changeWindow": "2026-11-13T12:00:00Z"},
            },
        )
        assert request.status_code == 201, request.text
        approval_id = request.json()["id"]
        assert request.json()["status"] == "PENDING"
        reviewed = self.client.post(
            f"/api/admin/approvals/{approval_id}/review",
            headers=auth("oncall-final", "oncall-pw"),
            json={"approved": True, "reviewNote": "Approved for the authorized operator to execute manually."},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "APPROVED"
        assert reviewed.json()["executionMode"] == "MANUAL_ONLY"
        assert self.db.query(IncidentApproval).one().status == "APPROVED"

    def test_allowlisted_tool_job_is_idempotent_and_unknown_tool_is_rejected(self):
        incident = self._create_incident()
        payload = {
            "toolName": "append_incident_note",
            "payload": {"content": "Checked application-side connection pool metrics."},
            "idempotencyKey": "test-note-job-001",
        }
        headers = auth("engineer-final", "engineer-pw")
        first = self.client.post(f"/api/incidents/{incident['id']}/tools", headers=headers, json=payload)
        assert first.status_code == 202, first.text
        second = self.client.post(f"/api/incidents/{incident['id']}/tools", headers=headers, json=payload)
        assert second.status_code == 202, second.text
        assert first.json()["id"] == second.json()["id"]
        assert self.db.query(IncidentToolJob).count() == 1
        blocked = self.client.post(
            f"/api/incidents/{incident['id']}/tools",
            headers=headers,
            json={"toolName": "run_shell_command", "payload": {"command": "rm -rf /"}},
        )
        assert blocked.status_code == 422

    def test_database_skill_is_selected_for_database_incident(self):
        context = OpsSkillLibrary.context_for("DATABASE", "MySQL connection pool timeout")
        assert "mysql_diagnostics" in context
        assert "change_safety" in context

    def test_requester_cannot_self_approve_and_rejection_requires_reason(self):
        incident = self._create_incident()
        created = self.client.post(
            f"/api/incidents/{incident['id']}/approvals",
            headers=auth("engineer-final", "engineer-pw"),
            json={
                "actionKey": "change_configuration",
                "target": "checkout-api production",
                "justification": "Request a reviewed change to the upstream timeout configuration.",
                "details": {},
            },
        )
        approval_id = created.json()["id"]
        self_review = self.client.post(
            f"/api/admin/approvals/{approval_id}/review",
            headers=auth("engineer-final", "engineer-pw"),
            json={"approved": True, "reviewNote": "self approval"},
        )
        assert self_review.status_code == 403
        no_reason = self.client.post(
            f"/api/admin/approvals/{approval_id}/review",
            headers=auth("oncall-final", "oncall-pw"),
            json={"approved": False, "reviewNote": "no"},
        )
        assert no_reason.status_code == 422

    def test_mcp_client_rejects_non_readonly_tool_names(self):
        client = NexusOpsMcpToolClient(self.settings)
        with pytest.raises(McpToolError):
            asyncio.run(client.call_readonly_tool("nexusops_execute_shell", {"command": "id"}))

    def test_stale_knowledge_sources_are_excluded_before_retrieval(self):
        self.db.add_all([
            KnowledgeSource(source_key="ops:mysql-current", name="Current MySQL Runbook", version_hash="a" * 64, status="ACTIVE"),
            KnowledgeSource(source_key="ops:mysql-old", name="Old MySQL Runbook", version_hash="b" * 64, status="STALE"),
        ])
        self.db.flush()
        self.db.add_all([
            KnowledgeChunk(source="ops:mysql-current", source_index=0, content="MySQL connection pool timeout; inspect Threads_connected and pool wait metrics."),
            KnowledgeChunk(source="ops:mysql-old", source_index=0, content="MySQL connection pool timeout old deprecated instructions."),
        ])
        self.db.commit()
        service = KnowledgeService(self.db, self.settings)
        results = service.retrieve("MySQL connection pool timeout", top_k=4)
        assert results
        assert all(item.source != "ops:mysql-old" for item in results)

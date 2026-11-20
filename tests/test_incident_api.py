import base64
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.incidents import router
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.models.entities import UserAccount


class NexusOpsIncidentApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        self.admin = self._add_user("admin-test", "admin-password", {"ROLE_ADMIN", "ROLE_USER"})
        self.engineer = self._add_user("engineer-test", "engineer-password", {"ROLE_ENGINEER", "ROLE_USER"})
        self.oncall = self._add_user("oncall-test", "oncall-password", {"ROLE_ONCALL", "ROLE_USER"})
        self.requester = self._add_user("requester-test", "requester-password", {"ROLE_USER"})

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = self._override_db
        self.client = TestClient(app)

    def _add_user(self, username, password, roles):
        user = UserAccount(
            username=username,
            display_name=username,
            password_hash=hash_password(password),
        )
        user.roles = roles
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _override_db(self):
        yield self.db

    @staticmethod
    def _auth(username, password):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_engineer_can_create_transition_and_add_note(self):
        headers = self._auth("engineer-test", "engineer-password")
        payload = {
            "title": "订单服务数据库连接失败",
            "description": "生产环境订单服务出现 MySQL connection timeout，接口返回 500。",
            "serviceName": "order-service",
            "environment": "PRODUCTION",
            "category": "DATABASE",
            "severity": "P1",
            "source": "MANUAL",
            "assignee": "oncall-test",
            "impactScope": "订单查询和创建接口受到影响",
        }
        created = self.client.post("/api/incidents", json=payload, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertTrue(body["incidentKey"].startswith("INC-"))
        self.assertEqual(body["status"], "NEW")
        self.assertEqual(len(body["history"]), 1)

        transitioned = self.client.post(
            f"/api/incidents/{body['id']}/transition",
            json={"status": "TRIAGED", "reason": "已确认生产环境影响", "expectedVersion": body["version"]},
            headers=headers,
        )
        self.assertEqual(transitioned.status_code, 200, transitioned.text)
        self.assertEqual(transitioned.json()["status"], "TRIAGED")
        self.assertEqual(len(transitioned.json()["history"]), 2)

        note = self.client.post(
            f"/api/incidents/{body['id']}/notes",
            json={"content": "检查数据库连接池和最大连接数。", "noteType": "INVESTIGATION"},
            headers=headers,
        )
        self.assertEqual(note.status_code, 201, note.text)
        self.assertEqual(note.json()["noteType"], "INVESTIGATION")

    def test_requester_can_only_see_own_incidents(self):
        requester_headers = self._auth("requester-test", "requester-password")
        engineer_headers = self._auth("engineer-test", "engineer-password")
        created = self.client.post(
            "/api/incidents",
            json={
                "title": "测试环境接口异常",
                "description": "测试环境健康检查接口超时。",
                "serviceName": "gateway",
                "environment": "TEST",
                "category": "API",
            },
            headers=requester_headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        incident_id = created.json()["id"]

        own = self.client.get(f"/api/incidents/{incident_id}", headers=requester_headers)
        self.assertEqual(own.status_code, 200)

        forbidden_update = self.client.patch(
            f"/api/incidents/{incident_id}",
            json={"severity": "P0"},
            headers=requester_headers,
        )
        self.assertEqual(forbidden_update.status_code, 403)

        visible_to_engineer = self.client.get("/api/incidents", headers=engineer_headers)
        self.assertEqual(visible_to_engineer.status_code, 200)
        self.assertEqual(visible_to_engineer.json()["total"], 1)

    def test_invalid_transition_returns_conflict(self):
        headers = self._auth("engineer-test", "engineer-password")
        created = self.client.post(
            "/api/incidents",
            json={
                "title": "Redis connection refused",
                "description": "Application cannot connect to Redis cache.",
                "serviceName": "session-service",
                "environment": "STAGING",
                "category": "CACHE",
            },
            headers=headers,
        )
        incident_id = created.json()["id"]
        invalid = self.client.post(
            f"/api/incidents/{incident_id}/transition",
            json={"status": "RESOLVED", "reason": "skip triage"},
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 409)

    def test_optimistic_version_check_prevents_stale_update(self):
        headers = self._auth("engineer-test", "engineer-password")
        created = self.client.post(
            "/api/incidents",
            json={
                "title": "Deployment failed",
                "description": "Release pipeline failed during deployment.",
                "serviceName": "release-service",
                "environment": "STAGING",
                "category": "DEPLOYMENT",
            },
            headers=headers,
        ).json()
        response = self.client.patch(
            f"/api/incidents/{created['id']}",
            json={"severity": "P1", "expectedVersion": created["version"] + 5},
            headers=headers,
        )
        self.assertEqual(response.status_code, 409)

    def test_admin_can_register_and_mark_knowledge_source_stale(self):
        headers = self._auth("admin-test", "admin-password")
        created = self.client.post(
            "/api/admin/knowledge-sources",
            json={
                "sourceKey": "mysql-connection-runbook",
                "name": "MySQL Connection Troubleshooting",
                "url": "https://dev.mysql.com/doc/",
                "versionHash": "a" * 64,
                "status": "ACTIVE",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        source_id = created.json()["id"]
        updated = self.client.patch(
            f"/api/admin/knowledge-sources/{source_id}/status",
            json={"status": "STALE"},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["status"], "STALE")


if __name__ == "__main__":
    unittest.main()

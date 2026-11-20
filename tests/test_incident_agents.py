import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import IncidentRecord, KnowledgeChunk, KnowledgeSource, UserAccount
from app.services.incident_analysis import IncidentAgentRuntimeService
from app.agents.ops_agents import _classify_operation


class IncidentAgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.actor = UserAccount(
            username="ops-engineer",
            display_name="Ops Engineer",
            password_hash=hash_password("secret"),
        )
        self.actor.roles = {"ROLE_ENGINEER", "ROLE_USER"}
        self.db.add(self.actor)
        self.db.commit()
        self.db.refresh(self.actor)
        self.incident = IncidentRecord(
            incident_key="INC-TEST-001",
            title="订单服务 MySQL connection timeout",
            description="生产环境订单查询接口大量返回 500，日志出现 MySQL connection timeout，部分用户无法下单。",
            service_name="order-service",
            environment="PRODUCTION",
            category="DATABASE",
            severity="P1",
            status="NEW",
            source="MANUAL",
            reporter_id=self.actor.id,
            assignee="oncall",
            impact_scope="订单查询和创建接口受到影响，影响范围待确认。",
            version=1,
        )
        self.db.add(self.incident)
        self.db.add(
            KnowledgeChunk(
                source="mysql-connection-runbook",
                source_index=0,
                content="MySQL connection timeout troubleshooting: inspect connection pool active, idle and waiting counts; review slow queries, lock waits, long transactions and recent configuration changes. Start with read-only diagnostics.",
            )
        )
        self.db.add(
            KnowledgeSource(
                source_key="mysql-connection-runbook",
                name="MySQL Connection Runbook",
                url="https://dev.mysql.com/doc/",
                version_hash="a" * 64,
                status="ACTIVE",
            )
        )
        self.db.add(
            KnowledgeChunk(
                source="old-mysql-runbook",
                source_index=0,
                content="MySQL connection timeout: restart the database and kill sessions immediately. This stale advice is retained only for status-filter tests.",
            )
        )
        self.db.add(
            KnowledgeSource(
                source_key="old-mysql-runbook",
                name="Old MySQL Runbook",
                url=None,
                version_hash="b" * 64,
                status="STALE",
            )
        )
        self.db.commit()
        self.settings = Settings(
            _env_file=None,
            ai_provider="mock",
            knowledge_vector_enabled=False,
            knowledge_vector_required=False,
            knowledge_embedding_provider="ollama",
            model_fallback_provider="mock",
            model_fallback_model="mock",
            incident_analysis_max_rounds=8,
            incident_analysis_min_confidence=0.5,
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_incident_analysis_runs_all_specialists_and_persists_trace(self):
        result = IncidentAgentRuntimeService(self.db, self.settings).analyze(self.incident.id, self.actor)

        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.incidentKey, "INC-TEST-001")
        self.assertEqual(result.understanding["inferredCategory"], "DATABASE")
        self.assertEqual(result.impactAssessment["suggestedSeverity"], "P1")
        self.assertGreaterEqual(result.retrievedContext["retrievedCount"], 1)
        self.assertTrue(all(item["sourceStatus"] != "STALE" for item in result.retrievedContext["evidence"]))
        self.assertFalse(result.resolutionPlan["rootCauseConfirmed"])
        self.assertFalse(result.operationGuard["executionAllowed"])
        self.assertEqual(result.operationGuard["decision"], "READ_ONLY_ACTIONS_APPROVED")
        actors = {item["actor"] for item in result.agentTrace}
        self.assertIn("IncidentUnderstandingAgent", actors)
        self.assertIn("ImpactAssessmentAgent", actors)
        self.assertIn("IncidentContextAgent", actors)
        self.assertIn("ResolutionAgent", actors)
        self.assertIn("OperationGuardAgent", actors)
        self.assertIn("CoordinatorAgent", actors)
        self.assertTrue(result.modelCalls)
        # A Mock response must never be advertised as real model-assisted analysis.
        self.assertFalse(result.resolutionPlan["modelAssisted"])
        self.assertEqual(result.resolutionPlan["modelProvider"], "mock")

        latest = IncidentAgentRuntimeService(self.db, self.settings).latest(self.incident.id, self.actor)
        self.assertEqual(latest.runId, result.runId)
        self.assertEqual(latest.status, "COMPLETED")

    def test_guard_requires_approval_for_mixed_read_and_start_instruction(self):
        decision, _ = _classify_operation(
            "Check MySQL service status; if stopped, start the MySQL service.",
            "PRODUCTION",
        )
        self.assertEqual(decision, "REQUIRES_APPROVAL")

    def test_guard_allows_explicit_read_only_diagnostic(self):
        decision, _ = _classify_operation(
            "只读检查数据库连接数、连接池 active/idle/waiting 指标。",
            "PRODUCTION",
        )
        self.assertEqual(decision, "READ_ONLY_APPROVED")

    def test_guard_blocks_destructive_instruction_even_with_read_only_words(self):
        decision, _ = _classify_operation(
            "检查数据库状态后执行 DROP TABLE orders。",
            "PRODUCTION",
        )
        self.assertEqual(decision, "BLOCKED")

    def test_analysis_requires_operator_role(self):
        requester = UserAccount(username="requester", display_name="Requester", password_hash=hash_password("secret"))
        requester.roles = {"ROLE_USER"}
        self.db.add(requester)
        self.db.commit()
        with self.assertRaises(Exception):
            IncidentAgentRuntimeService(self.db, self.settings).analyze(self.incident.id, requester)


if __name__ == "__main__":
    unittest.main()

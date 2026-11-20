"""Isolated SQLite + in-memory fake Redis tests for NexusOps incident memory.

No external Redis, MySQL, Chroma, model calls or production incident writes.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.models.entities import IncidentRecord, KnowledgeSource, UserAccount, IncidentContextSnapshot
from app.services.incident_context_memory import (
    IncidentContextMemory, bound_resolution_context, estimated_tokens,
)
from app.services.incident_analysis import IncidentAgentRuntimeService


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, seconds, value):
        self.data[key] = value
        self.ttls[key] = seconds

    def delete(self, key):
        self.data.pop(key, None)


class BrokenRedis(FakeRedis):
    def get(self, key):
        raise ConnectionError("Redis intentionally down")

    def setex(self, key, seconds, value):
        raise ConnectionError("Redis intentionally down")

    def delete(self, key):
        raise ConnectionError("Redis intentionally down")


class IncidentContextMemoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.settings = Settings(_env_file=None, knowledge_vector_enabled=False,
                                 ai_provider="mock", incident_context_token_budget=850)
        self.actor = UserAccount(username="test-operator", display_name="Test Operator", password_hash="not-used")
        self.actor.roles = {"ROLE_ENGINEER"}
        self.db.add(self.actor)
        self.db.flush()
        self.incident = self._new_incident("INC-001")
        self.db.add(KnowledgeSource(source_key="ops:test", name="Test Runbook",
                                    version_hash="a" * 64, status="ACTIVE"))
        self.db.commit()
        self.redis = FakeRedis()
        self.memory = IncidentContextMemory(self.db, self.settings, redis_client=self.redis)

    def _new_incident(self, key):
        incident = IncidentRecord(
            incident_key=key, title="MySQL 连接池等待", description="业务调用持续超时，先检查监控和连接池",
            service_name="order", environment="TEST", category="DATABASE", severity="P2",
            status="NEW", source="MANUAL", reporter_id=self.actor.id, assignee="oncall",
            impact_scope="订单接口", version=1,
        )
        self.db.add(incident)
        self.db.flush()
        return incident

    def _save(self, incident=None, run_id="run-001"):
        incident = incident or self.incident
        return self.memory.save(incident, run_id, "先检查数据库与上游网络",
                                {"symptomSummary": "连接池等待超时"},
                                {"impactScope": "订单接口", "suggestedSeverity": "P2"},
                                {"evidence": [{"source": "ops:test", "chunkId": 42,
                                               "excerpt": "只读检查"}]})

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_01_mysql_snapshot_and_redis_ttl(self):
        row = self._save()
        self.assertEqual(row.version, 1)
        self.assertEqual(self.memory.load(self.incident)["runId"], "run-001")
        self.assertEqual(self.redis.ttls[self.memory._cache_key(self.incident.id)], 3600)
        self.assertTrue(self.db.query(IncidentContextSnapshot).first())

    def test_02_redis_outage_falls_back_to_mysql(self):
        self._save()
        memory = IncidentContextMemory(self.db, self.settings, redis_client=BrokenRedis())
        self.assertEqual(memory.load(self.incident)["version"], 1)

    def test_03_incident_change_invalidates_cache_and_prior_snapshot(self):
        self._save()
        self.incident.description = "故障信息被更新"
        self.incident.version = 2
        self.db.commit()
        self.assertIsNone(self.memory.load(self.incident))
        self.assertNotIn(self.memory._cache_key(self.incident.id), self.redis.data)

    def test_04_runbook_update_invalidates_cache_and_snapshot(self):
        self._save()
        self.db.query(KnowledgeSource).filter_by(source_key="ops:test").first().version_hash = "b" * 64
        self.db.commit()
        self.assertIsNone(self.memory.load(self.incident))

    def test_05_snapshots_are_isolated_across_incidents(self):
        self._save()
        other = self._new_incident("INC-002")
        self.db.commit()
        self.assertIsNone(self.memory.load(other))
        self.assertTrue(self.memory.load(self.incident))

    def test_06_version_history_restore_and_stale_rejection(self):
        self._save()
        self._save(run_id="run-002")
        self.assertEqual([v["version"] for v in self.memory.versions(self.incident.id)], [2, 1])
        restored = self.memory.restore_version(self.incident, 1)
        self.assertEqual(restored["runId"], "run-001")
        self.assertEqual(self.memory.load(self.incident)["version"], 1)
        self.incident.version = 2
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.memory.restore_version(self.incident, 1)

    def test_07_save_is_idempotent_by_run_id(self):
        first = self._save()
        same = self._save()
        self.assertEqual(first.id, same.id)
        self.assertEqual(self.db.query(IncidentContextSnapshot).count(), 1)

    def test_08_budget_clips_excerpts_but_preserves_evidence_ids(self):
        payload = {
            "incident": {"key": "INC-001", "title": "timeout", "symptomSummary": "故障" * 150},
            "impact": {"impactScope": "影响" * 100, "assessmentReasons": ["测试"] * 3},
            "evidence": [{"source": f"ops:test-{i}", "chunkId": str(i), "excerpt": "证据" * 550}
                         for i in range(4)],
            "previousContext": {"summary": "历史" * 600, "sources": []},
        }
        bounded, report = bound_resolution_context(payload, 850)
        self.assertEqual(bounded["incident"]["key"], "INC-001")
        self.assertTrue(bounded["evidence"])
        self.assertEqual(bounded["evidence"][0]["chunkId"], "0")
        self.assertLess(estimated_tokens(bounded), estimated_tokens(payload))
        self.assertLessEqual(report["estimatedAfter"], report["budget"])

    def test_09_prompt_isolation_and_history_is_unverified(self):
        self._save()
        # Patch optional Redis (no real Redis) and verify a second analysis uses
        # previous context, but another incident doesn't receive the same history.
        with patch("app.services.incident_context_memory.IncidentContextMemory._redis", return_value=self.redis):
            first = IncidentAgentRuntimeService(self.db, self.settings).analyze(self.incident.id, self.actor)
            second = IncidentAgentRuntimeService(self.db, self.settings).analyze(self.incident.id, self.actor)
            self.assertEqual(second.resolutionPlan["restoredContextVersion"], 2)
            other = self._new_incident("INC-OTHER")
            self.db.commit()
            other_result = IncidentAgentRuntimeService(self.db, self.settings).analyze(other.id, self.actor)
            self.assertIsNone(other_result.resolutionPlan["restoredContextVersion"])
            self.assertFalse(second.resolutionPlan["rootCauseConfirmed"])

    def test_10_disabled_redis_uses_mysql(self):
        self._save()
        settings = Settings(_env_file=None, incident_context_cache_enabled=False)
        memory = IncidentContextMemory(self.db, settings, redis_client=BrokenRedis())
        self.assertEqual(memory.load(self.incident)["runId"], "run-001")


if __name__ == "__main__":
    unittest.main()

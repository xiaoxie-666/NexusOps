"""NexusOps incident-tool queue resilience acceptance tests.

Run in the existing app container:
    python scripts/test_incident_queue_resilience.py -v

ALL persistence happens in an in-memory SQLite test database. The real
MySQL/Chroma/Redis/Ollama instances are not read or written by these tests.
Tool execution is mocked for retry/dead-state tests. No email, network,
production restart, or irreversible command is executed.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.models.entities import (
    IncidentApproval,
    IncidentNote,
    IncidentRecord,
    IncidentToolAudit,
    IncidentToolJob,
    UserAccount,
)
from app.services import incident_tooling as tooling


class IncidentQueueResilienceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            'sqlite:///:memory:',
            connect_args={'check_same_thread': False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Factory()
        self.settings = Settings(
            _env_file=None,
            database_url='sqlite:///:memory:',
            app_env='development',
            incident_tool_queue_enabled=False,
            incident_tool_max_attempts=3,
            incident_tool_retry_delay_seconds=5.0,
            alert_email_delivery_mode='log',
        )
        self.actor = UserAccount(
            username='queue-test-engineer',
            display_name='Queue Test Engineer',
            password_hash='test-only-not-a-login',
            roles_csv='ROLE_ENGINEER,ROLE_USER',
        )
        self.db.add(self.actor)
        self.db.flush()
        self.incident = IncidentRecord(
            incident_key='INC-TEST-QUEUE-1',
            title='Synthetic queue test',
            description='Unit-test-only incident, never stored in MySQL.',
            service_name='test-service',
            environment='TEST',
            category='API',
            severity='P2',
            status='NEW',
            source='MANUAL',
            reporter_id=self.actor.id,
            assignee='unassigned',
        )
        self.db.add(self.incident)
        self.db.commit()
        self.db.refresh(self.actor)
        self.db.refresh(self.incident)
        self.service = tooling.IncidentToolService(self.db, self.settings)
        self.worker = tooling.IncidentToolQueueWorker(self.settings)
        self._session_patch = patch.object(tooling, 'SessionLocal', self.Factory)
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        self.db.close()
        self.engine.dispose()

    def enqueue(self, key='queue-test-1', content='safe diagnostic note'):
        return self.service.enqueue(
            incident_id=self.incident.id,
            actor=self.actor,
            tool_name='append_incident_note',
            payload={'content': content},
            idempotency_key=key,
        )

    def latest(self, job_id):
        self.db.expire_all()
        return self.db.get(IncidentToolJob, job_id)

    def due_now(self, job_id):
        row = self.latest(job_id)
        row.run_after = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        self.db.commit()

    def test_01_same_idempotency_key_creates_only_one_job(self):
        one = self.enqueue(key='same-key')
        two = self.enqueue(key='same-key')
        self.assertEqual(one.id, two.id)
        self.assertEqual(self.db.query(IncidentToolJob).count(), 1)

    def test_02_unknown_or_dangerous_tool_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            self.service.enqueue(
                self.incident.id,
                self.actor,
                'run_shell_command',
                {'command': 'unsafe-command-that-must-never-run'},
                'blocked-key',
            )
        self.assertEqual(self.db.query(IncidentToolJob).count(), 0)

    def test_03_valid_job_executes_with_audit(self):
        job = self.enqueue(key='note-audit')
        self.assertTrue(self.worker._dispatch_once())
        row = self.latest(job.id)
        self.assertEqual((row.status, row.attempts), ('SUCCESS', 1))
        self.assertEqual(self.db.query(IncidentNote).count(), 1)
        statuses = [r.status for r in self.db.query(IncidentToolAudit).all()]
        self.assertIn('QUEUED', statuses)
        self.assertIn('SUCCESS', statuses)

    def test_04_retry_after_transient_error_then_succeeds(self):
        job = self.enqueue(key='retry-then-success')
        with patch.object(self.worker, '_execute', side_effect=[RuntimeError('simulated timeout'), {'ok': True}]) as execute:
            self.assertTrue(self.worker._dispatch_once())
            first = self.latest(job.id)
            self.assertEqual((first.status, first.attempts), ('PENDING', 1))
            self.assertIn('simulated timeout', first.last_error)
            self.assertGreater(first.run_after, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=2))
            self.due_now(job.id)
            self.assertTrue(self.worker._dispatch_once())
            final = self.latest(job.id)
            self.assertEqual((final.status, final.attempts), ('SUCCESS', 2))
            self.assertEqual(execute.call_count, 2)
        statuses = [r.status for r in self.db.query(IncidentToolAudit).all()]
        self.assertIn('RETRY_SCHEDULED', statuses)
        self.assertIn('SUCCESS', statuses)

    def test_05_permanent_failure_reaches_dead_status(self):
        job = self.enqueue(key='always-fails')
        row = self.latest(job.id)
        row.max_attempts = 2
        self.db.commit()
        with patch.object(self.worker, '_execute', side_effect=RuntimeError('simulated persistent failure')) as execute:
            self.assertTrue(self.worker._dispatch_once())
            self.assertEqual(self.latest(job.id).status, 'PENDING')
            self.due_now(job.id)
            self.assertTrue(self.worker._dispatch_once())
            final = self.latest(job.id)
            self.assertEqual((final.status, final.attempts), ('DEAD', 2))
            self.assertEqual(execute.call_count, 2)
            self.assertFalse(self.worker._dispatch_once())
        statuses = [r.status for r in self.db.query(IncidentToolAudit).all()]
        self.assertIn('RETRY_SCHEDULED', statuses)
        self.assertIn('DEAD', statuses)

    def test_06_restart_recovers_running_jobs(self):
        job = self.enqueue(key='stuck-job')
        row = self.latest(job.id)
        row.status = 'RUNNING'
        row.attempts = 1
        self.db.commit()
        self.worker._recover_running()
        recovered = self.latest(job.id)
        self.assertEqual(recovered.status, 'PENDING')
        self.assertEqual(recovered.attempts, 1)
        self.assertIn('Recovered', recovered.last_error)

    def test_07_duplicate_note_side_effect_is_deduplicated(self):
        job = self.enqueue(key='same-side-effect')
        self.assertTrue(self.worker._dispatch_once())
        self.assertEqual(self.db.query(IncidentNote).count(), 1)
        row = self.latest(job.id)
        row.status = 'PENDING'  # simulate at-least-once redelivery of SAME job
        row.run_after = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        self.db.commit()
        self.assertTrue(self.worker._dispatch_once())
        final = self.latest(job.id)
        self.assertEqual((final.status, final.attempts), ('SUCCESS', 2))
        self.assertEqual(self.db.query(IncidentNote).count(), 1)

    def test_08_change_requires_approval_not_execution(self):
        approval = self.service.create_approval(
            incident_id=self.incident.id,
            actor=self.actor,
            action_key='restart_service',
            target='test-service',
            justification='Synthetic test requires review only, never execution.',
            payload={'testOnly': True},
        )
        self.assertEqual(approval.status, 'PENDING')
        self.assertEqual(self.db.query(IncidentApproval).count(), 1)
        self.assertEqual(self.db.query(IncidentToolJob).count(), 0)
        self.assertIn('REQUIRES_APPROVAL', [r.decision for r in self.db.query(IncidentToolAudit).all()])


if __name__ == '__main__':
    unittest.main(verbosity=2)

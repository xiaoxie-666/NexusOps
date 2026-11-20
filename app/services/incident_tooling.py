from __future__ import annotations

import json
import logging
import smtplib
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.models.entities import (
    IncidentApproval,
    IncidentNote,
    IncidentNotification,
    IncidentRecord,
    IncidentToolAudit,
    IncidentToolJob,
    UserAccount,
)

logger = logging.getLogger(__name__)

ALLOWED_TOOL_NAMES = {
    "append_incident_note",
    "export_incident_ledger",
    "send_oncall_notification",
    "request_human_approval",
}
APPROVAL_ACTIONS = {
    "restart_service",
    "rollback_deployment",
    "scale_service",
    "change_configuration",
    "database_failover",
    "rotate_secret",
    "other_change",
}


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IncidentToolService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def enqueue(
        self,
        incident_id: int,
        actor: UserAccount,
        tool_name: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> IncidentToolJob:
        if tool_name not in ALLOWED_TOOL_NAMES:
            raise ValueError(f"Tool '{tool_name}' is not in the allowlist")
        incident = self.db.get(IncidentRecord, incident_id)
        if incident is None:
            raise ValueError("Incident not found")
        encoded = _json(payload)
        if len(encoded) > 32_000:
            raise ValueError("Tool payload exceeds 32 KB")
        key = (idempotency_key or f"{incident_id}:{tool_name}:{uuid4().hex}").strip()
        if len(key) > 128:
            raise ValueError("Idempotency key must be at most 128 characters")
        existing = self.db.query(IncidentToolJob).filter(IncidentToolJob.idempotency_key == key).first()
        if existing is not None:
            return existing
        job = IncidentToolJob(
            incident_id=incident_id,
            created_by_user_id=actor.id,
            tool_name=tool_name,
            payload_json=encoded,
            idempotency_key=key,
            status="PENDING",
            attempts=0,
            max_attempts=max(1, self.settings.incident_tool_max_attempts),
            run_after=_now(),
            last_error="",
            result_json="{}",
        )
        self.db.add(job)
        self.db.flush()
        self._audit(
            incident_id=incident_id,
            job_id=job.id,
            actor_id=actor.id,
            tool_name=tool_name,
            decision="ALLOW",
            status="QUEUED",
            reason="Allowlisted non-destructive tool accepted for asynchronous execution",
            payload=payload,
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.query(IncidentToolJob).filter(IncidentToolJob.idempotency_key == key).first()
            if existing is not None:
                return existing
            raise
        self.db.refresh(job)
        return job

    def create_approval(
        self,
        incident_id: int,
        actor: UserAccount,
        action_key: str,
        target: str,
        justification: str,
        payload: dict[str, Any] | None = None,
    ) -> IncidentApproval:
        if action_key not in APPROVAL_ACTIONS:
            raise ValueError(f"Unsupported approval action: {action_key}")
        if self.db.get(IncidentRecord, incident_id) is None:
            raise ValueError("Incident not found")
        if len(justification.strip()) < 8:
            raise ValueError("Approval justification must contain at least 8 characters")
        approval = IncidentApproval(
            incident_id=incident_id,
            requested_by_user_id=actor.id,
            action_key=action_key,
            target=target[:256],
            justification=justification.strip()[:8000],
            payload_json=_json(payload or {}),
            status="PENDING",
        )
        self.db.add(approval)
        self.db.flush()
        self._audit(
            incident_id=incident_id,
            job_id=None,
            actor_id=actor.id,
            tool_name=action_key,
            decision="REQUIRES_APPROVAL",
            status="APPROVAL_REQUESTED",
            reason="State-changing operation is not executable by this platform; human review requested",
            payload={"target": target, "justification": justification, **(payload or {})},
        )
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def _audit(
        self,
        incident_id: int,
        job_id: int | None,
        actor_id: int | None,
        tool_name: str,
        decision: str,
        status: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            IncidentToolAudit(
                incident_id=incident_id,
                job_id=job_id,
                actor_user_id=actor_id,
                tool_name=tool_name,
                decision=decision,
                status=status,
                reason=reason,
                payload_json=_json(payload or {}),
                result_json=_json(result or {}),
            )
        )


class IncidentToolQueueWorker:
    """Single-process durable queue for allowlisted administrative tools.

    Jobs are at-least-once. Database-side actions are idempotent where possible;
    external email delivery may be retried if the process exits after send.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.settings.incident_tool_queue_enabled or self.thread is not None:
            return
        self._recover_running()
        self.thread = threading.Thread(target=self._loop, name="nexusops-incident-tools", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                did_work = self._dispatch_once()
            except Exception:
                logger.exception("Incident tool queue dispatch failed")
                did_work = False
            if not did_work:
                self.stop_event.wait(max(0.25, self.settings.incident_tool_poll_interval_seconds))

    def _dispatch_once(self) -> bool:
        db = SessionLocal()
        job_id: int | None = None
        try:
            now = _now()
            query = (
                db.query(IncidentToolJob)
                .filter(IncidentToolJob.status == "PENDING", IncidentToolJob.run_after <= now)
                .order_by(IncidentToolJob.created_at.asc(), IncidentToolJob.id.asc())
            )
            job = query.with_for_update(skip_locked=True).first()
            if job is None:
                return False
            job.status = "RUNNING"
            job.attempts += 1
            job.updated_at = now
            job_id = job.id
            db.commit()
        finally:
            db.close()
        if job_id is not None:
            self._run_job(job_id)
            return True
        return False

    def _run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.get(IncidentToolJob, job_id)
            if job is None or job.status != "RUNNING":
                return
            result = self._execute(db, job)
            job.status = "SUCCESS"
            job.result_json = _json(result)
            job.last_error = ""
            job.updated_at = _now()
            db.add(
                IncidentToolAudit(
                    incident_id=job.incident_id,
                    job_id=job.id,
                    actor_user_id=job.created_by_user_id,
                    tool_name=job.tool_name,
                    decision="ALLOW",
                    status="SUCCESS",
                    reason="Tool completed successfully",
                    payload_json=job.payload_json,
                    result_json=job.result_json,
                )
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(IncidentToolJob, job_id)
            if job is not None:
                message = f"{type(exc).__name__}: {exc}"[:4000]
                job.last_error = message
                job.updated_at = _now()
                if job.attempts >= job.max_attempts:
                    job.status = "DEAD"
                    audit_status = "DEAD"
                else:
                    job.status = "PENDING"
                    delay = self.settings.incident_tool_retry_delay_seconds * (2 ** max(0, job.attempts - 1))
                    job.run_after = _now() + timedelta(seconds=min(delay, 3600))
                    audit_status = "RETRY_SCHEDULED"
                db.add(
                    IncidentToolAudit(
                        incident_id=job.incident_id,
                        job_id=job.id,
                        actor_user_id=job.created_by_user_id,
                        tool_name=job.tool_name,
                        decision="ALLOW",
                        status=audit_status,
                        reason=message,
                        payload_json=job.payload_json,
                        result_json="{}",
                    )
                )
                db.commit()
        finally:
            db.close()

    def _execute(self, db: Session, job: IncidentToolJob) -> dict[str, Any]:
        if job.tool_name not in ALLOWED_TOOL_NAMES:
            raise RuntimeError("Tool is not allowlisted")
        incident = db.get(IncidentRecord, job.incident_id)
        if incident is None:
            raise RuntimeError("Incident no longer exists")
        payload = _loads(job.payload_json)

        if job.tool_name == "append_incident_note":
            content = str(payload.get("content") or "").strip()
            if not content:
                raise ValueError("content is required")
            existing = (
                db.query(IncidentNote)
                .filter(IncidentNote.incident_id == incident.id)
                .filter(IncidentNote.content == f"[tool:{job.idempotency_key}] {content}")
                .first()
            )
            if existing is None:
                actor = db.get(UserAccount, job.created_by_user_id)
                db.add(
                    IncidentNote(
                        incident_id=incident.id,
                        actor_user_id=job.created_by_user_id,
                        actor_name=actor.display_name if actor else "NexusOps Tool Worker",
                        note_type=str(payload.get("noteType") or "INVESTIGATION").upper()[:32],
                        content=f"[tool:{job.idempotency_key}] {content[:9000]}",
                    )
                )
            return {"incidentId": incident.id, "noteCreated": existing is None}

        if job.tool_name == "export_incident_ledger":
            return self._export_ledger(db, incident)

        if job.tool_name == "send_oncall_notification":
            return self._send_notification(db, incident, payload)

        if job.tool_name == "request_human_approval":
            service = IncidentToolService(db, self.settings)
            actor = db.get(UserAccount, job.created_by_user_id)
            if actor is None:
                raise RuntimeError("Approval requester account no longer exists")
            approval = service.create_approval(
                incident_id=incident.id,
                actor=actor,
                action_key=str(payload.get("actionKey") or "other_change"),
                target=str(payload.get("target") or incident.service_name),
                justification=str(payload.get("justification") or "Tool-requested approval"),
                payload=payload.get("details") if isinstance(payload.get("details"), dict) else {},
            )
            return {"approvalId": approval.id, "status": approval.status, "execution": "MANUAL_ONLY"}

        raise RuntimeError(f"Unsupported tool: {job.tool_name}")

    def _export_ledger(self, db: Session, incident: IncidentRecord) -> dict[str, Any]:
        rows = db.query(IncidentRecord).order_by(IncidentRecord.created_at.desc()).all()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Incidents"
        headers = ["Incident Key", "Title", "Service", "Environment", "Category", "Severity", "Status", "Assignee", "Source", "Created At", "Updated At"]
        sheet.append(headers)
        for row in rows:
            sheet.append([
                row.incident_key, row.title, row.service_name, row.environment, row.category,
                row.severity, row.status, row.assignee, row.source,
                row.created_at.isoformat() if row.created_at else "",
                row.updated_at.isoformat() if row.updated_at else "",
            ])
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(42, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
        path = Path(self.settings.excel_path)
        if not path.is_absolute():
            path = self.settings.project_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(path)
        return {"filePath": str(path), "downloadUrl": "/api/admin/exports/incident-ledger", "rowCount": len(rows), "triggerIncidentId": incident.id}

    def _send_notification(self, db: Session, incident: IncidentRecord, payload: dict[str, Any]) -> dict[str, Any]:
        recipients_raw = str(payload.get("recipients") or self.settings.oncall_notification_recipients or self.settings.alert_email_to or "")
        recipients = [item.strip() for item in recipients_raw.split(",") if item.strip()]
        subject = str(payload.get("subject") or f"[NexusOps] {incident.incident_key} {incident.title}")[:256]
        message = str(payload.get("message") or f"Incident {incident.incident_key}\nService: {incident.service_name}\nSeverity: {incident.severity}\nStatus: {incident.status}\n\n{incident.description}")[:12000]
        mode = (self.settings.alert_email_delivery_mode or "log").lower()
        channel = "LOG"
        status = "LOGGED"
        provider_message = "Notification recorded locally; SMTP delivery is not enabled."
        recipient_label = ",".join(recipients) or "on-call (not configured)"
        limit = max(1, int(self.settings.alert_email_rate_limit_per_minute))
        recent_count = db.query(IncidentNotification).filter(
            IncidentNotification.created_at >= _now() - timedelta(seconds=60)
        ).count()
        if recent_count >= limit:
            channel = "RATE_LIMIT"
            status = "RATE_LIMITED"
            provider_message = f"Notification rate limit reached ({limit} per minute); no message was sent."
        elif mode == "smtp":
            if not recipients or not self.settings.smtp_host or not self.settings.alert_email_from:
                raise RuntimeError("SMTP mode requires SMTP_HOST, ALERT_EMAIL_FROM, and at least one recipient")
            self._smtp_send(recipients, subject, message)
            channel = "SMTP"
            status = "SENT"
            provider_message = "SMTP server accepted the message"
        row = IncidentNotification(
            incident_id=incident.id,
            channel=channel,
            recipient=recipient_label,
            subject=subject,
            message=message,
            status=status,
            provider_message=provider_message,
        )
        db.add(row)
        db.flush()
        return {"notificationId": row.id, "channel": channel, "status": status, "recipient": recipient_label}

    def _smtp_send(self, recipients: list[str], subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.alert_email_from
        message["To"] = ", ".join(recipients)
        message["Subject"] = f"{self.settings.alert_email_subject_prefix} {subject}"[:300]
        message.set_content(body)
        timeout = self.settings.smtp_timeout_seconds
        if self.settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=timeout) as smtp:
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=timeout) as smtp:
                smtp.ehlo()
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)

    def _recover_running(self) -> None:
        db = SessionLocal()
        try:
            rows = db.query(IncidentToolJob).filter(IncidentToolJob.status == "RUNNING").all()
            for row in rows:
                row.status = "PENDING"
                row.run_after = _now()
                row.last_error = "Recovered after application restart; external side effects may require manual verification."
                row.updated_at = _now()
            db.commit()
        finally:
            db.close()


_worker: IncidentToolQueueWorker | None = None
_worker_lock = threading.Lock()


def get_incident_tool_worker(settings: Settings | None = None) -> IncidentToolQueueWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = IncidentToolQueueWorker(settings or get_settings())
        return _worker

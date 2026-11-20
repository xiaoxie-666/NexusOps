from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.entities import (
    AlertEvent,
    IncidentNote,
    IncidentRecord,
    IncidentStatusHistory,
    UserAccount,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _severity(value: Any) -> str:
    value = str(value or "warning").strip().lower()
    if value in {"critical", "emergency", "fatal", "page", "p0"}:
        return "P0"
    if value in {"high", "error", "major", "p1"}:
        return "P1"
    if value in {"warning", "warn", "medium", "p2"}:
        return "P2"
    if value in {"info", "low", "minor", "p3"}:
        return "P3"
    return "P2"


def _category(text: str, labels: dict[str, Any]) -> str:
    explicit = str(labels.get("category") or "").upper()
    allowed = {"DATABASE", "CACHE", "API", "NETWORK", "DEPLOYMENT", "RESOURCE", "SECURITY", "MIDDLEWARE"}
    if explicit in allowed:
        return explicit
    value = text.lower()
    rules = [
        ("SECURITY", ("breach", "unauthorized", "attack", "security", "leak", "入侵", "越权", "泄露")),
        ("DATABASE", ("mysql", "database", "sql", "deadlock", "数据库", "连接池", "死锁")),
        ("CACHE", ("redis", "cache", "缓存", "eviction")),
        ("NETWORK", ("dns", "tcp", "tls", "network", "网络", "连接超时")),
        ("DEPLOYMENT", ("deploy", "release", "pod", "rollout", "部署", "发布", "容器")),
        ("RESOURCE", ("cpu", "memory", "disk", "oom", "磁盘", "内存", "负载")),
        ("MIDDLEWARE", ("kafka", "rabbitmq", "queue", "消息队列", "积压")),
        ("API", ("http", "5xx", "500", "502", "503", "gateway", "接口")),
    ]
    for category, terms in rules:
        if any(term in value for term in terms):
            return category
    return "UNKNOWN"


class AlertIngestionService:
    """Normalize and deduplicate generic and Alertmanager-style webhooks."""

    def __init__(self, db: Session):
        self.db = db

    def ingest(self, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        alerts = payload.get("alerts")
        if isinstance(alerts, list):
            rows = [item for item in alerts if isinstance(item, dict)]
        else:
            rows = [payload]
        if not rows:
            raise HTTPException(422, "Webhook payload contains no alert objects")
        if len(rows) > 100:
            raise HTTPException(413, "A webhook request may contain at most 100 alerts")

        accepted: list[dict[str, Any]] = []
        for alert in rows:
            accepted.append(self._ingest_one(source, payload, alert))
        self.db.commit()
        return {
            "source": source,
            "received": len(rows),
            "created": sum(1 for item in accepted if item["created"]),
            "deduplicated": sum(1 for item in accepted if not item["created"]),
            "alerts": accepted,
        }

    def _ingest_one(self, source: str, envelope: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        if not labels:
            labels = envelope.get("commonLabels") if isinstance(envelope.get("commonLabels"), dict) else {}
        if not annotations:
            annotations = envelope.get("commonAnnotations") if isinstance(envelope.get("commonAnnotations"), dict) else {}

        name = str(labels.get("alertname") or alert.get("alertName") or alert.get("name") or envelope.get("alertName") or "GenericAlert")[:200]
        service = str(
            labels.get("service") or labels.get("service_name") or labels.get("app")
            or labels.get("job") or alert.get("serviceName") or envelope.get("serviceName") or "unknown-service"
        )[:128]
        environment = str(labels.get("environment") or labels.get("env") or alert.get("environment") or envelope.get("environment") or "UNKNOWN").upper()[:32]
        severity = _severity(labels.get("severity") or alert.get("severity") or envelope.get("severity"))
        status = str(alert.get("status") or envelope.get("status") or "firing").upper()
        status = "RESOLVED" if status in {"RESOLVED", "OK", "INACTIVE"} else "FIRING"
        summary = str(annotations.get("summary") or alert.get("summary") or envelope.get("summary") or name)[:4000]
        description = str(annotations.get("description") or alert.get("description") or envelope.get("description") or summary)[:12000]
        external_id = str(alert.get("fingerprint") or alert.get("id") or alert.get("externalId") or "")[:256] or None
        starts_at = alert.get("startsAt") or alert.get("starts_at") or envelope.get("startsAt") or ""
        supplied_fingerprint = str(alert.get("fingerprint") or alert.get("externalId") or "")
        if supplied_fingerprint:
            raw_identity = f"{source}|{supplied_fingerprint}|{starts_at}"
        else:
            raw_identity = "|".join(
                [source, name, service, environment, summary, str(starts_at)]
            )
        fingerprint = hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()
        now = _now()
        event_time = _parse_time(alert.get("startsAt") or alert.get("starts_at")) or now

        event = self.db.query(AlertEvent).filter(AlertEvent.fingerprint == fingerprint).first()
        created = event is None
        if event is None:
            event = AlertEvent(
                source=source[:48],
                external_id=external_id,
                fingerprint=fingerprint,
                alert_name=name,
                service_name=service,
                environment=environment,
                severity=severity,
                status=status,
                summary=summary,
                description=description,
                labels_json=_json(labels),
                annotations_json=_json(annotations),
                occurrence_count=1,
                first_seen_at=event_time,
                last_seen_at=now,
            )
            self.db.add(event)
            self.db.flush()
            if status == "FIRING":
                self._create_incident(event)
        else:
            event.occurrence_count += 1
            event.last_seen_at = now
            event.status = status
            event.severity = severity
            event.summary = summary
            event.description = description
            event.labels_json = _json(labels)
            event.annotations_json = _json(annotations)
            if status == "RESOLVED" and event.incident_id:
                self._append_system_note(
                    event.incident_id,
                    f"监控告警 {event.alert_name} 已恢复（Webhook source={source}）。请人工确认服务指标恢复后再关闭 Incident。",
                    self._system_actor_id(),
                    source,
                )
        self.db.flush()
        return {
            "id": event.id,
            "fingerprint": event.fingerprint,
            "alertName": event.alert_name,
            "serviceName": event.service_name,
            "severity": event.severity,
            "status": event.status,
            "incidentId": event.incident_id,
            "occurrenceCount": event.occurrence_count,
            "created": created,
        }

    def _system_actor_id(self) -> int:
        actor = (
            self.db.query(UserAccount)
            .filter(UserAccount.roles_csv.like("%ROLE_ADMIN%"))
            .order_by(UserAccount.id.asc())
            .first()
        )
        if actor is None:
            actor = self.db.query(UserAccount).order_by(UserAccount.id.asc()).first()
        if actor is None:
            raise HTTPException(503, "No bootstrap operator account is available for webhook attribution")
        return actor.id

    def _create_incident(self, event: AlertEvent) -> None:
        actor_id = self._system_actor_id()
        text = f"{event.alert_name} {event.summary} {event.description}"
        incident = IncidentRecord(
            incident_key=self._new_incident_key(),
            title=f"[告警] {event.alert_name}"[:200],
            description=event.description or event.summary,
            service_name=event.service_name,
            environment=event.environment,
            category=_category(text, self._loads(event.labels_json)),
            severity=event.severity,
            status="NEW",
            source="WEBHOOK",
            reporter_id=actor_id,
            assignee="unassigned",
            impact_scope=f"自动接入告警；来源={event.source}；告警指纹={event.fingerprint[:12]}",
            version=1,
        )
        self.db.add(incident)
        self.db.flush()
        self.db.add(
            IncidentStatusHistory(
                incident_id=incident.id,
                from_status=None,
                to_status="NEW",
                reason=f"Created from {event.source} webhook alert {event.alert_name}",
                actor_user_id=actor_id,
                actor_name="NexusOps Webhook",
            )
        )
        self.db.add(
            IncidentNote(
                incident_id=incident.id,
                actor_user_id=actor_id,
                actor_name="NexusOps Webhook",
                note_type="SYSTEM",
                content=f"自动创建自 {event.source} 告警。Severity={event.severity}; Environment={event.environment}; Summary={event.summary}",
            )
        )
        event.incident_id = incident.id

    def _new_incident_key(self) -> str:
        from secrets import token_hex
        from app.core.config import get_settings

        prefix = get_settings().incident_key_prefix or "INC"
        return f"{prefix}-{datetime.now(timezone.utc).replace(tzinfo=None):%Y%m%d%H%M%S}-{token_hex(3).upper()}"

    def _append_system_note(self, incident_id: int, content: str, actor_id: int, source: str) -> None:
        self.db.add(
            IncidentNote(
                incident_id=incident_id,
                actor_user_id=actor_id,
                actor_name=f"{source} Webhook",
                note_type="SYSTEM",
                content=content,
            )
        )

    @staticmethod
    def _loads(value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

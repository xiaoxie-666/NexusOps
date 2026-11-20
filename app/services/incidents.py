from __future__ import annotations

from datetime import datetime, timezone
from secrets import token_hex
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.enums import IncidentSeverity, IncidentStatus
from app.models.entities import (
    IncidentNote,
    IncidentRecord,
    IncidentStatusHistory,
    KnowledgeSource,
    UserAccount,
)
from app.schemas.dtos import (
    IncidentCreateRequest,
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentNoteCreateRequest,
    IncidentNoteResponse,
    IncidentResponse,
    IncidentStatusHistoryResponse,
    IncidentTransitionRequest,
    IncidentUpdateRequest,
    KnowledgeSourceCreateRequest,
    KnowledgeSourceResponse,
)


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"TRIAGED"},
    "TRIAGED": {"ACKNOWLEDGED", "INVESTIGATING"},
    "ACKNOWLEDGED": {"INVESTIGATING"},
    "INVESTIGATING": {"MITIGATED", "RESOLVED"},
    "MITIGATED": {"INVESTIGATING", "RESOLVED"},
    "RESOLVED": {"CLOSED", "INVESTIGATING"},
    "CLOSED": set(),
}


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


class IncidentService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def create(self, request: IncidentCreateRequest, actor: UserAccount) -> IncidentDetailResponse:
        incident = IncidentRecord(
            incident_key=self._new_incident_key(),
            title=request.title,
            description=request.description,
            service_name=request.serviceName,
            environment=_enum_value(request.environment),
            category=_enum_value(request.category),
            severity=_enum_value(request.severity),
            status=IncidentStatus.NEW.value,
            source=_enum_value(request.source),
            reporter_id=actor.id,
            assignee=request.assignee or "unassigned",
            impact_scope=request.impactScope or "",
            version=1,
        )
        self.db.add(incident)
        self.db.flush()
        self.db.add(
            IncidentStatusHistory(
                incident_id=incident.id,
                from_status=None,
                to_status=IncidentStatus.NEW.value,
                reason="Incident created",
                actor_user_id=actor.id,
                actor_name=actor.display_name or actor.username,
            )
        )
        self.db.commit()
        self.db.refresh(incident)
        return self.detail(incident.id, actor)

    def list(
        self,
        actor: UserAccount,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        severity: str | None = None,
        service_name: str | None = None,
        keyword: str | None = None,
    ) -> IncidentListResponse:
        query = self.db.query(IncidentRecord)
        if not self._is_operator(actor):
            query = query.filter(IncidentRecord.reporter_id == actor.id)
        if status:
            query = query.filter(IncidentRecord.status == status.upper())
        if severity:
            query = query.filter(IncidentRecord.severity == severity.upper())
        if service_name:
            query = query.filter(IncidentRecord.service_name == service_name)
        if keyword:
            like = f"%{keyword.strip()}%"
            query = query.filter(
                (IncidentRecord.title.ilike(like))
                | (IncidentRecord.description.ilike(like))
                | (IncidentRecord.incident_key.ilike(like))
            )
        total = query.count()
        rows = (
            query.order_by(desc(IncidentRecord.created_at), desc(IncidentRecord.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        reporter_ids = {row.reporter_id for row in rows}
        users = {
            user.id: user.username
            for user in self.db.query(UserAccount).filter(UserAccount.id.in_(reporter_ids)).all()
        } if reporter_ids else {}
        return IncidentListResponse(
            items=[self._incident_response(row, users.get(row.reporter_id, "unknown")) for row in rows],
            total=total,
            page=page,
            pageSize=page_size,
        )

    def detail(self, incident_id: int, actor: UserAccount) -> IncidentDetailResponse:
        query = self.db.query(IncidentRecord).options(
            joinedload(IncidentRecord.notes), joinedload(IncidentRecord.status_history)
        )
        incident = query.filter(IncidentRecord.id == incident_id).first()
        if incident is None or (not self._is_operator(actor) and incident.reporter_id != actor.id):
            raise HTTPException(404, "Incident not found")
        reporter = self.db.get(UserAccount, incident.reporter_id)
        base = self._incident_response(incident, reporter.username if reporter else "unknown").model_dump()
        notes = [
            IncidentNoteResponse(
                id=item.id,
                incidentId=item.incident_id,
                actorName=item.actor_name,
                noteType=item.note_type,
                content=item.content,
                createdAt=item.created_at,
            )
            for item in incident.notes
        ]
        history = [
            IncidentStatusHistoryResponse(
                id=item.id,
                incidentId=item.incident_id,
                fromStatus=item.from_status,
                toStatus=item.to_status,
                reason=item.reason,
                actorName=item.actor_name,
                createdAt=item.created_at,
            )
            for item in incident.status_history
        ]
        return IncidentDetailResponse(**base, notes=notes, history=history)

    def update(self, incident_id: int, request: IncidentUpdateRequest, actor: UserAccount) -> IncidentDetailResponse:
        incident = self._get_for_update(incident_id)
        self._assert_visible(incident, actor)
        if request.expectedVersion is not None and request.expectedVersion != incident.version:
            raise HTTPException(409, "Incident was modified by another request; reload and retry")

        changes = request.model_dump(exclude_unset=True, exclude={"expectedVersion"})
        if not changes:
            return self.detail(incident_id, actor)
        mapping = {
            "title": "title",
            "description": "description",
            "serviceName": "service_name",
            "environment": "environment",
            "category": "category",
            "severity": "severity",
            "assignee": "assignee",
            "impactScope": "impact_scope",
        }
        for key, value in changes.items():
            if key in mapping and value is not None:
                setattr(incident, mapping[key], _enum_value(value))
        incident.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        incident.version += 1
        self.db.commit()
        return self.detail(incident_id, actor)

    def transition(
        self, incident_id: int, request: IncidentTransitionRequest, actor: UserAccount
    ) -> IncidentDetailResponse:
        incident = self._get_for_update(incident_id)
        self._assert_visible(incident, actor)
        if request.expectedVersion is not None and request.expectedVersion != incident.version:
            raise HTTPException(409, "Incident was modified by another request; reload and retry")

        target = _enum_value(request.status)
        current = incident.status
        if target not in ALLOWED_TRANSITIONS.get(current, set()):
            raise HTTPException(409, f"Invalid incident transition: {current} -> {target}")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        incident.status = target
        incident.updated_at = now
        incident.version += 1
        if target == IncidentStatus.ACKNOWLEDGED.value and incident.acknowledged_at is None:
            incident.acknowledged_at = now
        if target == IncidentStatus.RESOLVED.value:
            incident.resolved_at = now
            if request.reason:
                incident.resolution_summary = request.reason
        if target == IncidentStatus.CLOSED.value:
            incident.closed_at = now
        self.db.add(
            IncidentStatusHistory(
                incident_id=incident.id,
                from_status=current,
                to_status=target,
                reason=request.reason or "",
                actor_user_id=actor.id,
                actor_name=actor.display_name or actor.username,
            )
        )
        self.db.commit()
        return self.detail(incident_id, actor)

    def add_note(
        self, incident_id: int, request: IncidentNoteCreateRequest, actor: UserAccount
    ) -> IncidentNoteResponse:
        incident = self._get_for_update(incident_id)
        self._assert_visible(incident, actor)
        note = IncidentNote(
            incident_id=incident.id,
            actor_user_id=actor.id,
            actor_name=actor.display_name or actor.username,
            note_type=_enum_value(request.noteType),
            content=request.content,
        )
        self.db.add(note)
        incident.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        incident.version += 1
        self.db.commit()
        self.db.refresh(note)
        return IncidentNoteResponse(
            id=note.id,
            incidentId=note.incident_id,
            actorName=note.actor_name,
            noteType=note.note_type,
            content=note.content,
            createdAt=note.created_at,
        )

    def create_knowledge_source(self, request: KnowledgeSourceCreateRequest) -> KnowledgeSourceResponse:
        existing = self.db.query(KnowledgeSource).filter(KnowledgeSource.source_key == request.sourceKey).first()
        if existing:
            raise HTTPException(409, "Knowledge source key already exists")
        source = KnowledgeSource(
            source_key=request.sourceKey,
            name=request.name,
            url=str(request.url) if request.url else None,
            version_hash=request.versionHash.lower(),
            status=_enum_value(request.status),
            reviewed_at=request.reviewedAt,
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return self._knowledge_source_response(source)

    def list_knowledge_sources(self) -> list[KnowledgeSourceResponse]:
        rows = self.db.query(KnowledgeSource).order_by(KnowledgeSource.name.asc()).all()
        return [self._knowledge_source_response(row) for row in rows]

    def set_knowledge_source_status(self, source_id: int, status: str) -> KnowledgeSourceResponse:
        source = self.db.get(KnowledgeSource, source_id)
        if source is None:
            raise HTTPException(404, "Knowledge source not found")
        source.status = status
        source.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if status == "ACTIVE":
            source.reviewed_at = source.updated_at
        self.db.commit()
        self.db.refresh(source)
        return self._knowledge_source_response(source)

    def _new_incident_key(self) -> str:
        prefix = self.settings.incident_key_prefix.upper()
        stamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d")
        # A unique constraint is the final collision guard.
        return f"{prefix}-{stamp}-{token_hex(3).upper()}"

    def _get_for_update(self, incident_id: int) -> IncidentRecord:
        row = (
            self.db.query(IncidentRecord)
            .filter(IncidentRecord.id == incident_id)
            .with_for_update()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Incident not found")
        return row

    @staticmethod
    def _is_operator(actor: UserAccount) -> bool:
        return bool({"ROLE_ADMIN", "ROLE_ENGINEER", "ROLE_ONCALL"}.intersection(set(actor.roles)))

    @classmethod
    def _assert_visible(cls, incident: IncidentRecord, actor: UserAccount) -> None:
        if not cls._is_operator(actor) and incident.reporter_id != actor.id:
            raise HTTPException(404, "Incident not found")

    @staticmethod
    def _incident_response(row: IncidentRecord, reporter_username: str) -> IncidentResponse:
        return IncidentResponse(
            id=row.id,
            incidentKey=row.incident_key,
            title=row.title,
            description=row.description,
            serviceName=row.service_name,
            environment=row.environment,
            category=row.category,
            severity=row.severity,
            status=row.status,
            source=row.source,
            reporterUsername=reporter_username,
            assignee=row.assignee,
            impactScope=row.impact_scope,
            resolutionSummary=row.resolution_summary,
            version=row.version,
            createdAt=row.created_at,
            updatedAt=row.updated_at,
            acknowledgedAt=row.acknowledged_at,
            resolvedAt=row.resolved_at,
            closedAt=row.closed_at,
        )

    @staticmethod
    def _knowledge_source_response(row: KnowledgeSource) -> KnowledgeSourceResponse:
        return KnowledgeSourceResponse(
            id=row.id,
            sourceKey=row.source_key,
            name=row.name,
            url=row.url,
            versionHash=row.version_hash,
            status=row.status,
            reviewedAt=row.reviewed_at,
            createdAt=row.created_at,
            updatedAt=row.updated_at,
        )

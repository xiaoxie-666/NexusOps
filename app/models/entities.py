from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(128))
    roles_csv: Mapped[str] = mapped_column(String(256), default="ROLE_USER")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    @property
    def roles(self) -> list[str]:
        return [role for role in self.roles_csv.split(",") if role]

    @roles.setter
    def roles(self, value: list[str] | set[str]) -> None:
        self.roles_csv = ",".join(sorted(value))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(256), index=True)
    source_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class IncidentRecord(Base):
    """A reportable engineering/operations incident."""

    __tablename__ = "incident_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text)
    service_name: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    category: Mapped[str] = mapped_column(String(48), default="UNKNOWN", index=True)
    severity: Mapped[str] = mapped_column(String(8), default="P2", index=True)
    status: Mapped[str] = mapped_column(String(24), default="NEW", index=True)
    source: Mapped[str] = mapped_column(String(24), default="MANUAL")
    reporter_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    assignee: Mapped[str] = mapped_column(String(128), default="unassigned", index=True)
    impact_scope: Mapped[str] = mapped_column(Text, default="")
    resolution_summary: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    notes: Mapped[list["IncidentNote"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="IncidentNote.created_at"
    )
    status_history: Mapped[list["IncidentStatusHistory"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="IncidentStatusHistory.created_at"
    )


class IncidentNote(Base):
    __tablename__ = "incident_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    actor_name: Mapped[str] = mapped_column(String(128))
    note_type: Mapped[str] = mapped_column(String(32), default="COMMENT")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    incident: Mapped[IncidentRecord] = relationship(back_populates="notes")


class IncidentStatusHistory(Base):
    __tablename__ = "incident_status_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    actor_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    incident: Mapped[IncidentRecord] = relationship(back_populates="status_history")


class KnowledgeSource(Base):
    """Version and freshness metadata for an indexed knowledge source."""

    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    version_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class IncidentAgentRun(Base):
    """Auditable output of one NexusOps multi-Agent incident analysis run."""

    __tablename__ = "incident_agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    initiated_by_user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="COMPLETED", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    understanding_json: Mapped[str] = mapped_column(Text, default="{}")
    impact_json: Mapped[str] = mapped_column(Text, default="{}")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    resolution_json: Mapped[str] = mapped_column(Text, default="{}")
    guard_json: Mapped[str] = mapped_column(Text, default="{}")
    agent_events_json: Mapped[str] = mapped_column(Text, default="[]")
    agent_tasks_json: Mapped[str] = mapped_column(Text, default="[]")
    model_calls_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AlertEvent(Base):
    """Normalized alert received from a monitoring or webhook integration."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(48), index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    alert_name: Mapped[str] = mapped_column(String(200), index=True)
    service_name: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    severity: Mapped[str] = mapped_column(String(8), default="P2", index=True)
    status: Mapped[str] = mapped_column(String(24), default="FIRING", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    labels_json: Mapped[str] = mapped_column(Text, default="{}")
    annotations_json: Mapped[str] = mapped_column(Text, default="{}")
    incident_id: Mapped[Optional[int]] = mapped_column(ForeignKey("incident_records.id"), nullable=True, index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class IncidentApproval(Base):
    """Human approval record for a proposed state-changing operation."""

    __tablename__ = "incident_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    action_key: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(256), default="")
    justification: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    reviewed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class IncidentToolJob(Base):
    """Durable asynchronous job for an allowlisted, non-destructive tool."""

    __tablename__ = "incident_tool_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class IncidentToolAudit(Base):
    """Append-only audit record for tool authorization and execution."""

    __tablename__ = "incident_tool_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("incident_tool_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32), default="ALLOW", index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class IncidentNotification(Base):
    """Delivery/log record for an incident notification."""

    __tablename__ = "incident_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="LOG", index=True)
    recipient: Mapped[str] = mapped_column(String(512), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="LOGGED", index=True)
    provider_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class IncidentContextSnapshot(Base):
    """Immutable, versioned incident context; Redis only caches a copy."""

    __tablename__ = "incident_context_snapshots"
    __table_args__ = (UniqueConstraint("incident_id", "version", name="uq_incident_context_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident_records.id", ondelete="CASCADE"), nullable=False, index=True)
    run_public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    incident_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_facts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now, index=True)

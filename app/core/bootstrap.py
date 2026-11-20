from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.security import hash_password
from app.models.entities import (
    AlertEvent,
    IncidentAgentRun,
    IncidentContextSnapshot,
    IncidentApproval,
    IncidentNote,
    IncidentNotification,
    IncidentRecord,
    IncidentStatusHistory,
    IncidentToolAudit,
    IncidentToolJob,
    KnowledgeChunk,
    KnowledgeSource,
    UserAccount,
)


def create_schema() -> None:
    """Create only the active NexusOps tables as a local-dev safety net.

    Production/container startup runs Alembic first. This selective create_all
    intentionally does not create unrelated legacy tables.
    It also never drops or mutates any existing table or data.
    """
    active_tables = [
        UserAccount.__table__,
        KnowledgeChunk.__table__,
        IncidentRecord.__table__,
        IncidentNote.__table__,
        IncidentStatusHistory.__table__,
        KnowledgeSource.__table__,
        IncidentAgentRun.__table__,
        IncidentContextSnapshot.__table__,
        AlertEvent.__table__,
        IncidentApproval.__table__,
        IncidentToolJob.__table__,
        IncidentToolAudit.__table__,
        IncidentNotification.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=active_tables)


def seed_data(db: Session) -> None:
    """Create demo operator accounts; create only NexusOps operator accounts."""
    settings = get_settings()
    demo_accounts = [
        ("admin", "NexusOps Administrator", settings.bootstrap_admin_password, {"ROLE_ADMIN", "ROLE_USER"}),
        ("engineer", "Demo Engineer", settings.bootstrap_engineer_password, {"ROLE_ENGINEER", "ROLE_USER"}),
        ("oncall", "Demo On-call Engineer", settings.bootstrap_oncall_password, {"ROLE_ONCALL", "ROLE_USER"}),
    ]
    changed = False
    for username, display_name, password, roles in demo_accounts:
        existing = db.query(UserAccount).filter(UserAccount.username == username).first()
        if existing is None:
            account = UserAccount(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
            )
            account.roles = roles
            db.add(account)
            changed = True
    if changed:
        db.commit()

    if settings.ops_knowledge_auto_seed:
        seed_builtin_ops_knowledge(db)


def seed_builtin_ops_knowledge(db: Session) -> int:
    """Seed bundled ops runbooks into MySQL without requiring an embedding API."""
    import hashlib
    from datetime import datetime, timezone

    from app.models.entities import KnowledgeChunk, KnowledgeSource
    from app.services.knowledge import chunk_text

    settings = get_settings()
    root = Path(__file__).resolve().parents[1] / "knowledge_ops"
    if not root.exists():
        return 0
    total = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for path in sorted(root.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        source_key = f"ops:{path.stem}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source = db.query(KnowledgeSource).filter(KnowledgeSource.source_key == source_key).first()
        existing = (
            db.query(KnowledgeChunk)
            .filter(KnowledgeChunk.source == source_key)
            .order_by(KnowledgeChunk.source_index.asc())
            .all()
        )
        chunks = chunk_text(content, settings.knowledge_chunk_size, settings.knowledge_chunk_overlap)
        current_text = [item.content for item in existing]
        if current_text != chunks:
            db.query(KnowledgeChunk).filter(KnowledgeChunk.source == source_key).delete(synchronize_session=False)
            for index, chunk in enumerate(chunks):
                db.add(KnowledgeChunk(source=source_key, source_index=index, content=chunk))
        if source is None:
            source = KnowledgeSource(
                source_key=source_key,
                name=path.stem.replace("_", " ").title(),
                url=None,
                version_hash=digest,
                status="ACTIVE",
                reviewed_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(source)
        else:
            if source.version_hash != digest:
                # A bundled Runbook changed since the last review. Keep the
                # new chunks available for audit but require an administrator
                # to review/reactivate the source before it is retrieved.
                source.version_hash = digest
                source.status = "STALE"
                source.updated_at = now
        total += len(chunks)
    db.commit()
    return total

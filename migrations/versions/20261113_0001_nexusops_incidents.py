"""Add NexusOps incident and knowledge-source tables.

Revision ID: 20261113_0001
Revises:
Create Date: 2026-11-13

This is an additive migration. It never drops or renames other application tables.
"""
from alembic import op

from app.models.entities import (
    IncidentNote,
    IncidentRecord,
    IncidentStatusHistory,
    KnowledgeSource,
    UserAccount,
)

revision = "20261113_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Incident records reference user_accounts. Create that shared identity
    # table only when absent; do not alter or drop it when it already exists.
    UserAccount.__table__.create(bind=bind, checkfirst=True)
    for table in (
        IncidentRecord.__table__,
        IncidentNote.__table__,
        IncidentStatusHistory.__table__,
        KnowledgeSource.__table__,
    ):
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    # Only remove tables introduced by this revision. user_accounts and all
    # legacy other application tables/data are intentionally preserved.
    for table in (
        KnowledgeSource.__table__,
        IncidentStatusHistory.__table__,
        IncidentNote.__table__,
        IncidentRecord.__table__,
    ):
        table.drop(bind=bind, checkfirst=True)

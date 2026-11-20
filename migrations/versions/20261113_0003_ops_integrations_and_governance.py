"""Add webhook alerts, approvals, incident tool jobs and audit records.

Revision ID: 20261113_0003
Revises: 20261113_0002
Create Date: 2026-11-13

All changes are additive. Existing Phase 1/2 tables and data are preserved.
"""
from alembic import op

from app.models.entities import (
    AlertEvent,
    IncidentApproval,
    IncidentNotification,
    IncidentToolAudit,
    IncidentToolJob,
)

revision = "20261113_0003"
down_revision = "20261113_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in (
        AlertEvent.__table__,
        IncidentApproval.__table__,
        IncidentToolJob.__table__,
        IncidentToolAudit.__table__,
        IncidentNotification.__table__,
    ):
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        IncidentNotification.__table__,
        IncidentToolAudit.__table__,
        IncidentToolJob.__table__,
        IncidentApproval.__table__,
        AlertEvent.__table__,
    ):
        table.drop(bind=bind, checkfirst=True)

"""Add auditable multi-Agent incident analysis runs.

Revision ID: 20261113_0002
Revises: 20261113_0001
Create Date: 2026-11-13

This migration is additive and only creates incident_agent_runs.
"""
from alembic import op

from app.models.entities import IncidentAgentRun

revision = "20261113_0002"
down_revision = "20261113_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    IncidentAgentRun.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    IncidentAgentRun.__table__.drop(bind=op.get_bind(), checkfirst=True)

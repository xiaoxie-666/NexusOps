"""Versioned incident context snapshots; additive, no legacy table deletions.

Revision ID: 20261115_0004
Revises: 20261113_0003
"""
from alembic import op
from app.models.entities import IncidentContextSnapshot

revision = "20261115_0004"
down_revision = "20261113_0003"
branch_labels = None
depends_on = None

def upgrade() -> None:
    IncidentContextSnapshot.__table__.create(bind=op.get_bind(), checkfirst=True)

def downgrade() -> None:
    IncidentContextSnapshot.__table__.drop(bind=op.get_bind(), checkfirst=True)

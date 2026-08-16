"""Add durable hosted scanner heartbeat and lease.

Revision ID: 20260815_02
Revises: 20260815_01
"""
from alembic import op

from app.persistence.schema import scanner_heartbeats

revision = "20260815_02"
down_revision = "20260815_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 01 historically imports live metadata. checkfirst keeps clean installs
    # compatible while still adding the table to already-deployed Stage 2 databases.
    scanner_heartbeats.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    scanner_heartbeats.drop(op.get_bind(), checkfirst=True)

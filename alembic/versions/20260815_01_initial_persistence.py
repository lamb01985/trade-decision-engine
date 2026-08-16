"""Initial application-owned durable persistence schema.

Revision ID: 20260815_01
Revises: None
"""
from alembic import op

from app.persistence.schema import metadata

revision = "20260815_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The reviewed table definitions live in one source shared by runtime tests and migrations.
    metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    for table in reversed(metadata.sorted_tables):
        # Later revisions may add tables to live metadata; revision 01 must only
        # remove the original Stage 2 schema it created.
        if table.name in {"scanner_heartbeats", "setup_instances", "setup_observations",
                          "setup_outcome_assessments", "research_scan_cycles",
                          "research_scan_symbol_coverage"}:
            continue
        op.drop_table(table.name)

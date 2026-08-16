"""Add append-only Phase 4 research measurement ledger.

Revision ID: 20260816_03
Revises: 20260815_02
"""
from alembic import op

from app.persistence.schema import (research_scan_cycles,
    research_scan_symbol_coverage, setup_instances, setup_observations,
    setup_outcome_assessments)

revision = "20260816_03"
down_revision = "20260815_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 01 imports live metadata. checkfirst therefore supports both a
    # clean install (where these tables may already exist) and an upgrade of the
    # deployed Stage 2/3 schema. All changes are additive.
    bind = op.get_bind()
    for table in (setup_instances, setup_observations,
                  setup_outcome_assessments, research_scan_cycles,
                  research_scan_symbol_coverage):
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (research_scan_symbol_coverage, research_scan_cycles,
                  setup_outcome_assessments, setup_observations,
                  setup_instances):
        table.drop(bind, checkfirst=True)


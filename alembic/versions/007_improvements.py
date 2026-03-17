"""Improvement batch: enrichment tracking, XOR constraint fix.

Revision ID: 007_improvements
Revises: 006_v2_redesign
"""

from alembic import op
import sqlalchemy as sa

revision = "007_improvements"
down_revision = "006_v2_redesign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add enrichment_status to video_people for idempotent pipeline
    op.add_column(
        "video_people",
        sa.Column("enrichment_status", sa.Text(), server_default="pending", nullable=False),
    )

    # 2. Fix claims XOR constraint (was OR, should be XOR)
    # Drop old constraint and add correct one
    op.drop_constraint("ck_claims_source_xor", "claims", type_="check")
    op.create_check_constraint(
        "ck_claims_source_xor",
        "claims",
        "(video_id IS NOT NULL) <> (x_post_id IS NOT NULL)",
    )


def downgrade() -> None:
    # Revert XOR back to OR
    op.drop_constraint("ck_claims_source_xor", "claims", type_="check")
    op.create_check_constraint(
        "ck_claims_source_xor",
        "claims",
        "(video_id IS NOT NULL) OR (x_post_id IS NOT NULL)",
    )

    # Remove enrichment_status
    op.drop_column("video_people", "enrichment_status")

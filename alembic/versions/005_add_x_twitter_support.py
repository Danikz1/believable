"""Add x_posts table, x_handle, x_post_id, relax evidence constraints (Phase 3).

Revision ID: 005_add_x_twitter_support
Revises: 004_add_favorites_and_summaries
Create Date: 2026-03-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005_add_x_twitter_support"
down_revision: Union[str, None] = "004_add_favorites_and_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- 1. Add x_handle to people --
    op.add_column("people", sa.Column("x_handle", sa.Text(), nullable=True))

    # -- 2. Create x_posts table --
    op.create_table(
        "x_posts",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("platform_post_id", sa.Text(), unique=True, nullable=False),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("post_text", sa.Text(), nullable=False),
        sa.Column("post_url", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_thread", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("thread_parent_id", sa.UUID(), sa.ForeignKey("x_posts.id"), nullable=True),
        sa.Column("discovery_method", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_x_posts_person", "x_posts", ["person_id"])
    op.create_index("idx_x_posts_status", "x_posts", ["status"])
    op.create_index("idx_x_posts_posted_at", "x_posts", ["posted_at"])

    # -- 3. Modify claims: video_id nullable, add x_post_id, add XOR constraint --
    op.alter_column("claims", "video_id", nullable=True)
    op.add_column("claims", sa.Column("x_post_id", sa.UUID(), sa.ForeignKey("x_posts.id"), nullable=True))
    op.create_index("ix_claims_x_post_id", "claims", ["x_post_id"])
    op.create_check_constraint(
        "ck_claims_source_required", "claims",
        "(video_id IS NOT NULL) OR (x_post_id IS NOT NULL)",
    )

    # -- 4. Relax claim_evidence constraints --
    op.alter_column("claim_evidence", "segment_id", nullable=True)
    op.alter_column("claim_evidence", "start_ms", nullable=True)
    op.alter_column("claim_evidence", "end_ms", nullable=True)

    # Replace the old strict timestamp constraint with a nullable-safe version
    op.drop_constraint("ck_evidence_timestamps", "claim_evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_timestamps", "claim_evidence",
        "start_ms IS NULL OR end_ms IS NULL OR start_ms < end_ms",
    )


def downgrade() -> None:
    # Reverse evidence constraint
    op.drop_constraint("ck_evidence_timestamps", "claim_evidence", type_="check")
    op.create_check_constraint(
        "ck_evidence_timestamps", "claim_evidence",
        "start_ms < end_ms",
    )
    op.alter_column("claim_evidence", "end_ms", nullable=False)
    op.alter_column("claim_evidence", "start_ms", nullable=False)
    op.alter_column("claim_evidence", "segment_id", nullable=False)

    # Reverse claims changes
    op.drop_constraint("ck_claims_source_required", "claims", type_="check")
    op.drop_index("ix_claims_x_post_id", table_name="claims")
    op.drop_column("claims", "x_post_id")
    op.alter_column("claims", "video_id", nullable=False)

    # Drop x_posts
    op.drop_table("x_posts")

    # Drop x_handle
    op.drop_column("people", "x_handle")

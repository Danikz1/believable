"""Add favorites and episode_summaries tables (Phase 2).

Revision ID: 004_add_favorites_and_summaries
Revises: 003_add_briefs_table
Create Date: 2026-03-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004_add_favorites_and_summaries"
down_revision: Union[str, None] = "003_add_briefs_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- favorites --
    op.create_table(
        "favorites",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=True),
        sa.Column("channel_id", sa.UUID(), sa.ForeignKey("podcast_channels.id", ondelete="CASCADE"), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("(person_id IS NOT NULL) <> (channel_id IS NOT NULL)", name="ck_favorites_xor"),
        sa.CheckConstraint("priority >= 1 AND priority <= 10", name="ck_favorites_priority"),
    )
    op.create_index(
        "idx_favorites_person", "favorites", ["person_id"],
        unique=True, postgresql_where=sa.text("person_id IS NOT NULL"),
    )
    op.create_index(
        "idx_favorites_channel", "favorites", ["channel_id"],
        unique=True, postgresql_where=sa.text("channel_id IS NOT NULL"),
    )

    # -- episode_summaries --
    op.create_table(
        "episode_summaries",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", sa.UUID(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "summary_type", sa.Text(), nullable=False,
        ),
        sa.Column("person_focus_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("tldr", sa.Text(), nullable=False),
        sa.Column("summary_body", sa.Text(), nullable=False),
        sa.Column("detailed_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("whats_new", sa.Text(), nullable=True),
        sa.Column("watch_verdict", sa.Text(), nullable=False),
        sa.Column("watch_verdict_reason", sa.Text(), nullable=False),
        sa.Column("model_used", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "summary_type IN ('full_episode', 'person_focused')",
            name="ck_episode_summaries_type",
        ),
        sa.CheckConstraint(
            "watch_verdict IN ('essential', 'worth_skimming', 'skip_unless_fan')",
            name="ck_episode_summaries_verdict",
        ),
    )
    op.create_index("idx_episode_summaries_video", "episode_summaries", ["video_id"])
    op.create_index("idx_episode_summaries_person_focus", "episode_summaries", ["person_focus_id"])
    op.create_index(
        "idx_episode_summaries_full", "episode_summaries", ["video_id"],
        unique=True, postgresql_where=sa.text("summary_type = 'full_episode'"),
    )
    op.create_index(
        "idx_episode_summaries_person", "episode_summaries", ["video_id", "person_focus_id"],
        unique=True,
        postgresql_where=sa.text("summary_type = 'person_focused' AND person_focus_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("episode_summaries")
    op.drop_table("favorites")

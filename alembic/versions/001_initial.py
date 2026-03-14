"""Initial schema — 14 core tables with pgvector extension.

Revision ID: 001_initial
Revises: None
Create Date: 2026-03-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- pgvector extension FIRST ----------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. people
    op.create_table(
        "people",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text()),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("inclusion_notes", sa.Text(), nullable=False),
        sa.Column("expertise_domains", postgresql.ARRAY(sa.Text())),
        sa.Column("youtube_search_queries", postgresql.ARRAY(sa.Text())),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("tier >= 1 AND tier <= 3", name="ck_people_tier"),
    )

    # 2. podcast_channels
    op.create_table(
        "podcast_channels",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("youtube_channel_id", sa.Text(), unique=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("monitoring_mode", sa.Text(), server_default="channel_feed", nullable=False),
        sa.Column("uploads_playlist_id", sa.Text()),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("tier >= 1 AND tier <= 3", name="ck_channels_tier"),
    )

    # 3. channel_roles
    op.create_table(
        "channel_roles",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("channel_id", sa.UUID(), sa.ForeignKey("podcast_channels.id"), nullable=False),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.UniqueConstraint("channel_id", "person_id", "role", name="uq_channel_person_role"),
    )

    # 4. videos
    op.create_table(
        "videos",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("youtube_video_id", sa.Text(), unique=True, nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("podcast_channel_id", sa.UUID(), sa.ForeignKey("podcast_channels.id"), nullable=True),
        sa.Column("source_channel_youtube_id", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("discovery_method", sa.Text()),
        sa.Column("discovered_by_person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("transcript_type", sa.Text()),
        sa.Column("status", sa.Text(), server_default="discovered", nullable=False),
        sa.Column("skip_reason", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_videos_youtube_video_id", "videos", ["youtube_video_id"])
    op.create_index("ix_videos_status", "videos", ["status"])

    # 5. transcript_runs
    op.create_table(
        "transcript_runs",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", sa.UUID(), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_model", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("language_code", sa.Text()),
        sa.Column("speaker_config", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
    )

    # 6. transcript_segments
    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("transcript_run_id", sa.UUID(), sa.ForeignKey("transcript_runs.id"), nullable=False),
        sa.Column("video_id", sa.UUID(), sa.ForeignKey("videos.id"), nullable=True),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("speaker_label", sa.Text()),
        sa.Column("speaker_name", sa.Text()),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("transcript_run_id", "segment_index", name="uq_segment_run_index"),
        sa.CheckConstraint("start_ms < end_ms", name="ck_segment_timestamps"),
    )

    # 7. video_people
    op.create_table(
        "video_people",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", sa.UUID(), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("role", sa.Text()),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("identified_via", sa.Text()),
        sa.UniqueConstraint("video_id", "person_id", name="uq_video_person"),
    )

    # 8. topics
    op.create_table(
        "topics",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.UUID(), sa.ForeignKey("topics.id"), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # 9. claims
    op.create_table(
        "claims",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("video_id", sa.UUID(), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("reasoning_text", sa.Text()),
        sa.Column("claim_type", sa.Text()),
        sa.Column("speaker_certainty", sa.Text()),
        sa.Column("attribution_confidence", sa.Numeric(4, 3)),
        sa.Column("extraction_confidence", sa.Numeric(4, 3)),
        sa.Column("trust_level", sa.Text(), nullable=False),  # NO default
        sa.Column("topics", postgresql.ARRAY(sa.Text())),
        sa.Column("sentiment", sa.Text()),
        sa.Column("temporal_marker", sa.Text()),
        sa.Column("review_status", sa.Text(), nullable=False),  # NO default
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_claims_person_id", "claims", ["person_id"])
    op.create_index("ix_claims_review_status", "claims", ["review_status"])

    # 10. claim_topics
    op.create_table(
        "claim_topics",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("claim_id", sa.UUID(), sa.ForeignKey("claims.id"), nullable=False),
        sa.Column("topic_id", sa.UUID(), sa.ForeignKey("topics.id"), nullable=False),
        sa.UniqueConstraint("claim_id", "topic_id", name="uq_claim_topic"),
    )

    # 11. claim_evidence
    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("claim_id", sa.UUID(), sa.ForeignKey("claims.id"), nullable=False),
        sa.Column("segment_id", sa.UUID(), sa.ForeignKey("transcript_segments.id"), nullable=False),
        sa.Column("evidence_order", sa.Integer(), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("quote_type", sa.Text(), nullable=False),
        sa.CheckConstraint("start_ms < end_ms", name="ck_evidence_timestamps"),
    )

    # 12. claim_embeddings
    op.create_table(
        "claim_embeddings",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("claim_id", sa.UUID(), sa.ForeignKey("claims.id"), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("claim_id", "model_name", name="uq_claim_model_embedding"),
    )
    # Add vector column and dimension check via raw SQL (Alembic doesn't natively support pgvector)
    op.execute("ALTER TABLE claim_embeddings ADD COLUMN embedding vector NOT NULL;")
    op.execute("""
        ALTER TABLE claim_embeddings
        ADD CONSTRAINT ck_embedding_dims
        CHECK (dimensions = vector_dims(embedding));
    """)

    # 13. person_topic_positions
    op.create_table(
        "person_topic_positions",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("topic_id", sa.UUID(), sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("current_position", sa.Text()),
        sa.Column("last_updated", sa.DateTime(timezone=True)),
        sa.Column("claim_count", sa.Integer()),
        sa.UniqueConstraint("person_id", "topic_id", name="uq_person_topic_position"),
    )

    # 14. position_history_log
    op.create_table(
        "position_history_log",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", sa.UUID(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("topic_id", sa.UUID(), sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("position_summary", sa.Text(), nullable=False),
        sa.Column("source_claim_id", sa.UUID(), sa.ForeignKey("claims.id"), nullable=True),
        sa.Column("is_shift", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("position_history_log")
    op.drop_table("person_topic_positions")
    op.drop_table("claim_embeddings")
    op.drop_table("claim_evidence")
    op.drop_table("claim_topics")
    op.drop_table("claims")
    op.drop_table("topics")
    op.drop_table("video_people")
    op.drop_table("transcript_segments")
    op.drop_table("transcript_runs")
    op.drop_table("videos")
    op.drop_table("channel_roles")
    op.drop_table("podcast_channels")
    op.drop_table("people")
    op.execute("DROP EXTENSION IF EXISTS vector;")

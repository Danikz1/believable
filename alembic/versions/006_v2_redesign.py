"""v2 redesign schema additions.

- People: bio, role_title, net_worth, age, photo_initials, accent_color
- PersonTopicPositions: sentiment
- PositionHistoryLog: shift_note, previous_position
- PodcastChannels: last_scanned_at, video_count
- Videos: source_channel_youtube_id nullable
- Claims: XOR constraint fix

Revision ID: 006_v2_redesign
Revises: 005_add_x_twitter_support
"""

from alembic import op
import sqlalchemy as sa


revision = "006_v2_redesign"
down_revision = "005_add_x_twitter_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1A. People bio fields
    op.add_column("people", sa.Column("bio", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("role_title", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("net_worth", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column("people", sa.Column("photo_initials", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("accent_color", sa.Text(), nullable=True))

    # 1B. Position sentiment
    op.add_column("person_topic_positions", sa.Column("sentiment", sa.Text(), nullable=True))

    # 1C. Shift context
    op.add_column("position_history_log", sa.Column("shift_note", sa.Text(), nullable=True))
    op.add_column("position_history_log", sa.Column("previous_position", sa.Text(), nullable=True))

    # 1D. Channel scan metadata
    op.add_column("podcast_channels", sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("podcast_channels", sa.Column("video_count", sa.Integer(), server_default="0", nullable=True))

    # 1E. Make source_channel_youtube_id nullable
    op.alter_column(
        "videos",
        "source_channel_youtube_id",
        existing_type=sa.Text(),
        nullable=True,
    )

    # 1F. Fix claims XOR constraint
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claims_source_required")
    op.execute(
        "ALTER TABLE claims ADD CONSTRAINT ck_claims_source_xor "
        "CHECK ((video_id IS NOT NULL) OR (x_post_id IS NOT NULL))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claims_source_xor")
    op.execute(
        "ALTER TABLE claims ADD CONSTRAINT ck_claims_source_required "
        "CHECK ((video_id IS NOT NULL) OR (x_post_id IS NOT NULL))"
    )

    op.alter_column(
        "videos",
        "source_channel_youtube_id",
        existing_type=sa.Text(),
        nullable=False,
    )

    op.drop_column("podcast_channels", "video_count")
    op.drop_column("podcast_channels", "last_scanned_at")
    op.drop_column("position_history_log", "previous_position")
    op.drop_column("position_history_log", "shift_note")
    op.drop_column("person_topic_positions", "sentiment")
    op.drop_column("people", "accent_color")
    op.drop_column("people", "photo_initials")
    op.drop_column("people", "age")
    op.drop_column("people", "net_worth")
    op.drop_column("people", "role_title")
    op.drop_column("people", "bio")

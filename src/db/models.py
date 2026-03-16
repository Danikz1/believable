"""SQLAlchemy 2.0 ORM models for Believable Minds."""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# 1. people
# ---------------------------------------------------------------------------
class People(Base):
    __tablename__ = "people"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    domain = Column(Text)
    tier = Column(Integer, nullable=False)  # 1–3
    inclusion_notes = Column(Text, nullable=False)
    expertise_domains = Column(ARRAY(Text))
    youtube_search_queries = Column(ARRAY(Text))
    x_handle = Column(Text, nullable=True)  # Phase 3: X/Twitter handle
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    channel_roles = relationship("ChannelRoles", back_populates="person")
    video_people = relationship("VideoPeople", back_populates="person")
    claims = relationship("Claims", back_populates="person")
    topic_positions = relationship("PersonTopicPositions", back_populates="person")
    position_history = relationship("PositionHistoryLog", back_populates="person")
    transcript_segments = relationship("TranscriptSegments", back_populates="person")

    __table_args__ = (
        CheckConstraint("tier >= 1 AND tier <= 3", name="ck_people_tier"),
    )


# ---------------------------------------------------------------------------
# 2. podcast_channels
# ---------------------------------------------------------------------------
class PodcastChannels(Base):
    __tablename__ = "podcast_channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    youtube_channel_id = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    tier = Column(Integer, nullable=False)
    monitoring_mode = Column(Text, default="channel_feed", nullable=False)
    uploads_playlist_id = Column(Text)
    transcript_url_pattern = Column(Text)  # e.g. 'https://www.dwarkesh.com/p/{slug}'
    transcript_parser = Column(Text)  # e.g. 'dwarkesh_substack', 'lex_fridman'
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    channel_roles = relationship("ChannelRoles", back_populates="channel")
    videos = relationship("Videos", back_populates="podcast_channel")

    __table_args__ = (
        CheckConstraint("tier >= 1 AND tier <= 3", name="ck_channels_tier"),
    )


# ---------------------------------------------------------------------------
# 3. channel_roles
# ---------------------------------------------------------------------------
class ChannelRoles(Base):
    __tablename__ = "channel_roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(
        UUID(as_uuid=True), ForeignKey("podcast_channels.id"), nullable=False
    )
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    role = Column(Text, nullable=False)  # 'host' / 'cohost' / 'frequent_guest'

    # relationships
    channel = relationship("PodcastChannels", back_populates="channel_roles")
    person = relationship("People", back_populates="channel_roles")

    __table_args__ = (
        UniqueConstraint("channel_id", "person_id", "role", name="uq_channel_person_role"),
    )


# ---------------------------------------------------------------------------
# 4. videos
# ---------------------------------------------------------------------------
class Videos(Base):
    __tablename__ = "videos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    youtube_video_id = Column(Text, unique=True, nullable=False)
    title = Column(Text)
    podcast_channel_id = Column(
        UUID(as_uuid=True), ForeignKey("podcast_channels.id"), nullable=True
    )
    source_channel_youtube_id = Column(Text, nullable=False)
    published_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    description = Column(Text)
    discovery_method = Column(Text)  # 'channel_feed' / 'search_gap_fill' / 'manual'
    discovered_by_person_id = Column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    transcript_type = Column(Text)  # 'deep' / 'fast'
    status = Column(Text, default="discovered", nullable=False)
    skip_reason = Column(Text)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    podcast_channel = relationship("PodcastChannels", back_populates="videos")
    transcript_runs = relationship("TranscriptRuns", back_populates="video")
    transcript_segments = relationship("TranscriptSegments", back_populates="video")
    video_people = relationship("VideoPeople", back_populates="video")
    claims = relationship("Claims", back_populates="video")

    __table_args__ = (
        Index("ix_videos_youtube_video_id", "youtube_video_id"),
        Index("ix_videos_status", "status"),
    )


# ---------------------------------------------------------------------------
# 5. transcript_runs
# ---------------------------------------------------------------------------
class TranscriptRuns(Base):
    __tablename__ = "transcript_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id = Column(
        UUID(as_uuid=True), ForeignKey("videos.id"), nullable=False
    )
    mode = Column(Text, nullable=False)  # 'caption' / 'asr_plain' / 'asr_diarized'
    provider = Column(Text, nullable=False)  # 'yt-dlp' / 'whisperx' / 'deepgram'
    provider_model = Column(Text)
    status = Column(Text, nullable=False)  # 'created' / 'running' / 'succeeded' / 'failed'
    language_code = Column(Text)
    speaker_config = Column(JSONB)  # {"mode": "exact", "count": 5} or {"mode": "range", "min": 3, "max": 6}
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at = Column(DateTime(timezone=True))
    error_message = Column(Text)

    # relationships
    video = relationship("Videos", back_populates="transcript_runs")
    segments = relationship("TranscriptSegments", back_populates="transcript_run")


# ---------------------------------------------------------------------------
# 6. transcript_segments
# ---------------------------------------------------------------------------
class TranscriptSegments(Base):
    __tablename__ = "transcript_segments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_run_id = Column(
        UUID(as_uuid=True), ForeignKey("transcript_runs.id"), nullable=False
    )
    video_id = Column(
        UUID(as_uuid=True), ForeignKey("videos.id"), nullable=True
    )  # denormalized for fast queries
    segment_index = Column(Integer, nullable=False)
    speaker_label = Column(Text)  # "SPEAKER_00" or NULL
    speaker_name = Column(Text)  # resolved name, NULL until Stage 4
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    start_ms = Column(BigInteger, nullable=False)
    end_ms = Column(BigInteger, nullable=False)
    text = Column(Text, nullable=False)
    source_kind = Column(Text, nullable=False)  # 'caption' / 'asr' / 'asr_diarized'
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    transcript_run = relationship("TranscriptRuns", back_populates="segments")
    video = relationship("Videos", back_populates="transcript_segments")
    person = relationship("People", back_populates="transcript_segments")
    claim_evidence = relationship("ClaimEvidence", back_populates="segment")

    __table_args__ = (
        UniqueConstraint(
            "transcript_run_id", "segment_index", name="uq_segment_run_index"
        ),
        CheckConstraint("start_ms < end_ms", name="ck_segment_timestamps"),
    )


# ---------------------------------------------------------------------------
# 7. video_people (junction)
# ---------------------------------------------------------------------------
class VideoPeople(Base):
    __tablename__ = "video_people"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id"), nullable=False)
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    role = Column(Text)  # 'host' / 'guest' / 'unknown'
    confidence = Column(Numeric(4, 3))  # 0.000–1.000
    identified_via = Column(Text)  # 'known_host' / 'diarization_llm' / 'metadata_only' / 'manual'

    # relationships
    video = relationship("Videos", back_populates="video_people")
    person = relationship("People", back_populates="video_people")

    __table_args__ = (
        UniqueConstraint("video_id", "person_id", name="uq_video_person"),
    )


# ---------------------------------------------------------------------------
# 8. topics
# ---------------------------------------------------------------------------
class Topics(Base):
    __tablename__ = "topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("topics.id"), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # self-referential relationship
    parent = relationship("Topics", remote_side="Topics.id")
    claim_topics = relationship("ClaimTopics", back_populates="topic")
    person_positions = relationship("PersonTopicPositions", back_populates="topic")
    position_history = relationship("PositionHistoryLog", back_populates="topic")


# ---------------------------------------------------------------------------
# 9. x_posts (Phase 3: X/Twitter)
# ---------------------------------------------------------------------------
class XPosts(Base):
    __tablename__ = "x_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform_post_id = Column(Text, unique=True, nullable=False)  # e.g. "1234567890"
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    post_text = Column(Text, nullable=False)
    post_url = Column(Text, nullable=False)
    posted_at = Column(DateTime(timezone=True), nullable=True)
    is_thread = Column(Boolean, default=False, nullable=False)
    thread_parent_id = Column(UUID(as_uuid=True), ForeignKey("x_posts.id"), nullable=True)
    discovery_method = Column(Text, nullable=False)  # 'manual' / 'api_scan'
    status = Column(Text, nullable=False, default="pending")  # 'pending' / 'enriched' / 'skipped'
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    person = relationship("People")
    thread_parent = relationship("XPosts", remote_side="XPosts.id")
    claims = relationship("Claims", back_populates="x_post")

    __table_args__ = (
        Index("idx_x_posts_person", "person_id"),
        Index("idx_x_posts_status", "status"),
        Index("idx_x_posts_posted_at", "posted_at"),
    )


# ---------------------------------------------------------------------------
# 10. claims
# ---------------------------------------------------------------------------
class Claims(Base):
    __tablename__ = "claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id"), nullable=True)  # nullable for X posts
    x_post_id = Column(UUID(as_uuid=True), ForeignKey("x_posts.id"), nullable=True)  # Phase 3
    claim_text = Column(Text, nullable=False)
    reasoning_text = Column(Text)
    claim_type = Column(Text)  # prediction / opinion / recommendation / observation / analysis
    speaker_certainty = Column(Text)  # definitive / high / moderate / speculative / hedged
    attribution_confidence = Column(Numeric(4, 3))  # 0.000–1.000
    extraction_confidence = Column(Numeric(4, 3))  # 0.000–1.000
    trust_level = Column(Text, nullable=False)  # 'high' / 'medium' / 'low' — NO DEFAULT
    topics = Column(ARRAY(Text))  # denormalized cache
    sentiment = Column(Text)  # bullish / bearish / neutral / mixed
    temporal_marker = Column(Text)
    review_status = Column(Text, nullable=False)  # 'approved' / 'pending_review' / 'rejected' — NO DEFAULT
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    person = relationship("People", back_populates="claims")
    video = relationship("Videos", back_populates="claims")
    x_post = relationship("XPosts", back_populates="claims")
    claim_topics = relationship("ClaimTopics", back_populates="claim")
    evidence = relationship("ClaimEvidence", back_populates="claim")
    embeddings = relationship("ClaimEmbeddings", back_populates="claim")

    __table_args__ = (
        Index("ix_claims_person_id", "person_id"),
        Index("ix_claims_review_status", "review_status"),
        Index("ix_claims_x_post_id", "x_post_id"),
        CheckConstraint(
            "(video_id IS NOT NULL) OR (x_post_id IS NOT NULL)",
            name="ck_claims_source_required",
        ),
    )


# ---------------------------------------------------------------------------
# 10. claim_topics (junction)
# ---------------------------------------------------------------------------
class ClaimTopics(Base):
    __tablename__ = "claim_topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id"), nullable=False)

    # relationships
    claim = relationship("Claims", back_populates="claim_topics")
    topic = relationship("Topics", back_populates="claim_topics")

    __table_args__ = (
        UniqueConstraint("claim_id", "topic_id", name="uq_claim_topic"),
    )


# ---------------------------------------------------------------------------
# 11. claim_evidence
# ---------------------------------------------------------------------------
class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False)
    segment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transcript_segments.id"),
        nullable=True,  # nullable for X post claims
    )
    evidence_order = Column(Integer, nullable=False)
    quote_text = Column(Text, nullable=False)
    start_ms = Column(BigInteger, nullable=True)  # nullable for X posts
    end_ms = Column(BigInteger, nullable=True)    # nullable for X posts
    quote_type = Column(Text, nullable=False)  # 'direct_quote' / 'paraphrase' / 'multi_segment_synthesis' / 'x_post_text'

    # relationships
    claim = relationship("Claims", back_populates="evidence")
    segment = relationship("TranscriptSegments", back_populates="claim_evidence")

    __table_args__ = (
        CheckConstraint(
            "start_ms IS NULL OR end_ms IS NULL OR start_ms < end_ms",
            name="ck_evidence_timestamps",
        ),
    )


# ---------------------------------------------------------------------------
# 12. claim_embeddings
# ---------------------------------------------------------------------------
class ClaimEmbeddings(Base):
    __tablename__ = "claim_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False)
    model_name = Column(Text, nullable=False)
    dimensions = Column(Integer, nullable=False)
    embedding = Column(Vector(), nullable=False)  # no hardcoded dimension
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    claim = relationship("Claims", back_populates="embeddings")

    __table_args__ = (
        UniqueConstraint("claim_id", "model_name", name="uq_claim_model_embedding"),
        # CHECK dimensions = vector_dims(embedding) — enforced at DB level via raw SQL in migration
    )


# ---------------------------------------------------------------------------
# 13. person_topic_positions
# ---------------------------------------------------------------------------
class PersonTopicPositions(Base):
    __tablename__ = "person_topic_positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id"), nullable=False)
    current_position = Column(Text)
    last_updated = Column(DateTime(timezone=True))
    claim_count = Column(Integer)

    # relationships
    person = relationship("People", back_populates="topic_positions")
    topic = relationship("Topics", back_populates="person_positions")

    __table_args__ = (
        UniqueConstraint("person_id", "topic_id", name="uq_person_topic_position"),
    )


# ---------------------------------------------------------------------------
# 14. position_history_log
# ---------------------------------------------------------------------------
class PositionHistoryLog(Base):
    __tablename__ = "position_history_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id"), nullable=False)
    position_summary = Column(Text, nullable=False)
    source_claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True)
    is_shift = Column(Boolean, default=False, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    person = relationship("People", back_populates="position_history")
    topic = relationship("Topics", back_populates="position_history")


class Briefs(Base):
    __tablename__ = "briefs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(Text, nullable=False)
    content_markdown = Column(Text, nullable=False)  # Full brief in markdown
    sections = Column(JSONB, default=dict)  # Structured sections data
    claim_ids = Column(ARRAY(UUID(as_uuid=True)), default=list)  # Referenced claims
    status = Column(Text, default="draft")  # draft, published
    generation_cost = Column(Numeric, default=0.0)
    delivered_telegram = Column(Boolean, default=False)
    delivered_email = Column(Boolean, default=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    published_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# 10. favorites (Amendment 5: real FK constraints)
# ---------------------------------------------------------------------------
class Favorites(Base):
    __tablename__ = "favorites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id = Column(UUID(as_uuid=True), ForeignKey("people.id", ondelete="CASCADE"), nullable=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("podcast_channels.id", ondelete="CASCADE"), nullable=True)
    priority = Column(Integer, nullable=False, default=5)
    notify = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    person = relationship("People")
    channel = relationship("PodcastChannels")

    __table_args__ = (
        CheckConstraint(
            "(person_id IS NOT NULL) <> (channel_id IS NOT NULL)",
            name="ck_favorites_xor",
        ),
        CheckConstraint(
            "priority >= 1 AND priority <= 10",
            name="ck_favorites_priority",
        ),
        Index("idx_favorites_person", "person_id", unique=True, postgresql_where=text("person_id IS NOT NULL")),
        Index("idx_favorites_channel", "channel_id", unique=True, postgresql_where=text("channel_id IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# 11. episode_summaries (Amendment 1: 3 levels + watch verdict)
# ---------------------------------------------------------------------------
class EpisodeSummaries(Base):
    __tablename__ = "episode_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    summary_type = Column(Text, nullable=False)  # 'full_episode' or 'person_focused'
    person_focus_id = Column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)

    # Three summary levels (Amendment 1)
    tldr = Column(Text, nullable=False)
    summary_body = Column(Text, nullable=False)
    detailed_json = Column(JSONB, nullable=False, default=dict)
    whats_new = Column(Text, nullable=True)

    # Watch verdict (Amendment 1)
    watch_verdict = Column(Text, nullable=False)  # essential, worth_skimming, skip_unless_fan
    watch_verdict_reason = Column(Text, nullable=False)

    # Metadata
    model_used = Column(Text, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    generated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # relationships
    video = relationship("Videos")
    person_focus = relationship("People")

    __table_args__ = (
        CheckConstraint(
            "summary_type IN ('full_episode', 'person_focused')",
            name="ck_episode_summaries_type",
        ),
        CheckConstraint(
            "watch_verdict IN ('essential', 'worth_skimming', 'skip_unless_fan')",
            name="ck_episode_summaries_verdict",
        ),
        Index("idx_episode_summaries_full", "video_id", unique=True,
              postgresql_where=text("summary_type = 'full_episode'")),
        Index("idx_episode_summaries_person", "video_id", "person_focus_id", unique=True,
              postgresql_where=text("summary_type = 'person_focused' AND person_focus_id IS NOT NULL")),
        Index("idx_episode_summaries_video", "video_id"),
        Index("idx_episode_summaries_person_focus", "person_focus_id"),
    )

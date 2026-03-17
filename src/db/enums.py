"""Enums for all magic-string columns in the database.

These replace raw text columns with validated Python enums.
The DB still stores plain text (no Postgres ENUM type migration needed),
but the application layer now validates values at write time.
"""

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python < 3.11."""
        pass


# ── Video pipeline ──────────────────────────────────────────────────
class VideoStatus(StrEnum):
    DISCOVERED = "discovered"
    TRANSCRIBED = "transcribed"
    IDENTIFIED = "identified"
    ENRICHED = "enriched"
    SKIPPED = "skipped"
    ERROR = "error"


class DiscoveryMethod(StrEnum):
    CHANNEL_FEED = "channel_feed"
    SEARCH_GAP_FILL = "search_gap_fill"
    MANUAL = "manual"


class TranscriptType(StrEnum):
    DEEP = "deep"
    FAST = "fast"
    OFFICIAL = "official"


# ── Transcript runs ─────────────────────────────────────────────────
class TranscriptMode(StrEnum):
    CAPTION = "caption"
    ASR_PLAIN = "asr_plain"
    ASR_DIARIZED = "asr_diarized"


class TranscriptProvider(StrEnum):
    YT_DLP = "yt-dlp"
    WHISPERX = "whisperx"
    DEEPGRAM = "deepgram"
    ASSEMBLYAI = "assemblyai"


class TranscriptRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ── Transcript segments ─────────────────────────────────────────────
class SourceKind(StrEnum):
    CAPTION = "caption"
    ASR = "asr"
    ASR_DIARIZED = "asr_diarized"


# ── Channel / people roles ──────────────────────────────────────────
class ChannelRole(StrEnum):
    HOST = "host"
    COHOST = "cohost"
    FREQUENT_GUEST = "frequent_guest"


class VideoPersonRole(StrEnum):
    HOST = "host"
    GUEST = "guest"
    UNKNOWN = "unknown"


class IdentificationMethod(StrEnum):
    KNOWN_HOST = "known_host"
    DIARIZATION_LLM = "diarization_llm"
    METADATA_ONLY = "metadata_only"
    MANUAL = "manual"


# ── Monitoring ──────────────────────────────────────────────────────
class MonitoringMode(StrEnum):
    CHANNEL_FEED = "channel_feed"
    SEARCH_ONLY = "search_only"


# ── Claims ──────────────────────────────────────────────────────────
class ClaimType(StrEnum):
    PREDICTION = "prediction"
    OPINION = "opinion"
    RECOMMENDATION = "recommendation"
    OBSERVATION = "observation"
    ANALYSIS = "analysis"


class SpeakerCertainty(StrEnum):
    DEFINITIVE = "definitive"
    HIGH = "high"
    MODERATE = "moderate"
    SPECULATIVE = "speculative"
    HEDGED = "hedged"


class TrustLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClaimSentiment(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


# ── Evidence ────────────────────────────────────────────────────────
class QuoteType(StrEnum):
    DIRECT_QUOTE = "direct_quote"
    PARAPHRASE = "paraphrase"
    MULTI_SEGMENT_SYNTHESIS = "multi_segment_synthesis"
    X_POST_TEXT = "x_post_text"


# ── Position sentiment (broader than claim sentiment) ───────────────
class PositionSentiment(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    CAUTIOUS = "cautious"
    URGENT = "urgent"
    STRONG = "strong"


# ── Episode summaries ──────────────────────────────────────────────
class SummaryType(StrEnum):
    FULL_EPISODE = "full_episode"
    PERSON_FOCUSED = "person_focused"


class WatchVerdict(StrEnum):
    ESSENTIAL = "essential"
    WORTH_SKIMMING = "worth_skimming"
    SKIP_UNLESS_FAN = "skip_unless_fan"


# ── X/Twitter posts ────────────────────────────────────────────────
class XPostDiscoveryMethod(StrEnum):
    MANUAL = "manual"
    API_SCAN = "api_scan"


class XPostStatus(StrEnum):
    PENDING = "pending"
    ENRICHED = "enriched"
    SKIPPED = "skipped"


# ── Briefs ──────────────────────────────────────────────────────────
class BriefStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


# ── Enrichment pipeline ────────────────────────────────────────────
class EnrichmentStatus(StrEnum):
    """Per-speaker enrichment tracking for idempotent pipeline resumption."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

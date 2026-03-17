"""LLM Enrichment pipeline — extract structured claims from identified transcripts.

Uses Qwen-Plus (primary) or Claude (fallback) with tool_use for structured output.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import (
    ClaimEvidence,
    Claims,
    ClaimTopics,
    People,
    Topics,
    TranscriptSegments,
    VideoPeople,
    Videos,
)

logger = logging.getLogger(__name__)


class EnrichmentError(RuntimeError):
    """Raised when claim extraction fails and the video should remain pending."""

# ── Topic Taxonomy ───────────────────────────────────────────────────

# Hardcoded fallback — only used when the DB is unreachable.
_FALLBACK_TOPIC_SLUGS = [
    "macro", "interest_rates", "duration", "inflation", "fiscal_policy",
    "debt_cycles", "geopolitics", "us_china", "ai_infrastructure", "ai_safety",
    "ai_regulation", "ai_open_source", "inference_compute", "enterprise_ai",
    "saas_pricing", "crypto", "stablecoins", "payments", "venture_capital",
    "startup_formation", "energy", "climate", "real_estate", "labor_market",
    "healthcare", "defense", "space", "creator_economy", "platform_dynamics",
    "value_investing",
]


def _get_topic_slugs(session: Session) -> list[str]:
    """Load active topic slugs from the database (single source of truth).

    Falls back to the hardcoded list if the query fails.
    """
    try:
        slugs = [
            t.slug for t in session.query(Topics).filter(Topics.active == True).all()  # noqa: E712
        ]
        return slugs if slugs else _FALLBACK_TOPIC_SLUGS
    except Exception:
        logger.warning("Failed to load topics from DB, using fallback list")
        return _FALLBACK_TOPIC_SLUGS

# ── Prompts ──────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are an expert analyst who extracts structured claims from podcast and interview transcripts.

For each meaningful claim, prediction, recommendation, or analysis made by the speaker, extract it as a structured object with evidence.

TOPIC TAXONOMY (use these slugs, flag new topics only if nothing fits):
{topics}

RULES:
- Extract only substantive claims — not greetings, transitions, or filler
- Each claim needs at least one evidence span referencing a segment_id from the input
- Reasoning should explain WHY the speaker believes this
- Temporal markers like "this year", "by 2030", "next quarter" should be captured
- Use the exact segment_id values from the input transcript
- extraction_confidence: 0.9+ for clear, direct claims; 0.7-0.9 for inferred; <0.7 for ambiguous"""

EXTRACTION_USER_TEMPLATE = """TRANSCRIPT SEGMENTS FOR {person_name}:
{segments_text}

Extract all substantive claims from these segments. Return the claims using the extract_claims tool."""

# ── Tool Schema ──────────────────────────────────────────────────────

EXTRACT_CLAIMS_TOOL = {
    "name": "extract_claims",
    "description": "Extract structured claims from transcript segments. Cite segment IDs for evidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_text": {"type": "string"},
                        "reasoning_text": {"type": "string"},
                        "claim_type": {
                            "type": "string",
                            "enum": ["prediction", "opinion", "recommendation", "observation", "analysis"],
                        },
                        "speaker_certainty": {
                            "type": "string",
                            "enum": ["definitive", "high", "moderate", "speculative", "hedged"],
                        },
                        "extraction_confidence": {
                            "type": "number",
                            "description": "0.0-1.0",
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "sentiment": {
                            "type": "string",
                            "enum": ["bullish", "bearish", "neutral", "mixed"],
                        },
                        "temporal_marker": {"type": "string"},
                        "evidence_spans": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "segment_id": {"type": "string"},
                                    "quote_text": {"type": "string"},
                                    "start_ms": {"type": "integer"},
                                    "end_ms": {"type": "integer"},
                                    "quote_type": {
                                        "type": "string",
                                        "enum": ["direct_quote", "paraphrase", "multi_segment_synthesis"],
                                    },
                                },
                                "required": ["segment_id", "quote_text", "start_ms", "end_ms", "quote_type"],
                            },
                        },
                    },
                    "required": [
                        "claim_text", "reasoning_text", "claim_type",
                        "extraction_confidence", "topics", "evidence_spans",
                    ],
                },
            },
        },
        "required": ["claims"],
    },
}


# ── Core Extraction ──────────────────────────────────────────────────

def _format_segments_for_llm(segments: list[TranscriptSegments]) -> str:
    """Format segments into the spec's input format."""
    lines = []
    for seg in segments:
        start = _ms_to_timestamp(seg.start_ms)
        end = _ms_to_timestamp(seg.end_ms)
        text = seg.text[:300]  # Limit per-segment text
        lines.append(f'[seg_id: {seg.id} | {start}–{end}] "{text}"')
    return "\n".join(lines)


def _ms_to_timestamp(ms: int) -> str:
    """Convert milliseconds to MM:SS.mmm format."""
    total_seconds = ms // 1000
    remainder_ms = ms % 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}.{remainder_ms:03d}"


def extract_claims_from_segments(
    session: Session,
    video: Videos,
    person: People,
    segments: list[TranscriptSegments],
    attribution_confidence: float,
    batch_size: int = 30,
) -> list[dict]:
    """Extract claims from a person's transcript segments via LLM.

    Batches segments to keep context manageable.
    """
    if not segments:
        return []

    all_stored_claims = []

    # Batch segments
    for batch_start in range(0, len(segments), batch_size):
        batch = segments[batch_start : batch_start + batch_size]
        claims = _extract_batch(
            session, video, person, batch, segments, attribution_confidence
        )
        all_stored_claims.extend(claims)

    return all_stored_claims


def _extract_batch(
    session: Session,
    video: Videos,
    person: People,
    batch: list[TranscriptSegments],
    all_segments: list[TranscriptSegments],
    attribution_confidence: float,
) -> list[dict]:
    """Extract claims from a single batch of segments."""
    from src.providers.llm import call_llm_json, call_llm_tool

    # Build segment map for validation
    seg_map = {str(seg.id): seg for seg in all_segments}

    # Format for LLM — load topics from DB
    segments_text = _format_segments_for_llm(batch)
    topic_slugs = _get_topic_slugs(session)
    topics_str = ", ".join(topic_slugs)

    system = EXTRACTION_SYSTEM_PROMPT.format(topics=topics_str)
    user = EXTRACTION_USER_TEMPLATE.format(
        person_name=person.name,
        segments_text=segments_text,
    )

    # Call LLM
    try:
        result = call_llm_tool(system, user, EXTRACT_CLAIMS_TOOL)
    except Exception as e:
        logger.warning(f"Tool-based extraction failed for {person.name}, falling back to JSON: {e}")
        try:
            result = call_llm_json(system, user)
        except Exception as fallback_error:
            logger.error(f"LLM extraction failed for {person.name}: {fallback_error}")
            raise EnrichmentError(str(fallback_error)) from fallback_error

    raw_claims = []
    if isinstance(result, dict):
        raw_claims = result.get("claims", [])
    elif isinstance(result, list):
        # LLM returned a list directly — treat as claims list
        raw_claims = result
    else:
        logger.warning(f"Unexpected LLM result type: {type(result)}")
    stored_claims = []

    # Derive trust level
    trust_level = _derive_trust_level(attribution_confidence)

    # Build topic lookup
    topic_lookup = {}
    for t in session.query(Topics).all():
        topic_lookup[t.slug] = t

    for raw in raw_claims:
        claim_text = raw.get("claim_text", "")
        if not claim_text:
            continue

        extraction_conf = raw.get("extraction_confidence", 0.7)
        claim_topics = raw.get("topics", [])

        # Determine review_status per auto-review rules
        review_status = _determine_review_status(
            trust_level, claim_topics, topic_slugs
        )

        # Create claim
        claim = Claims(
            person_id=person.id,
            video_id=video.id,
            claim_text=claim_text,
            reasoning_text=raw.get("reasoning_text", ""),
            claim_type=raw.get("claim_type"),
            speaker_certainty=raw.get("speaker_certainty"),
            attribution_confidence=attribution_confidence,
            extraction_confidence=extraction_conf,
            trust_level=trust_level,
            topics=claim_topics,
            sentiment=raw.get("sentiment"),
            temporal_marker=raw.get("temporal_marker"),
            review_status=review_status,
        )
        session.add(claim)
        session.flush()

        # Store evidence spans
        for i, ev in enumerate(raw.get("evidence_spans", [])):
            seg_id = ev.get("segment_id", "")
            seg = seg_map.get(seg_id)

            evidence = ClaimEvidence(
                claim_id=claim.id,
                segment_id=seg.id if seg else batch[0].id,
                evidence_order=i,
                quote_text=ev.get("quote_text", ""),
                start_ms=ev.get("start_ms", 0),
                end_ms=max(ev.get("end_ms", 1), ev.get("start_ms", 0) + 1),
                quote_type=ev.get("quote_type", "direct_quote"),
            )
            session.add(evidence)

        # Link to topics (junction table is the single source of truth)
        for slug in claim_topics:
            topic = topic_lookup.get(slug)
            if topic:
                ct = ClaimTopics(claim_id=claim.id, topic_id=topic.id)
                session.add(ct)

        # Sync the denormalized cache from the junction table
        claim.sync_topics_cache()

        stored_claims.append({
            "claim_id": str(claim.id),
            "text": claim_text[:80],
            "topics": claim_topics,
            "trust": trust_level,
            "review": review_status,
        })

    session.flush()
    return stored_claims


# ── Trust & Review Rules ─────────────────────────────────────────────

def _derive_trust_level(attribution_confidence: float) -> str:
    """Derive trust_level from attribution_confidence."""
    if attribution_confidence >= 0.80:
        return "high"
    elif attribution_confidence >= 0.50:
        return "medium"
    else:
        return "low"


def _determine_review_status(
    trust_level: str, claim_topics: list[str], known_topics: list[str]
) -> str:
    """Apply auto-review rules per spec precedence."""
    # Priority 1: low trust → always pending
    if trust_level == "low":
        return "pending_review"

    # Priority 2: unknown topic → pending
    has_unknown_topic = any(t not in known_topics for t in claim_topics)
    if has_unknown_topic:
        return "pending_review"

    # Priority 3-6: medium/high trust → approved
    # (Position shift detection happens in positions.py after storage)
    if trust_level == "medium":
        return "approved"
    if trust_level == "high":
        return "approved"

    return "pending_review"


# ── Orchestrator ─────────────────────────────────────────────────────

def enrich_video(session: Session, video: Videos) -> dict:
    """Extract claims from all tracked speakers in a video.

    Idempotent: tracks per-speaker enrichment_status on VideoPeople so that
    partially-completed runs can be resumed without duplicating claims.
    """
    result = {"claims_extracted": 0, "people_processed": 0, "errors": [], "skipped": False}
    try:
        with session.begin_nested():
            # Get identified people for this video
            video_people = session.query(VideoPeople).filter(
                VideoPeople.video_id == video.id
            ).all()

            if not video_people:
                result["skipped"] = True
                result["errors"].append("No identified people")
            else:
                for vp in video_people:
                    # Skip speakers already enriched (idempotent resumption)
                    if vp.enrichment_status == "completed":
                        logger.info(f"Skipping already-enriched speaker: {vp.person.name}")
                        result["people_processed"] += 1
                        continue

                    person = vp.person

                    # Mark in-progress for crash recovery visibility
                    vp.enrichment_status = "in_progress"
                    session.flush()

                    # Get segments for this person
                    segments = (
                        session.query(TranscriptSegments)
                        .filter(
                            TranscriptSegments.video_id == video.id,
                            TranscriptSegments.person_id == person.id,
                        )
                        .order_by(TranscriptSegments.segment_index)
                        .all()
                    )

                    # For fast-path: if no person_id on segments, check sole-speaker rule
                    if not segments and video.transcript_type == "fast":
                        vp_count = session.query(VideoPeople).filter(
                            VideoPeople.video_id == video.id
                        ).count()
                        if vp_count == 1:
                            # Sole speaker — assign all segments to this person
                            segments = (
                                session.query(TranscriptSegments)
                                .filter(TranscriptSegments.video_id == video.id)
                                .order_by(TranscriptSegments.segment_index)
                                .all()
                            )
                            for seg in segments:
                                seg.person_id = person.id
                            session.flush()
                        else:
                            vp.enrichment_status = "failed"
                            result["errors"].append(
                                f"Skipped {person.name}: fast-path multi-speaker not upgraded"
                            )
                            continue

                    # For official transcripts: segments have speaker_name but need person_id match
                    if not segments and video.transcript_type == "official":
                        segments = (
                            session.query(TranscriptSegments)
                            .filter(
                                TranscriptSegments.video_id == video.id,
                                TranscriptSegments.speaker_name.ilike(f"%{person.name.split()[0]}%"),
                            )
                            .order_by(TranscriptSegments.segment_index)
                            .all()
                        )

                    if not segments:
                        logger.warning(f"No segments for {person.name} in {video.youtube_video_id}")
                        vp.enrichment_status = "completed"  # Nothing to do, don't retry
                        continue

                    # Extract claims
                    attribution_conf = float(vp.confidence or 0.5)
                    claims = extract_claims_from_segments(
                        session, video, person, segments, attribution_conf
                    )

                    # Mark speaker as completed
                    vp.enrichment_status = "completed"
                    result["claims_extracted"] += len(claims)
                    result["people_processed"] += 1
                    logger.info(f"Extracted {len(claims)} claims from {person.name}")

                # Update video status only when extraction completed cleanly.
                if result["claims_extracted"] > 0:
                    video.status = "enriched"
                    video.error_message = None
                elif not result["skipped"]:
                    video.status = "enriched"  # No claims but processed OK
                    video.error_message = None
    except EnrichmentError as exc:
        result["errors"].append(str(exc))
        video.error_message = str(exc)
        video.retry_count = (video.retry_count or 0) + 1
        session.commit()
        return result
    except Exception as exc:
        logger.exception("Unexpected enrichment failure for %s", video.youtube_video_id)
        result["errors"].append(str(exc))
        video.error_message = str(exc)
        video.retry_count = (video.retry_count or 0) + 1
        session.commit()
        return result

    session.commit()
    return result


def enrich_pending(session: Session, limit: int = 5) -> dict:
    """Enrich all identified videos.

    Prioritises videos from favorited channels/people.
    """
    from src.db.models import Favorites
    from sqlalchemy import case, func

    fav_channel = (
        session.query(Favorites.channel_id, Favorites.priority)
        .filter(Favorites.channel_id.isnot(None))
        .subquery()
    )
    fav_person = (
        session.query(Favorites.person_id, Favorites.priority)
        .filter(Favorites.person_id.isnot(None))
        .subquery()
    )

    videos = (
        session.query(Videos)
        .outerjoin(fav_channel, Videos.podcast_channel_id == fav_channel.c.channel_id)
        .outerjoin(fav_person, Videos.discovered_by_person_id == fav_person.c.person_id)
        .filter(Videos.status == "identified")
        .order_by(
            case(
                (fav_channel.c.priority.isnot(None), 0),
                (fav_person.c.priority.isnot(None), 0),
                else_=1,
            ),
            func.coalesce(fav_channel.c.priority, fav_person.c.priority, 99),
            Videos.created_at,
        )
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "total_claims": 0, "errors": []}

    for video in videos:
        logger.info(f"Enriching: {video.title or video.youtube_video_id}")
        stats["processed"] += 1

        result = enrich_video(session, video)
        stats["total_claims"] += result["claims_extracted"]
        stats["errors"].extend(result["errors"])

    return stats


def get_enrich_status(session: Session) -> dict:
    """Get enrichment pipeline stats."""
    from src.db.models import ClaimEmbeddings, PersonTopicPositions

    total_videos = session.query(Videos).count()
    enriched = session.query(Videos).filter(Videos.status == "enriched").count()
    identified = session.query(Videos).filter(Videos.status == "identified").count()

    total_claims = session.query(Claims).count()
    approved = session.query(Claims).filter(Claims.review_status == "approved").count()
    pending = session.query(Claims).filter(Claims.review_status == "pending_review").count()

    evidence = session.query(ClaimEvidence).count()
    embeddings = session.query(ClaimEmbeddings).count()
    positions = session.query(PersonTopicPositions).count()

    return {
        "total_videos": total_videos,
        "enriched": enriched,
        "pending_enrichment": identified,
        "total_claims": total_claims,
        "approved_claims": approved,
        "pending_claims": pending,
        "evidence_spans": evidence,
        "embeddings": embeddings,
        "positions": positions,
    }

"""Public read-only API endpoints.

All claim endpoints default to review_status='approved'.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimEvidence,
    ClaimEmbeddings,
    Claims,
    ClaimTopics,
    EpisodeSummaries,
    Favorites,
    People,
    PersonTopicPositions,
    PodcastChannels,
    PositionHistoryLog,
    Topics,
    VideoPeople,
    Videos,
    XPosts,
)
from src.db.session import get_session

router = APIRouter()


def get_db():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


# ── People ───────────────────────────────────────────────────────────

@router.get("/people")
def list_people(
    tier: int | None = None,
    domain: str | None = None,
    topic: list[str] | None = Query(None),
    active: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(People).filter(People.active == active)
    if tier:
        q = q.filter(People.tier == tier)
    if domain:
        q = q.filter(People.domain == domain)
    if topic:
        # Filter people who have approved claims on any of the given topics
        q = q.filter(
            People.id.in_(
                db.query(Claims.person_id)
                .filter(Claims.review_status == "approved")
                .filter(or_(*[Claims.topics.any(t) for t in topic]))
                .distinct()
            )
        )

    people = q.order_by(People.tier, People.name).all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "tier": p.tier,
            "domain": p.domain,
            "expertise_domains": p.expertise_domains,
            "role_title": p.role_title,
            "bio": p.bio,
            "net_worth": p.net_worth,
            "age": p.age,
            "photo_initials": p.photo_initials or _initials(p.name),
            "accent_color": p.accent_color or "#666",
            "claim_count": db.query(Claims).filter(
                Claims.person_id == p.id,
                Claims.review_status == "approved",
            ).count(),
        }
        for p in people
    ]


@router.get("/people/{person_id}")
def get_person(
    person_id: UUID,
    claim_limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    person = db.query(People).filter(People.id == person_id).first()
    if not person:
        raise HTTPException(404, "Person not found")

    claim_count = (
        db.query(Claims)
        .filter(Claims.person_id == person.id, Claims.review_status == "approved")
        .count()
    )

    claims = (
        db.query(Claims)
        .filter(Claims.person_id == person.id, Claims.review_status == "approved")
        .order_by(Claims.created_at.desc())
        .limit(claim_limit)
        .all()
    )

    positions = (
        db.query(PersonTopicPositions)
        .filter(PersonTopicPositions.person_id == person.id)
        .all()
    )

    return {
        "id": str(person.id),
        "name": person.name,
        "tier": person.tier,
        "domain": person.domain,
        "expertise_domains": person.expertise_domains,
        "inclusion_notes": person.inclusion_notes,
        "role_title": person.role_title,
        "bio": person.bio,
        "net_worth": person.net_worth,
        "age": person.age,
        "photo_initials": person.photo_initials or _initials(person.name),
        "accent_color": person.accent_color or "#666",
        "active": person.active,
        "claim_count": claim_count,
        "claims": [_claim_summary(c) for c in claims],
        "positions": [
            {
                "topic_id": str(p.topic_id),
                "topic": p.topic.slug if p.topic else None,
                "topic_name": p.topic.name if p.topic else None,
                "current_position": p.current_position,
                "sentiment": p.sentiment,
                "claim_count": p.claim_count,
                "last_updated": p.last_updated.isoformat() if p.last_updated else None,
            }
            for p in positions
        ],
        "shifts": [
            {
                "topic_name": s.topic.name if s.topic else None,
                "topic_slug": s.topic.slug if s.topic else None,
                "position_summary": s.position_summary,
                "previous_position": s.previous_position,
                "shift_note": s.shift_note,
                "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
            }
            for s in (
                db.query(PositionHistoryLog)
                .filter(PositionHistoryLog.person_id == person.id, PositionHistoryLog.is_shift == True)
                .order_by(PositionHistoryLog.recorded_at.desc())
                .limit(10)
                .all()
            )
        ],
        "appearances": [
            {
                "video_id": str(s.video_id),
                "video_title": s.video.title if s.video else None,
                "channel_name": s.video.podcast_channel.name if s.video and s.video.podcast_channel else None,
                "published_at": s.video.published_at.isoformat() if s.video and s.video.published_at else None,
                "watch_verdict": s.watch_verdict,
                "tldr": s.tldr,
            }
            for s in (
                db.query(EpisodeSummaries)
                .filter(EpisodeSummaries.person_focus_id == person.id)
                .order_by(EpisodeSummaries.generated_at.desc())
                .limit(10)
                .all()
            )
        ],
    }


# ── Claims ───────────────────────────────────────────────────────────

@router.get("/claims")
def list_claims(
    person_id: UUID | None = None,
    topic: list[str] | None = Query(None),
    claim_type: str | None = None,
    trust_level: str | None = None,
    source: str | None = None,  # 'video' / 'x_post' / None (all)
    review_status: str = "approved",  # 'approved' / 'pending_review' / 'rejected' / 'all'
    days_back: int = 30,  # 0 = all time
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Claims)
    if review_status != "all":
        q = q.filter(Claims.review_status == review_status)

    if person_id:
        q = q.filter(Claims.person_id == person_id)
    if topic:
        q = q.filter(or_(*[Claims.topics.any(t) for t in topic])).distinct(Claims.id)
    if claim_type:
        q = q.filter(Claims.claim_type == claim_type)
    if trust_level:
        q = q.filter(Claims.trust_level == trust_level)
    if source == "video":
        q = q.filter(Claims.video_id.isnot(None))
    elif source == "x_post":
        q = q.filter(Claims.x_post_id.isnot(None))
    if days_back and days_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        q = q.filter(Claims.created_at >= cutoff)

    claims = q.order_by(Claims.created_at.desc()).limit(limit).all()
    return [_claim_summary(c) for c in claims]


@router.get("/claims/{claim_id}")
def get_claim(claim_id: UUID, db: Session = Depends(get_db)):
    claim = db.query(Claims).filter(Claims.id == claim_id).first()
    if not claim:
        raise HTTPException(404, "Claim not found")

    evidence = (
        db.query(ClaimEvidence)
        .filter(ClaimEvidence.claim_id == claim.id)
        .order_by(ClaimEvidence.evidence_order)
        .all()
    )

    video = db.query(Videos).filter(Videos.id == claim.video_id).first()

    return {
        **_claim_summary(claim),
        "reasoning_text": claim.reasoning_text,
        "evidence_spans": [
            {
                "order": e.evidence_order,
                "segment_id": str(e.segment_id) if e.segment_id else None,
                "quote_text": e.quote_text,
                "quote_type": e.quote_type,
                "start_ms": e.start_ms,
                "end_ms": e.end_ms,
                "source_url": (
                    f"https://youtube.com/watch?v={video.youtube_video_id}&t={e.start_ms // 1000}s"
                    if video and video.youtube_video_id and e.start_ms
                    else None
                ),
                "timestamp_display": _format_timestamp(e.start_ms // 1000) if e.start_ms else "",
            }
            for e in evidence
        ],
        "source_video": {
            "id": str(video.id),
            "youtube_video_id": video.youtube_video_id,
            "title": video.title,
            "url": f"https://youtube.com/watch?v={video.youtube_video_id}",
        } if video else None,
    }


@router.get("/claims/search")
def search_claims(
    q: str = Query(..., min_length=3),
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db),
):
    """Semantic search across approved claims using embeddings."""
    from src.pipeline.embeddings import generate_embedding

    try:
        query_vector = generate_embedding(q)
    except Exception as e:
        raise HTTPException(500, f"Embedding failed: {e}")

    # Use pgvector cosine distance
    from sqlalchemy import text
    results = db.execute(
        text("""
            SELECT ce.claim_id,
                   1 - (ce.embedding <=> :vec::vector) as similarity
            FROM claim_embeddings ce
            JOIN claims c ON c.id = ce.claim_id
            WHERE c.review_status = 'approved'
            ORDER BY ce.embedding <=> :vec::vector
            LIMIT :lim
        """),
        {"vec": str(query_vector), "lim": limit},
    ).fetchall()

    claim_ids = [r[0] for r in results]
    similarities = {str(r[0]): float(r[1]) for r in results}

    claims = db.query(Claims).filter(Claims.id.in_(claim_ids)).all()
    return [
        {**_claim_summary(c), "similarity": similarities.get(str(c.id), 0)}
        for c in sorted(claims, key=lambda c: -similarities.get(str(c.id), 0))
    ]


# ── Topics ───────────────────────────────────────────────────────────

@router.get("/topics")
def list_topics(db: Session = Depends(get_db)):
    topics = db.query(Topics).order_by(Topics.slug).all()
    return [
        {
            "id": str(t.id),
            "slug": t.slug,
            "label": t.name,
            "claim_count": (
                db.query(ClaimTopics)
                .join(Claims)
                .filter(ClaimTopics.topic_id == t.id, Claims.review_status == "approved")
                .count()
            ),
        }
        for t in topics
    ]


@router.get("/topics/{slug}")
def topic_detail(slug: str, db: Session = Depends(get_db)):
    """Enriched topic detail with recent claims and people with positions."""
    topic = db.query(Topics).filter(Topics.slug == slug).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    # Recent approved claims on this topic
    recent_claims = (
        db.query(Claims)
        .filter(Claims.topics.any(slug), Claims.review_status == "approved")
        .order_by(Claims.created_at.desc())
        .limit(10)
        .all()
    )

    # People with positions on this topic
    positions = (
        db.query(PersonTopicPositions)
        .filter(PersonTopicPositions.topic_id == topic.id)
        .all()
    )

    # Total claim count
    claim_count = (
        db.query(ClaimTopics)
        .join(Claims)
        .filter(ClaimTopics.topic_id == topic.id, Claims.review_status == "approved")
        .count()
    )

    return {
        "slug": topic.slug,
        "name": topic.name,
        "claim_count": claim_count,
        "person_count": len(positions),
        "recent_claims": [_claim_summary(c) for c in recent_claims],
        "people_with_positions": [
            {
                "person_id": str(p.person_id),
                "person_name": p.person.name if p.person else None,
                "current_position": p.current_position,
                "claim_count": p.claim_count,
                "last_updated": p.last_updated.isoformat() if p.last_updated else None,
            }
            for p in positions
        ],
    }


@router.get("/topics/{slug}/positions")
def topic_positions(slug: str, db: Session = Depends(get_db)):
    topic = db.query(Topics).filter(Topics.slug == slug).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    positions = (
        db.query(PersonTopicPositions)
        .filter(PersonTopicPositions.topic_id == topic.id)
        .all()
    )

    return {
        "topic": slug,
        "positions": [
            {
                "person_id": str(p.person_id),
                "person_name": p.person.name if p.person else None,
                "current_position": p.current_position,
                "claim_count": p.claim_count,
                "last_updated": p.last_updated.isoformat() if p.last_updated else None,
            }
            for p in positions
        ],
    }


@router.get("/topics/{slug}/consensus")
def topic_consensus(slug: str, db: Session = Depends(get_db)):
    topic = db.query(Topics).filter(Topics.slug == slug).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    claims = (
        db.query(Claims)
        .filter(
            Claims.topics.any(slug),
            Claims.review_status == "approved",
        )
        .all()
    )

    by_sentiment = {"bullish": [], "bearish": [], "neutral": [], "mixed": []}
    for c in claims:
        sentiment = c.sentiment or "neutral"
        if sentiment in by_sentiment:
            by_sentiment[sentiment].append(_claim_summary(c))

    return {
        "topic": slug,
        "total_claims": len(claims),
        "consensus": by_sentiment,
    }


# ── Intelligence ─────────────────────────────────────────────────────

class RelevantRequest(BaseModel):
    topics: list[str]
    max_results: int = 5
    min_confidence: float = 0.7
    days_back: int = 30


@router.post("/intelligence/relevant")
def intelligence_relevant(req: RelevantRequest, db: Session = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=req.days_back)

    q = db.query(Claims).filter(
        Claims.review_status == "approved",
        Claims.extraction_confidence >= req.min_confidence,
        Claims.created_at >= cutoff,
    )

    # Filter by any matching topic
    from sqlalchemy import or_
    topic_filters = [Claims.topics.any(t) for t in req.topics]
    if topic_filters:
        q = q.filter(or_(*topic_filters))

    claims = q.order_by(Claims.extraction_confidence.desc()).limit(req.max_results).all()

    return [
        {
            **_claim_summary(c),
            "reasoning_text": c.reasoning_text,
            "source_url": f"https://youtube.com/watch?v={c.video.youtube_video_id}" if c.video else None,
        }
        for c in claims
    ]


class DivergenceRequest(BaseModel):
    position: str
    topic: str


@router.post("/intelligence/divergence")
def intelligence_divergence(req: DivergenceRequest, db: Session = Depends(get_db)):
    """Two-step: vector search + LLM classification."""
    from src.pipeline.embeddings import generate_embedding
    from src.providers.llm import call_llm_json

    # Step 1: Vector search for top 20 relevant claims
    try:
        query_vector = generate_embedding(req.position)
    except Exception as e:
        raise HTTPException(500, f"Embedding failed: {e}")

    from sqlalchemy import text
    results = db.execute(
        text("""
            SELECT ce.claim_id
            FROM claim_embeddings ce
            JOIN claims c ON c.id = ce.claim_id
            WHERE c.review_status = 'approved'
            ORDER BY ce.embedding <=> :vec::vector
            LIMIT 20
        """),
        {"vec": str(query_vector)},
    ).fetchall()

    claim_ids = [r[0] for r in results]
    claims = db.query(Claims).filter(Claims.id.in_(claim_ids)).all()

    if not claims:
        return {"agrees": [], "disagrees": [], "nuanced": []}

    # Step 2: LLM classification
    claims_text = "\n".join(
        f"[{i}] {c.person.name}: \"{c.claim_text}\"" for i, c in enumerate(claims)
    )

    try:
        classification = call_llm_json(
            f"""Classify each claim as 'agrees', 'disagrees', or 'nuanced' relative to this position:
"{req.position}"

Return JSON: {{"classifications": [{{"index": 0, "stance": "agrees"}}, ...]}}""",
            f"Claims:\n{claims_text}",
        )
    except Exception:
        # Fallback: return all as nuanced
        return {
            "agrees": [],
            "disagrees": [],
            "nuanced": [_claim_summary(c) for c in claims],
        }

    # Build response
    result = {"agrees": [], "disagrees": [], "nuanced": []}
    for cl in classification.get("classifications", []):
        idx = cl.get("index", 0)
        stance = cl.get("stance", "nuanced")
        if 0 <= idx < len(claims):
            claim = claims[idx]
            entry = {
                **_claim_summary(claim),
                "reasoning_text": claim.reasoning_text,
            }
            if stance in result:
                result[stance].append(entry)
            else:
                result["nuanced"].append(entry)

    return result


# ── Briefs ───────────────────────────────────────────────────────────

@router.get("/briefs/latest")
def get_latest_brief(db: Session = Depends(get_db)):
    from src.db.models import Briefs

    brief = (
        db.query(Briefs)
        .filter(Briefs.status == "published")
        .order_by(Briefs.created_at.desc())
        .first()
    )

    if not brief:
        # Fall back to any brief
        brief = db.query(Briefs).order_by(Briefs.created_at.desc()).first()

    if not brief:
        raise HTTPException(404, "No briefs generated yet")

    return {
        "id": str(brief.id),
        "title": brief.title,
        "content_markdown": brief.content_markdown,
        "sections": brief.sections,
        "claim_ids": [str(c) for c in (brief.claim_ids or [])],
        "status": brief.status,
        "created_at": brief.created_at.isoformat() if brief.created_at else None,
        "published_at": brief.published_at.isoformat() if brief.published_at else None,
    }


# ── Summaries Feed ───────────────────────────────────────────────────

@router.get("/summaries/feed")
def summaries_feed(
    limit: int = Query(default=20, le=50),
    person_id: UUID | None = None,
    channel_id: UUID | None = None,
    summary_type: str | None = None,
    db: Session = Depends(get_db),
):
    """Reverse-chronological feed of episode summaries."""
    q = db.query(EpisodeSummaries)

    if person_id:
        q = q.filter(EpisodeSummaries.person_focus_id == person_id)
    if channel_id:
        q = q.join(Videos).filter(Videos.podcast_channel_id == channel_id)
    if summary_type:
        q = q.filter(EpisodeSummaries.summary_type == summary_type)

    summaries = q.order_by(EpisodeSummaries.generated_at.desc()).limit(limit).all()

    return {
        "items": [_summary_card(s, db) for s in summaries],
        "total": q.count(),
    }


@router.get("/summaries/{video_id}")
def video_summaries(video_id: UUID, db: Session = Depends(get_db)):
    """All summaries for a specific video."""
    summaries = (
        db.query(EpisodeSummaries)
        .filter(EpisodeSummaries.video_id == video_id)
        .all()
    )
    return [_summary_card(s, db) for s in summaries]


def _summary_card(s: EpisodeSummaries, db: Session) -> dict:
    """Format a summary for the feed API response."""
    video = s.video
    yt_id = video.youtube_video_id if video else None
    return {
        "id": str(s.id),
        "video_id": str(s.video_id),
        "video_title": video.title if video else None,
        "channel_name": video.podcast_channel.name if video and video.podcast_channel else None,
        "channel_id": str(video.podcast_channel_id) if video and video.podcast_channel_id else None,
        "published_at": video.published_at.isoformat() if video and video.published_at else None,
        "duration_seconds": video.duration_seconds if video else None,
        "duration_display": _format_duration(video.duration_seconds) if video and video.duration_seconds else None,
        "youtube_video_id": yt_id,
        "discovery_method": video.discovery_method if video else None,
        "summary_type": s.summary_type,
        "person_focus_name": s.person_focus.name if s.person_focus else None,
        "person_focus_id": str(s.person_focus_id) if s.person_focus_id else None,
        "tldr": s.tldr,
        "summary_body": s.summary_body,
        "detailed": s.detailed_json,
        "whats_new": s.whats_new,
        "watch_verdict": s.watch_verdict,
        "watch_verdict_reason": s.watch_verdict_reason,
        "source_url": f"https://youtube.com/watch?v={yt_id}" if yt_id else None,
        "generated_at": s.generated_at.isoformat() if s.generated_at else None,
    }


# ── Favorites ────────────────────────────────────────────────────────

@router.get("/favorites")
def list_favorites(db: Session = Depends(get_db)):
    """List all favorites."""
    favs = db.query(Favorites).order_by(Favorites.priority, Favorites.created_at).all()
    return [
        {
            "id": str(f.id),
            "type": "person" if f.person_id else "channel",
            "person_id": str(f.person_id) if f.person_id else None,
            "person_name": f.person.name if f.person else None,
            "channel_id": str(f.channel_id) if f.channel_id else None,
            "channel_name": f.channel.name if f.channel else None,
            "priority": f.priority,
            "notify": f.notify,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in favs
    ]


class FavoriteCreate(BaseModel):
    person_id: UUID | None = None
    channel_id: UUID | None = None
    priority: int = 5
    notify: bool = True


@router.post("/favorites")
def add_favorite(req: FavoriteCreate, db: Session = Depends(get_db)):
    """Add a person or channel as a favorite."""
    if not req.person_id and not req.channel_id:
        raise HTTPException(400, "Must specify person_id or channel_id")
    if req.person_id and req.channel_id:
        raise HTTPException(400, "Specify only one of person_id or channel_id")

    fav = Favorites(
        person_id=req.person_id,
        channel_id=req.channel_id,
        priority=req.priority,
        notify=req.notify,
    )
    db.add(fav)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(409, f"Favorite already exists or invalid: {e}")

    return {"id": str(fav.id), "status": "created"}


@router.delete("/favorites/{fav_id}")
def remove_favorite(fav_id: UUID, db: Session = Depends(get_db)):
    """Remove a favorite."""
    fav = db.query(Favorites).filter(Favorites.id == fav_id).first()
    if not fav:
        raise HTTPException(404, "Favorite not found")
    db.delete(fav)
    db.commit()
    return {"status": "deleted"}


# ── Pipeline Status ──────────────────────────────────────────────────

@router.get("/pipeline/status")
def pipeline_status(db: Session = Depends(get_db)):
    from src.db.models import TranscriptRuns, TranscriptSegments

    return {
        "people": db.query(People).filter(People.active == True).count(),
        "videos": {
            "total": db.query(Videos).count(),
            "discovered": db.query(Videos).filter(Videos.status == "discovered").count(),
            "transcribed": db.query(Videos).filter(Videos.status == "transcribed").count(),
            "identified": db.query(Videos).filter(Videos.status == "identified").count(),
            "enriched": db.query(Videos).filter(Videos.status == "enriched").count(),
            "skipped": db.query(Videos).filter(Videos.status == "skipped").count(),
        },
        "claims": {
            "total": db.query(Claims).count(),
            "approved": db.query(Claims).filter(Claims.review_status == "approved").count(),
            "pending": db.query(Claims).filter(Claims.review_status == "pending_review").count(),
        },
        "segments": db.query(TranscriptSegments).count(),
        "embeddings": db.query(ClaimEmbeddings).count(),
        "positions": db.query(PersonTopicPositions).count(),
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _claim_summary(c: Claims) -> dict:
    # Best evidence span for provenance
    evidence_data = _get_best_evidence(c) if c.video else None

    # Build source object (spec-compliant nested structure)
    source = _build_source_object(c, evidence_data)

    return {
        "id": str(c.id),
        "person_id": str(c.person_id),
        "person_name": c.person.name if c.person else None,
        "video_id": str(c.video_id) if c.video_id else None,
        "claim_text": c.claim_text,
        "claim_type": c.claim_type,
        "speaker_certainty": c.speaker_certainty,
        "trust_level": c.trust_level,
        "attribution_confidence": float(c.attribution_confidence) if c.attribution_confidence else None,
        "extraction_confidence": float(c.extraction_confidence) if c.extraction_confidence else None,
        "topics": c.topics,
        "sentiment": c.sentiment,
        "temporal_marker": c.temporal_marker,
        "review_status": c.review_status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "source": source,
        # Flat fields kept for backward compatibility with existing frontend
        "source_type": source["type"] if source else "video",
        "source_video_title": source.get("title") if source else None,
        "source_video_url": source.get("source_url") if source else None,
        "source_timestamp_display": source.get("timestamp_display") if source else None,
        "best_quote": source.get("evidence_quote") if source else None,
    }


def _build_source_object(c: Claims, evidence_data: dict | None) -> dict | None:
    """Build spec-compliant source object for a claim (video or X post)."""

    # X post source
    if c.x_post_id and c.x_post:
        xp = c.x_post
        return {
            "type": "x_post",
            "title": f"@{xp.platform_post_id}" if xp else "X Post",
            "source_url": xp.post_url,
            "timestamp_display": "",
            "timestamp_ms": None,
            "published_at": xp.posted_at.isoformat() if xp.posted_at else None,
            "evidence_quote": (evidence_data or {}).get("quote_text"),
            "evidence_type": "x_post_text",
            "x_handle": c.person.x_handle if c.person else None,
        }

    # Video source
    if not c.video:
        return None

    yt_id = c.video.youtube_video_id
    if not yt_id:
        return None

    ev = evidence_data or {}
    start_ms = ev.get("start_ms")
    start_s = int(start_ms / 1000) if start_ms else None

    base_url = f"https://youtube.com/watch?v={yt_id}"
    source_url = f"{base_url}&t={start_s}s" if start_s else base_url

    return {
        "type": "video",
        "title": c.video.title or "Unknown video",
        "source_url": source_url,
        "timestamp_display": _format_timestamp(start_s) if start_s else "",
        "timestamp_ms": start_ms,
        "published_at": c.video.published_at.isoformat() if c.video.published_at else None,
        "evidence_quote": ev.get("quote_text"),
        "evidence_type": ev.get("quote_type", "direct_quote"),
    }


def _get_best_evidence(claim: Claims) -> dict | None:
    """Return the first evidence span for a claim (cheapest query)."""
    from sqlalchemy.orm import object_session

    session = object_session(claim)
    if not session:
        return None

    ev = (
        session.query(ClaimEvidence)
        .filter(ClaimEvidence.claim_id == claim.id)
        .order_by(ClaimEvidence.evidence_order)
        .first()
    )
    if not ev:
        return None

    return {
        "quote_text": ev.quote_text,
        "quote_type": ev.quote_type,
        "start_ms": ev.start_ms,
        "end_ms": ev.end_ms,
    }


def _format_timestamp(seconds: int) -> str:
    """Format seconds as MM:SS or H:MM:SS."""
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _format_duration(seconds: int | None) -> str:
    """Format seconds as a human-readable duration string."""
    if not seconds:
        return ""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _initials(name: str) -> str:
    """Generate 2-char initials from a name."""
    parts = name.split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else name[:2].upper()


# ── Channels ────────────────────────────────────────────────────────────

@router.get("/channels")
def list_channels(db: Session = Depends(get_db)):
    """List all active channels with video counts and scan status."""
    channels = (
        db.query(PodcastChannels)
        .filter(PodcastChannels.active == True)  # noqa: E712
        .order_by(PodcastChannels.tier, PodcastChannels.name)
        .all()
    )
    return [
        {
            "id": str(ch.id),
            "name": ch.name,
            "youtube_channel_id": ch.youtube_channel_id,
            "tier": ch.tier,
            "monitoring_mode": ch.monitoring_mode,
            "video_count": (
                db.query(Videos)
                .filter(Videos.podcast_channel_id == ch.id)
                .count()
            ),
            "last_scanned_at": (
                ch.last_scanned_at.isoformat() if ch.last_scanned_at else None
            ),
        }
        for ch in channels
    ]


class ChannelCreate(BaseModel):
    url_or_handle: str  # "@dwarkesh" or "https://youtube.com/@dwarkesh" or channel ID


@router.post("/channels")
def add_channel(req: ChannelCreate, db: Session = Depends(get_db)):
    """Add a new YouTube channel to monitor."""
    import re
    import subprocess

    raw = req.url_or_handle.strip()

    # Resolve to channel ID
    if raw.startswith("UC") and len(raw) == 24:
        # Already a channel ID
        channel_id = raw
        channel_name = raw
    else:
        # Build URL if just a handle
        if raw.startswith("@"):
            url = f"https://youtube.com/{raw}"
        elif not raw.startswith("http"):
            url = f"https://youtube.com/@{raw}"
        else:
            url = raw

        # Resolve via yt-dlp (metadata only — no download/format needed)
        try:
            import json as _json
            from src.youtube import run_yt_dlp
            proc = run_yt_dlp(
                [
                    "--flat-playlist",
                    "--skip-download",
                    "--dump-single-json",
                    "--playlist-items", "1",
                    url,
                ],
                timeout=30,
            )
            if proc.returncode != 0:
                raise HTTPException(400, f"Could not resolve channel: {(proc.stderr or '')[:200]}")
            meta = _json.loads(proc.stdout)
            channel_id = meta.get("channel_id")
            channel_name = meta.get("channel") or meta.get("uploader") or raw
            if not channel_id:
                raise HTTPException(400, "Could not resolve channel ID")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Failed to resolve channel: {e}")

    # Check for duplicates
    existing = db.query(PodcastChannels).filter(
        PodcastChannels.youtube_channel_id == channel_id
    ).first()
    if existing:
        raise HTTPException(409, f"Channel already tracked: {existing.name}")

    # Look up known channel config from seed data (transcript settings, tier, etc.)
    import json as _json2
    from pathlib import Path as _Path
    seed_config = {}
    try:
        seed_path = _Path(__file__).parent.parent.parent / "data" / "channels_seed.json"
        if seed_path.exists():
            seed_channels = _json2.loads(seed_path.read_text())
            for sc in seed_channels:
                if sc.get("youtube_channel_id") == channel_id:
                    seed_config = sc
                    break
    except Exception:
        pass  # Seed lookup is best-effort

    ch = PodcastChannels(
        youtube_channel_id=channel_id,
        name=seed_config.get("name") or channel_name,
        tier=seed_config.get("tier", 2),
        monitoring_mode=seed_config.get("monitoring_mode", "channel_feed"),
        transcript_url_pattern=seed_config.get("transcript_url_pattern"),
        transcript_parser=seed_config.get("transcript_parser"),
    )
    db.add(ch)
    db.commit()

    # Auto-scan the new channel for recent videos (limited to 5 to avoid overwhelm)
    scan_result = {"new_videos": 0}
    if ch.transcript_url_pattern:
        scan_result["transcript_source"] = ch.transcript_parser or "official"
    try:
        from src.pipeline.discovery import _scan_single_channel, ScanResult
        result = ScanResult()
        _scan_single_channel(db, ch, result, max_videos=5)
        ch.last_scanned_at = datetime.now(timezone.utc)
        ch.video_count = db.query(Videos).filter(Videos.podcast_channel_id == ch.id).count()
        db.commit()
        scan_result["new_videos"] = result.videos_found
    except Exception as e:
        # Don't fail the channel creation if scan fails
        scan_result["scan_error"] = str(e)[:200]

    return {
        "id": str(ch.id),
        "name": ch.name,
        "youtube_channel_id": ch.youtube_channel_id,
        "status": "created",
        **scan_result,
    }


@router.post("/channels/{channel_id}/scan")
def trigger_channel_scan(channel_id: UUID, db: Session = Depends(get_db)):
    """Scan a single channel for new videos."""
    channel = db.query(PodcastChannels).filter(PodcastChannels.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")

    from src.pipeline.discovery import _scan_single_channel, ScanResult
    result = ScanResult()
    try:
        _scan_single_channel(db, channel, result, max_videos=10)
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {e}")

    channel.last_scanned_at = datetime.now(timezone.utc)
    channel.video_count = db.query(Videos).filter(Videos.podcast_channel_id == channel.id).count()
    db.commit()

    return {
        "status": "scanned",
        "new_videos": result.videos_found,
        "total_videos": channel.video_count,
    }


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: UUID, db: Session = Depends(get_db)):
    """Remove a channel and unlink its videos."""
    from src.db.models import ChannelRoles

    channel = db.query(PodcastChannels).filter(PodcastChannels.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")

    name = channel.name

    # 1. Delete channel favorites
    db.query(Favorites).filter(Favorites.channel_id == channel_id).delete(synchronize_session=False)
    # 2. Delete channel roles
    db.query(ChannelRoles).filter(ChannelRoles.channel_id == channel_id).delete(synchronize_session=False)
    # 3. Unlink videos (keep the videos, just remove channel reference)
    db.query(Videos).filter(Videos.podcast_channel_id == channel_id).update(
        {Videos.podcast_channel_id: None}, synchronize_session=False
    )
    # 4. Delete the channel
    db.delete(channel)
    db.commit()

    return {"status": "deleted", "name": name}


# ── Video Queue ────────────────────────────────────────────────────────

@router.get("/videos/queue")
def list_video_queue(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List all videos with their pipeline status."""
    videos = (
        db.query(Videos)
        .order_by(Videos.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for v in videos:
        channel_name = None
        if v.podcast_channel:
            channel_name = v.podcast_channel.name

        result.append({
            "id": str(v.id),
            "youtube_video_id": v.youtube_video_id,
            "title": v.title,
            "status": v.status,
            "channel_name": channel_name,
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "transcript_type": v.transcript_type,
            "error_message": v.error_message,
        })

    return result


# ── Video Add ──────────────────────────────────────────────────────────

class VideoAddRequest(BaseModel):
    youtube_url: str


@router.post("/videos/add")
def add_video(req: VideoAddRequest, db: Session = Depends(get_db)):
    """Add a single video for transcription and summarization."""
    import re

    # Parse YouTube video ID from various URL formats
    url = req.youtube_url.strip()
    video_id = None

    # youtu.be/ID
    m = re.search(r"youtu\.be/([\w-]+)", url)
    if m:
        video_id = m.group(1)

    # youtube.com/watch?v=ID
    if not video_id:
        m = re.search(r"[?&]v=([\w-]+)", url)
        if m:
            video_id = m.group(1)

    # youtube.com/embed/ID or /v/ID
    if not video_id:
        m = re.search(r"youtube\.com/(?:embed|v)/([\w-]+)", url)
        if m:
            video_id = m.group(1)

    # Bare ID (11 chars)
    if not video_id and re.match(r"^[\w-]{11}$", url):
        video_id = url

    if not video_id:
        raise HTTPException(400, "Could not parse YouTube video ID from URL")

    # Check for duplicates
    existing = db.query(Videos).filter(Videos.youtube_video_id == video_id).first()
    if existing:
        return {
            "video_id": str(existing.id),
            "youtube_video_id": video_id,
            "status": "already_exists",
            "current_status": existing.status,
        }

    # Fetch title via yt-dlp (quick metadata grab)
    title = None
    channel_yt_id = None
    try:
        from src.youtube import run_yt_dlp
        proc = run_yt_dlp(
            ["--print", "title", "--print", "channel_id", "--skip-download",
             f"https://youtube.com/watch?v={video_id}"],
            timeout=15,
        )
        if proc.returncode == 0:
            lines = proc.stdout.strip().split("\n")
            title = lines[0] if lines and lines[0] != "NA" else None
            channel_yt_id = lines[1].strip() if len(lines) > 1 and lines[1].strip() != "NA" else None
    except Exception:
        pass  # Not critical, we can proceed without title

    # Try to match to a tracked channel
    podcast_channel_id = None
    if channel_yt_id:
        tracked = db.query(PodcastChannels).filter(
            PodcastChannels.youtube_channel_id == channel_yt_id
        ).first()
        if tracked:
            podcast_channel_id = tracked.id

    video = Videos(
        youtube_video_id=video_id,
        title=title,
        source_channel_youtube_id=channel_yt_id,
        podcast_channel_id=podcast_channel_id,
        discovery_method="manual",
        status="discovered",
    )
    db.add(video)
    db.commit()

    return {
        "video_id": str(video.id),
        "youtube_video_id": video_id,
        "title": title,
        "status": "queued",
    }


# ── Position Shifts ─────────────────────────────────────────────────────

@router.get("/positions/shifts")
def recent_position_shifts(
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db),
):
    """Recent position shifts across all tracked people."""
    shifts = (
        db.query(PositionHistoryLog)
        .filter(PositionHistoryLog.is_shift == True)  # noqa: E712
        .order_by(PositionHistoryLog.recorded_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(s.id),
            "person_id": str(s.person_id),
            "person_name": s.person.name if s.person else None,
            "person_photo_initials": (s.person.photo_initials or _initials(s.person.name)) if s.person else None,
            "person_accent_color": (s.person.accent_color or "#666") if s.person else None,
            "topic_id": str(s.topic_id),
            "topic_name": s.topic.name if s.topic else None,
            "topic_slug": s.topic.slug if s.topic else None,
            "position_summary": s.position_summary,
            "previous_position": s.previous_position,
            "shift_note": s.shift_note,
            "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
        }
        for s in shifts
    ]

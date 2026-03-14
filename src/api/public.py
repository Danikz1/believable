"""Public read-only API endpoints.

All claim endpoints default to review_status='approved'.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimEvidence,
    ClaimEmbeddings,
    Claims,
    ClaimTopics,
    People,
    PersonTopicPositions,
    PositionHistoryLog,
    Topics,
    VideoPeople,
    Videos,
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
    active: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(People).filter(People.active == active)
    if tier:
        q = q.filter(People.tier == tier)
    if domain:
        q = q.filter(People.domain == domain)

    people = q.order_by(People.tier, People.name).all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "tier": p.tier,
            "domain": p.domain,
            "expertise_domains": p.expertise_domains,
            "claim_count": db.query(Claims).filter(
                Claims.person_id == p.id,
                Claims.review_status == "approved",
            ).count(),
        }
        for p in people
    ]


@router.get("/people/{person_id}")
def get_person(person_id: UUID, db: Session = Depends(get_db)):
    person = db.query(People).filter(People.id == person_id).first()
    if not person:
        raise HTTPException(404, "Person not found")

    claims = (
        db.query(Claims)
        .filter(Claims.person_id == person.id, Claims.review_status == "approved")
        .order_by(Claims.created_at.desc())
        .limit(20)
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
        "active": person.active,
        "claims": [_claim_summary(c) for c in claims],
        "positions": [
            {
                "topic_id": str(p.topic_id),
                "topic": p.topic.slug if p.topic else None,
                "current_position": p.current_position,
                "claim_count": p.claim_count,
                "last_updated": p.last_updated.isoformat() if p.last_updated else None,
            }
            for p in positions
        ],
    }


# ── Claims ───────────────────────────────────────────────────────────

@router.get("/claims")
def list_claims(
    person_id: UUID | None = None,
    topic: str | None = None,
    claim_type: str | None = None,
    trust_level: str | None = None,
    days_back: int = 30,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Claims).filter(Claims.review_status == "approved")

    if person_id:
        q = q.filter(Claims.person_id == person_id)
    if topic:
        q = q.filter(Claims.topics.any(topic))
    if claim_type:
        q = q.filter(Claims.claim_type == claim_type)
    if trust_level:
        q = q.filter(Claims.trust_level == trust_level)
    if days_back:
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
                "segment_id": str(e.segment_id),
                "quote_text": e.quote_text,
                "start_ms": e.start_ms,
                "end_ms": e.end_ms,
                "quote_type": e.quote_type,
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
    return {
        "id": str(c.id),
        "person_id": str(c.person_id),
        "person_name": c.person.name if c.person else None,
        "video_id": str(c.video_id),
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
    }

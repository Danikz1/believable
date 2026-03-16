"""Admin API endpoints — protected by API key."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.config import settings
from src.db.models import Claims, People, Videos
from src.db.session import get_session

router = APIRouter()

ADMIN_KEY = settings.admin_api_key


def get_db():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def verify_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")


# ── Pipeline Control ─────────────────────────────────────────────────

@router.post("/pipeline/trigger/{stage}")
def trigger_pipeline(
    stage: str,
    limit: int = 5,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    if stage == "scan":
        from src.pipeline.discovery import scan_all_channels

        result = scan_all_channels(db, limit=limit)
    elif stage == "transcribe":
        from src.pipeline.transcription import transcribe_pending

        result = transcribe_pending(db, limit=limit)
    elif stage == "identify":
        from src.pipeline.identification import identify_pending

        result = identify_pending(db, limit=limit)
    elif stage == "enrich":
        from src.pipeline.enrichment import enrich_pending

        result = enrich_pending(db, limit=limit)
    else:
        raise HTTPException(400, f"Unknown stage: {stage}")

    return {"stage": stage, "result": result}


# ── Claim Review ─────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    review_status: str  # 'approved' or 'rejected'


@router.post("/claims/{claim_id}/review")
def review_claim(
    claim_id: UUID,
    req: ReviewRequest,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    claim = db.query(Claims).filter(Claims.id == claim_id).first()
    if not claim:
        raise HTTPException(404, "Claim not found")

    if req.review_status not in ("approved", "rejected"):
        raise HTTPException(400, "review_status must be 'approved' or 'rejected'")

    claim.review_status = req.review_status
    db.commit()

    # If approved, update positions
    if req.review_status == "approved":
        from src.pipeline.positions import update_positions_for_claim
        update_positions_for_claim(db, claim)

    return {"id": str(claim.id), "review_status": claim.review_status}


# ── People Management ────────────────────────────────────────────────

class PersonRequest(BaseModel):
    name: str
    tier: int = Field(default=2, ge=1, le=3)
    domain: str | None = None
    inclusion_notes: str
    expertise_domains: list[str] = Field(default_factory=list)
    youtube_search_queries: list[str] = Field(default_factory=list)
    active: bool = True


class PersonUpdateRequest(BaseModel):
    name: str | None = None
    tier: int | None = Field(default=None, ge=1, le=3)
    domain: str | None = None
    inclusion_notes: str | None = None
    expertise_domains: list[str] | None = None
    youtube_search_queries: list[str] | None = None
    active: bool | None = None


@router.post("/people")
def add_person(
    req: PersonRequest,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    person = People(
        name=req.name,
        tier=req.tier,
        domain=req.domain,
        inclusion_notes=req.inclusion_notes,
        expertise_domains=req.expertise_domains,
        youtube_search_queries=req.youtube_search_queries,
        active=req.active,
    )
    db.add(person)
    db.commit()
    return {"id": str(person.id), "name": person.name}


@router.put("/people/{person_id}")
def update_person(
    person_id: UUID,
    req: PersonUpdateRequest,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    person = db.query(People).filter(People.id == person_id).first()
    if not person:
        raise HTTPException(404, "Person not found")

    if req.name is not None:
        person.name = req.name
    if req.tier is not None:
        person.tier = req.tier
    if req.domain is not None:
        person.domain = req.domain
    if req.inclusion_notes is not None:
        person.inclusion_notes = req.inclusion_notes
    if req.expertise_domains is not None:
        person.expertise_domains = req.expertise_domains
    if req.youtube_search_queries is not None:
        person.youtube_search_queries = req.youtube_search_queries
    if req.active is not None:
        person.active = req.active
    db.commit()

    return {"id": str(person.id), "name": person.name}


# ── Error Retry ──────────────────────────────────────────────────────

@router.post("/retry/errors")
def retry_errors(
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Re-queue all error-status videos back to discovered."""
    from src.db.models import TranscriptRuns

    failed_runs = db.query(TranscriptRuns).filter(TranscriptRuns.status == "failed").all()
    video_ids = set(r.video_id for r in failed_runs)

    count = 0
    for vid in video_ids:
        video = db.query(Videos).filter(Videos.id == vid).first()
        if video and video.status == "error":
            video.status = "discovered"
            video.error_message = None
            count += 1

    db.commit()
    return {"requeued": count}


# ── Data Cleanup ─────────────────────────────────────────────────────

@router.post("/wipe/videos")
def wipe_all_video_data(
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Delete ALL video-related data for a clean slate."""
    from src.db.models import (
        ClaimEmbeddings, ClaimEvidence, ClaimTopics,
        EpisodeSummaries, PersonTopicPositions, PositionHistoryLog,
        TranscriptSegments, VideoPeople,
    )

    counts = {}
    for model, name in [
        (PositionHistoryLog, "position_history"),
        (PersonTopicPositions, "person_topic_positions"),
        (ClaimEmbeddings, "claim_embeddings"),
        (ClaimEvidence, "claim_evidence"),
        (ClaimTopics, "claim_topics"),
        (Claims, "claims"),
        (EpisodeSummaries, "episode_summaries"),
        (TranscriptSegments, "transcript_segments"),
        (VideoPeople, "video_people"),
        (Videos, "videos"),
    ]:
        n = db.query(model).delete(synchronize_session=False)
        counts[name] = n

    db.commit()
    return {"status": "wiped", "deleted": counts}

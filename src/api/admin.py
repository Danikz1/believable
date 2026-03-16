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


@router.post("/pipeline/process-all")
def process_all(
    limit: int = 10,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Run the full pipeline: transcribe → identify → enrich → summarize."""
    from src.pipeline.transcription import transcribe_pending
    from src.pipeline.identification import identify_pending
    from src.pipeline.enrichment import enrich_pending

    results = {}

    # Step 1: Transcribe discovered videos
    results["transcribe"] = transcribe_pending(db, limit=limit)

    # Step 2: Identify speakers in transcribed videos
    results["identify"] = identify_pending(db, limit=limit)

    # Step 3: Enrich identified videos
    results["enrich"] = enrich_pending(db, limit=limit)

    # Step 4: Generate summaries for enriched videos without one
    from src.pipeline.summaries import generate_episode_summary
    from src.db.models import EpisodeSummaries, Videos as VideosModel

    enriched_videos = (
        db.query(VideosModel)
        .filter(VideosModel.status == "enriched")
        .all()
    )

    summary_stats = {"generated": 0, "errors": []}
    for video in enriched_videos[:limit]:
        existing = (
            db.query(EpisodeSummaries)
            .filter(
                EpisodeSummaries.video_id == video.id,
                EpisodeSummaries.summary_type == "full_episode",
            )
            .first()
        )
        if existing:
            continue
        try:
            summary = generate_episode_summary(video.id, "full_episode", db)
            if summary:
                summary_stats["generated"] += 1
        except Exception as e:
            summary_stats["errors"].append(f"{video.title}: {str(e)[:100]}")

    results["summarize"] = summary_stats

    return {"status": "completed", "results": results}


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
    """Delete ALL video-related data for a clean slate using TRUNCATE CASCADE."""
    from sqlalchemy import text

    tables = [
        "briefs", "position_history_log", "person_topic_positions",
        "claim_embeddings", "claim_evidence", "claim_topics", "claims",
        "episode_summaries", "transcript_segments", "transcript_runs",
        "video_people", "x_posts", "videos",
    ]

    counts = {}
    for table in tables:
        try:
            count_result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            counts[table] = count_result.scalar() or 0
        except Exception:
            counts[table] = -1  # table might not exist

    try:
        # TRUNCATE CASCADE handles all FK constraints automatically
        existing_tables = [t for t in tables if counts.get(t, -1) >= 0]
        if existing_tables:
            table_list = ", ".join(existing_tables)
            db.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))
            db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Wipe failed: {str(e)[:300]}")

    return {"status": "wiped", "deleted": counts}

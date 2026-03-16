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
    """Run the full pipeline: configure → clean → transcribe → identify → enrich → summarize."""
    from src.pipeline.transcription import transcribe_pending
    from src.pipeline.identification import identify_pending, _is_valid_person_name

    results = {}

    # Step 0a: Auto-configure transcript settings for known channels
    from src.db.models import PodcastChannels
    channels = db.query(PodcastChannels).all()
    configured_count = 0
    for channel in channels:
        config = KNOWN_TRANSCRIPT_CONFIGS.get(channel.youtube_channel_id)
        if config and not channel.transcript_url_pattern:
            channel.transcript_url_pattern = config["transcript_url_pattern"]
            channel.transcript_parser = config["transcript_parser"]
            configured_count += 1
    if configured_count:
        db.commit()
        results["configure_transcripts"] = configured_count

    # Step 0b: Clean up invalid people names (transcript fragments, etc.)
    from src.db.models import People as PeopleModel
    from sqlalchemy import text

    invalid_people = [p for p in db.query(PeopleModel).all() if not _is_valid_person_name(p.name)]
    if invalid_people:
        # Tables that reference person_id — try each, skip if table missing
        fk_tables = [
            ("DELETE FROM favorites WHERE person_id = :pid", False),
            ("DELETE FROM episode_summaries WHERE person_focus_id = :pid", False),
            ("DELETE FROM shift_notes WHERE person_id = :pid", False),
            ("DELETE FROM person_topic_positions WHERE person_id = :pid", False),
            ("DELETE FROM x_posts WHERE person_id = :pid", False),
            ("DELETE FROM channel_roles WHERE person_id = :pid", False),
            ("DELETE FROM video_people WHERE person_id = :pid", False),
            ("UPDATE transcript_segments SET person_id = NULL WHERE person_id = :pid", True),
            ("UPDATE claims SET person_id = NULL WHERE person_id = :pid", True),
        ]

        removed = 0
        for p in invalid_people:
            pid = str(p.id)
            for stmt, _ in fk_tables:
                try:
                    db.execute(text(stmt), {"pid": pid})
                except Exception:
                    db.rollback()  # Reset after error
            try:
                db.execute(text("DELETE FROM people WHERE id = :pid"), {"pid": pid})
                db.commit()
                removed += 1
            except Exception:
                db.rollback()

        results["cleanup"] = {"removed_invalid_people": removed}

    from src.pipeline.enrichment import enrich_pending

    # Step 1: Transcribe discovered videos
    results["transcribe"] = transcribe_pending(db, limit=limit)

    # Step 2: Identify speakers in transcribed videos
    results["identify"] = identify_pending(db, limit=limit)

    # Step 3: Enrich identified videos
    results["enrich"] = enrich_pending(db, limit=limit)

    # Step 4: Generate summaries for enriched videos (overwrite existing)
    from src.pipeline.summaries import generate_episode_summary
    from src.db.models import EpisodeSummaries, Videos as VideosModel

    # Find videos that need summarization:
    # 1. Enriched videos without summaries
    # 2. Videos with existing summaries but empty sections (need regeneration)
    enriched_videos = (
        db.query(VideosModel)
        .filter(VideosModel.status == "enriched")
        .all()
    )

    # Also find videos with existing summaries that have empty sections
    existing_summaries = db.query(EpisodeSummaries).filter(
        EpisodeSummaries.summary_type == "full_episode"
    ).all()

    videos_needing_regen = set()
    for s in existing_summaries:
        detailed = s.detailed_json or {}
        sections = detailed.get("sections", []) if isinstance(detailed, dict) else []
        if not sections:
            videos_needing_regen.add(s.video_id)

    regen_videos = []
    if videos_needing_regen:
        regen_videos = (
            db.query(VideosModel)
            .filter(VideosModel.id.in_(videos_needing_regen))
            .all()
        )

    all_videos_to_summarize = {v.id: v for v in enriched_videos}
    for v in regen_videos:
        all_videos_to_summarize[v.id] = v

    summary_stats = {"generated": 0, "regenerated": 0, "errors": []}
    for video in list(all_videos_to_summarize.values())[:limit]:
        try:
            is_regen = video.id in videos_needing_regen
            summary = generate_episode_summary(video.id, "full_episode", db)
            if summary:
                if is_regen:
                    summary_stats["regenerated"] += 1
                else:
                    summary_stats["generated"] += 1
        except Exception as e:
            summary_stats["errors"].append(f"{video.title}: {str(e)[:100]}")

    results["summarize"] = summary_stats

    return {"status": "completed", "results": results}


@router.post("/pipeline/regenerate-summaries")
def regenerate_summaries(
    limit: int = 1,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Regenerate summaries for videos with empty sections. Limit=1 to avoid timeout."""
    from src.pipeline.summaries import generate_episode_summary
    from src.db.models import EpisodeSummaries, Videos as VideosModel

    existing_summaries = db.query(EpisodeSummaries).filter(
        EpisodeSummaries.summary_type == "full_episode"
    ).all()

    videos_needing_regen = []
    for s in existing_summaries:
        detailed = s.detailed_json or {}
        sections = detailed.get("sections", []) if isinstance(detailed, dict) else []
        if not sections:
            videos_needing_regen.append(s.video_id)

    if not videos_needing_regen:
        return {"status": "no_work", "message": "All summaries already have sections"}

    videos = db.query(VideosModel).filter(
        VideosModel.id.in_(videos_needing_regen)
    ).all()

    stats = {"regenerated": 0, "errors": [], "remaining": len(videos) - limit}
    for video in videos[:limit]:
        try:
            summary = generate_episode_summary(video.id, "full_episode", db)
            if summary:
                # Check if sections were actually generated
                detailed = summary.detailed_json or {}
                sec_count = len(detailed.get("sections", [])) if isinstance(detailed, dict) else 0
                stats["regenerated"] += 1
                stats["errors"].append(f"OK: {video.title[:40]} → {sec_count} sections")
            else:
                stats["errors"].append(f"RETURNED_NONE: {video.title[:60]}")
        except Exception as e:
            stats["errors"].append(f"EXCEPTION: {video.title[:40]}: {str(e)[:200]}")

    return {"status": "completed", "stats": stats}


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


# ── Channel Transcript Config ──────────────────────────────────────────

# Known channel transcript configurations
KNOWN_TRANSCRIPT_CONFIGS = {
    "UCSHZKyawb77ixDdsGog4iWA": {  # Lex Fridman Podcast
        "transcript_url_pattern": "https://lexfridman.com/{slug}-transcript",
        "transcript_parser": "lex_fridman",
    },
    "UC2LCFMxIkk0VtFbPaX3s00A": {  # Dwarkesh Podcast
        "transcript_url_pattern": "https://www.dwarkesh.com/p/{slug}",
        "transcript_parser": "dwarkesh_substack",
    },
    "UCXl4i9dYBrFOabk0xGmbkRA": {  # Dwarkesh Patel (alt channel ID)
        "transcript_url_pattern": "https://www.dwarkesh.com/p/{slug}",
        "transcript_parser": "dwarkesh_substack",
    },
}


class ChannelTranscriptConfig(BaseModel):
    transcript_url_pattern: str | None = None
    transcript_parser: str | None = None


@router.patch("/channels/{channel_id}/transcript-config")
def update_channel_transcript_config(
    channel_id: UUID,
    config: ChannelTranscriptConfig,
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Update transcript URL pattern and parser for a channel."""
    from src.db.models import PodcastChannels

    channel = db.query(PodcastChannels).filter(PodcastChannels.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")

    if config.transcript_url_pattern is not None:
        channel.transcript_url_pattern = config.transcript_url_pattern
    if config.transcript_parser is not None:
        channel.transcript_parser = config.transcript_parser

    db.commit()
    return {
        "channel_id": str(channel.id),
        "name": channel.name,
        "transcript_url_pattern": channel.transcript_url_pattern,
        "transcript_parser": channel.transcript_parser,
    }


@router.post("/channels/auto-configure-transcripts")
def auto_configure_transcripts(
    db: Session = Depends(get_db),
    _: str = Depends(verify_admin),
):
    """Auto-configure transcript settings for known channels."""
    from src.db.models import PodcastChannels

    channels = db.query(PodcastChannels).all()
    configured = []

    for channel in channels:
        config = KNOWN_TRANSCRIPT_CONFIGS.get(channel.youtube_channel_id)
        if config:
            channel.transcript_url_pattern = config["transcript_url_pattern"]
            channel.transcript_parser = config["transcript_parser"]
            configured.append({
                "name": channel.name,
                "transcript_url_pattern": config["transcript_url_pattern"],
                "transcript_parser": config["transcript_parser"],
            })

    db.commit()
    return {"configured": configured, "total": len(configured)}

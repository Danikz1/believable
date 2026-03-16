"""Episode Summary generation pipeline — creates 3-level summaries with watch verdicts.

Amendment 1: Three summary levels (tldr, summary_body, detailed_json) + watch verdict
Amendment 2: Prior position injection into "What's New" prompt
Amendment 3: Decoupled triggers (full_episode after transcription, person_focused after enrichment)
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from src.db.models import (
    Claims,
    EpisodeSummaries,
    Favorites,
    People,
    PersonTopicPositions,
    TranscriptSegments,
    VideoPeople,
    Videos,
)
from src.providers.llm import call_llm_json

logger = logging.getLogger(__name__)


# ── Prompts ──────────────────────────────────────────────────────────

EPISODE_SUMMARY_SYSTEM = """You are an expert analyst producing rich, detailed episode summaries for an intelligence platform.

Return ONLY valid JSON with these fields:

1. "tldr": 2 substantial paragraphs (6-8 sentences total). Cover ALL major topics discussed,
   the key arguments made, and why this episode matters. This should feel like a proper executive briefing,
   not a throwaway blurb. A reader should understand the core value of this episode from the TLDR alone.

2. "summary_body": 8-12 detailed paragraphs. This is the "5-minute deep read" version.
   Cover every major topic in depth. Include specific claims, data points, and arguments.
   Capture the nuance and tensions between speakers. Include direct quotes where impactful.
   Structure it as a flowing article — not bullet points. Each paragraph should add new information.
   Cover: the main thesis, supporting arguments, counterpoints, surprising revelations,
   practical implications, and forward-looking predictions.

3. "sections": Array of 15-25 episode sections (be VERY granular — treat each distinct topic shift as a new section!), each with:
   - "title": descriptive section title
   - "start_ms": approximate start timestamp in milliseconds
   - "summary": 10-15 sentences per section. This is the MOST IMPORTANT field — be extremely thorough.
     Cover: the exact arguments made, specific examples and anecdotes mentioned, data points or numbers cited,
     counterarguments or pushback, practical implications discussed,
     and any conclusions reached. Include the full context of WHY something was said.
     A reader should feel like they watched this part of the episode after reading the section.
   - "quotes": Array of 1-3 notable direct quotes from this section. These are CITATIONS — verbatim words
     from the transcript that support the summary. Each quote object has:
     - "speaker": who said it
     - "text": the exact quote (verbatim from transcript, 1-3 sentences, under 200 chars)
     - "timestamp_ms": approximate timestamp in milliseconds
   - "claims": array of claim references [{claim_id, text, speaker}]

4. "best_moments": 5-8 most important/surprising/quotable moments with:
   - "description": 2-3 sentences explaining what makes this moment notable and its context
   - "timestamp_ms": approximate timestamp in milliseconds
   - "quote_snippet": the key quote (under 150 chars)

5. "speakers": Per-speaker summary array:
   - "person_name": speaker name
   - "main_positions": 4-6 sentences summarizing their positions, arguments, and stance
   - "claim_count": number of claims from this speaker

6. "whats_new": Position shifts vs. prior history. If no shifts, say "Consistent with prior positions."
   If first appearance, say "First tracked appearance — baseline positions established."

7. "watch_verdict": One of "essential", "worth_skimming", "skip_unless_fan"
   - "essential": Drop everything and watch/read this
   - "worth_skimming": Worth 10 minutes, jump to the key moments
   - "skip_unless_fan": Only if you specifically follow this person/topic

8. "watch_verdict_reason": 2-3 sentences justifying the verdict with specific reasons."""


PERSON_FOCUSED_SYSTEM = """You are an expert analyst producing a person-focused episode summary.

Focus ONLY on {person_name}'s statements, positions, and claims.
Ignore other speakers except when they directly prompt {person_name}'s responses.

Return ONLY valid JSON with the same structure as a full episode summary,
but filtered to this one person's contributions."""


# ── Prior Position Context (Amendment 2) ─────────────────────────────

def build_whats_new_context(person_id: UUID, session: Session) -> str:
    """Build prior-position context for a person to inject into the summary prompt."""

    # Get their current topic positions
    positions = (
        session.query(PersonTopicPositions)
        .filter(PersonTopicPositions.person_id == person_id)
        .all()
    )

    # Get their last 10 approved claims
    recent_claims = (
        session.query(Claims)
        .filter(
            Claims.person_id == person_id,
            Claims.review_status == "approved",
        )
        .order_by(Claims.created_at.desc())
        .limit(10)
        .all()
    )

    person = session.query(People).get(person_id)
    name = person.name if person else "Unknown"

    if not positions and not recent_claims:
        return ""

    context = f"PRIOR POSITIONS FOR {name}:\n"
    for pos in positions:
        topic = pos.topic
        topic_name = topic.name if topic else pos.topic_id
        context += f"- {topic_name}: {pos.current_position} "
        context += f"(as of {pos.last_updated.strftime('%Y-%m-%d') if pos.last_updated else 'unknown'})\n"

    context += f"\nRECENT CLAIMS BY {name}:\n"
    for claim in recent_claims:
        date_str = claim.created_at.strftime('%Y-%m-%d') if claim.created_at else "unknown"
        context += f"- [{date_str}] {claim.claim_text}\n"

    context += "\nWhen evaluating 'What's New', compare the current episode's positions "
    context += "against the PRIOR POSITIONS and RECENT CLAIMS above. Only flag a shift "
    context += "if the person's current statement meaningfully contradicts or evolves "
    context += "their documented prior position. If no prior positions exist, "
    context += "note 'First tracked appearance' instead.\n"

    return context


# ── Summary Generation ───────────────────────────────────────────────

def generate_episode_summary(
    video_id: UUID,
    summary_type: str,
    session: Session,
    person_focus_id: UUID | None = None,
) -> EpisodeSummaries | None:
    """Generate an episode summary using LLM.

    Returns the created EpisodeSummaries row, or None if generation fails.
    """
    video = session.query(Videos).get(video_id)
    if not video:
        logger.error(f"Video {video_id} not found")
        return None

    # Get claims for this video
    claims_q = session.query(Claims).filter(
        Claims.video_id == video_id,
        Claims.review_status == "approved",
    )
    if person_focus_id and summary_type == "person_focused":
        claims_q = claims_q.filter(Claims.person_id == person_focus_id)
    claims = claims_q.all()

    # Get transcript segments
    segments = (
        session.query(TranscriptSegments)
        .filter(TranscriptSegments.video_id == video_id)
        .order_by(TranscriptSegments.start_ms)
        .all()
    )

    # Build claims JSON for prompt
    claims_json_list = []
    for c in claims:
        claims_json_list.append({
            "claim_id": str(c.id),
            "text": c.claim_text,
            "speaker": c.person.name if c.person else "Unknown",
            "type": c.claim_type,
            "topics": c.topics or [],
        })

    # Build transcript text (truncated to fit)
    transcript_parts = []
    for seg in segments[:500]:  # Cap at 500 segments to stay within token limits
        speaker = seg.person.name if seg.person else seg.speaker_label or "Speaker"
        ts = f"[{seg.start_ms // 1000}s]"
        transcript_parts.append(f"{ts} {speaker}: {seg.text}")
    transcript_text = "\n".join(transcript_parts)

    # Get speakers
    video_people = session.query(VideoPeople).filter(VideoPeople.video_id == video_id).all()
    speaker_names = [vp.person.name for vp in video_people if vp.person]

    # Build prior position context (Amendment 2)
    whats_new_context = ""
    if person_focus_id:
        whats_new_context = build_whats_new_context(person_focus_id, session)
    else:
        for vp in video_people:
            if vp.person_id:
                whats_new_context += build_whats_new_context(vp.person_id, session)

    # Choose system prompt
    if summary_type == "person_focused" and person_focus_id:
        person = session.query(People).get(person_focus_id)
        system_prompt = PERSON_FOCUSED_SYSTEM.format(
            person_name=person.name if person else "Unknown"
        )
    else:
        system_prompt = EPISODE_SUMMARY_SYSTEM

    # Build user prompt
    user_prompt = f"""VIDEO: {video.title}
CHANNEL: {video.podcast_channel.name if video.podcast_channel else 'Unknown'}
DATE: {video.published_at.strftime('%Y-%m-%d') if video.published_at else 'Unknown'}
SPEAKERS: {', '.join(speaker_names) if speaker_names else 'Unknown'}

EXTRACTED CLAIMS ({len(claims)} total, showing first 30):
{_format_claims(claims_json_list[:30])}

{f'PRIOR CONTEXT FOR TRACKED SPEAKERS:{chr(10)}{whats_new_context}' if whats_new_context else ''}

TRANSCRIPT (truncated):
{transcript_text[:40000]}"""

    try:
        result = call_llm_json(system_prompt, user_prompt, max_tokens=16384)
    except Exception as e:
        logger.error(f"LLM call failed for summary of video {video_id}: {e}")
        return None

    # Extract or default values
    tldr = result.get("tldr", "Summary unavailable.")
    summary_body = result.get("summary_body", tldr)
    watch_verdict = result.get("watch_verdict", "worth_skimming")
    if watch_verdict not in ("essential", "worth_skimming", "skip_unless_fan"):
        watch_verdict = "worth_skimming"

    sections = result.get("sections", [])
    speakers = result.get("speakers", [])
    best_moments = result.get("best_moments", [])

    logger.info(
        f"Summary LLM result for {video.title}: "
        f"tldr={len(tldr)}ch, body={len(summary_body)}ch, "
        f"sections={len(sections)}, speakers={len(speakers)}, "
        f"best_moments={len(best_moments)}, "
        f"result_keys={list(result.keys())}"
    )

    detailed = {
        "sections": sections,
        "speakers": speakers,
        "best_moments": best_moments,
    }

    # Create or update summary
    existing = None
    if summary_type == "full_episode":
        existing = (
            session.query(EpisodeSummaries)
            .filter(
                EpisodeSummaries.video_id == video_id,
                EpisodeSummaries.summary_type == "full_episode",
            )
            .first()
        )
    elif summary_type == "person_focused" and person_focus_id:
        existing = (
            session.query(EpisodeSummaries)
            .filter(
                EpisodeSummaries.video_id == video_id,
                EpisodeSummaries.summary_type == "person_focused",
                EpisodeSummaries.person_focus_id == person_focus_id,
            )
            .first()
        )

    if existing:
        existing.tldr = tldr
        existing.summary_body = summary_body
        existing.detailed_json = detailed
        existing.whats_new = result.get("whats_new")
        existing.watch_verdict = watch_verdict
        existing.watch_verdict_reason = result.get("watch_verdict_reason", "")
        existing.model_used = "qwen-plus"
        existing.generated_at = datetime.now(timezone.utc)
        summary = existing
    else:
        summary = EpisodeSummaries(
            video_id=video_id,
            summary_type=summary_type,
            person_focus_id=person_focus_id,
            tldr=tldr,
            summary_body=summary_body,
            detailed_json=detailed,
            whats_new=result.get("whats_new"),
            watch_verdict=watch_verdict,
            watch_verdict_reason=result.get("watch_verdict_reason", ""),
            model_used="qwen-plus",
        )
        session.add(summary)

    session.commit()
    logger.info(
        f"Generated {summary_type} summary for video {video.title} "
        f"(verdict: {watch_verdict})"
    )
    return summary


def _format_claims(claims_list: list[dict]) -> str:
    """Format claims for prompt inclusion."""
    if not claims_list:
        return "No claims extracted yet."
    lines = []
    for i, c in enumerate(claims_list, 1):
        topics = ", ".join(c.get("topics", []))
        lines.append(
            f"{i}. [{c['speaker']}] {c['text']}"
            f" (type: {c.get('type', 'unknown')}, topics: {topics})"
        )
    return "\n".join(lines)


# ── Trigger Functions ────────────────────────────────────────────────

def maybe_generate_summaries_after_enrichment(video_id: UUID, session: Session):
    """Trigger B: After enrichment, generate person-focused summaries for favorite speakers.

    Also regenerate full-episode summary if it exists (now with claims).
    """
    video = session.query(Videos).get(video_id)
    if not video:
        return

    # Check if any speaker is favorited
    speakers = session.query(VideoPeople).filter(VideoPeople.video_id == video_id).all()
    for sp in speakers:
        if not sp.person_id:
            continue
        fav = session.query(Favorites).filter(Favorites.person_id == sp.person_id).first()
        if fav:
            logger.info(f"Generating person-focused summary for {sp.person.name}")
            generate_episode_summary(
                video_id, "person_focused", session, person_focus_id=sp.person_id
            )

    # Regenerate full-episode summary if channel is favorited (now with claims)
    if video.podcast_channel_id:
        channel_fav = session.query(Favorites).filter(
            Favorites.channel_id == video.podcast_channel_id
        ).first()
        if channel_fav:
            logger.info(f"Regenerating full-episode summary with claims for {video.title}")
            generate_episode_summary(video_id, "full_episode", session)


def maybe_generate_full_episode_summary(video_id: UUID, session: Session):
    """Trigger A (Amendment 3): After transcription, generate full-episode summary
    for favorite channels. Does NOT require enrichment or tracked speakers."""
    video = session.query(Videos).get(video_id)
    if not video or not video.podcast_channel_id:
        return

    # Check if channel is favorited
    channel_fav = session.query(Favorites).filter(
        Favorites.channel_id == video.podcast_channel_id
    ).first()
    if not channel_fav:
        return

    # Check if summary already exists
    existing = (
        session.query(EpisodeSummaries)
        .filter(
            EpisodeSummaries.video_id == video_id,
            EpisodeSummaries.summary_type == "full_episode",
        )
        .first()
    )
    if existing:
        return

    logger.info(f"Generating full-episode summary (post-transcription) for {video.title}")
    generate_episode_summary(video_id, "full_episode", session)


def generate_pending_summaries(session: Session) -> dict:
    """Generate summaries for all enriched favorite videos that lack them.

    Used by: bm summaries generate --pending
    """
    stats = {"generated": 0, "skipped": 0, "errors": []}

    # Find all favorite person IDs and channel IDs
    fav_person_ids = [
        f.person_id for f in session.query(Favorites).filter(Favorites.person_id.isnot(None)).all()
    ]
    fav_channel_ids = [
        f.channel_id for f in session.query(Favorites).filter(Favorites.channel_id.isnot(None)).all()
    ]

    # Find enriched videos from favorite channels or with favorite speakers
    from sqlalchemy import or_

    videos = (
        session.query(Videos)
        .filter(
            Videos.status == "enriched",
            or_(
                Videos.podcast_channel_id.in_(fav_channel_ids) if fav_channel_ids else False,
                Videos.id.in_(
                    session.query(VideoPeople.video_id)
                    .filter(VideoPeople.person_id.in_(fav_person_ids))
                ) if fav_person_ids else False,
            ),
        )
        .all()
    )

    for video in videos:
        # Full episode for favorite channels
        if video.podcast_channel_id in fav_channel_ids:
            existing = (
                session.query(EpisodeSummaries)
                .filter(
                    EpisodeSummaries.video_id == video.id,
                    EpisodeSummaries.summary_type == "full_episode",
                )
                .first()
            )
            if not existing:
                try:
                    generate_episode_summary(video.id, "full_episode", session)
                    stats["generated"] += 1
                except Exception as e:
                    stats["errors"].append(f"{video.title}: {e}")

        # Person-focused for favorite speakers
        speakers = session.query(VideoPeople).filter(VideoPeople.video_id == video.id).all()
        for sp in speakers:
            if sp.person_id and sp.person_id in fav_person_ids:
                existing = (
                    session.query(EpisodeSummaries)
                    .filter(
                        EpisodeSummaries.video_id == video.id,
                        EpisodeSummaries.summary_type == "person_focused",
                        EpisodeSummaries.person_focus_id == sp.person_id,
                    )
                    .first()
                )
                if not existing:
                    try:
                        generate_episode_summary(
                            video.id, "person_focused", session, person_focus_id=sp.person_id
                        )
                        stats["generated"] += 1
                    except Exception as e:
                        stats["errors"].append(f"{video.title}/{sp.person.name}: {e}")

    return stats

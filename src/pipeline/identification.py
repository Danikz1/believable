"""Speaker identification pipeline.

Deep-path: Match speakers via channel_roles + LLM stratified sampling.
Fast-path: Metadata-only LLM identification + auto-upgrade triggers.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import (
    ChannelRoles,
    People,
    PodcastChannels,
    TranscriptSegments,
    VideoPeople,
    Videos,
)

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────────────────

SPEAKER_ID_SYSTEM_PROMPT = """You are an expert at identifying speakers in podcast and interview transcripts.

Given a video's metadata and sample utterances from each speaker, identify who each speaker is.
You know the following tracked people (match to these when possible):
{people_list}

Rules:
- Match speaker labels to real names based on content, speaking patterns, channel context
- Known hosts of the channel are listed — match them first
- Use the video title/description to identify guests
- If you cannot identify a speaker, use "Unknown"
- Return ONLY valid JSON"""

SPEAKER_ID_USER_TEMPLATE = """VIDEO: {title}
CHANNEL: {channel_name}
DESCRIPTION: {description}

KNOWN HOSTS: {known_hosts}

SPEAKER SAMPLES:
{speaker_samples}

Return JSON: {{"speakers": [{{"speaker_label": "SPEAKER_00", "name": "Person Name", "confidence": 0.85}}]}}"""

METADATA_ID_SYSTEM_PROMPT = """You identify which tracked people ACTUALLY SPEAK in a YouTube video based on the title, description, and channel.

You know these tracked people:
{people_list}

CRITICAL rules:
- Only identify people who are ACTUALLY SPEAKING in the video (as a host, guest, or interviewee)
- Do NOT identify someone just because they are MENTIONED or DISCUSSED in the video
- Do NOT identify someone from AI-generated summary videos, compilations, or commentary channels ABOUT them
- Videos titled "X and the ..." or "What X thinks about ..." often discuss the person without them being present
- Look for signals like "interview with", "joins", "sits down with", "featuring" as evidence of actual presence
- When in doubt, set a LOW confidence score (below 0.5)
- Return ONLY valid JSON"""

METADATA_ID_USER_TEMPLATE = """VIDEO: {title}
CHANNEL: {channel_name}
DESCRIPTION: {description}

Which tracked people are ACTUALLY SPEAKING in this video (not just mentioned or discussed)?
Return JSON: {{"people": [{{"name": "Person Name", "confidence": 0.7, "role": "guest"}}]}}"""


# ── Deep Path: Diarized Speaker Matching ─────────────────────────────

def identify_speakers_deep(session: Session, video: Videos) -> dict:
    """Identify speakers in a diarized transcript.

    1. Auto-match known hosts from channel_roles
    2. Use LLM for remaining speakers (if LLM available)
    3. Populate video_people
    """
    result = {"matched": 0, "unmatched": 0, "method": "deep", "error": None}

    # Get transcript segments
    segments = (
        session.query(TranscriptSegments)
        .filter(
            TranscriptSegments.video_id == video.id,
            TranscriptSegments.source_kind == "asr_diarized",
        )
        .order_by(TranscriptSegments.segment_index)
        .all()
    )

    if not segments:
        # Fall back to fast-path identification
        return identify_speakers_fast(session, video)

    # Step 1: Get known hosts for this channel
    known_hosts = _get_known_hosts(session, video)

    # Step 2: Get unique speaker labels
    speaker_labels = set(s.speaker_label for s in segments if s.speaker_label)

    # Step 3: Build stratified samples per speaker
    speaker_samples = _build_stratified_samples(segments)

    # Step 4: Try LLM identification
    try:
        from src.providers.llm import call_llm_json, get_available_provider

        if get_available_provider():
            people_list = _get_tracked_people_list(session)

            known_hosts_str = ", ".join(
                f"{h['name']} ({h['role']})" for h in known_hosts
            ) or "None known"

            system = SPEAKER_ID_SYSTEM_PROMPT.format(people_list=people_list)
            user = SPEAKER_ID_USER_TEMPLATE.format(
                title=video.title or "Unknown",
                channel_name=video.podcast_channel.name if video.podcast_channel else "Unknown",
                description=(video.description or "")[:500],
                known_hosts=known_hosts_str,
                speaker_samples=speaker_samples,
            )

            llm_result = call_llm_json(system, user)
            speakers = llm_result.get("speakers", [])

            for sp in speakers:
                label = sp.get("speaker_label")
                name = sp.get("name", "Unknown")
                confidence = sp.get("confidence", 0.5)

                if name == "Unknown" or not label:
                    result["unmatched"] += 1
                    continue

                # Try to match to a tracked person
                person = session.query(People).filter(
                    People.name.ilike(f"%{name}%"),
                    People.active == True,  # noqa: E712
                ).first()

                if person:
                    _upsert_video_person(
                        session, video, person,
                        role="host" if any(h["name"] == person.name for h in known_hosts) else "guest",
                        confidence=confidence,
                        identified_via="diarization_llm" if confidence < 0.9 else "known_host",
                    )
                    # Update segment speaker_name
                    _update_segment_speaker(session, video, label, person)
                    result["matched"] += 1
                else:
                    result["unmatched"] += 1

        else:
            # No LLM — use known hosts only
            _match_known_hosts_only(session, video, known_hosts, result)

    except Exception as e:
        logger.error(f"LLM identification failed: {e}")
        result["error"] = str(e)
        # Fall back to known hosts only
        _match_known_hosts_only(session, video, known_hosts, result)

    # Step 5: Update video status
    if result["matched"] > 0:
        video.status = "identified"
    else:
        video.status = "skipped"
        video.skip_reason = "no_tracked_speakers"

    session.commit()
    return result


# ── Fast Path: Metadata-Only ────────────────────────────────────────

def identify_speakers_fast(session: Session, video: Videos) -> dict:
    """Identify tracked people from metadata only (title, description, channel)."""
    result = {"matched": 0, "unmatched": 0, "method": "fast", "error": None}

    # Step 1: Match known hosts automatically
    known_hosts = _get_known_hosts(session, video)
    for host in known_hosts:
        person = session.query(People).filter(People.id == host["person_id"]).first()
        if person:
            _upsert_video_person(
                session, video, person,
                role="host",
                confidence=0.95,
                identified_via="known_host",
            )
            result["matched"] += 1

    # Step 2: Try LLM for metadata-based identification
    try:
        from src.providers.llm import call_llm_json, get_available_provider

        if get_available_provider():
            people_list = _get_tracked_people_list(session)
            system = METADATA_ID_SYSTEM_PROMPT.format(people_list=people_list)
            user = METADATA_ID_USER_TEMPLATE.format(
                title=video.title or "Unknown",
                channel_name=video.podcast_channel.name if video.podcast_channel else "Unknown",
                description=(video.description or "")[:500],
            )

            llm_result = call_llm_json(system, user)
            people = llm_result.get("people", [])

            for p in people:
                name = p.get("name", "")
                confidence = p.get("confidence", 0.5)
                role = p.get("role", "guest")

                person = session.query(People).filter(
                    People.name.ilike(f"%{name}%"),
                    People.active == True,  # noqa: E712
                ).first()

                if person:
                    # Skip low-confidence metadata-only matches
                    if confidence < 0.75:
                        logger.info(
                            f"Skipping low-confidence metadata match: "
                            f"{person.name} ({confidence:.2f}) in {video.title}"
                        )
                        continue
                    # Don't duplicate if already matched as host
                    existing = session.query(VideoPeople).filter(
                        VideoPeople.video_id == video.id,
                        VideoPeople.person_id == person.id,
                    ).first()
                    if not existing:
                        _upsert_video_person(
                            session, video, person,
                            role=role,
                            confidence=confidence,
                            identified_via="metadata_only",
                        )
                        result["matched"] += 1

    except Exception as e:
        logger.error(f"LLM metadata identification failed: {e}")
        result["error"] = str(e)

    # Step 3: Check auto-upgrade triggers
    _check_upgrade_triggers(session, video, result)

    # Step 4: Update video status
    if result["matched"] > 0:
        video.status = "identified"
    else:
        video.status = "skipped"
        video.skip_reason = "no_tracked_speakers"

    session.commit()
    return result


# ── Mode C: Official Transcript ─────────────────────────────────────

def identify_speakers_official(session: Session, video: Videos) -> dict:
    """Identify speakers from an official transcript.

    speaker_name is already populated from the transcript source.
    We just need to map names to person_ids (exact + alias matching).
    No LLM call needed.
    """
    result = {"matched": 0, "unmatched": 0, "method": "official", "error": None}

    segments = (
        session.query(TranscriptSegments)
        .filter(
            TranscriptSegments.video_id == video.id,
            TranscriptSegments.source_kind == "official",
        )
        .all()
    )

    if not segments:
        return identify_speakers_fast(session, video)

    # Get unique speaker names
    speaker_names = set(s.speaker_name for s in segments if s.speaker_name)
    known_hosts = _get_known_hosts(session, video)

    for name in speaker_names:
        person = _match_speaker_name(session, name, known_hosts)
        if person:
            _upsert_video_person(
                session, video, person,
                role="host" if any(h["name"] == person.name for h in known_hosts) else "guest",
                confidence=0.98,
                identified_via="official_transcript",
            )
            # Update segments with person_id
            for seg in segments:
                if seg.speaker_name == name:
                    seg.person_id = person.id
            result["matched"] += 1
        else:
            result["unmatched"] += 1

    session.flush()

    if result["matched"] > 0:
        video.status = "identified"
    else:
        video.status = "skipped"
        video.skip_reason = "no_tracked_speakers"

    session.commit()
    return result


def _match_speaker_name(
    session: Session, name: str, known_hosts: list[dict]
) -> People | None:
    """Match a speaker display name to a tracked person.

    1. Exact match (case-insensitive)
    2. Alias match (first name + channel_roles prior)
    """
    # Exact match
    person = session.query(People).filter(
        People.name.ilike(name.strip()),
        People.active == True,  # noqa: E712
    ).first()
    if person:
        return person

    # Partial match (contains)
    person = session.query(People).filter(
        People.name.ilike(f"%{name.strip()}%"),
        People.active == True,  # noqa: E712
    ).first()
    if person:
        return person

    # Alias match: first name against known hosts
    first_name = name.strip().split()[0] if name.strip() else ""
    if first_name:
        for host in known_hosts:
            if host["name"].lower().startswith(first_name.lower()):
                return session.query(People).filter(People.id == host["person_id"]).first()

    return None


# ── Orchestrator ─────────────────────────────────────────────────────

def identify_video(session: Session, video: Videos) -> dict:
    """Identify speakers in a video (auto-selects mode)."""
    if video.transcript_type == "official":
        return identify_speakers_official(session, video)
    elif video.transcript_type == "deep":
        return identify_speakers_deep(session, video)
    else:
        return identify_speakers_fast(session, video)


def identify_pending(session: Session, limit: int = 10) -> dict:
    """Identify speakers in all transcribed videos."""
    videos = (
        session.query(Videos)
        .filter(Videos.status == "transcribed")
        .order_by(Videos.created_at)
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "identified": 0, "skipped": 0, "errors": []}

    for video in videos:
        logger.info(f"Identifying: {video.title or video.youtube_video_id}")
        stats["processed"] += 1

        result = identify_video(session, video)
        if result.get("error"):
            stats["errors"].append(f"{video.youtube_video_id}: {result['error']}")
        if result["matched"] > 0:
            stats["identified"] += 1
        else:
            stats["skipped"] += 1

    return stats


def manual_identify(
    session: Session,
    video: Videos,
    speaker_label: str,
    person_name: str,
) -> bool:
    """Manually assign a speaker label to a person."""
    person = session.query(People).filter(
        People.name.ilike(f"%{person_name}%"),
        People.active == True,  # noqa: E712
    ).first()

    if not person:
        return False

    _upsert_video_person(
        session, video, person,
        role="guest",
        confidence=1.0,
        identified_via="manual",
    )

    # Update segments
    _update_segment_speaker(session, video, speaker_label, person)

    if video.status != "identified":
        video.status = "identified"

    session.commit()
    return True


def get_identify_status(session: Session) -> dict:
    """Get identification pipeline stats."""
    total = session.query(Videos).count()
    identified = session.query(Videos).filter(Videos.status == "identified").count()
    skipped = session.query(Videos).filter(Videos.status == "skipped").count()
    transcribed = session.query(Videos).filter(Videos.status == "transcribed").count()
    discovered = session.query(Videos).filter(Videos.status == "discovered").count()

    video_people_count = session.query(VideoPeople).count()

    low_confidence = (
        session.query(VideoPeople)
        .filter(VideoPeople.confidence < 0.7)
        .count()
    )

    return {
        "total_videos": total,
        "identified": identified,
        "skipped": skipped,
        "pending_identification": transcribed,
        "pending_transcription": discovered,
        "video_people_records": video_people_count,
        "low_confidence": low_confidence,
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _get_known_hosts(session: Session, video: Videos) -> list[dict]:
    """Get known hosts/cohosts for a video's channel."""
    if not video.podcast_channel_id:
        return []

    roles = (
        session.query(ChannelRoles)
        .filter(ChannelRoles.channel_id == video.podcast_channel_id)
        .all()
    )

    return [
        {
            "person_id": r.person_id,
            "name": r.person.name,
            "role": r.role,
        }
        for r in roles
    ]


def _build_stratified_samples(segments: list[TranscriptSegments]) -> str:
    """Build stratified speaker samples (5-10 substantive utterances per speaker)."""
    by_speaker: dict[str, list[str]] = {}

    for seg in segments:
        label = seg.speaker_label or "UNKNOWN"
        if label not in by_speaker:
            by_speaker[label] = []
        # Skip short utterances (< 10 words)
        if len(seg.text.split()) >= 10 and len(by_speaker[label]) < 10:
            by_speaker[label].append(seg.text)

    lines = []
    for label, utterances in by_speaker.items():
        lines.append(f"\n{label}:")
        for i, utt in enumerate(utterances[:5], 1):
            lines.append(f"  [{i}] \"{utt[:200]}\"")

    return "\n".join(lines)


def _get_tracked_people_list(session: Session) -> str:
    """Get a formatted list of all active tracked people."""
    people = (
        session.query(People)
        .filter(People.active == True)  # noqa: E712
        .order_by(People.tier, People.name)
        .all()
    )

    return ", ".join(
        f"{p.name} ({p.domain or 'General'})" for p in people
    )


def _upsert_video_person(
    session: Session,
    video: Videos,
    person: People,
    role: str,
    confidence: float,
    identified_via: str,
):
    """Insert or update a video_people record."""
    existing = session.query(VideoPeople).filter(
        VideoPeople.video_id == video.id,
        VideoPeople.person_id == person.id,
    ).first()

    if existing:
        # Update if higher confidence
        if confidence > (existing.confidence or 0):
            existing.confidence = confidence
            existing.identified_via = identified_via
            existing.role = role
    else:
        vp = VideoPeople(
            video_id=video.id,
            person_id=person.id,
            role=role,
            confidence=confidence,
            identified_via=identified_via,
        )
        session.add(vp)

    session.flush()


def _update_segment_speaker(
    session: Session,
    video: Videos,
    speaker_label: str,
    person: People,
):
    """Update transcript segments with resolved speaker info."""
    segments = (
        session.query(TranscriptSegments)
        .filter(
            TranscriptSegments.video_id == video.id,
            TranscriptSegments.speaker_label == speaker_label,
        )
        .all()
    )

    for seg in segments:
        seg.speaker_name = person.name
        seg.person_id = person.id

    session.flush()


def _check_upgrade_triggers(session: Session, video: Videos, result: dict):
    """Check if fast-path video should be upgraded to deep path."""
    tracked_count = (
        session.query(VideoPeople)
        .filter(VideoPeople.video_id == video.id)
        .count()
    )

    should_upgrade = False
    reason = ""

    # 2+ tracked people
    if tracked_count >= 2:
        should_upgrade = True
        reason = "2+ tracked people detected"

    # 1 tracked person + conversational format
    elif tracked_count == 1:
        title = (video.title or "").lower()
        conversational_keywords = ["interview", "conversation", "podcast", "discussion", "debate"]
        if any(kw in title for kw in conversational_keywords):
            should_upgrade = True
            reason = "conversational format with tracked person"

        # 1 tracked person + channel has known hosts
        elif video.podcast_channel_id:
            host_count = (
                session.query(ChannelRoles)
                .filter(ChannelRoles.channel_id == video.podcast_channel_id)
                .count()
            )
            if host_count > 0:
                should_upgrade = True
                reason = "channel has known hosts"

    if should_upgrade:
        result["upgrade_triggered"] = True
        result["upgrade_reason"] = reason
        logger.info(f"Auto-upgrade triggered for {video.youtube_video_id}: {reason}")

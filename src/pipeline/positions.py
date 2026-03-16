"""Position aggregation and shift detection.

Fans out approved claims by topic → updates person_topic_positions → detects shifts.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import (
    Claims,
    ClaimTopics,
    PersonTopicPositions,
    PositionHistoryLog,
    Topics,
)

logger = logging.getLogger(__name__)


def update_positions_for_claim(session: Session, claim: Claims) -> list[dict]:
    """Update positions for all topics linked to an approved claim.

    Only processes approved claims per spec.
    """
    if claim.review_status != "approved":
        return []

    results = []

    # Get all topics for this claim
    claim_topic_links = (
        session.query(ClaimTopics)
        .filter(ClaimTopics.claim_id == claim.id)
        .all()
    )

    for ct in claim_topic_links:
        topic = session.query(Topics).filter(Topics.id == ct.topic_id).first()
        if not topic:
            continue

        result = _update_position_for_topic(
            session, claim.person_id, topic, claim
        )
        results.append(result)

    session.flush()
    return results


def _update_position_for_topic(
    session: Session, person_id, topic: Topics, claim: Claims
) -> dict:
    """Update a single person_topic_position entry."""
    # Get or create position
    position = session.query(PersonTopicPositions).filter(
        PersonTopicPositions.person_id == person_id,
        PersonTopicPositions.topic_id == topic.id,
    ).first()

    previous_position = None

    if position:
        previous_position = position.current_position
        position.current_position = claim.claim_text[:500]
        position.last_updated = datetime.now(timezone.utc)
        position.claim_count = (position.claim_count or 0) + 1
        position.sentiment = claim.sentiment  # v2: track sentiment
    else:
        position = PersonTopicPositions(
            person_id=person_id,
            topic_id=topic.id,
            current_position=claim.claim_text[:500],
            last_updated=datetime.now(timezone.utc),
            claim_count=1,
            sentiment=claim.sentiment,  # v2: track sentiment
        )
        session.add(position)

    # Detect position shift
    is_shift = False
    if previous_position and claim.claim_text:
        is_shift = _detect_shift(previous_position, claim.claim_text)

    # Generate shift note if shift detected (v2)
    shift_note = None
    if is_shift:
        shift_note = _generate_shift_note(
            person_name=claim.person.name if claim.person else "Unknown",
            topic_name=topic.name,
            previous=previous_position,
            current=claim.claim_text,
        )

    # Log to position history
    log_entry = PositionHistoryLog(
        person_id=person_id,
        topic_id=topic.id,
        position_summary=claim.claim_text[:500],
        source_claim_id=claim.id,
        is_shift=is_shift,
        previous_position=previous_position,  # v2
        shift_note=shift_note,                # v2
    )
    session.add(log_entry)

    result = {
        "topic": topic.slug,
        "is_shift": is_shift,
        "claim_count": position.claim_count,
    }

    if is_shift:
        logger.info(
            f"Position shift detected: {topic.slug} — "
            f"'{previous_position[:50]}...' → '{claim.claim_text[:50]}...'"
        )

    return result


def _detect_shift(previous: str, current: str) -> bool:
    """Simple heuristic for detecting position shifts.

    Looks for sentiment/directional reversals.
    """
    # Simple keyword-based detection
    bullish_words = {"bullish", "optimistic", "positive", "growth", "increase", "up", "rising"}
    bearish_words = {"bearish", "pessimistic", "negative", "decline", "decrease", "down", "falling"}

    prev_lower = previous.lower()
    curr_lower = current.lower()

    prev_bullish = any(w in prev_lower for w in bullish_words)
    prev_bearish = any(w in prev_lower for w in bearish_words)
    curr_bullish = any(w in curr_lower for w in bullish_words)
    curr_bearish = any(w in curr_lower for w in bearish_words)

    # Detect reversal
    if prev_bullish and curr_bearish:
        return True
    if prev_bearish and curr_bullish:
        return True

    return False


def _generate_shift_note(
    person_name: str, topic_name: str, previous: str, current: str
) -> str:
    """Generate a human-readable shift explanation using LLM."""
    try:
        from src.providers.llm import call_llm, get_available_provider

        if not get_available_provider():
            return f"Position shifted from a prior view on {topic_name}."

        prompt = (
            f"{person_name}'s previous position on {topic_name} was:\n"
            f'"{previous}"\n\n'
            f"Their new position is:\n"
            f'"{current}"\n\n'
            "In 1-2 sentences, explain what shifted and why it matters. "
            "Be specific about what changed. Do not say 'the position shifted.' "
            "Say WHAT changed and in WHICH direction."
        )
        return call_llm(
            system_prompt="You are an analyst summarizing position changes.",
            user_prompt=prompt,
            max_tokens=200,
        )
    except Exception as e:
        logger.warning(f"Failed to generate shift note: {e}")
        return f"Position shifted from a prior view on {topic_name}."


def update_positions_for_video(session: Session, video_id) -> dict:
    """Update positions for all approved claims from a video."""
    claims = (
        session.query(Claims)
        .filter(
            Claims.video_id == video_id,
            Claims.review_status == "approved",
        )
        .all()
    )

    stats = {"claims_processed": 0, "positions_updated": 0, "shifts": 0}

    for claim in claims:
        results = update_positions_for_claim(session, claim)
        stats["claims_processed"] += 1
        stats["positions_updated"] += len(results)
        stats["shifts"] += sum(1 for r in results if r["is_shift"])

    session.commit()
    return stats

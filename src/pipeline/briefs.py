"""Brief generation — produces daily intelligence briefs from approved claims."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import (
    Briefs,
    Claims,
    People,
    PositionHistoryLog,
    Videos,
)

logger = logging.getLogger(__name__)


def generate_brief(session: Session, days_back: int = 7) -> Briefs:
    """Generate an intelligence brief from recent approved claims.

    4 sections:
    1. Headlines — top claims from Tier 1 people
    2. Position Shifts — contradictions to known positions
    3. Topic Pulse — most active topics
    4. New Discoveries — recently processed videos
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # ── Section 1: Headlines ──
    headlines = _build_headlines(session, cutoff)

    # ── Section 2: Position Shifts ──
    shifts = _build_shifts(session, cutoff)

    # ── Section 3: Topic Pulse ──
    topic_pulse = _build_topic_pulse(session, cutoff)

    # ── Section 4: New Discoveries ──
    discoveries = _build_discoveries(session, cutoff)

    # ── Collect claim IDs ──
    claim_ids = []
    for h in headlines:
        claim_ids.append(h["claim_id"])
    for s in shifts:
        if s.get("claim_id"):
            claim_ids.append(s["claim_id"])
    claim_ids = list(dict.fromkeys(claim_ids))

    # ── Generate narrative via LLM ──
    sections_data = {
        "headlines": headlines,
        "shifts": shifts,
        "topic_pulse": topic_pulse,
        "discoveries": discoveries,
    }

    markdown = _generate_narrative(sections_data, today)

    # ── Store brief ──
    brief = Briefs(
        title=f"Intelligence Brief — {today}",
        content_markdown=markdown,
        sections=sections_data,
        claim_ids=claim_ids,
        status="draft",
    )
    session.add(brief)
    session.commit()

    logger.info(f"Generated brief: {brief.title}")
    return brief


def publish_brief(session: Session, brief_id) -> Briefs:
    """Mark a brief as published."""
    brief = session.query(Briefs).filter(Briefs.id == brief_id).first()
    if brief:
        brief.status = "published"
        brief.published_at = datetime.now(timezone.utc)
        session.commit()
    return brief


def _build_headlines(session: Session, cutoff) -> list[dict]:
    """Top 5 notable claims — Tier 1 people + high confidence."""
    claims = (
        session.query(Claims)
        .join(People)
        .filter(
            Claims.review_status == "approved",
            Claims.created_at >= cutoff,
            Claims.trust_level == "high",
        )
        .order_by(People.tier, Claims.extraction_confidence.desc())
        .limit(5)
        .all()
    )

    return [
        {
            "claim_id": str(c.id),
            "person_name": c.person.name if c.person else "Unknown",
            "person_tier": c.person.tier if c.person else 0,
            "claim_text": c.claim_text,
            "claim_type": c.claim_type,
            "topics": c.topics or [],
            "confidence": float(c.extraction_confidence) if c.extraction_confidence else 0,
        }
        for c in claims
    ]


def _build_shifts(session: Session, cutoff) -> list[dict]:
    """Position shifts — claims contradicting known positions."""
    shifts = (
        session.query(PositionHistoryLog)
        .filter(
            PositionHistoryLog.is_shift == True,
            PositionHistoryLog.recorded_at >= cutoff,
        )
        .all()
    )

    results = []
    for shift in shifts:
        previous = (
            session.query(PositionHistoryLog)
            .filter(
                PositionHistoryLog.person_id == shift.person_id,
                PositionHistoryLog.topic_id == shift.topic_id,
                PositionHistoryLog.recorded_at < shift.recorded_at,
            )
            .order_by(PositionHistoryLog.recorded_at.desc())
            .first()
        )
        results.append(
            {
                "person_name": shift.person.name if shift.person else "Unknown",
                "topic": shift.topic.slug if shift.topic else "unknown",
                "previous_position": previous.position_summary if previous else None,
                "new_position": shift.position_summary,
                "claim_id": str(shift.source_claim_id) if shift.source_claim_id else None,
            }
        )

    return results


def _build_topic_pulse(session: Session, cutoff) -> list[dict]:
    """Topics ranked by recent claim activity."""
    results = (
        session.query(
            func.unnest(Claims.topics).label("topic"),
            func.count().label("count"),
        )
        .filter(
            Claims.review_status == "approved",
            Claims.created_at >= cutoff,
        )
        .group_by("topic")
        .order_by(func.count().desc())
        .limit(10)
        .all()
    )

    return [{"topic": r.topic, "claim_count": r.count} for r in results]


def _build_discoveries(session: Session, cutoff) -> list[dict]:
    """Recently processed videos."""
    videos = (
        session.query(Videos)
        .filter(
            Videos.status.in_(["enriched", "identified", "transcribed"]),
            Videos.created_at >= cutoff,
        )
        .order_by(Videos.created_at.desc())
        .limit(10)
        .all()
    )

    return [
        {
            "title": v.title,
            "youtube_id": v.youtube_video_id,
            "url": f"https://youtube.com/watch?v={v.youtube_video_id}",
            "status": v.status,
        }
        for v in videos
    ]


def _generate_narrative(sections: dict, date: str) -> str:
    """Render a grounded markdown brief from the structured sections."""
    return _template_brief(sections, date)


def _format_sections_for_llm(sections: dict) -> str:
    """Format sections data for LLM prompt."""
    parts = []

    parts.append("## Headlines (top claims)")
    for h in sections.get("headlines", []):
        parts.append(f"- [{h['person_name']}] {h['claim_text']} (type: {h['claim_type']}, topics: {', '.join(h['topics'])})")

    parts.append("\n## Position Shifts")
    shifts = sections.get("shifts", [])
    if shifts:
        for s in shifts:
            parts.append(f"- {s['person_name']} on {s['topic']}: was \"{s['previous_position']}\" → now \"{s['new_position']}\"")
    else:
        parts.append("- No position shifts detected this period")

    parts.append("\n## Topic Pulse")
    for t in sections.get("topic_pulse", []):
        parts.append(f"- {t['topic']}: {t['claim_count']} claims")

    parts.append("\n## New Discoveries")
    for d in sections.get("discoveries", []):
        parts.append(f"- [{d['title']}]({d['url']}) — {d['status']}")

    return "\n".join(parts)


def _template_brief(sections: dict, date: str) -> str:
    """Template-based brief renderer that never invents unsupported facts."""
    md = [f"# Believable Minds — Intelligence Brief\n**{date}**\n"]

    md.append("## Headlines\n")
    headlines = sections.get("headlines", [])
    if headlines:
        for h in headlines:
            topics = ", ".join(h["topics"]) if h["topics"] else "unclassified"
            md.append(f"**{h['person_name']}** ({topics})")
            md.append(f"> {h['claim_text']}\n")
    else:
        md.append("*No approved high-trust claims in this window.*\n")

    md.append("## Position Shifts\n")
    shifts = sections.get("shifts", [])
    if shifts:
        for s in shifts:
            md.append(f"**{s['person_name']}** on *{s['topic']}*")
            if s.get("previous_position"):
                md.append(f"- Was: {s['previous_position']}")
            md.append(f"- Now: {s['new_position']}\n")
    else:
        md.append("*No position shifts detected this period.*\n")

    md.append("## Topic Pulse\n")
    topic_pulse = sections.get("topic_pulse", [])
    if topic_pulse:
        for t in topic_pulse:
            md.append(f"- **{t['topic']}**: {t['claim_count']} claims")
    else:
        md.append("*No topic activity in this window.*")
    md.append("")

    md.append("## New Discoveries\n")
    discoveries = sections.get("discoveries", [])
    if discoveries:
        for d in discoveries:
            md.append(f"- [{d['title']}]({d['url']}) — {d['status']}")
    else:
        md.append("*No newly processed videos in this window.*")

    return "\n".join(md)

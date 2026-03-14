"""Seed the database with initial people, channels, channel_roles, and topics."""

import json
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.db.models import ChannelRoles, People, PodcastChannels, Topics

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_json(filename: str) -> list[dict]:
    filepath = DATA_DIR / filename
    with open(filepath) as f:
        return json.load(f)


def seed_people(session: Session) -> int:
    """Seed people from data/people_seed.json. Returns count of inserted records."""
    data = _load_json("people_seed.json")
    count = 0
    for item in data:
        existing = session.query(People).filter(People.name == item["name"]).first()
        if existing:
            continue
        person = People(
            name=item["name"],
            domain=item.get("domain"),
            tier=item["tier"],
            inclusion_notes=item["inclusion_notes"],
            expertise_domains=item.get("expertise_domains", []),
            youtube_search_queries=item.get("youtube_search_queries", []),
        )
        session.add(person)
        count += 1
    session.flush()
    return count


def seed_channels(session: Session) -> int:
    """Seed podcast channels from data/channels_seed.json."""
    data = _load_json("channels_seed.json")
    count = 0
    for item in data:
        existing = session.query(PodcastChannels).filter(
            or_(
                PodcastChannels.youtube_channel_id == item["youtube_channel_id"],
                PodcastChannels.name == item["name"],
            )
        ).first()
        if existing:
            existing.youtube_channel_id = item["youtube_channel_id"]
            existing.name = item["name"]
            existing.tier = item["tier"]
            existing.monitoring_mode = item.get("monitoring_mode", existing.monitoring_mode)
            existing.uploads_playlist_id = item.get("uploads_playlist_id") or existing.uploads_playlist_id
            existing.transcript_url_pattern = item.get("transcript_url_pattern") or existing.transcript_url_pattern
            existing.transcript_parser = item.get("transcript_parser") or existing.transcript_parser
            continue
        channel = PodcastChannels(
            youtube_channel_id=item["youtube_channel_id"],
            name=item["name"],
            tier=item["tier"],
            monitoring_mode=item.get("monitoring_mode", "channel_feed"),
            uploads_playlist_id=item.get("uploads_playlist_id"),
            transcript_url_pattern=item.get("transcript_url_pattern"),
            transcript_parser=item.get("transcript_parser"),
        )
        session.add(channel)
        count += 1
    session.flush()
    return count


def seed_topics(session: Session) -> int:
    """Seed topics from data/topics_seed.json."""
    data = _load_json("topics_seed.json")
    count = 0
    for item in data:
        existing = session.query(Topics).filter(Topics.slug == item["slug"]).first()
        if existing:
            continue
        topic = Topics(
            slug=item["slug"],
            name=item["name"],
        )
        session.add(topic)
        count += 1
    session.flush()
    return count


def seed_channel_roles(session: Session) -> int:
    """Seed channel_roles from data/channel_roles_seed.json. Resolves names to IDs."""
    data = _load_json("channel_roles_seed.json")
    count = 0
    for item in data:
        channel = (
            session.query(PodcastChannels)
            .filter(PodcastChannels.name == item["channel_name"])
            .first()
        )
        person = (
            session.query(People).filter(People.name == item["person_name"]).first()
        )
        if not channel or not person:
            continue

        existing = (
            session.query(ChannelRoles)
            .filter(
                ChannelRoles.channel_id == channel.id,
                ChannelRoles.person_id == person.id,
                ChannelRoles.role == item["role"],
            )
            .first()
        )
        if existing:
            continue

        role = ChannelRoles(
            channel_id=channel.id,
            person_id=person.id,
            role=item["role"],
        )
        session.add(role)
        count += 1
    session.flush()
    return count


def seed_all(session: Session) -> dict[str, int]:
    """Run all seed functions. Returns counts per entity type."""
    counts = {}
    counts["people"] = seed_people(session)
    counts["channels"] = seed_channels(session)
    counts["topics"] = seed_topics(session)
    counts["channel_roles"] = seed_channel_roles(session)
    session.commit()
    return counts

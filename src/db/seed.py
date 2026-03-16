"""Seed the database with initial people, channels, channel_roles, and topics."""

import json
import os
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.db.models import ChannelRoles, People, PodcastChannels, Topics


def _candidate_data_dirs(
    *,
    cwd: Path | None = None,
    module_file: Path | None = None,
    explicit_data_dir: str | None = None,
) -> list[Path]:
    roots: list[Path] = []

    if explicit_data_dir:
        roots.append(Path(explicit_data_dir))

    cwd_path = Path.cwd() if cwd is None else Path(cwd)
    roots.append(cwd_path / "data")
    roots.extend(parent / "data" for parent in cwd_path.parents)

    module_path = Path(__file__).resolve() if module_file is None else Path(module_file)
    roots.extend(parent / "data" for parent in module_path.parents)

    # Railway containers keep the checked-out project under /app.
    roots.append(Path("/app/data"))

    unique_roots: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        unique_roots.append(root)
    return unique_roots


def _resolve_data_file(
    filename: str,
    *,
    cwd: Path | None = None,
    module_file: Path | None = None,
    explicit_data_dir: str | None = None,
) -> Path:
    candidates = [
        data_dir / filename
        for data_dir in _candidate_data_dirs(
            cwd=cwd,
            module_file=module_file,
            explicit_data_dir=explicit_data_dir,
        )
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    searched = "\n".join(f"  - {candidate}" for candidate in candidates)
    raise FileNotFoundError(
        f"Could not find seed file '{filename}'. Looked in:\n{searched}"
    )


def _load_json(filename: str) -> list[dict]:
    filepath = _resolve_data_file(
        filename,
        explicit_data_dir=os.getenv("BELIEVABLE_MINDS_DATA_DIR"),
    )
    return json.loads(filepath.read_text(encoding="utf-8"))


def seed_people(session: Session) -> int:
    """Seed people from data/people_seed.json. Returns count of inserted records."""
    data = _load_json("people_seed.json")
    count = 0
    for item in data:
        existing = session.query(People).filter(People.name == item["name"]).first()
        if existing:
            # Upsert: update bio fields on existing records (v2)
            existing.domain = item.get("domain") or existing.domain
            existing.tier = item.get("tier", existing.tier)
            existing.inclusion_notes = item.get("inclusion_notes") or existing.inclusion_notes
            existing.expertise_domains = item.get("expertise_domains") or existing.expertise_domains
            existing.youtube_search_queries = item.get("youtube_search_queries") or existing.youtube_search_queries
            existing.bio = item.get("bio") or existing.bio
            existing.role_title = item.get("role_title") or existing.role_title
            existing.net_worth = item.get("net_worth") or existing.net_worth
            existing.age = item.get("age") or existing.age
            existing.photo_initials = item.get("photo_initials") or existing.photo_initials
            existing.accent_color = item.get("accent_color") or existing.accent_color
            continue
        person = People(
            name=item["name"],
            domain=item.get("domain"),
            tier=item["tier"],
            inclusion_notes=item["inclusion_notes"],
            expertise_domains=item.get("expertise_domains", []),
            youtube_search_queries=item.get("youtube_search_queries", []),
            bio=item.get("bio"),
            role_title=item.get("role_title"),
            net_worth=item.get("net_worth"),
            age=item.get("age"),
            photo_initials=item.get("photo_initials"),
            accent_color=item.get("accent_color"),
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

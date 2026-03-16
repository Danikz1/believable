"""YouTube video discovery pipeline.

Mode 1: Channel Feed Monitor — yt-dlp, zero API quota
Mode 2: Person Search Gap-Fill — YouTube Data API v3, bounded quota
"""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import httpx
from sqlalchemy.orm import Session

from src.config import settings
from src.db.models import People, PodcastChannels, Videos
from src.youtube import run_yt_dlp

logger = logging.getLogger(__name__)

# ── Quota tracking ──────────────────────────────────────────────────────
QUOTA_SEARCH = 100  # units per search.list call
QUOTA_VIDEO_LIST = 1  # units per videos.list call (per video)
MAX_DAILY_SEARCHES = 20
CHANNEL_REPAIR_THRESHOLD = 0.72


@dataclass
class ScanResult:
    """Accumulates results across a scan run."""
    videos_found: int = 0
    videos_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    quota_used: int = 0
    channels_scanned: int = 0
    people_searched: int = 0


# ── Mode 1: Channel Feed Monitor ─────────────────────────────────────

def scan_channel_feeds(
    session: Session,
    channel_name: str | None = None,
    limit: int | None = None,
) -> ScanResult:
    """Scan tracked channels for new videos via yt-dlp. Zero API quota.

    Args:
        session: DB session
        channel_name: If provided, only scan this channel
    """
    result = ScanResult()
    query = session.query(PodcastChannels).filter(
        PodcastChannels.active == True,  # noqa: E712
        PodcastChannels.monitoring_mode == "channel_feed",
    )
    if channel_name:
        query = query.filter(PodcastChannels.name.ilike(f"%{channel_name}%"))

    query = query.order_by(PodcastChannels.tier, PodcastChannels.name)
    if limit is not None:
        query = query.limit(limit)
    channels = query.all()

    for channel in channels:
        try:
            new_count = _scan_single_channel(session, channel, result)
            result.channels_scanned += 1
            logger.info(f"Channel '{channel.name}': {new_count} new videos")
        except Exception as e:
            err_msg = f"Channel '{channel.name}': {e}"
            logger.error(err_msg)
            result.errors.append(err_msg)

    session.commit()
    return result


def scan_all_channels(session: Session, limit: int | None = None) -> ScanResult:
    """Compatibility wrapper used by the admin API."""
    return scan_channel_feeds(session, limit=limit)


def _scan_single_channel(
    session: Session,
    channel: PodcastChannels,
    result: ScanResult,
    *,
    allow_channel_repair: bool = True,
    max_videos: int = 20,
) -> int:
    """Scan a single channel for new videos. Returns new video count."""
    channel_url = f"https://www.youtube.com/channel/{channel.youtube_channel_id}/videos"

    # yt-dlp: list recent videos from channel
    try:
        proc = run_yt_dlp(
            [
                "--flat-playlist",
                "-I", f"1:{max_videos}",
                "--print", "%(id)s\t%(title)s\t%(upload_date)s\t%(duration)s\t%(description)s",
                channel_url,
            ],
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"yt-dlp timed out for channel {channel.name}")

    if proc.returncode != 0:
        stderr = proc.stderr.strip()[:200] if proc.stderr else "unknown error"
        if allow_channel_repair and _looks_like_missing_channel(stderr):
            repaired = _repair_channel_id(session, channel, result)
            if repaired:
                return _scan_single_channel(
                    session,
                    channel,
                    result,
                    allow_channel_repair=False,
                )
        raise RuntimeError(f"yt-dlp failed: {stderr}")

    new_count = 0
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue

        parts = line.split("\t", 4)
        if len(parts) < 2:
            continue

        video_id = parts[0].strip()
        title = parts[1].strip() if len(parts) > 1 else None
        upload_date_str = parts[2].strip() if len(parts) > 2 else None
        duration_str = parts[3].strip() if len(parts) > 3 else None
        description = parts[4].strip() if len(parts) > 4 else None

        # Skip NA / invalid IDs
        if not video_id or video_id == "NA":
            continue

        # Deduplication check
        existing = session.query(Videos).filter(
            Videos.youtube_video_id == video_id
        ).first()
        if existing:
            result.videos_skipped += 1
            continue

        # Parse upload date
        published_at = None
        if upload_date_str and upload_date_str != "NA":
            try:
                published_at = datetime.strptime(upload_date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Parse duration
        duration_seconds = None
        if duration_str and duration_str != "NA":
            try:
                duration_seconds = int(float(duration_str))
            except (ValueError, TypeError):
                pass

        # Clean up description
        if description == "NA":
            description = None

        video = Videos(
            youtube_video_id=video_id,
            title=title if title != "NA" else None,
            podcast_channel_id=channel.id,
            source_channel_youtube_id=channel.youtube_channel_id,
            published_at=published_at,
            duration_seconds=duration_seconds,
            description=description,
            discovery_method="channel_feed",
            status="discovered",
        )
        session.add(video)
        new_count += 1
        result.videos_found += 1

    session.flush()
    return new_count


def _looks_like_missing_channel(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "this channel does not exist" in lowered
        or "requested page could not be downloaded" in lowered
        or "unable to download api page" in lowered
    )


def _repair_channel_id(session: Session, channel: PodcastChannels, result: ScanResult) -> bool:
    """Try to repair a stale YouTube channel id using the YouTube API."""
    if not settings.youtube_api_key:
        return False

    with httpx.Client(timeout=30) as client:
        match = _find_channel_match(client, channel.name, result)

    if not match or match["channel_id"] == channel.youtube_channel_id:
        return False

    old_id = channel.youtube_channel_id
    channel.youtube_channel_id = match["channel_id"]
    session.flush()
    logger.info(
        "Repaired YouTube channel id for %s: %s -> %s (%s)",
        channel.name,
        old_id,
        channel.youtube_channel_id,
        match["title"],
    )
    return True


def _find_channel_match(
    client: httpx.Client,
    channel_name: str,
    result: ScanResult,
) -> dict | None:
    params = {
        "part": "snippet",
        "q": channel_name,
        "type": "channel",
        "maxResults": 5,
        "key": settings.youtube_api_key,
    }
    result.quota_used += QUOTA_SEARCH
    response = _youtube_api_call(client, f"{YOUTUBE_API_BASE}/search", params)
    return _select_best_channel_match(channel_name, response.get("items", []))


def _select_best_channel_match(channel_name: str, items: list[dict]) -> dict | None:
    best_match = None
    best_score = 0.0
    target = _normalize_channel_name(channel_name)

    for item in items:
        snippet = item.get("snippet", {})
        title = snippet.get("channelTitle") or snippet.get("title") or ""
        score = SequenceMatcher(None, target, _normalize_channel_name(title)).ratio()
        if score > best_score:
            channel_id = item.get("snippet", {}).get("channelId") or item.get("id", {}).get("channelId")
            if channel_id:
                best_score = score
                best_match = {
                    "channel_id": channel_id,
                    "title": title,
                    "score": score,
                }

    if best_match and best_score >= CHANNEL_REPAIR_THRESHOLD:
        return best_match
    return None


def _normalize_channel_name(value: str) -> str:
    cleaned = "".join(ch for ch in value.lower() if ch.isalnum() or ch.isspace())
    tokens = [token for token in cleaned.split() if token not in {"podcast", "youtube", "official"}]
    return " ".join(tokens)


# ── Mode 2: Person Search Gap-Fill ───────────────────────────────────

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def scan_search_gap_fill(
    session: Session,
    person_name: str | None = None,
    days_back: int = 7,
) -> ScanResult:
    """Search YouTube for videos featuring tracked people. Uses YouTube Data API v3.

    Args:
        session: DB session
        person_name: If provided, only search for this person
        days_back: How far back to search (default 7 days)
    """
    result = ScanResult()

    if not settings.youtube_api_key:
        result.errors.append("YOUTUBE_API_KEY not set — gap-fill search requires it")
        return result

    # Get Tier 1 people (or specific person)
    query = session.query(People).filter(
        People.active == True,  # noqa: E712
    )
    if person_name:
        query = query.filter(People.name.ilike(f"%{person_name}%"))
    else:
        query = query.filter(People.tier == 1)

    people = query.all()
    published_after = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    searches_done = 0

    with httpx.Client(timeout=30) as client:
        for person in people:
            if searches_done >= MAX_DAILY_SEARCHES:
                logger.warning(f"Reached max daily searches ({MAX_DAILY_SEARCHES})")
                result.errors.append(f"Stopped: reached {MAX_DAILY_SEARCHES} search limit")
                break

            queries = person.youtube_search_queries or [f"{person.name} interview"]
            for search_query in queries:
                if searches_done >= MAX_DAILY_SEARCHES:
                    break

                try:
                    new_count = _search_single_query(
                        session, client, person, search_query, published_after, result
                    )
                    searches_done += 1
                    result.quota_used += QUOTA_SEARCH
                    result.people_searched += 1
                    logger.info(
                        f"Search '{search_query}': {new_count} new videos "
                        f"(quota: {result.quota_used})"
                    )
                except QuotaExhaustedError:
                    result.errors.append("YouTube API quota exhausted")
                    session.commit()
                    return result
                except AuthError as e:
                    result.errors.append(f"YouTube API auth error: {e}")
                    session.commit()
                    return result
                except Exception as e:
                    err_msg = f"Search '{search_query}': {e}"
                    logger.error(err_msg)
                    result.errors.append(err_msg)

    session.commit()
    return result


class QuotaExhaustedError(Exception):
    pass


class AuthError(Exception):
    pass


def _search_single_query(
    session: Session,
    client: httpx.Client,
    person: People,
    search_query: str,
    published_after: str,
    result: ScanResult,
) -> int:
    """Execute a single YouTube search query. Returns new video count."""
    # Step 1: Search
    params = {
        "part": "snippet",
        "q": search_query,
        "type": "video",
        "publishedAfter": published_after,
        "maxResults": 5,
        "order": "date",
        "key": settings.youtube_api_key,
    }

    response = _youtube_api_call(client, f"{YOUTUBE_API_BASE}/search", params)
    items = response.get("items", [])

    if not items:
        return 0

    # Step 2: Get video details (duration, description)
    video_ids = [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]
    video_details = _get_video_details(client, video_ids, result)

    new_count = 0
    for item in items:
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue

        # Deduplication
        existing = session.query(Videos).filter(
            Videos.youtube_video_id == video_id
        ).first()
        if existing:
            result.videos_skipped += 1
            continue

        snippet = item.get("snippet", {})
        details = video_details.get(video_id, {})

        # Parse published date
        published_at = None
        if snippet.get("publishedAt"):
            try:
                published_at = datetime.fromisoformat(
                    snippet["publishedAt"].replace("Z", "+00:00")
                )
            except ValueError:
                pass

        # Parse duration from ISO 8601 (PT1H2M3S)
        duration_seconds = _parse_iso_duration(details.get("duration"))

        video = Videos(
            youtube_video_id=video_id,
            title=snippet.get("title"),
            source_channel_youtube_id=snippet.get("channelId", "unknown"),
            published_at=published_at,
            duration_seconds=duration_seconds,
            description=snippet.get("description") or details.get("description"),
            discovery_method="search_gap_fill",
            discovered_by_person_id=person.id,
            status="discovered",
        )

        # Try to link to a tracked podcast channel
        channel_yt_id = snippet.get("channelId")
        if channel_yt_id:
            tracked = session.query(PodcastChannels).filter(
                PodcastChannels.youtube_channel_id == channel_yt_id
            ).first()
            if tracked:
                video.podcast_channel_id = tracked.id

        session.add(video)
        new_count += 1
        result.videos_found += 1

    session.flush()
    return new_count


def _get_video_details(
    client: httpx.Client, video_ids: list[str], result: ScanResult
) -> dict:
    """Fetch video details (duration, description) via videos.list API."""
    if not video_ids:
        return {}

    params = {
        "part": "contentDetails,snippet",
        "id": ",".join(video_ids),
        "key": settings.youtube_api_key,
    }

    result.quota_used += QUOTA_VIDEO_LIST * len(video_ids)

    try:
        response = _youtube_api_call(client, f"{YOUTUBE_API_BASE}/videos", params)
    except Exception:
        return {}

    details = {}
    for item in response.get("items", []):
        vid = item["id"]
        details[vid] = {
            "duration": item.get("contentDetails", {}).get("duration"),
            "description": item.get("snippet", {}).get("description"),
        }
    return details


def _youtube_api_call(client: httpx.Client, url: str, params: dict) -> dict:
    """Make a YouTube API call with retry logic per spec."""
    max_retries = 5
    for attempt in range(max_retries + 1):
        try:
            resp = client.get(url, params=params)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < 3:
                time.sleep(10)
                continue
            raise RuntimeError(f"Network error after {attempt + 1} attempts: {e}")

        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            # Rate limit — exponential backoff
            backoff = 2 ** attempt
            logger.warning(f"Rate limited, backing off {backoff}s")
            if attempt < max_retries:
                time.sleep(backoff)
                continue
            raise QuotaExhaustedError("Rate limited after max retries")
        elif resp.status_code in (401, 403):
            body = resp.text[:200]
            if "quotaExceeded" in body:
                raise QuotaExhaustedError("YouTube API quota exhausted")
            raise AuthError(f"Auth error {resp.status_code}: {body}")
        elif resp.status_code >= 500:
            if attempt < 3:
                time.sleep(30)
                continue
            raise RuntimeError(f"YouTube API error {resp.status_code} after retries")
        else:
            raise RuntimeError(f"YouTube API error {resp.status_code}: {resp.text[:200]}")

    raise RuntimeError("Max retries exceeded")


def _parse_iso_duration(duration_str: str | None) -> int | None:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    if not duration_str:
        return None

    import re
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


# ── Status / Stats ───────────────────────────────────────────────────

def get_scan_status(session: Session) -> dict:
    """Get discovery pipeline stats."""
    total = session.query(Videos).count()
    by_method = {}
    for method in ["channel_feed", "search_gap_fill", "manual"]:
        count = session.query(Videos).filter(Videos.discovery_method == method).count()
        if count > 0:
            by_method[method] = count

    by_status = {}
    for status in ["discovered", "transcribed", "identified", "enriched", "skipped", "error"]:
        count = session.query(Videos).filter(Videos.status == status).count()
        if count > 0:
            by_status[status] = count

    return {
        "total_videos": total,
        "by_method": by_method,
        "by_status": by_status,
    }

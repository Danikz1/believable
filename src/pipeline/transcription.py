"""Transcript extraction pipeline with provider abstraction.

Fast path: yt-dlp YouTube captions (free, no speaker labels)
Deep path: Deepgram Nova-3 with diarization (paid, speaker-labeled)
"""

import logging
import os
import re
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from src.config import settings
from src.db.models import ChannelRoles, PodcastChannels, TranscriptRuns, TranscriptSegments, Videos
from src.executables import resolve_executable

logger = logging.getLogger(__name__)
YT_DLP_BIN = resolve_executable("yt-dlp")


# ── Data types ───────────────────────────────────────────────────────

@dataclass
class Segment:
    """Intermediate segment representation before DB storage."""
    index: int
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None = None
    source_kind: str = "caption"


@dataclass
class TranscribeResult:
    """Result of a transcription run."""
    segments: list[Segment] = field(default_factory=list)
    provider: str = ""
    mode: str = ""
    error: str | None = None


# ── Provider interface ───────────────────────────────────────────────

class TranscriptionProvider(ABC):
    """Abstract base for transcription providers."""

    @abstractmethod
    def transcribe(
        self, youtube_video_id: str, speaker_config: dict | None = None
    ) -> TranscribeResult:
        """Transcribe a video. Returns segments."""
        ...


# ── Fast Path: yt-dlp captions ───────────────────────────────────────

class CaptionProvider(TranscriptionProvider):
    """Extract captions via yt-dlp (free, no diarization)."""

    def transcribe(
        self, youtube_video_id: str, speaker_config: dict | None = None
    ) -> TranscribeResult:
        result = TranscribeResult(provider="yt-dlp", mode="caption")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(id)s")

            # Download auto-generated and manual captions
            proc = subprocess.run(
                [
                    YT_DLP_BIN,
                    "--write-auto-sub",
                    "--write-sub",
                    "--sub-lang", "en,en-orig,en-US",
                    "--skip-download",
                    "--sub-format", "vtt",
                    "-o", output_template,
                    "--no-warnings",
                    f"https://www.youtube.com/watch?v={youtube_video_id}",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if proc.returncode != 0:
                result.error = f"yt-dlp caption download failed: {proc.stderr[:300]}"
                return result

            # Find the VTT file (try various name patterns)
            vtt_files = sorted(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                result.error = "No VTT subtitle file found — video may not have captions"
                return result

            vtt_path = vtt_files[0]
            raw_text = vtt_path.read_text(encoding="utf-8")

        # Parse VTT into segments (~30s windows)
        result.segments = _parse_vtt_to_segments(raw_text)
        return result


def _parse_vtt_to_segments(vtt_text: str, window_ms: int = 30_000) -> list[Segment]:
    """Parse VTT subtitle text into aggregated ~30s segments."""
    # Parse individual cues
    cues = _parse_vtt_cues(vtt_text)
    if not cues:
        return []

    # Aggregate into windows
    segments = []
    window_start = cues[0]["start_ms"]
    window_texts = []
    seg_index = 0

    for cue in cues:
        if cue["start_ms"] - window_start >= window_ms and window_texts:
            # Flush window
            segments.append(Segment(
                index=seg_index,
                start_ms=window_start,
                end_ms=cue["start_ms"],
                text=" ".join(window_texts),
                speaker_label=None,
                source_kind="caption",
            ))
            seg_index += 1
            window_start = cue["start_ms"]
            window_texts = []

        window_texts.append(cue["text"])

    # Final window
    if window_texts:
        segments.append(Segment(
            index=seg_index,
            start_ms=window_start,
            end_ms=cues[-1]["end_ms"],
            text=" ".join(window_texts),
            speaker_label=None,
            source_kind="caption",
        ))

    return segments


def _parse_vtt_cues(vtt_text: str) -> list[dict]:
    """Parse WebVTT into individual cues with timestamps."""
    cues = []
    # Match timestamp lines: 00:00:01.234 --> 00:00:04.567
    timestamp_pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
    )

    lines = vtt_text.split("\n")
    i = 0
    seen_texts = set()  # deduplicate repeated auto-caption lines

    while i < len(lines):
        match = timestamp_pattern.match(lines[i].strip())
        if match:
            start_ms = _vtt_timestamp_to_ms(match.group(1))
            end_ms = _vtt_timestamp_to_ms(match.group(2))

            # Collect text lines until blank line
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                # Strip VTT tags like <c> and positioning
                cleaned = re.sub(r"<[^>]+>", "", lines[i].strip())
                if cleaned and not cleaned.startswith("WEBVTT") and not cleaned.startswith("Kind:"):
                    text_lines.append(cleaned)
                i += 1

            text = " ".join(text_lines).strip()
            if text and text not in seen_texts:
                seen_texts.add(text)
                cues.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
        else:
            i += 1

    return cues


def _vtt_timestamp_to_ms(ts: str) -> int:
    """Convert 'HH:MM:SS.mmm' to milliseconds."""
    parts = ts.split(":")
    h, m = int(parts[0]), int(parts[1])
    s_parts = parts[2].split(".")
    s = int(s_parts[0])
    ms = int(s_parts[1]) if len(s_parts) > 1 else 0
    return (h * 3600 + m * 60 + s) * 1000 + ms


# ── Deep Path: Deepgram Nova-3 ───────────────────────────────────────

class DeepgramProvider(TranscriptionProvider):
    """Transcribe via Deepgram Nova-3 API with speaker diarization."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.deepgram_api_key

    def transcribe(
        self, youtube_video_id: str, speaker_config: dict | None = None
    ) -> TranscribeResult:
        result = TranscribeResult(provider="deepgram", mode="asr_diarized")

        if not self.api_key:
            result.error = "DEEPGRAM_API_KEY not set"
            return result

        audio_path = None
        try:
            # Step 1: Download audio via yt-dlp
            audio_path = self._download_audio(youtube_video_id)

            # Step 2: Send to Deepgram
            response = self._call_deepgram(audio_path, speaker_config)

            # Step 3: Parse response into segments
            result.segments = self._parse_response(response)

        except Exception as e:
            result.error = str(e)
        finally:
            # Step 4: Clean up audio file
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
                logger.info(f"Cleaned up audio: {audio_path}")

        return result

    def _download_audio(self, youtube_video_id: str) -> str:
        """Download audio to temp file via yt-dlp."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, f"{youtube_video_id}.%(ext)s")

        proc = subprocess.run(
            [
                YT_DLP_BIN,
                "-x",
                "--audio-format", "wav",
                "--audio-quality", "0",
                "-o", output_path,
                f"https://www.youtube.com/watch?v={youtube_video_id}",
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min for long videos
        )

        if proc.returncode != 0:
            raise RuntimeError(f"Audio download failed: {proc.stderr[:300]}")

        # Find the wav file
        wav_files = list(Path(tmpdir).glob("*.wav"))
        if not wav_files:
            raise RuntimeError("No WAV file produced by yt-dlp")

        return str(wav_files[0])

    def _call_deepgram(self, audio_path: str, speaker_config: dict | None) -> dict:
        """Send audio to Deepgram Nova-3 for transcription."""
        url = "https://api.deepgram.com/v1/listen"

        params = {
            "model": "nova-3",
            "smart_format": "true",
            "diarize": "true",
            "punctuate": "true",
            "utterances": "true",
        }

        # Apply speaker config hints
        if speaker_config:
            mode = speaker_config.get("mode")
            if mode == "exact":
                params["diarize_config"] = f"num_speakers:{speaker_config['count']}"
            elif mode == "range":
                params["diarize_config"] = (
                    f"min_speakers:{speaker_config['min']},"
                    f"max_speakers:{speaker_config['max']}"
                )

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }

        with open(audio_path, "rb") as audio_file:
            audio_data = audio_file.read()

        # Retry logic per spec
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                with httpx.Client(timeout=600) as client:
                    resp = client.post(url, params=params, headers=headers, content=audio_data)

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    backoff = 2 ** attempt
                    logger.warning(f"Deepgram rate limited, backing off {backoff}s")
                    time.sleep(backoff)
                    continue
                elif resp.status_code in (401, 403):
                    raise RuntimeError(f"Deepgram auth error: {resp.text[:200]}")
                elif resp.status_code >= 500:
                    if attempt < max_retries:
                        time.sleep(30)
                        continue
                    raise RuntimeError(f"Deepgram server error {resp.status_code}")
                else:
                    raise RuntimeError(f"Deepgram error {resp.status_code}: {resp.text[:200]}")

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < max_retries:
                    time.sleep(10)
                    continue
                raise RuntimeError(f"Deepgram network error: {e}")

        raise RuntimeError("Deepgram max retries exceeded")

    def _parse_response(self, response: dict) -> list[Segment]:
        """Parse Deepgram response into segments."""
        segments = []
        utterances = response.get("results", {}).get("utterances", [])

        for i, utt in enumerate(utterances):
            segments.append(Segment(
                index=i,
                start_ms=int(utt["start"] * 1000),
                end_ms=int(utt["end"] * 1000),
                text=utt["transcript"],
                speaker_label=f"SPEAKER_{utt.get('speaker', 0):02d}",
                source_kind="asr_diarized",
            ))

        return segments


# ── Orchestrator ─────────────────────────────────────────────────────

def get_provider(force_deep: bool = False) -> TranscriptionProvider:
    """Get the right transcription provider."""
    if force_deep:
        return DeepgramProvider()
    return CaptionProvider()


def determine_path(video: Videos, session: Session) -> str:
    """Determine whether to use 'fast' or 'deep' path for a video."""
    # Tier 1 channels always get deep path
    if video.podcast_channel:
        if video.podcast_channel.tier == 1:
            return "deep"

    # Search gap-fill videos with Tier 1 person get deep path
    if video.discovery_method == "search_gap_fill":
        if video.discovered_by_person_id:
            from src.db.models import People
            person = session.query(People).filter(People.id == video.discovered_by_person_id).first()
            if person and person.tier == 1:
                return "deep"

    return "fast"


def build_speaker_config(video: Videos, session: Session) -> dict | None:
    """Build speaker_config JSONB from channel_roles count."""
    if not video.podcast_channel_id:
        return None

    role_count = (
        session.query(ChannelRoles)
        .filter(ChannelRoles.channel_id == video.podcast_channel_id)
        .count()
    )

    if role_count == 0:
        return None

    # Known hosts + 1 guest
    return {"mode": "exact", "count": role_count + 1}


def transcribe_video(
    session: Session,
    video: Videos,
    force_deep: bool = False,
) -> TranscribeResult:
    """Transcribe a single video end-to-end.

    Priority 1: Official transcript (if channel has transcript_url_pattern)
    Priority 2: Deep path (Deepgram) for Tier 1 content
    Priority 3: Fast path (yt-dlp captions)
    """
    # Priority 1: Try official transcript
    if not force_deep and video.podcast_channel:
        channel = video.podcast_channel
        if channel.transcript_url_pattern and channel.transcript_parser:
            official_result = _try_official_transcript(session, video, channel)
            if official_result and not official_result.error:
                return official_result
            # Fall through to other paths on failure
            if official_result and official_result.error:
                logger.info(f"Official transcript failed, falling back: {official_result.error}")

    # Priority 2/3: Deep or fast path
    path = "deep" if force_deep else determine_path(video, session)
    speaker_config = build_speaker_config(video, session) if path == "deep" else None

    # Pick provider
    if path == "deep" and settings.deepgram_api_key:
        provider = DeepgramProvider()
    else:
        provider = CaptionProvider()
        path = "fast"  # fallback if no API key

    # Create transcript run record
    run = TranscriptRuns(
        video_id=video.id,
        mode="asr_diarized" if path == "deep" else "caption",
        provider=provider.__class__.__name__.replace("Provider", "").lower(),
        status="running",
        speaker_config=speaker_config,
    )
    session.add(run)
    session.flush()

    # Run transcription
    result = provider.transcribe(video.youtube_video_id, speaker_config)
    if result.provider:
        run.provider = result.provider
    if result.mode:
        run.mode = result.mode

    if result.error:
        # Mark the primary run as failed.
        run.status = "failed"
        run.error_message = result.error
        run.completed_at = datetime.now(timezone.utc)
        session.flush()

        # If the deep path fails, fall back to captions before marking the video as errored.
        if path == "deep" and not force_deep:
            logger.warning(
                "Deep transcription failed for %s, falling back to captions: %s",
                video.youtube_video_id,
                result.error,
            )
            fallback_provider = CaptionProvider()
            fallback_run = TranscriptRuns(
                video_id=video.id,
                mode="caption",
                provider="yt-dlp",
                status="running",
            )
            session.add(fallback_run)
            session.flush()

            fallback_result = fallback_provider.transcribe(video.youtube_video_id)
            if not fallback_result.error:
                for seg in fallback_result.segments:
                    db_seg = TranscriptSegments(
                        transcript_run_id=fallback_run.id,
                        video_id=video.id,
                        segment_index=seg.index,
                        speaker_label=seg.speaker_label,
                        start_ms=seg.start_ms,
                        end_ms=seg.end_ms,
                        text=seg.text,
                        source_kind=seg.source_kind,
                    )
                    session.add(db_seg)

                fallback_run.status = "succeeded"
                fallback_run.completed_at = datetime.now(timezone.utc)
                video.transcript_type = "fast"
                video.status = "transcribed"
                video.error_message = None
                session.commit()
                return fallback_result

            fallback_run.status = "failed"
            fallback_run.error_message = fallback_result.error
            fallback_run.completed_at = datetime.now(timezone.utc)
            result.error = f"{result.error} | caption fallback failed: {fallback_result.error}"

        video.status = "error"
        video.error_message = result.error
        video.retry_count = (video.retry_count or 0) + 1
        session.commit()
        return result

    # Store segments
    for seg in result.segments:
        db_seg = TranscriptSegments(
            transcript_run_id=run.id,
            video_id=video.id,
            segment_index=seg.index,
            speaker_label=seg.speaker_label,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            text=seg.text,
            source_kind=seg.source_kind,
        )
        session.add(db_seg)

    # Mark run as succeeded
    run.status = "succeeded"
    run.completed_at = datetime.now(timezone.utc)

    # Update video
    video.transcript_type = path
    video.status = "transcribed"
    video.error_message = None

    session.commit()
    return result


def _try_official_transcript(
    session: Session, video: Videos, channel: PodcastChannels
) -> TranscribeResult | None:
    """Try to fetch and parse an official transcript.

    Returns TranscribeResult on success/failure, None if not applicable.
    """
    from src.providers.official_transcript import OfficialTranscriptProvider

    result = TranscribeResult(provider=channel.transcript_parser, mode="official_transcript")
    provider = OfficialTranscriptProvider(channel.transcript_parser)

    # Step 1: Resolve URL
    url = provider.resolve_url(
        video.description, video.title, channel.transcript_url_pattern
    )
    if not url:
        result.error = "Could not resolve transcript URL"
        return result

    # Step 2: Validate URL exists
    if not provider.validate_url(url):
        result.error = f"Transcript URL returned 404: {url}"
        return result

    # Step 3: Create transcript run record
    run = TranscriptRuns(
        video_id=video.id,
        mode="official_transcript",
        provider=channel.transcript_parser,
        status="running",
    )
    session.add(run)
    session.flush()

    # Step 4: Fetch page
    page_html = provider.fetch_page(url)
    if not page_html:
        run.status = "failed"
        run.error_message = f"Failed to fetch: {url}"
        run.completed_at = datetime.now(timezone.utc)
        result.error = run.error_message
        session.commit()
        return result

    # Step 5: Parse
    parsed = provider.parse_page(page_html)
    if not parsed:
        run.status = "failed"
        run.error_message = "Parser returned no segments"
        run.completed_at = datetime.now(timezone.utc)
        result.error = run.error_message
        session.commit()
        return result

    # Check if it's a failed parse (single unknown segment)
    if len(parsed) == 1 and parsed[0].speaker_name == "unknown":
        run.status = "failed"
        run.error_message = "Parser could not identify speaker structure"
        run.completed_at = datetime.now(timezone.utc)
        result.error = run.error_message
        session.commit()
        return result

    # Step 6: Store segments
    for i, seg in enumerate(parsed):
        db_seg = TranscriptSegments(
            transcript_run_id=run.id,
            video_id=video.id,
            segment_index=i,
            speaker_label=None,  # Official transcripts don't use SPEAKER_XX labels
            speaker_name=seg.speaker_name,  # Populated directly from source
            start_ms=seg.start_ms,
            end_ms=seg.end_ms if seg.end_ms > seg.start_ms else seg.start_ms + 1,
            text=seg.text,
            source_kind="official",
        )
        session.add(db_seg)
        result.segments.append(Segment(
            index=i,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            text=seg.text,
            speaker_label=None,
            source_kind="official",
        ))

    # Mark run as succeeded
    run.status = "succeeded"
    run.completed_at = datetime.now(timezone.utc)

    # Update video
    video.transcript_type = "official"
    video.status = "transcribed"

    session.commit()
    logger.info(f"Official transcript: {len(parsed)} segments from {url}")
    return result


def transcribe_pending(session: Session, limit: int = 10) -> dict:
    """Transcribe all pending (discovered) videos, up to limit."""
    videos = (
        session.query(Videos)
        .filter(Videos.status == "discovered")
        .order_by(Videos.created_at)
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "succeeded": 0, "failed": 0, "errors": []}

    for video in videos:
        logger.info(f"Transcribing: {video.title or video.youtube_video_id}")
        stats["processed"] += 1

        result = transcribe_video(session, video)
        if result.error:
            stats["failed"] += 1
            stats["errors"].append(f"{video.youtube_video_id}: {result.error}")
        else:
            stats["succeeded"] += 1

    return stats


def get_transcribe_status(session: Session) -> dict:
    """Get transcription pipeline stats."""
    total_videos = session.query(Videos).count()
    transcribed = session.query(Videos).filter(Videos.status == "transcribed").count()
    discovered = session.query(Videos).filter(Videos.status == "discovered").count()

    runs = session.query(TranscriptRuns).count()
    succeeded = session.query(TranscriptRuns).filter(TranscriptRuns.status == "succeeded").count()
    failed = session.query(TranscriptRuns).filter(TranscriptRuns.status == "failed").count()

    segments = session.query(TranscriptSegments).count()

    return {
        "total_videos": total_videos,
        "transcribed": transcribed,
        "pending": discovered,
        "runs_total": runs,
        "runs_succeeded": succeeded,
        "runs_failed": failed,
        "total_segments": segments,
    }

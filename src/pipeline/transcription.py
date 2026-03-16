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
from src.youtube import run_yt_dlp

logger = logging.getLogger(__name__)


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

        direct_error = None
        try:
            direct_segments = _fetch_direct_transcript_segments(youtube_video_id)
            if direct_segments:
                result.provider = "youtube-transcript-api"
                result.segments = direct_segments
                return result
        except Exception as exc:
            direct_error = str(exc)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(id)s")

            # Download auto-generated and manual captions
            proc = run_yt_dlp(
                [
                    "--write-auto-sub",
                    "--write-sub",
                    "--sub-lang", "en,en-orig,en-US",
                    "--skip-download",
                    "--sub-format", "vtt",
                    "-o", output_template,
                    f"https://www.youtube.com/watch?v={youtube_video_id}",
                ],
                timeout=60,
            )

            if proc.returncode != 0:
                ytdlp_error = f"yt-dlp caption download failed: {proc.stderr[:300]}"
                result.error = f"{direct_error} | {ytdlp_error}" if direct_error else ytdlp_error
                return result

            # Find the VTT file (try various name patterns)
            vtt_files = sorted(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                ytdlp_error = "No VTT subtitle file found — video may not have captions"
                result.error = f"{direct_error} | {ytdlp_error}" if direct_error else ytdlp_error
                return result

            vtt_path = vtt_files[0]
            raw_text = vtt_path.read_text(encoding="utf-8")

        # Parse VTT into segments (~30s windows)
        result.segments = _parse_vtt_to_segments(raw_text)
        return result


def _fetch_direct_transcript_segments(video_id: str) -> list[Segment]:
    """Fetch captions without yt-dlp when YouTube exposes a transcript endpoint."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as exc:
        raise RuntimeError(f"direct transcript fetch unavailable: {exc}") from exc

    try:
        transcript = YouTubeTranscriptApi().fetch(
            video_id,
            languages=["en", "en-US", "en-GB", "en-orig"],
        )
    except Exception as exc:
        raise RuntimeError(f"direct transcript fetch failed: {exc}") from exc

    cues = []
    for item in transcript:
        text = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
        start = getattr(item, "start", None) if not isinstance(item, dict) else item.get("start")
        duration = getattr(item, "duration", None) if not isinstance(item, dict) else item.get("duration")

        if not text:
            continue

        start_ms = int(float(start or 0) * 1000)
        duration_ms = int(float(duration or 0) * 1000)
        cues.append(
            {
                "start_ms": start_ms,
                "end_ms": start_ms + max(duration_ms, 1000),
                "text": str(text).strip(),
            }
        )

    return _aggregate_cues_to_segments(cues)


def _parse_vtt_to_segments(vtt_text: str, window_ms: int = 30_000) -> list[Segment]:
    """Parse VTT subtitle text into aggregated ~30s segments."""
    # Parse individual cues
    cues = _parse_vtt_cues(vtt_text)
    return _aggregate_cues_to_segments(cues, window_ms=window_ms)


def _aggregate_cues_to_segments(cues: list[dict], window_ms: int = 30_000) -> list[Segment]:
    """Aggregate timestamped caption cues into larger readable windows."""
    if not cues:
        return []

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

        proc = run_yt_dlp(
            [
                "-x",
                "--audio-format", "wav",
                "--audio-quality", "0",
                "-o", output_path,
                f"https://www.youtube.com/watch?v={youtube_video_id}",
            ],
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


# ── Deep Path: AssemblyAI ────────────────────────────────────────────

class AssemblyAIProvider(TranscriptionProvider):
    """Transcribe via AssemblyAI with speaker diarization."""

    BASE_URL = "https://api.assemblyai.com/v2"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.assemblyai_api_key

    def transcribe(
        self, youtube_video_id: str, speaker_config: dict | None = None
    ) -> TranscribeResult:
        result = TranscribeResult(provider="assemblyai", mode="asr_diarized")

        if not self.api_key:
            result.error = "ASSEMBLYAI_API_KEY not set"
            return result

        audio_url = None
        audio_path = None
        try:
            # Step 1: Download audio and upload to AssemblyAI
            audio_path = self._download_audio(youtube_video_id)
            audio_url = self._upload_audio(audio_path)

            # Step 2: Submit transcription request
            transcript_id = self._submit_transcription(audio_url, speaker_config)

            # Step 3: Poll until complete
            transcript_data = self._poll_for_completion(transcript_id)

            # Step 4: Parse response into segments
            result.segments = self._parse_response(transcript_data)
            logger.info(
                f"AssemblyAI transcribed {youtube_video_id}: "
                f"{len(result.segments)} segments"
            )

        except Exception as e:
            result.error = str(e)
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    parent = os.path.dirname(audio_path)
                    if parent and os.path.isdir(parent):
                        os.rmdir(parent)
                except Exception:
                    pass

        return result

    def _download_audio(self, youtube_video_id: str) -> str:
        """Download audio to temp file via yt-dlp."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, f"{youtube_video_id}.%(ext)s")

        proc = run_yt_dlp(
            [
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "5",  # medium quality, smaller file
                "-o", output_path,
                f"https://www.youtube.com/watch?v={youtube_video_id}",
            ],
            timeout=600,
        )

        if proc.returncode != 0:
            raise RuntimeError(f"Audio download failed: {proc.stderr[:300]}")

        audio_files = list(Path(tmpdir).glob("*.mp3")) + list(Path(tmpdir).glob("*.m4a")) + list(Path(tmpdir).glob("*.wav"))
        if not audio_files:
            raise RuntimeError("No audio file produced by yt-dlp")

        return str(audio_files[0])

    def _upload_audio(self, audio_path: str) -> str:
        """Upload audio file to AssemblyAI and get the URL."""
        headers = {"authorization": self.api_key}

        with open(audio_path, "rb") as f:
            with httpx.Client(timeout=300) as client:
                resp = client.post(
                    f"{self.BASE_URL}/upload",
                    headers=headers,
                    content=f,
                )

        if resp.status_code != 200:
            raise RuntimeError(f"AssemblyAI upload failed: {resp.status_code} {resp.text[:200]}")

        return resp.json()["upload_url"]

    def _submit_transcription(self, audio_url: str, speaker_config: dict | None) -> str:
        """Submit a transcription job to AssemblyAI."""
        headers = {
            "authorization": self.api_key,
            "content-type": "application/json",
        }

        payload = {
            "audio_url": audio_url,
            "speaker_labels": True,  # Enable diarization
            "language_code": "en",
        }

        # Apply speaker hints if available
        if speaker_config:
            mode = speaker_config.get("mode")
            if mode == "exact":
                payload["speakers_expected"] = speaker_config["count"]

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{self.BASE_URL}/transcript",
                headers=headers,
                json=payload,
            )

        if resp.status_code != 200:
            raise RuntimeError(f"AssemblyAI submit failed: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        transcript_id = data.get("id")
        if not transcript_id:
            raise RuntimeError("AssemblyAI returned no transcript ID")

        logger.info(f"AssemblyAI job submitted: {transcript_id}")
        return transcript_id

    def _poll_for_completion(self, transcript_id: str, max_wait: int = 1800) -> dict:
        """Poll AssemblyAI until transcription is complete (up to 30 min)."""
        headers = {"authorization": self.api_key}
        url = f"{self.BASE_URL}/transcript/{transcript_id}"

        start_time = time.time()
        poll_interval = 5

        while time.time() - start_time < max_wait:
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=headers)

            if resp.status_code != 200:
                raise RuntimeError(f"AssemblyAI poll failed: {resp.status_code}")

            data = resp.json()
            status = data.get("status")

            if status == "completed":
                return data
            elif status == "error":
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"AssemblyAI transcription failed: {error}")
            else:
                logger.debug(f"AssemblyAI job {transcript_id}: {status}")
                time.sleep(poll_interval)
                # Increase interval for long jobs
                if poll_interval < 30:
                    poll_interval = min(poll_interval + 5, 30)

        raise RuntimeError(f"AssemblyAI timed out after {max_wait}s")

    def _parse_response(self, data: dict) -> list[Segment]:
        """Parse AssemblyAI response into segments with speaker labels."""
        segments = []
        utterances = data.get("utterances", [])

        if not utterances:
            # Fallback: use words grouped by speaker
            words = data.get("words", [])
            if words:
                return self._words_to_segments(words)
            return segments

        for i, utt in enumerate(utterances):
            segments.append(Segment(
                index=i,
                start_ms=utt["start"],
                end_ms=utt["end"],
                text=utt["text"],
                speaker_label=f"SPEAKER_{ord(utt.get('speaker', 'A')) - ord('A'):02d}",
                source_kind="asr_diarized",
            ))

        return segments

    def _words_to_segments(self, words: list[dict]) -> list[Segment]:
        """Fallback: group words by speaker into segments."""
        segments = []
        current_speaker = None
        current_words = []
        current_start = 0

        for word in words:
            speaker = word.get("speaker")
            if speaker != current_speaker and current_words:
                segments.append(Segment(
                    index=len(segments),
                    start_ms=current_start,
                    end_ms=word["start"],
                    text=" ".join(w["text"] for w in current_words),
                    speaker_label=f"SPEAKER_{ord(current_speaker or 'A') - ord('A'):02d}",
                    source_kind="asr_diarized",
                ))
                current_words = []
                current_start = word["start"]

            current_speaker = speaker
            current_words.append(word)

        if current_words:
            segments.append(Segment(
                index=len(segments),
                start_ms=current_start,
                end_ms=current_words[-1].get("end", current_start),
                text=" ".join(w["text"] for w in current_words),
                speaker_label=f"SPEAKER_{ord(current_speaker or 'A') - ord('A'):02d}",
                source_kind="asr_diarized",
            ))

        return segments


# ── Orchestrator ─────────────────────────────────────────────────────

def get_provider(force_deep: bool = False) -> TranscriptionProvider:
    """Get the right transcription provider."""
    if force_deep or settings.assemblyai_api_key:
        if settings.assemblyai_api_key:
            return AssemblyAIProvider()
        return DeepgramProvider()
    return CaptionProvider()


def determine_path(video: Videos, session: Session) -> str:
    """Determine whether to use 'fast' or 'deep' path for a video."""
    # If AssemblyAI is configured, always use deep path for diarization
    if settings.assemblyai_api_key:
        return "deep"

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
    if path == "deep" and settings.assemblyai_api_key:
        provider = AssemblyAIProvider()
    elif path == "deep" and settings.deepgram_api_key:
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
    """Transcribe all pending (discovered) videos, up to limit.

    Prioritises videos from favorited channels/people.
    """
    from src.db.models import Favorites, PodcastChannels
    from sqlalchemy import case, func

    # LEFT JOIN to favorites via channel to get priority (lower = more important)
    fav_channel = (
        session.query(Favorites.channel_id, Favorites.priority)
        .filter(Favorites.channel_id.isnot(None))
        .subquery()
    )
    fav_person = (
        session.query(Favorites.person_id, Favorites.priority)
        .filter(Favorites.person_id.isnot(None))
        .subquery()
    )

    # Priority: fav channel first, then fav discovered_by_person, then the rest
    videos = (
        session.query(Videos)
        .outerjoin(fav_channel, Videos.podcast_channel_id == fav_channel.c.channel_id)
        .outerjoin(fav_person, Videos.discovered_by_person_id == fav_person.c.person_id)
        .filter(Videos.status == "discovered")
        .order_by(
            # Favorited items first (non-null priority → 0, null → 1)
            case(
                (fav_channel.c.priority.isnot(None), 0),
                (fav_person.c.priority.isnot(None), 0),
                else_=1,
            ),
            # Within favorites, lower priority number = higher importance
            func.coalesce(fav_channel.c.priority, fav_person.c.priority, 99),
            Videos.created_at,
        )
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

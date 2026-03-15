"""Shared YouTube/yt-dlp helpers used across discovery and transcription."""

from __future__ import annotations

import logging
import subprocess
import time

from src.executables import resolve_executable

logger = logging.getLogger(__name__)
YT_DLP_BIN = resolve_executable("yt-dlp")
YOUTUBE_EXTRACTOR_ARGS = "youtube:player_client=tv_simply_embedded,web_safari,web"
TRANSIENT_YTDLP_MARKERS = (
    "HTTP Error 429",
    "Too Many Requests",
    "temporarily unavailable",
    "timed out",
)


def run_yt_dlp(
    args: list[str],
    *,
    timeout: int,
    retries: int = 2,
) -> subprocess.CompletedProcess[str]:
    """Run yt-dlp with shared hardened defaults and bounded retries."""
    cmd = [
        YT_DLP_BIN,
        "--ignore-config",
        "--no-warnings",
        "--extractor-retries",
        "3",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--sleep-requests",
        "1",
        "--extractor-args",
        YOUTUBE_EXTRACTOR_ARGS,
        *args,
    ]

    last_proc: subprocess.CompletedProcess[str] | None = None
    for attempt in range(retries + 1):
        last_proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if last_proc.returncode == 0:
            return last_proc

        stderr = last_proc.stderr or ""
        if attempt < retries and _is_transient_yt_dlp_error(stderr):
            backoff = 2 ** attempt
            logger.warning("yt-dlp transient error, retrying in %ss: %s", backoff, stderr[:200])
            time.sleep(backoff)
            continue

        return last_proc

    return last_proc if last_proc is not None else subprocess.CompletedProcess(cmd, 1, "", "")


def _is_transient_yt_dlp_error(stderr: str) -> bool:
    return any(marker in stderr for marker in TRANSIENT_YTDLP_MARKERS)

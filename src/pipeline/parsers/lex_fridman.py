"""Parser for Lex Fridman Podcast transcripts.

Format:
    Speaker Name
    [(00:00:31)](https://youtube.com/watch?v=XXX&t=31)
    Utterance text here.
"""

import re

from src.pipeline.parsers import ParsedSegment, TranscriptParser


class LexFridmanParser(TranscriptParser):
    """Parse Lex Fridman podcast transcripts."""

    def can_parse(self, html: str) -> bool:
        """Check for [(HH:MM:SS)] timestamp links."""
        return bool(re.search(r"\[\(\d{1,2}:\d{2}:\d{2}\)\]", html))

    def parse(self, html: str) -> list[ParsedSegment]:
        """Extract segments from Lex Fridman transcript."""
        segments = []
        lines = html.split("\n")

        current_speaker = None
        current_text_lines = []
        current_timestamp_ms = 0
        segment_index = 0

        pending_speaker = None

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()

            # Skip empty lines
            if not stripped:
                i += 1
                continue

            # Check for timestamp line: [(HH:MM:SS)](url)
            ts_match = re.match(r"\[\((\d{1,2}):(\d{2}):(\d{2})\)\]", stripped)
            if ts_match:
                # Flush previous segment
                if current_speaker and current_text_lines:
                    text = " ".join(current_text_lines).strip()
                    if text:
                        new_ts = (
                            int(ts_match.group(1)) * 3600
                            + int(ts_match.group(2)) * 60
                            + int(ts_match.group(3))
                        ) * 1000
                        segments.append(ParsedSegment(
                            speaker_name=current_speaker,
                            text=text,
                            start_ms=current_timestamp_ms,
                            end_ms=new_ts if new_ts > current_timestamp_ms else current_timestamp_ms + 1,
                            has_timestamps=True,
                        ))
                        segment_index += 1
                    current_text_lines = []

                # Update speaker if pending
                if pending_speaker:
                    current_speaker = pending_speaker
                    pending_speaker = None

                # Update timestamp
                h, m, s = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3))
                current_timestamp_ms = (h * 3600 + m * 60 + s) * 1000
                i += 1
                continue

            # Check if this is a speaker name (bare line before a timestamp)
            # Look ahead to see if next non-empty line starts with [(
            if not stripped.startswith("[") and not stripped.startswith("#"):
                # Could be a speaker name — check next non-empty line
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and re.match(r"\[\(\d{1,2}:\d{2}:\d{2}\)\]", lines[j].strip()):
                    # This is a speaker name
                    pending_speaker = stripped
                    i += 1
                    continue

            # Regular text line
            if current_speaker:
                cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
                cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
                if cleaned and not cleaned.startswith("#"):
                    current_text_lines.append(cleaned)

            i += 1

        # Flush last segment
        if current_speaker and current_text_lines:
            text = " ".join(current_text_lines).strip()
            if text:
                segments.append(ParsedSegment(
                    speaker_name=current_speaker,
                    text=text,
                    start_ms=current_timestamp_ms,
                    end_ms=current_timestamp_ms + 1,
                    has_timestamps=True,
                ))

        return segments

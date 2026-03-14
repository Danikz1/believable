"""Parser for Dwarkesh Podcast (Substack) transcripts.

Format:
    **Speaker Name**

    Utterance text here.

    ### 00:00:00 - Section Title
"""

import re

from src.pipeline.parsers import ParsedSegment, TranscriptParser


class DwarkeshSubstackParser(TranscriptParser):
    """Parse Dwarkesh Podcast Substack transcripts."""

    def can_parse(self, html: str) -> bool:
        """Check for bold speaker names and ### timestamp headers."""
        has_bold_speakers = bool(re.search(r"\*\*[A-Z][a-z]+ [A-Z][a-z]+\*\*", html))
        return has_bold_speakers

    def parse(self, html: str) -> list[ParsedSegment]:
        """Extract segments from Dwarkesh-style markdown."""
        segments = []
        lines = html.split("\n")

        current_speaker = None
        current_text_lines = []
        current_timestamp_ms = 0
        next_timestamp_ms = None
        has_timestamps = False
        segment_index = 0

        # First pass: find all timestamp headers
        timestamps = []
        for i, line in enumerate(lines):
            ts_match = re.match(r"###\s+(\d{1,2}):(\d{2}):(\d{2})", line.strip())
            if ts_match:
                h, m, s = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3))
                ms = (h * 3600 + m * 60 + s) * 1000
                timestamps.append((i, ms))
                has_timestamps = True

        def _get_end_ms(start_ms):
            """Find next timestamp after this one."""
            for _, ts_ms in timestamps:
                if ts_ms > start_ms:
                    return ts_ms
            return start_ms + 1  # Satisfy CHECK constraint

        def _flush():
            nonlocal current_speaker, current_text_lines, segment_index
            if current_speaker and current_text_lines:
                text = " ".join(current_text_lines).strip()
                if text:
                    end_ms = _get_end_ms(current_timestamp_ms)
                    segments.append(ParsedSegment(
                        speaker_name=current_speaker,
                        text=text,
                        start_ms=current_timestamp_ms,
                        end_ms=end_ms,
                        has_timestamps=has_timestamps,
                    ))
                    segment_index += 1
            current_text_lines = []

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                continue

            # Check for timestamp header: ### HH:MM:SS - Title
            ts_match = re.match(r"###\s+(\d{1,2}):(\d{2}):(\d{2})", stripped)
            if ts_match:
                _flush()
                h, m, s = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3))
                current_timestamp_ms = (h * 3600 + m * 60 + s) * 1000
                continue

            # Check for speaker change: **Name**
            speaker_match = re.match(r"^\*\*(.+?)\*\*\s*$", stripped)
            if speaker_match:
                _flush()
                current_speaker = speaker_match.group(1).strip()
                continue

            # Regular text line
            if current_speaker:
                # Strip markdown formatting
                cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
                cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
                if cleaned:
                    current_text_lines.append(cleaned)

        # Flush last segment
        _flush()

        return segments

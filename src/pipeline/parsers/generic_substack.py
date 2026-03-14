"""Generic Substack transcript parser (fallback).

Handles common patterns:
- Bold speaker names: **Name**
- Colon-prefixed speakers: Name: text
- Timestamp headers: ### HH:MM:SS or (HH:MM:SS)
"""

import re

from src.pipeline.parsers import ParsedSegment, TranscriptParser


class GenericSubstackParser(TranscriptParser):
    """Fallback parser for Substack-hosted transcripts."""

    def can_parse(self, html: str) -> bool:
        """Always returns True as fallback."""
        return True

    def parse(self, html: str) -> list[ParsedSegment]:
        """Try to parse common transcript formats."""
        # Try bold speaker format first
        segments = self._try_bold_speakers(html)
        if segments and len(segments) > 1:
            return segments

        # Try colon-prefixed format
        segments = self._try_colon_speakers(html)
        if segments and len(segments) > 1:
            return segments

        # Cannot reliably parse — return as single segment
        text = re.sub(r"<[^>]+>", "", html)  # strip HTML
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return [ParsedSegment(
                speaker_name="unknown",
                text=text[:5000],  # cap at 5000 chars
                start_ms=0,
                end_ms=1,
                has_timestamps=False,
            )]
        return []

    def _try_bold_speakers(self, html: str) -> list[ParsedSegment]:
        """Parse **Speaker Name** format."""
        segments = []
        lines = html.split("\n")
        current_speaker = None
        current_text = []
        current_ms = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Timestamp
            ts_match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", stripped)

            # Bold speaker
            speaker_match = re.match(r"^\*\*(.+?)\*\*\s*$", stripped)
            if speaker_match:
                if current_speaker and current_text:
                    segments.append(ParsedSegment(
                        speaker_name=current_speaker,
                        text=" ".join(current_text),
                        start_ms=current_ms,
                        end_ms=current_ms + 1,
                        has_timestamps=False,
                    ))
                    current_text = []
                current_speaker = speaker_match.group(1).strip()
                continue

            if stripped.startswith("###") and ts_match:
                h, m, s = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3))
                current_ms = (h * 3600 + m * 60 + s) * 1000
                continue

            if current_speaker:
                current_text.append(stripped)

        if current_speaker and current_text:
            segments.append(ParsedSegment(
                speaker_name=current_speaker,
                text=" ".join(current_text),
                start_ms=current_ms,
                end_ms=current_ms + 1,
                has_timestamps=False,
            ))

        return segments

    def _try_colon_speakers(self, html: str) -> list[ParsedSegment]:
        """Parse 'Speaker Name: text' format."""
        segments = []
        lines = html.split("\n")
        colon_pattern = re.compile(r"^([A-Z][a-zA-Z\s\.]+):\s+(.+)")

        current_speaker = None
        current_text = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            match = colon_pattern.match(stripped)
            if match:
                if current_speaker and current_text:
                    segments.append(ParsedSegment(
                        speaker_name=current_speaker,
                        text=" ".join(current_text),
                        start_ms=0,
                        end_ms=1,
                        has_timestamps=False,
                    ))
                    current_text = []
                current_speaker = match.group(1).strip()
                current_text.append(match.group(2))
            elif current_speaker:
                current_text.append(stripped)

        if current_speaker and current_text:
            segments.append(ParsedSegment(
                speaker_name=current_speaker,
                text=" ".join(current_text),
                start_ms=0,
                end_ms=1,
                has_timestamps=False,
            ))

        return segments

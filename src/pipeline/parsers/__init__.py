"""Transcript parser base class and shared data types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ParsedSegment:
    """A single parsed transcript segment."""
    speaker_name: str       # "Elon Musk"
    text: str               # The utterance text
    start_ms: int           # Milliseconds from start (0 if not available)
    end_ms: int             # Milliseconds from start (1 if not available)
    has_timestamps: bool    # Whether timestamps were parsed or defaulted


class TranscriptParser(ABC):
    """Abstract base for transcript parsers."""

    @abstractmethod
    def can_parse(self, html: str) -> bool:
        """Quick check if this parser handles the format."""
        ...

    @abstractmethod
    def parse(self, html: str) -> list[ParsedSegment]:
        """Extract structured segments from HTML/markdown."""
        ...

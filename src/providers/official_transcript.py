"""Official transcript provider — fetch and parse human-edited transcripts."""

import logging
import re

import httpx

from src.pipeline.parsers import ParsedSegment
from src.pipeline.parsers.dwarkesh_substack import DwarkeshSubstackParser
from src.pipeline.parsers.generic_substack import GenericSubstackParser
from src.pipeline.parsers.lex_fridman import LexFridmanParser

logger = logging.getLogger(__name__)

# Parser registry
PARSERS = {
    "dwarkesh_substack": DwarkeshSubstackParser(),
    "lex_fridman": LexFridmanParser(),
    "generic_substack": GenericSubstackParser(),
}


class OfficialTranscriptProvider:
    """Resolve, fetch, and parse human-edited transcript pages."""

    def __init__(self, parser_name: str):
        self.parser_name = parser_name

    def resolve_url(
        self,
        video_description: str | None,
        video_title: str | None,
        url_pattern: str | None,
    ) -> str | None:
        return resolve_transcript_url(video_description, video_title, url_pattern)

    def validate_url(self, url: str) -> bool:
        return validate_url(url)

    def fetch_page(self, url: str) -> str | None:
        return fetch_transcript_page(url)

    def parse_page(self, html: str) -> list[ParsedSegment] | None:
        return parse_transcript(html, self.parser_name)


def resolve_transcript_url(
    video_description: str | None,
    video_title: str | None,
    url_pattern: str | None,
) -> str | None:
    """Resolve the transcript URL for a video.

    Strategy 1: Extract from video description (preferred)
    Strategy 2: Derive slug from title
    """
    if not url_pattern:
        return None

    # Extract domain from pattern
    domain_match = re.match(r"https?://([^/]+)", url_pattern)
    if not domain_match:
        return None
    domain = domain_match.group(1)

    # Strategy 1: Find URL in description matching the domain
    if video_description:
        url_pattern_re = re.compile(
            rf"https?://{re.escape(domain)}/\S+", re.IGNORECASE
        )
        urls = url_pattern_re.findall(video_description)
        if urls:
            url = urls[0].rstrip(")")
            logger.info(f"Found transcript URL in description: {url}")
            return url

    # Strategy 2: Derive slug from title
    if video_title:
        slug = _title_to_slug(video_title)
        if slug and "{slug}" in url_pattern:
            url = url_pattern.replace("{slug}", slug)
            logger.info(f"Derived transcript URL from title: {url}")
            return url

    return None


def _title_to_slug(title: str) -> str | None:
    """Convert video title to URL slug.

    Takes the guest name (before em-dash or colon) and slugifies.
    """
    # Try splitting on common delimiters
    for delimiter in [" — ", " - ", " – ", ": ", " | "]:
        if delimiter in title:
            guest_part = title.split(delimiter)[0].strip()
            break
    else:
        guest_part = title

    # Remove episode numbers like "#493" or "Ep. 12"
    guest_part = re.sub(r"#\d+", "", guest_part)
    guest_part = re.sub(r"Ep\.?\s*\d+", "", guest_part, flags=re.IGNORECASE)

    # Slugify
    slug = guest_part.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = slug.strip("-")

    return slug if slug else None


def validate_url(url: str) -> bool:
    """Check if a URL resolves (HEAD request)."""
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.head(url)
            return resp.status_code == 200
    except Exception:
        return False


def fetch_transcript_page(url: str) -> str | None:
    """Fetch a transcript page and return cleaned text content."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Transcript page returned {resp.status_code}: {url}")
                return None
            # Convert HTML to clean text for parsing
            return _html_to_text(resp.text)
    except Exception as e:
        logger.error(f"Failed to fetch transcript page: {e}")
        return None


def _html_to_text(html: str) -> str:
    """Convert HTML to clean text preserving structure.

    Converts <strong> to **bold**, <h3> to ### headers,
    <p> to paragraph breaks, strips other tags.
    """
    import re

    # Extract main content area (Substack puts transcript in .body class)
    body_match = re.search(r'class="body[^"]*"[^>]*>(.*)', html, re.DOTALL)
    text = body_match.group(1) if body_match else html

    # Convert structural elements to markdown
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', text, flags=re.DOTALL)
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text, flags=re.DOTALL)
    text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<em>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)

    # Paragraph and line breaks
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<p[^>]*>', '\n', text)
    text = re.sub(r'</div>', '\n', text)

    # Remove remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode HTML entities
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    text = text.replace('&nbsp;', ' ')
    text = text.replace('\u200b', '')  # zero-width space
    text = text.replace('\xa0', ' ')   # non-breaking space

    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' +', ' ', text)

    return text.strip()


def parse_transcript(
    html: str, parser_name: str
) -> list[ParsedSegment] | None:
    """Parse a transcript page using the specified parser."""
    parser = PARSERS.get(parser_name)
    if not parser:
        logger.error(f"Unknown parser: {parser_name}")
        return None

    if not parser.can_parse(html):
        logger.warning(f"Parser {parser_name} cannot handle this format")
        # Try generic fallback
        fallback = PARSERS.get("generic_substack")
        if fallback:
            return fallback.parse(html)
        return None

    try:
        segments = parser.parse(html)
        if not segments:
            logger.warning(f"Parser {parser_name} returned no segments")
            return None
        return segments
    except Exception as e:
        logger.error(f"Parser {parser_name} failed: {e}")
        return None

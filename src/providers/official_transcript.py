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

    # Strategy 2: Try multiple candidate slugs derived from the title
    if video_title and "{slug}" in url_pattern:
        candidates = _generate_candidate_slugs(video_title)

        # Also try the original single-slug approach
        original_slug = _title_to_slug(video_title)
        if original_slug and original_slug not in candidates:
            candidates.append(original_slug)

        for slug in candidates:
            url = url_pattern.replace("{slug}", slug)
            logger.info(f"Trying transcript slug: {slug} → {url}")
            if validate_url(url):
                logger.info(f"Found valid transcript URL: {url}")
                return url

        logger.info(f"No valid transcript URL found from {len(candidates)} candidates")

    # Strategy 3: Scrape the channel website for transcript links
    if video_title and domain:
        url = _find_transcript_on_website(domain, video_title)
        if url:
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


def _generate_candidate_slugs(title: str) -> list[str]:
    """Generate multiple candidate slugs from a video title.

    Handles formats like:
    - "Guest Name: Topic | Channel Name #123"
    - "Topic - Guest Name | Channel Name"
    - "Guest Name | Channel Name"
    """
    candidates = []

    # Remove channel name suffix (e.g., "| Lex Fridman Podcast #491")
    clean = re.sub(r"\|[^|]+$", "", title).strip()
    clean = re.sub(r"#\d+", "", clean).strip()
    # Remove trailing "- Transcript" suffix (metadata, not content)
    clean = re.sub(r"\s*[-–—]\s*Transcript\s*$", "", clean, flags=re.IGNORECASE).strip()

    # Candidate 1: part after last " - " (often the guest name)
    for delim in [" - ", " – ", " — "]:
        if delim in clean:
            after = clean.rsplit(delim, 1)[1].strip()
            slug = _slugify(after)
            if slug:
                candidates.append(slug)
            break

    # Candidate 2: part before first ":" or "-" (could be guest name)
    for delim in [": ", " - ", " – ", " — "]:
        if delim in clean:
            before = clean.split(delim, 1)[0].strip()
            slug = _slugify(before)
            if slug:
                candidates.append(slug)
            break

    # Candidate 3: the full cleaned title slugified
    slug = _slugify(clean)
    if slug:
        candidates.append(slug)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in candidates:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return unique


def _slugify(text: str) -> str | None:
    """Convert text to URL slug."""
    slug = text.lower().strip()
    slug = re.sub(r"#\d+", "", slug)
    slug = re.sub(r"Ep\.?\s*\d+", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = slug.strip("-")
    return slug if slug else None


def _find_transcript_on_website(domain: str, video_title: str) -> str | None:
    """Scrape the channel's website for transcript links matching the video title.

    Strategy: fetch the podcast/episodes page, find all transcript links,
    and fuzzy-match against the video title.
    """
    # Common podcast listing paths to try
    listing_urls = [
        f"https://{domain}/podcast",
        f"https://{domain}/episodes",
        f"https://{domain}/",
    ]

    # Extract meaningful keywords from title (skip common words)
    stop_words = {"the", "a", "an", "and", "or", "of", "in", "on", "for", "with",
                  "is", "it", "to", "at", "by", "lex", "fridman", "podcast", "full",
                  "exclusive", "footage", "interview"}
    title_words = set(
        w.lower() for w in re.sub(r"[^a-zA-Z0-9\s]", "", video_title).split()
        if len(w) > 2 and w.lower() not in stop_words
    )

    if len(title_words) < 2:
        return None

    logger.info(f"Searching {domain} for transcript matching: {title_words}")

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            for listing_url in listing_urls:
                try:
                    resp = client.get(listing_url)
                    if resp.status_code != 200:
                        continue
                except Exception:
                    continue

                # Find all transcript links in the page
                transcript_links = re.findall(
                    rf'href="(https?://{re.escape(domain)}/[^"]*-transcript[^"]*)"',
                    resp.text
                )
                # Also find relative transcript links
                relative_links = re.findall(
                    r'href="(/[^"]*-transcript[^"]*)"',
                    resp.text
                )
                transcript_links.extend(
                    f"https://{domain}{link}" for link in relative_links
                )

                if not transcript_links:
                    continue

                # Score each transcript link by keyword overlap
                best_url = None
                best_score = 0

                for link in transcript_links:
                    # Extract the slug part from the URL
                    slug_part = link.rsplit("/", 1)[-1].replace("-transcript", "")
                    slug_words = set(slug_part.split("-"))

                    # Count matching keywords
                    overlap = len(title_words & slug_words)
                    # Also check if title words appear in the slug (partial match)
                    partial = sum(1 for tw in title_words if tw in slug_part)

                    score = overlap + partial * 0.5
                    if score > best_score:
                        best_score = score
                        best_url = link

                if best_url and best_score >= 1:
                    logger.info(f"Found transcript via website scrape: {best_url} (score={best_score})")
                    return best_url

                logger.info(f"No matching transcript found on {listing_url} ({len(transcript_links)} links checked)")
                break  # Only try the first successful listing page

    except Exception as e:
        logger.warning(f"Website transcript search failed: {e}")

    return None


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

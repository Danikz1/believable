"""X/Twitter ingestion pipeline — manual post ingestion and claim extraction.

Amendment 4: XOR source constraint (video/X)
Amendment 7: Thread-aware ingestion with --thread flag
"""

import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from src.db.models import (
    ClaimEvidence,
    Claims,
    ClaimTopics,
    People,
    Topics,
    XPosts,
)
from src.providers.llm import call_llm_json

logger = logging.getLogger(__name__)


# ── X Post URL Parser ────────────────────────────────────────────────

def parse_x_url(url: str) -> dict | None:
    """Parse an X/Twitter URL and extract the platform post ID and handle.

    Supports:
    - https://x.com/username/status/1234567890
    - https://twitter.com/username/status/1234567890
    """
    pattern = r"https?://(?:x|twitter)\.com/(\w+)/status/(\d+)"
    match = re.match(pattern, url.strip())
    if not match:
        return None
    return {
        "handle": match.group(1),
        "post_id": match.group(2),
        "url": url.strip(),
    }


# ── LLM Prompts ──────────────────────────────────────────────────────

SUBSTANTIVENESS_SYSTEM = """You are evaluating whether an X/Twitter post contains substantive claims worth tracking.

A post is substantive if it:
- Makes a prediction, analysis, or recommendation
- Takes a position on a topic (economics, AI, markets, politics, etc.)
- Shares a non-obvious observation or insight
- Provides evidence-based reasoning

A post is NOT substantive if it:
- Is just sharing/retweeting news without commentary
- Is casual conversation, jokes, or engagement farming
- Is self-promotion without substance
- Is too vague to extract a meaningful claim

Return JSON: {"is_substantive": true/false, "reason": "why"}"""


X_EXTRACTION_SYSTEM = """You are an expert analyst extracting structured claims from X/Twitter posts.

TOPIC TAXONOMY (use these slugs):
{topics}

Extract each substantive claim as an object with:
- "claim_text": The claim in clear, attributable language (40-150 words)
- "reasoning_text": Brief context/reasoning behind the claim (optional)
- "claim_type": prediction / opinion / recommendation / observation / analysis
- "trust_level": high / medium / low
- "speaker_certainty": definitive / high / moderate / speculative / hedged
- "extraction_confidence": 0.0-1.0
- "topics": list of topic slugs that apply
- "sentiment": bullish / bearish / neutral / mixed (if applicable)

Return JSON: {"claims": [...]}

Rules:
- Each claim should be self-contained and understandable without context
- A single post may contain 0-3 claims
- Threads may contain more claims
- Attribution is already known — the speaker is the poster"""


# ── Ingestion Functions ──────────────────────────────────────────────

def ingest_x_post(
    url: str,
    text: str,
    person_id: UUID,
    session: Session,
    is_thread: bool = False,
    thread_parent_id: UUID | None = None,
    posted_at: datetime | None = None,
    auto_enrich: bool = True,
) -> dict:
    """Ingest a single X post and optionally extract claims from it.

    Returns: {"post_id": UUID, "claims_extracted": int, "status": str}
    """
    parsed = parse_x_url(url)
    if not parsed:
        raise ValueError(f"Invalid X/Twitter URL: {url}")

    # Check if already ingested
    existing = session.query(XPosts).filter(
        XPosts.platform_post_id == parsed["post_id"]
    ).first()
    if existing:
        return {
            "post_id": str(existing.id),
            "claims_extracted": 0,
            "status": "already_exists",
        }

    # Create the X post record
    x_post = XPosts(
        platform_post_id=parsed["post_id"],
        person_id=person_id,
        post_text=text,
        post_url=parsed["url"],
        posted_at=posted_at or datetime.now(timezone.utc),
        is_thread=is_thread,
        thread_parent_id=thread_parent_id,
        discovery_method="manual",
        status="pending",
    )
    session.add(x_post)
    session.flush()  # get the ID

    if not auto_enrich:
        session.commit()
        return {
            "post_id": str(x_post.id),
            "claims_extracted": 0,
            "status": "pending",
        }

    # Check substantiveness
    try:
        subst = call_llm_json(
            SUBSTANTIVENESS_SYSTEM,
            f"POST BY @{parsed['handle']}:\n\n{text}",
        )
        if not subst.get("is_substantive", False):
            x_post.status = "skipped"
            session.commit()
            logger.info(f"Post skipped (not substantive): {subst.get('reason', '')}")
            return {
                "post_id": str(x_post.id),
                "claims_extracted": 0,
                "status": "skipped",
                "reason": subst.get("reason", "Not substantive"),
            }
    except Exception as e:
        logger.warning(f"Substantiveness check failed, proceeding anyway: {e}")

    # Extract claims
    claims_extracted = _extract_claims_from_x(x_post, session)
    x_post.status = "enriched"
    session.commit()

    return {
        "post_id": str(x_post.id),
        "claims_extracted": claims_extracted,
        "status": "enriched",
    }


def _extract_claims_from_x(x_post: XPosts, session: Session) -> int:
    """Extract structured claims from an X post using LLM."""
    from src.pipeline.enrichment import TOPIC_SLUGS

    person = session.query(People).get(x_post.person_id)
    person_name = person.name if person else "Unknown"

    system_prompt = X_EXTRACTION_SYSTEM.format(topics=", ".join(TOPIC_SLUGS))
    user_prompt = f"""POST BY {person_name} (@{x_post.platform_post_id}):
URL: {x_post.post_url}
DATE: {x_post.posted_at.strftime('%Y-%m-%d') if x_post.posted_at else 'Unknown'}
IS THREAD: {x_post.is_thread}

TEXT:
{x_post.post_text}"""

    try:
        result = call_llm_json(system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"Claim extraction from X post failed: {e}")
        return 0

    extracted_claims = result.get("claims", [])
    if not extracted_claims:
        return 0

    count = 0
    for cl_data in extracted_claims:
        claim_text = cl_data.get("claim_text", "").strip()
        if not claim_text or len(claim_text) < 20:
            continue

        trust_level = cl_data.get("trust_level", "medium")
        if trust_level not in ("high", "medium", "low"):
            trust_level = "medium"

        claim = Claims(
            person_id=x_post.person_id,
            x_post_id=x_post.id,
            video_id=None,  # X posts have no video
            claim_text=claim_text,
            reasoning_text=cl_data.get("reasoning_text"),
            claim_type=cl_data.get("claim_type", "opinion"),
            speaker_certainty=cl_data.get("speaker_certainty", "moderate"),
            extraction_confidence=cl_data.get("extraction_confidence", 0.8),
            trust_level=trust_level,
            topics=cl_data.get("topics", []),
            sentiment=cl_data.get("sentiment"),
            review_status="pending_review",
        )
        session.add(claim)
        session.flush()

        # Add evidence (the post text itself)
        evidence = ClaimEvidence(
            claim_id=claim.id,
            segment_id=None,  # no transcript segment for X
            evidence_order=1,
            quote_text=x_post.post_text[:1000],  # Cap at 1000 chars
            start_ms=None,
            end_ms=None,
            quote_type="x_post_text",
        )
        session.add(evidence)

        # Link topics
        for topic_slug in cl_data.get("topics", []):
            topic = session.query(Topics).filter(Topics.slug == topic_slug).first()
            if topic:
                ct = ClaimTopics(claim_id=claim.id, topic_id=topic.id)
                session.add(ct)

        count += 1

    logger.info(f"Extracted {count} claims from X post {x_post.post_url}")
    return count


def ingest_thread(
    urls_and_texts: list[dict],
    person_id: UUID,
    session: Session,
) -> dict:
    """Ingest a thread of X posts (Amendment 7).

    Args:
        urls_and_texts: List of {"url": str, "text": str} dicts, in order.

    Returns a stats dict.
    """
    stats = {"posts": 0, "claims": 0, "errors": []}
    thread_parent_id = None

    # Combine all thread text for extraction
    combined_text = "\n\n".join(
        f"[{i+1}/{len(urls_and_texts)}] {item['text']}"
        for i, item in enumerate(urls_and_texts)
    )

    # Ingest the first post as the thread parent
    for i, item in enumerate(urls_and_texts):
        try:
            # Only auto-enrich the last post in the thread (use combined text)
            is_last = (i == len(urls_and_texts) - 1)
            result = ingest_x_post(
                url=item["url"],
                text=combined_text if is_last else item["text"],
                person_id=person_id,
                session=session,
                is_thread=True,
                thread_parent_id=thread_parent_id,
                auto_enrich=is_last,  # Only extract claims from full thread
            )
            if i == 0:
                thread_parent_id = result["post_id"]
            stats["posts"] += 1
            stats["claims"] += result.get("claims_extracted", 0)
        except Exception as e:
            stats["errors"].append(f"Post {i+1}: {e}")

    return stats

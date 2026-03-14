"""Embedding generation for claims using OpenAI text-embedding-3-small."""

import logging

import httpx

from src.config import settings
from src.db.models import ClaimEmbeddings, Claims

logger = logging.getLogger(__name__)

MODEL_NAME = "text-embedding-3-small"


def generate_embedding(text: str, dimensions: int | None = None) -> list[float]:
    """Generate an embedding vector using OpenAI API."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set — needed for embeddings")

    dims = dimensions or settings.embedding_dimensions

    url = "https://api.openai.com/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_NAME,
        "input": text[:8000],  # Cap at ~8k chars
        "dimensions": dims,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI embeddings error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return data["data"][0]["embedding"]


def embed_claim(session, claim: Claims) -> bool:
    """Generate and store an embedding for a claim."""
    # Check if already embedded
    existing = session.query(ClaimEmbeddings).filter(
        ClaimEmbeddings.claim_id == claim.id,
        ClaimEmbeddings.model_name == MODEL_NAME,
    ).first()

    if existing:
        return False  # Already embedded

    # Combine claim_text + reasoning_text for richer embedding
    text = claim.claim_text
    if claim.reasoning_text:
        text += " " + claim.reasoning_text

    try:
        dims = settings.embedding_dimensions
        vector = generate_embedding(text, dims)

        emb = ClaimEmbeddings(
            claim_id=claim.id,
            model_name=MODEL_NAME,
            dimensions=dims,
            embedding=vector,
        )
        session.add(emb)
        session.flush()
        return True

    except Exception as e:
        logger.error(f"Failed to embed claim {claim.id}: {e}")
        return False


def embed_pending_claims(session, limit: int = 50) -> dict:
    """Embed all claims that don't have embeddings yet."""
    claims = (
        session.query(Claims)
        .outerjoin(ClaimEmbeddings, Claims.id == ClaimEmbeddings.claim_id)
        .filter(ClaimEmbeddings.id.is_(None))
        .limit(limit)
        .all()
    )

    stats = {"processed": 0, "embedded": 0, "errors": 0}

    for claim in claims:
        stats["processed"] += 1
        if embed_claim(session, claim):
            stats["embedded"] += 1
        else:
            stats["errors"] += 1

    session.commit()
    return stats

# Believable Minds — Improvement Spec Amendment v2

**Status:** Addendum to the Improvement Spec. Apply before starting Phase 2.  
**Date:** March 2026  
**Trigger:** Cross-model review (ChatGPT) identified 7 fixes. Claude validated and scoped each.  
**Scope:** Targeted changes only. The core 4-phase structure is unchanged.

---

## Amendment Summary

| # | Change | Phase affected | Severity | Type |
|---|---|---|---|---|
| 1 | Three summary levels + watch verdict | Phase 2 | High | New feature |
| 2 | Inject prior positions into "What's New" prompt | Phase 2 | High | Bug fix |
| 3 | Decouple full-episode summaries from tracked speakers | Phase 2 | High | Logic fix |
| 4 | XOR constraint on claim sources | Phase 3 | Medium | Schema fix |
| 5 | Favorites schema: real FK constraints | Phase 2 | Medium | Schema fix |
| 6 | DISTINCT on topic filter joins | Phase 1 | Low | Bug fix |
| 7 | Thread-aware X ingestion | Phase 3 | Medium | Feature enhancement |

---

## Amendment 1: Three Summary Levels + Watch Verdict

### Problem

The original spec produces one summary per episode at one depth level. For someone who doesn't have time for long podcasts, "medium depth" satisfies no use case perfectly. Morning coffee needs 30 seconds. A commute needs 2 minutes. Weekend deep-dive needs the full outline.

### Change

Replace the single-summary structure with three tiers plus a watch verdict.

**Modify `episode_summaries` table:**

```sql
-- Replace the existing columns:
--   overview TEXT NOT NULL
--   key_claims_json JSONB NOT NULL
--   speakers_json JSONB NOT NULL
--   whats_new TEXT

-- With:
    tldr TEXT NOT NULL,
    -- 2-3 sentences. The "30-second" version. Example:
    -- "Dario Amodei argues we're a few years from AGI-level systems
    --  and urges massive safety investment now. Shifted from his
    --  previous 'decade' framing to explicit urgency."

    summary_body TEXT NOT NULL,
    -- The "2-minute read" version. 2-4 paragraphs covering:
    -- who was on, what was discussed, key positions taken,
    -- and any notable shifts or disagreements.

    detailed_json JSONB NOT NULL,
    -- The "deep dive" version. Structured as:
    -- {
    --   "sections": [
    --     {
    --       "title": "AI Scaling and the End of the Exponential",
    --       "start_ms": 142000,
    --       "source_url": "https://youtube.com/watch?v=ABC&t=142s",
    --       "summary": "Dario argues that...",
    --       "claims": [{"claim_id": "...", "text": "...", "speaker": "..."}]
    --     }
    --   ],
    --   "speakers": [
    --     {
    --       "person_name": "Dario Amodei",
    --       "person_id": "uuid-or-null",
    --       "main_positions": "...",
    --       "claim_count": 5
    --     }
    --   ],
    --   "best_moments": [
    --     {
    --       "description": "Dario's most direct statement on timelines",
    --       "timestamp_display": "45:22",
    --       "source_url": "https://youtube.com/watch?v=ABC&t=2722s",
    --       "quote_snippet": "We are just a few years away..."
    --     }
    --   ]
    -- }

    whats_new TEXT,
    -- Position shifts vs. prior history. See Amendment 2 for how
    -- this is populated with real context.

    watch_verdict TEXT NOT NULL CHECK (watch_verdict IN (
        'essential',        -- "Drop everything and watch/read this"
        'worth_skimming',   -- "Worth 10 minutes, jump to the key moments"
        'skip_unless_fan'   -- "Only if you specifically follow [person/topic]"
    )),
    watch_verdict_reason TEXT NOT NULL,
    -- 1 sentence justifying the verdict. Example:
    -- "Dario's timeline shift is the most significant public
    --  statement from an AI lab CEO this quarter."
```

**Updated feed API response:**

```json
{
  "id": "uuid",
  "video_title": "Dario Amodei — We are near the end of the exponential",
  "channel_name": "Dwarkesh Podcast",
  "published_at": "2026-02-13",
  "watch_verdict": "essential",
  "watch_verdict_reason": "Dario's timeline shift is the most significant...",
  "tldr": "Dario Amodei argues we're a few years from AGI-level systems...",
  "summary_body": "In a wide-ranging conversation with Dwarkesh Patel...",
  "detailed": { "sections": [...], "speakers": [...], "best_moments": [...] },
  "whats_new": "Shifted from cautious 'decade' framing to explicit urgency...",
  "source_url": "https://youtube.com/watch?v=ABC123"
}
```

**Frontend: Feed card renders the TL;DR by default, with "Read more" expanding to summary_body, and "Full breakdown" expanding to the detailed sections + best moments.**

### Updated summary prompt structure

The LLM call now needs to produce all three levels. Structure the prompt output as:

```
Return as JSON with these fields:
1. "tldr": 2-3 sentences. What happened and why it matters.
2. "summary_body": 2-4 paragraphs. Key positions, arguments, tensions.
3. "sections": Array of episode sections, each with title, timestamp,
   summary, and linked claim_ids.
4. "best_moments": The 3 most important/surprising/quotable moments
   with timestamps.
5. "speakers": Per-speaker position summary.
6. "whats_new": Position shifts vs. prior history (see context below).
7. "watch_verdict": One of "essential", "worth_skimming", "skip_unless_fan".
8. "watch_verdict_reason": 1 sentence justifying the verdict.
```

**Cost impact:** Slightly larger completion (~2x tokens), still under $0.10/episode with Qwen3.5-Plus. Negligible at 20 episodes/month.

---

## Amendment 2: Inject Prior Positions into "What's New" Prompt

### Problem

The original prompt asks the LLM to detect position shifts, but only provides the current episode's transcript and claims. Without the person's prior positions and recent claims, "What's New" is guesswork.

### Change

Before generating a summary, query each tracked speaker's existing context and inject it into the prompt:

```python
async def build_whats_new_context(person_id: UUID, episode_topics: list[str], db: Session) -> str:
    """Build prior-position context for a person to inject into the summary prompt."""
    
    # 1. Get their current topic positions on overlapping topics
    positions = db.query(PersonTopicPosition).filter(
        PersonTopicPosition.person_id == person_id,
        PersonTopicPosition.topic_slug.in_(episode_topics)
    ).all()
    
    # 2. Get their last 10 approved claims on overlapping topics
    recent_claims = db.query(Claim).join(ClaimTopic).join(Topic).filter(
        Claim.person_id == person_id,
        Claim.review_status == 'approved',
        Topic.slug.in_(episode_topics)
    ).order_by(Claim.created_at.desc()).limit(10).all()
    
    # 3. Format as context block
    context = f"PRIOR POSITIONS FOR {person.name}:\n"
    for pos in positions:
        context += f"- {pos.topic_name}: {pos.position_summary} "
        context += f"(as of {pos.updated_at.strftime('%Y-%m-%d')})\n"
    
    context += f"\nRECENT CLAIMS BY {person.name}:\n"
    for claim in recent_claims:
        context += f"- [{claim.created_at.strftime('%Y-%m-%d')}] {claim.claim_text}\n"
    
    return context
```

**Add to the summary prompt, before the "WHAT'S NEW" instruction:**

```
PRIOR CONTEXT FOR TRACKED SPEAKERS:
{whats_new_context}

When evaluating "What's New", compare the current episode's positions
against the PRIOR POSITIONS and RECENT CLAIMS above. Only flag a shift
if the person's current statement meaningfully contradicts or evolves
their documented prior position. If no prior positions exist for a
speaker, note "First tracked appearance" instead.
```

**Edge case:** If a speaker has no prior positions or claims (first time being tracked in an episode), the context block is empty and "What's New" should say "First tracked appearance — baseline positions established."

**Token budget impact:** ~500-2000 tokens per speaker for prior context. For a 2-speaker episode, this adds ~1K-4K tokens to the prompt — well within budget.

---

## Amendment 3: Decouple Full-Episode Summaries from Tracked Speakers

### Problem

The original trigger is: generate a summary after a video reaches `status='enriched'`. But enrichment requires tracked speakers to be identified. If a favorite channel interviews an untracked guest, the pipeline may produce zero claims and no enrichment — meaning no summary for an episode you specifically said you care about.

Example: Dwarkesh interviews a historian you don't track. The video gets discovered and transcribed, but speaker identification finds no tracked person, so enrichment is skipped. Your "Dwarkesh is a favorite channel" setting produces nothing.

### Change

Split summary generation into two independent triggers:

**Trigger A (existing, modified): Person-focused summaries**

Fires when: video reaches `status='enriched'` AND any speaker in `video_people` is a favorite person.

Requires: extracted claims (needs enrichment to have run).

Produces: `summary_type='person_focused'` summary.

No change from the original spec except the trigger condition is now explicit.

**Trigger B (new): Full-episode summaries for favorite channels**

Fires when: video is from a favorite channel AND has a completed transcript (any `transcript_runs` row with `status='completed'`).

Does NOT require: enrichment, tracked speakers, or extracted claims.

Input: raw transcript segments. If claims exist (because some speakers were tracked and enriched), include them as supplementary input.

Produces: `summary_type='full_episode'` summary.

```python
async def maybe_generate_full_episode_summary(video_id: UUID, db: Session):
    """Generate full-episode summary for favorite channels.
    
    Called after transcription completes (not after enrichment).
    Does not require tracked speakers or claims.
    """
    video = get_video(video_id, db)
    
    # Check if channel is favorited
    channel_fav = db.query(Favorite).filter(
        Favorite.channel_id == video.podcast_channel_id
    ).first()
    if not channel_fav:
        return
    
    # Check if transcript exists
    transcript_run = db.query(TranscriptRun).filter(
        TranscriptRun.video_id == video_id,
        TranscriptRun.status == 'completed'
    ).first()
    if not transcript_run:
        return
    
    # Check if summary already exists
    existing = db.query(EpisodeSummary).filter(
        EpisodeSummary.video_id == video_id,
        EpisodeSummary.summary_type == 'full_episode'
    ).first()
    if existing:
        return
    
    # Get transcript
    segments = get_transcript_segments(video_id, transcript_run.id, db)
    transcript_text = format_segments_for_prompt(segments)
    
    # Get claims if they exist (optional — enrichment may not have run)
    claims = db.query(Claim).filter(
        Claim.video_id == video_id,
        Claim.review_status == 'approved'
    ).all()
    claims_json = format_claims_for_prompt(claims) if claims else ""
    
    # Get prior positions for any identified speakers (optional)
    speakers = db.query(VideoPeople).filter(
        VideoPeople.video_id == video_id
    ).all()
    whats_new_context = ""
    for s in speakers:
        if s.person_id:
            whats_new_context += await build_whats_new_context(
                s.person_id, extract_topics_from_transcript(transcript_text), db
            )
    
    # Generate summary from transcript (claims are supplementary)
    await generate_summary(
        video_id=video_id,
        summary_type='full_episode',
        transcript_text=transcript_text,
        claims_json=claims_json,
        whats_new_context=whats_new_context,
        db=db
    )
```

**Pipeline integration:** Add the trigger call at the end of `transcribe_video()` (Stage 2), not just at the end of `enrich_video()` (Stage 4):

```python
# In transcription.py, after successful transcription:
await maybe_generate_full_episode_summary(video_id, db)

# In enrichment.py, after successful enrichment:
# Regenerate full-episode summary if it exists (now with claims)
await maybe_regenerate_full_episode_summary_with_claims(video_id, db)
# Generate person-focused summaries for favorite speakers
await maybe_generate_person_summaries(video_id, db)
```

**Regeneration note:** When a video gets enriched after the full-episode summary was already generated from transcript-only, regenerate the full-episode summary to incorporate the claims. The `generated_at` timestamp updates. The old summary is overwritten (partial unique index ensures only one full_episode summary per video).

### Test Criteria

- Favorite channel episode with NO tracked speakers: full-episode summary is generated after transcription
- Favorite channel episode with tracked speakers: full-episode summary is generated after transcription, then regenerated (improved) after enrichment
- Non-favorite channel episode: no summary generated regardless of speakers

---

## Amendment 4: XOR Constraint on Claim Sources

### Problem

The original constraint `CHECK (video_id IS NOT NULL OR x_post_id IS NOT NULL)` allows a claim to point at both a video and an X post simultaneously. This is semantically wrong — a claim comes from one source.

### Change

Replace OR with XOR:

```sql
-- In the Phase 3 migration, replace:
--   CHECK (video_id IS NOT NULL OR x_post_id IS NOT NULL)
-- With:
ALTER TABLE claims ADD CONSTRAINT claims_source_xor
    CHECK ((video_id IS NOT NULL) <> (x_post_id IS NOT NULL));
```

The `<>` operator on booleans acts as XOR in PostgreSQL: exactly one must be true.

---

## Amendment 5: Favorites Schema with Real FK Constraints

### Problem

The polymorphic `entity_type + entity_id` design gives up foreign key integrity. Orphaned favorites are possible if a person or channel is deleted.

### Change

Replace the `favorites` table with:

```sql
CREATE TABLE favorites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID REFERENCES people(id) ON DELETE CASCADE,
    channel_id UUID REFERENCES podcast_channels(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    notify BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Exactly one of person_id or channel_id must be set
    CHECK ((person_id IS NOT NULL) <> (channel_id IS NOT NULL))
);

-- Prevent duplicate favorites for the same entity
CREATE UNIQUE INDEX idx_favorites_person ON favorites(person_id)
    WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX idx_favorites_channel ON favorites(channel_id)
    WHERE channel_id IS NOT NULL;
```

**Update all queries that reference favorites:**

```python
# Old: filter by entity_type + entity_id
db.query(Favorite).filter(
    Favorite.entity_type == 'person',
    Favorite.entity_id == person_id
)

# New: filter directly by FK column
db.query(Favorite).filter(Favorite.person_id == person_id)
```

**CLI commands stay the same** — the `bm favorites add person "Ray Dalio"` command just sets `person_id` instead of `entity_type='person', entity_id=X`.

**Benefits:** Real cascading deletes, no orphans, type-safe joins, slightly simpler queries.

---

## Amendment 6: DISTINCT on Topic Filter Joins

### Problem

When filtering claims by multiple topics (`?topic=macro&topic=interest_rates`), a claim tagged with both topics produces duplicate rows from the JOIN.

### Change

Add DISTINCT to the query in `public.py`:

```python
@router.get("/api/claims")
async def list_claims(
    ...,
    topic: list[str] | None = Query(None),
):
    query = select(Claim).where(Claim.review_status == 'approved')
    if topic:
        query = (
            query
            .join(ClaimTopic)
            .join(Topic)
            .where(Topic.slug.in_(topic))
            .distinct(Claim.id)  # Prevent duplicates from multi-topic matches
        )
```

One-line fix. Add to the Phase 1 implementation notes.

---

## Amendment 7: Thread-Aware X Ingestion

### Problem

The original spec treats X posts as individual items, but many substantive X posts are threads. A thread's value is in the concatenated argument, not individual posts.

### Change

**Modify `bm x add` to accept thread root URLs:**

```bash
# Single post (unchanged)
bm x add "https://x.com/raydalio/status/123" --text "Post text"

# Thread (new: pass --thread flag or auto-detect)
bm x add "https://x.com/pmarca/status/456" --thread
# Or with pasted text:
bm x add "https://x.com/pmarca/status/456" --thread \
    --text "1/5: The thing about AI agents is...
2/5: What most people miss is...
3/5: The real shift happens when...
4/5: This is why I think...
5/5: In summary..."
```

**Thread handling:**

1. **Storage:** The thread root is stored as an `x_posts` row with `is_thread=true`. Each child post is stored as a separate `x_posts` row with `thread_parent_id` pointing to the root. This preserves the raw data.

2. **Extraction:** For claim extraction, concatenate all posts in the thread into a single text block, preserving order. Send the concatenated text to the LLM. The claim's `x_post_id` points to the thread root post.

3. **Display:** The source citation shows "@pmarca on X (thread, 5 posts)" with a link to the root.

```python
async def ingest_x_thread(root_url: str, posts: list[dict], person_id: UUID, db: Session):
    """Store a thread and extract claims from the concatenated text."""
    
    # Store root post
    root = XPost(
        platform_post_id=posts[0]["id"],
        person_id=person_id,
        post_text=posts[0]["text"],
        post_url=root_url,
        posted_at=posts[0]["posted_at"],
        is_thread=True,
        discovery_method='manual',
        status='discovered',
    )
    db.add(root)
    db.flush()  # Get root.id
    
    # Store child posts
    for post in posts[1:]:
        child = XPost(
            platform_post_id=post["id"],
            person_id=person_id,
            post_text=post["text"],
            post_url=f"https://x.com/{person.x_handle}/status/{post['id']}",
            posted_at=post["posted_at"],
            is_thread=False,
            thread_parent_id=root.id,
            discovery_method='manual',
            status='discovered',
        )
        db.add(child)
    
    # Concatenate for extraction
    full_text = "\n\n".join(p["text"] for p in posts)
    
    # Run substantiveness filter on full thread text
    if await is_substantive(full_text, person.name):
        claims = await extract_claims_from_x(full_text, person_id, root.id, db)
        root.status = 'extracted'
    else:
        root.status = 'skipped'
        root.skip_reason = 'thread_not_substantive'
    
    db.commit()
```

**For MVP manual ingestion:** The user pastes the concatenated thread text via `--text`. Automated thread fetching (following `conversation_id` via the X API) is a Phase 4 concern.

**Source citation for thread claims:**

```json
{
  "type": "x_post",
  "title": "@pmarca on X (thread)",
  "source_url": "https://x.com/pmarca/status/456",
  "timestamp_display": "Mar 10, 2026",
  "evidence_quote": "The thing about AI agents is...",
  "evidence_type": "original_post"
}
```

---

## Changes NOT Made (with rationale)

### Ranked feed (deferred)

ChatGPT suggested ranking summaries by `priority × novelty × shift × topic_match × recency` instead of reverse-chronological. This is a good idea at scale but unnecessary for ~20 summaries/month. Reverse-chronological with the `watch_verdict` badges ("essential" / "worth skimming" / "skip unless fan") achieves 80% of the ranking value with zero tuning. Add algorithmic ranking when volume exceeds ~50 summaries/month.

### Favorite topics / saved searches (deferred)

At 47 people and 30 topics, person + channel favorites cover the use case. Topic-based favorites matter when serving multiple users with diverse interests. Add this when the product is multi-tenant.

### Approved-vs-draft feed split (deferred)

ChatGPT suggested splitting into a public feed (approved claims only) and private draft feed (unreviewed). For a personal tool, this adds complexity without value — you're the only reviewer. The current spec already generates summaries from whatever claims exist; you review them in the review queue. If the product becomes multi-user, add the split then.

### Worker/job queue (acknowledged, not scoped here)

The Railway 429 problem is real but it's an infrastructure concern, not a feature. The workaround (running ingestion locally against the production DB) works. A proper job queue (Railway cron or a lightweight Celery setup) should be added before onboarding external users, but it doesn't affect the 4-phase improvement plan.

### Summary staleness on claim review changes (acknowledged, not scoped)

ChatGPT noted summaries could drift from the claim store if review status changes after generation. True in theory, but in practice: you review claims within hours of extraction, and summaries reference claim_ids which remain stable. If a claim is rejected, the summary's `key_claims` section might reference a now-rejected claim, but the TL;DR and overview are still valid. Add a staleness check when this becomes a real problem.

---

## Updated Phase 2 Schema (with all amendments applied)

For reference, here is the complete Phase 2 schema incorporating Amendments 1, 2, 3, and 5:

```sql
-- Favorites (Amendment 5: real FK constraints)
CREATE TABLE favorites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID REFERENCES people(id) ON DELETE CASCADE,
    channel_id UUID REFERENCES podcast_channels(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    notify BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((person_id IS NOT NULL) <> (channel_id IS NOT NULL))
);

CREATE UNIQUE INDEX idx_favorites_person ON favorites(person_id)
    WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX idx_favorites_channel ON favorites(channel_id)
    WHERE channel_id IS NOT NULL;

-- Episode Summaries (Amendment 1: three levels + verdict)
CREATE TABLE episode_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    summary_type TEXT NOT NULL
        CHECK (summary_type IN ('full_episode', 'person_focused')),
    person_focus_id UUID REFERENCES people(id),
    
    -- Three summary levels (Amendment 1)
    tldr TEXT NOT NULL,
    summary_body TEXT NOT NULL,
    detailed_json JSONB NOT NULL,
    whats_new TEXT,
    
    -- Watch verdict (Amendment 1)
    watch_verdict TEXT NOT NULL
        CHECK (watch_verdict IN ('essential', 'worth_skimming', 'skip_unless_fan')),
    watch_verdict_reason TEXT NOT NULL,
    
    -- Metadata
    model_used TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial unique indexes (from main spec fix)
CREATE UNIQUE INDEX idx_episode_summaries_full
    ON episode_summaries(video_id)
    WHERE summary_type = 'full_episode';

CREATE UNIQUE INDEX idx_episode_summaries_person
    ON episode_summaries(video_id, person_focus_id)
    WHERE summary_type = 'person_focused' AND person_focus_id IS NOT NULL;

CREATE INDEX idx_episode_summaries_video ON episode_summaries(video_id);
CREATE INDEX idx_episode_summaries_person_focus ON episode_summaries(person_focus_id);
```

---

## Updated Phase 3 Claim Constraint (Amendment 4)

```sql
-- Replace the OR constraint from the main spec:
ALTER TABLE claims ADD CONSTRAINT claims_source_xor
    CHECK ((video_id IS NOT NULL) <> (x_post_id IS NOT NULL));
```

---

## Test Criteria for Amendments

**Amendment 1 (summary levels):**
- Feed API returns `tldr`, `summary_body`, `detailed_json`, `watch_verdict`, `watch_verdict_reason`
- Feed card renders TL;DR by default with expandable "Read more" and "Full breakdown"
- `watch_verdict` is one of exactly three values
- `detailed_json.best_moments` has 2-3 entries with valid timestamp links

**Amendment 2 (prior positions):**
- Summary prompt for a person with existing positions includes their `person_topic_positions` rows
- Summary prompt for a person with no prior positions produces "First tracked appearance"
- "What's New" section references specific prior positions when flagging shifts

**Amendment 3 (decoupled summaries):**
- Favorite channel episode with NO tracked speakers → full-episode summary generated after transcription
- Favorite channel episode with tracked speakers → full-episode summary generated after transcription, regenerated after enrichment
- Non-favorite channel episode → no summary regardless of speakers
- `bm summaries generate --pending` catches favorite-channel videos that have transcripts but no summaries

**Amendment 4 (XOR constraint):**
- Inserting a claim with both `video_id` and `x_post_id` set → rejected by database
- Inserting a claim with neither set → rejected by database
- Inserting a claim with exactly one set → succeeds

**Amendment 5 (favorites FK):**
- Deleting a person cascades to their favorite entry
- Deleting a channel cascades to their favorite entry
- Inserting a favorite with both `person_id` and `channel_id` → rejected
- Inserting a favorite with neither → rejected
- No duplicate favorites for the same person or channel

**Amendment 6 (DISTINCT):**
- `GET /api/claims?topic=macro&topic=interest_rates` returns each claim once, even if tagged with both

**Amendment 7 (threads):**
- `bm x add <url> --thread --text "1/5: ..."` stores root + children with correct parent references
- Claims extracted from thread have `x_post_id` pointing to thread root
- Source citation shows "(thread)" in the title

# Believable Minds — Improvement Spec: Four Features

**Status:** Implementation-ready for Claude Code  
**Date:** March 2026  
**Base:** Live production system at serikson.com (47 people, 339 videos, 86 claims)  
**Build order:** Phase 1 → 2 → 3 → 4, each independently deployable

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Build Order Summary

|Phase|Feature|Effort|Schema changes|Why this order|
|---|---|---|---|---|
|1|Source citations + clickable topics|2-3 days|None|Makes existing product feel real. Zero migration risk|
|2|Favorites + episode summaries|3-4 days|2 new tables|The “open every morning” feature. Biggest product differentiation|
|3|X/Twitter ingestion|3-4 days|1 new table + 1 column on claims + 1 column on people|Expands source coverage beyond YouTube|
|4|Automated X discovery + polish|2-3 days|None|Makes X hands-off|

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

# Phase 1: Source Citations + Clickable Topics

**Goal:** Every claim shows exactly where it came from with a clickable link. Every topic badge is a navigation element.

**Schema changes:** None. All data already exists.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## 1A: Source Citations

### What already exists

The system already stores everything needed:

•          claims.video_id → videos.youtube_video_id (the YouTube video ID)

•          claim_evidence.start_ms / end_ms (exact timestamp in milliseconds)

•          claim_evidence.quote_text (the evidence snippet)

•          videos.title (source video title)

•          videos.published_at (when the video was published)

### API Changes

**Modify** **GET /api/claims** response to include a source object on each claim:

{  
  "id": "uuid",  
  "person_name": "Ray Dalio",  
  "claim_text": "We are in the early stages of a debt crisis...",  
  "reasoning_text": "...",  
  "source": {  
    "type": "video",  
    "title": "Ray Dalio on the Changing World Order",  
    "source_url": "https://youtube.com/watch?v=ABC123&t=142s",  
    "timestamp_display": "2:22",  
    "timestamp_ms": 142000,  
    "published_at": "2026-03-10T00:00:00Z",  
    "evidence_quote": "We are near the early stages of what I believe will be a debt crisis...",  
    "evidence_type": "direct_quote"  
  },  
  "topics": [...],  
  ...  
}

**Implementation in** **public.py****:**

For each claim, join to claim_evidence (ordered by evidence_order, take the first row) and join to videos:

@router.get("/api/claims")  
async def list_claims(...):    _# Existing query + add:_    _# LEFT JOIN claim_evidence ce ON ce.claim_id = c.id AND ce.evidence_order = 1_    _# LEFT JOIN videos v ON v.id = c.video_id_       for claim in results:        if claim.video_id and claim.youtube_video_id:            start_seconds = (claim.evidence_start_ms or 0) // 1000  
            base_url = f"https://youtube.com/watch?v={claim.youtube_video_id}"  
            _# Only append &t= when we have a real timestamp (not 0)_            source_url = f"{base_url}&t={start_seconds}s" if start_seconds > 0 else base_url            claim.source = {                "type": "video",  
                "title": claim.video_title,  
                "source_url": source_url,  
                "timestamp_display": format_timestamp(claim.evidence_start_ms),  
                "timestamp_ms": claim.evidence_start_ms,  
                "published_at": claim.video_published_at,  
                "evidence_quote": claim.evidence_quote_text,  
                "evidence_type": claim.evidence_quote_type,  
            }

**Helper function:**

def format_timestamp(ms: int | None) -> str:  
    _"""Convert milliseconds to human-readable timestamp like '1:23:45' or '2:22'"""_    if ms is None or ms <= 0:  
        return ""  
    seconds = ms // 1000  
    hours = seconds // 3600  
    minutes = (seconds % 3600) // 60  
    secs = seconds % 60  
    if hours > 0:  
        return f"{hours}:{minutes:02d}:{secs:02d}"  
    return f"{minutes}:{secs:02d}"

**Also modify** **GET /api/claims/{claim_id}** to include ALL evidence spans (not just the primary):

{  
  "id": "uuid",  
  "source": { ... },  // Same as above (primary evidence)  
  "all_evidence": [  
    {  
      "order": 1,  
      "quote_text": "...",  
      "quote_type": "direct_quote",  
      "start_ms": 142000,  
      "end_ms": 158000,  
      "source_url": "https://youtube.com/watch?v=ABC123&t=142s",  
      "timestamp_display": "2:22"  
    },  
    {  
      "order": 2,  
      "quote_text": "...",  
      "quote_type": "supporting",  
      "start_ms": 165000,  
      "end_ms": 180000,  
      "source_url": "https://youtube.com/watch?v=ABC123&t=165s",  
      "timestamp_display": "2:45"  
    }  
  ]  
}

### Frontend Changes

**Claim card in Claims Explorer and Person detail:**

Every claim card currently shows claim text, person name, topic badges, and confidence. Add:

_<!-- Source citation row — add at bottom of each claim card -->_  
<div class="claim-source">  
  <span class="source-icon">▶</span>  
  <a href="${source.source_url}" target="_blank" class="source-link">  
    ${source.title}  </a>  
  <span class="source-timestamp">${source.timestamp_display}</span>  
  <span class="source-date">${formatDate(source.published_at)}</span>  
</div>  
  
_<!-- Evidence quote — show on hover or click-to-expand -->_  
<div class="evidence-quote" data-expanded="false">  
  <span class="quote-type-badge">${source.evidence_type}</span>  
  <blockquote>"${source.evidence_quote}"</blockquote>  
</div>

**CSS:**

.claim-source {  display: flex;  
  align-items: center;  
  gap: 8px;  
  margin-top: 10px;  
  padding-top: 10px;  
  border-top: 1px solid var(--line);  
  font-size: 0.88rem;  
  color: var(--muted);  
}  
  
.source-link {  color: var(--accent);  
  text-decoration: none;  
  font-weight: 500;  
}  
  
.source-link_:hover_ {  text-decoration: underline;  
}  
  
.source-timestamp {  font-family: monospace;  
  background: var(--accent-soft);  
  color: var(--accent);  
  padding: 2px 8px;  
  border-radius: 4px;  
  font-size: 0.82rem;  
  font-weight: 600;  
}  
  
.evidence-quote {  margin-top: 8px;  
  padding: 10px 14px;  
  border-left: 3px solid var(--accent);  
  background: var(--accent-soft);  
  border-radius: 0 8px 8px 0;  
  font-size: 0.92rem;  
  line-height: 1.5;  
  display: none;  
}  
  
.evidence-quote[data-expanded="true"] {  display: block;  
}  
  
.quote-type-badge {  font-size: 0.75rem;  
  text-transform: uppercase;  
  letter-spacing: 0.5px;  
  color: var(--accent);  
  font-weight: 600;  
  margin-bottom: 4px;  
  display: block;  
}

### Person Detail Page Enhancement

On the person detail page, the claims timeline should show source citations for each claim. For the person’s “Recent Appearances” section (if it exists), show the video title + link. Each claim in the scrollable history should have the source timestamp badge.

### Test Criteria: Phase 1A

•          Every claim in /api/claims response includes a source object with source_url, timestamp_display, title, and published_at

•          Claims without evidence (edge case) have source: null — the frontend handles this gracefully

•          The source URL is a valid YouTube link with &t=Xs parameter

•          timestamp_display correctly formats: “2:22” for short, “1:23:45” for long

•          Frontend claim cards show the source row with clickable link

•          Clicking the timestamp link opens YouTube at the correct moment

•          Evidence quote expands on click

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## 1B: Clickable Topics

### API Changes

**Add topic filter to** **GET /api/claims****:**

GET /api/claims?topic=ai_infrastructure  
GET /api/claims?topic=macro&topic=interest_rates  (multiple topics, OR logic)

**Implementation:** Filter claim_topics junction:

@router.get("/api/claims")  
async def list_claims(    ...,    topic: list[str] | None = Query(None),  _# topic slugs_  
):  
    query = select(Claim).where(Claim.review_status == 'approved')  
    if topic:        query = query.join(ClaimTopic).join(Topic).where(Topic.slug.in_(topic))

**Add topic filter to** **GET /api/people****:**

GET /api/people?topic=ai_infrastructure

Returns people who have approved claims on that topic.

**Enhance** **GET /api/topics/{slug}** to return:

{  
  "slug": "ai_infrastructure",  
  "name": "AI Infrastructure",  
  "claim_count": 23,  
  "person_count": 8,  
  "recent_claims": [...],  // Last 10 approved claims on this topic  
  "people_with_positions": [  
    {  
      "person_id": "...",  
      "person_name": "Jensen Huang",  
      "current_position": "Inference demand will exceed training by 10x...",  
      "claim_count": 5,  
      "latest_claim_date": "2026-03-10"  
    }  
  ]  
}

### Frontend Changes

**Make every topic badge a link:**

Wherever topic badges appear (claim cards, person pages, topic lists), wrap them in <a> tags:

_<!-- Before -->_  
<span class="topic-badge">AI Infrastructure</span>  
  
_<!-- After -->_  
<a href="#topics/ai_infrastructure" class="topic-badge topic-link">AI Infrastructure</a>

Clicking navigates to the topic detail view (which already exists but may need enrichment).

**Add topic filter chips to Claims Explorer:**

Above the claims list, add a row of the most popular topics as clickable filter chips:

<div class="topic-filters">  
  <button class="topic-chip active" data-topic="">All</button>  
  <button class="topic-chip" data-topic="ai_infrastructure">AI Infrastructure</button>  
  <button class="topic-chip" data-topic="macro">Macro</button>  
  <button class="topic-chip" data-topic="interest_rates">Interest Rates</button>  
  _<!-- Populate from GET /api/topics sorted by claim_count desc, top 10 -->_</div>

Clicking a chip re-fetches claims with ?topic=slug filter.

**Enrich person detail page with topic links:**

In the person’s expertise section, make each expertise domain link to the corresponding topic page if a matching topic exists:

<div class="expertise-tags">  
  <a href="#topics/macro" class="topic-link">Macro</a>  
  <a href="#topics/debt_cycles" class="topic-link">Debt Cycles</a>  
  <a href="#topics/geopolitics" class="topic-link">Geopolitics</a>  
</div>

### Test Criteria: Phase 1B

•          Every topic badge in the UI is a clickable link

•          /api/claims?topic=macro returns only claims tagged with “macro”

•          /api/people?topic=ai_infrastructure returns only people with claims on that topic

•          Topic detail page shows recent claims and people with positions

•          Claims Explorer has topic filter chips that update the list

•          Person detail page expertise tags link to topic pages

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

# Phase 2: Favorites + Episode Summaries

**Goal:** For your top 5-10 people and channels, automatically generate rich episode summaries. Create a “Feed” view for quick morning reading.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Schema Changes

### New table: favorites

CREATE TABLE favorites (    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),    entity_type TEXT NOT NULL CHECK (entity_type IN ('person', 'channel')),  
    entity_id UUID NOT NULL,  _-- FK to people.id or podcast_channels.id_  
    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),  
    notify BOOLEAN NOT NULL DEFAULT true,  _-- include in feed/briefs_  
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),    UNIQUE (entity_type, entity_id));

### New table: episode_summaries

CREATE TABLE episode_summaries (    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),    video_id UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,  
    summary_type TEXT NOT NULL CHECK (summary_type IN ('full_episode', 'person_focused')),  
    person_focus_id UUID REFERENCES people(id),  _-- NULL for full_episode, set for person_focused_  
       _-- Summary content_    overview TEXT NOT NULL,            _-- 3-4 sentence episode overview_  
    key_claims_json JSONB NOT NULL,    _-- Array of {claim_id, claim_text, reasoning_summary, timestamp_display, source_url}_  
    speakers_json JSONB NOT NULL,      _-- Array of {person_name, person_id, main_positions: [...], claim_count}_  
    whats_new TEXT,                    _-- Position shifts or novel claims compared to history_       _-- Metadata_    model_used TEXT NOT NULL,  
    prompt_tokens INTEGER,  
    completion_tokens INTEGER,  
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),       _--_ NOTE_: Cannot use simple UNIQUE(video_id, summary_type, person_focus_id) because_  
    _-- PostgreSQL treats NULLs as distinct, allowing duplicate full_episode summaries._    _-- Use partial unique indexes instead:_);  
  
CREATE UNIQUE INDEX idx_episode_summaries_full    ON episode_summaries(video_id)    WHERE summary_type = 'full_episode';  
  
CREATE UNIQUE INDEX idx_episode_summaries_person    ON episode_summaries(video_id, person_focus_id)    WHERE summary_type = 'person_focused' AND person_focus_id IS NOT NULL;

### Migration

_-- Alembic migration_  
CREATE TABLE favorites (...);  
CREATE TABLE episode_summaries (...);  
CREATE INDEX idx_favorites_entity ON favorites(entity_type, entity_id);CREATE INDEX idx_episode_summaries_video ON episode_summaries(video_id);CREATE INDEX idx_episode_summaries_person ON episode_summaries(person_focus_id);

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Pipeline: Summary Generation

### When to generate

After a video reaches status=‘enriched’ AND (the video’s channel is a favorite OR any speaker in video_people is a favorite person):

async def maybe_generate_summaries(video_id: UUID, db: Session):    video = get_video(video_id, db)       _# Check if channel is favorited_    channel_fav = db.query(Favorite).filter(  
        Favorite.entity_type == 'channel',  
        Favorite.entity_id == video.podcast_channel_id    ).first()       _# Check if any speaker is favorited_    speakers = db.query(VideoPeople).filter(VideoPeople.video_id == video_id).all()  
    fav_people = db.query(Favorite).filter(  
        Favorite.entity_type == 'person',  
        Favorite.entity_id.in_([s.person_id for s in speakers if s.person_id])    ).all()  
       _# Generate full episode summary if channel is favorited_    if channel_fav:        await generate_episode_summary(video_id, summary_type='full_episode', db=db)  
       _# Generate person-focused summary for each favorited speaker_    for fav in fav_people:        await generate_episode_summary(            video_id,            summary_type='person_focused',  
            person_focus_id=fav.entity_id,  
            db=db  
        )

### Summary generation prompt

**Context window strategy:** Long episodes (Acquired = 4+ hrs, Lex = 3+ hrs) can produce 100K+ tokens of raw transcript, exceeding most model context windows. Use this tiered approach:

1.        **Primary input (always):** Extracted claims + evidence quotes from this episode. These are already structured, attributed, and timestamped. Typically 2K-10K tokens even for long episodes.

2.        **Supplementary input (if space permits):** Transcript segments, truncated to fit the remaining context budget. For Qwen3.5-Plus with 128K context, budget ~80K tokens for transcript after claims + prompt overhead.

3.        **Fallback (sparse claims):** If fewer than 3 claims were extracted for the episode, use the full transcript (or truncated transcript) as primary input instead — the claims are too sparse to summarize from.

The implementation should calculate available token budget dynamically:

MAX_PROMPT_TOKENS = 100_000  _# Leave headroom for response_claims_tokens = estimate_tokens(claims_json)prompt_overhead = 2000  _# Template, instructions, etc._transcript_budget = MAX_PROMPT_TOKENS - claims_tokens - prompt_overheadif len(claims) < 3:  
    _# Sparse claims — use transcript as primary, truncate if needed_    transcript_text = truncate_to_tokens(full_transcript, MAX_PROMPT_TOKENS - prompt_overhead)    claims_json = ""  _# Don't include sparse claims_else:  
    _# Normal — claims are primary, transcript is supplementary_    transcript_text = truncate_to_tokens(full_transcript, transcript_budget)

EPISODE_SUMMARY_PROMPT = """You are analyzing a podcast/interview transcript.  
  
VIDEO: {video_title}  
CHANNEL: {channel_name}  
DATE: {published_at}  
SPEAKERS: {speaker_list}  
  
TRANSCRIPT SEGMENTS:  
{transcript_text}  
  
EXTRACTED CLAIMS FOR THIS EPISODE:  
{claims_json}  
  
Generate a structured episode summary with these sections:  
  
1. OVERVIEW: 3-4 sentences describing what was discussed and why it matters.  
  
2. KEY CLAIMS: The 3-5 most significant claims from this episode. For each:  
   - The claim text   - Who said it   - Why it matters (1 sentence)   - The claim_id from the extracted claims list3. SPEAKERS: For each speaker who is a tracked person, summarize their main positions in this episode (2-3 sentences each).  
  
4. WHAT'S NEW: Any position shifts compared to their known positions, or genuinely novel claims that haven't been said before. If nothing is notably new, say "Consistent with prior positions."  
  
Return as JSON:  
{  
  "overview": "...",  "key_claims": [{"claim_id": "...", "summary": "...", "speaker": "...", "why_it_matters": "..."}],  "speakers": [{"person_name": "...", "person_id": "...", "main_positions": "...", "claim_count": N}],  "whats_new": "..."}  
"""

For **person-focused summaries**, modify the prompt to focus on one person:

PERSON_FOCUSED_PROMPT = """You are analyzing what {person_name} said in this episode.Focus ONLY on {person_name}'s statements, positions, and claims.  
Ignore other speakers except when they directly prompt {person_name}'s responses.  
  
{... same structure but filtered to one person ...}  
"""

### Integration with enrichment pipeline

Add to enrichment.py — at the end of enrich_video():

_# After claims are extracted and stored:_  
await maybe_generate_summaries(video_id, db)

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## API Endpoints

### Favorites management

GET  /admin/favorites                              -- List all favorites  
POST /admin/favorites                              -- Add a favorite  
     body: { "entity_type": "person", "entity_id": "uuid", "priority": 1 }DELETE /admin/favorites/{id}                        -- Remove a favorite

### Episode summaries

GET /api/summaries/feed                            -- Reverse-chronological feed of summaries  
    ?limit=20                                       -- Pagination    ?person_id=uuid                                 -- Filter to one person    ?channel_id=uuid                                -- Filter to one channel    ?type=full_episode|person_focused               -- Filter by typeGET /api/summaries/{video_id}                      -- All summaries for a video

**Feed response format:**

{  
  "items": [  
    {  
      "id": "uuid",  
      "video_id": "uuid",  
      "video_title": "Dario Amodei — We are near the end of the exponential",  
      "channel_name": "Dwarkesh Podcast",  
      "published_at": "2026-02-13T00:00:00Z",  
      "summary_type": "full_episode",  
      "person_focus_name": null,  
      "overview": "Dario Amodei discusses the scaling hypothesis...",  
      "key_claims": [...],  
      "speakers": [...],  
      "whats_new": "Shifted from cautious optimism to urgency on timelines...",  
      "source_url": "https://youtube.com/watch?v=ABC123",  
      "generated_at": "2026-02-14T10:00:00Z"  
    }  
  ],  
  "total": 42  
}

### CLI Commands

bm favorites list                          _# Show all favorites_bm favorites add person "Ray Dalio" --priority 1bm favorites add channel "Dwarkesh Podcast" --priority 1bm favorites remove person "Ray Dalio"  
  
bm summaries generate <video_id>           # Generate summary for a specific videobm summaries generate --pending            _# Generate for all enriched favorite videos without summaries_bm summaries feed --limit 10              _# Preview the feed in terminal_

**Backfill note:** The --pending flag queries for videos where status='enriched' AND (the channel is in favorites with entity_type='channel' OR any speaker in video_people is in favorites with entity_type='person') AND no episode_summaries row exists for that video. This means --pending acts as both the backfill-on-first-run command AND the catch-up-for-missed-triggers command.

**Important:** When a user first adds favorites, they should immediately run bm summaries generate --pending to backfill summaries for the 5 already-enriched videos in production. The auto-trigger in the enrichment pipeline only fires on newly enriched videos — it won’t retroactively generate summaries for videos enriched before the favorite was added.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Frontend: Feed Page

Add a new “Feed” page as the default landing or second nav item.

### Feed layout

┌─────────────────────────────────────────────────┐  
│  FEED                        [Manage Favorites] │  
│  Intelligence summaries for your tracked people  │  
├─────────────────────────────────────────────────┤  
│                                                  │  
│  ┌──────────────────────────────────────────┐   │  
│  │ 📌 Dwarkesh Podcast · Feb 13, 2026       │   │  
│  │ Dario Amodei — "We are near the end      │   │  
│  │ of the exponential"                       │   │  
│  │                                           │   │  
│  │ Dario discusses the scaling hypothesis    │   │  
│  │ in the current RL regime, how AI will     │   │  
│  │ diffuse throughout the economy...         │   │  
│  │                                           │   │  
│  │ KEY CLAIMS                                │   │  
│  │ ▸ "We are just a few years away from a   │   │  
│  │   country of geniuses in a datacenter"    │   │  
│  │   Dario Amodei · 12:45 ▶                 │   │  
│  │ ▸ "The investments companies make in      │   │  
│  │   interpretability today determine..."    │   │  
│  │   Dario Amodei · 45:22 ▶                 │   │  
│  │                                           │   │  
│  │ WHAT'S NEW                                │   │  
│  │ Shifted from cautious to urgent on        │   │  
│  │ timelines. Previously used "decade"       │   │  
│  │ framing, now says "few years."            │   │  
│  │                                           │   │  
│  │ [View Full Claims] [Watch Episode ▶]      │   │  
│  └──────────────────────────────────────────┘   │  
│                                                  │  
│  ┌──────────────────────────────────────────┐   │  
│  │ 📌 Lex Fridman · Feb 18, 2026            │   │  
│  │ Peter Steinberger — OpenClaw: The Viral   │   │  
│  │ AI Agent that Broke the Internet          │   │  
│  │ ...                                       │   │  
│  └──────────────────────────────────────────┘   │  
│                                                  │  
└─────────────────────────────────────────────────┘

### Summary card component

Each summary card shows: - Channel name + date (header) - Episode title (linked to YouTube) - Overview paragraph - Key claims (expandable, each with timestamp link) - Speakers (avatars/initials + brief positions) - “What’s New” section (highlighted if there are position shifts) - Action buttons: “View Full Claims” (links to claims filtered by video), “Watch Episode” (YouTube link)

### Person profile enhancement

On the person detail page, add a “Recent Appearances” section that shows person-focused summaries:

RECENT APPEARANCES  
─────────────────  
📌 Dwarkesh Podcast · Feb 13, 2026  
"We are near the end of the exponential"  
Discussed scaling, timelines, regulation. Key position: urgency on safety investments.  
[3 claims extracted] [Watch at 12:45 ▶]  
  
📌 Lex Fridman · Jan 15, 2026  
"Machines of Loving Grace"  
Discussed the positive case for AI. Key position: ...  
[5 claims extracted] [Watch at 8:30 ▶]

### Test Criteria: Phase 2

•          bm favorites add person "Ray Dalio" --priority 1 creates a favorite

•          After a favorite channel’s video is enriched, an episode summary is auto-generated

•          After a favorite person appears in any enriched video, a person-focused summary is auto-generated

•          Feed page shows summaries in reverse chronological order

•          Each summary card has expandable key claims with timestamp links

•          “What’s New” section correctly identifies position shifts vs. consistent positions

•          Person detail page shows “Recent Appearances” with person-focused summaries

•          Cost per summary: <$0.05 (verify with Qwen3.5-Plus)

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

# Phase 3: X/Twitter Integration

**Goal:** Track what important people say on X. Show X-sourced claims alongside video-sourced claims.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Schema Changes

### New column on people

ALTER TABLE people ADD COLUMN x_handle TEXT;  _-- e.g., 'raydalio' (without @)_

### New table: x_posts

CREATE TABLE x_posts (    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),    platform_post_id TEXT UNIQUE NOT NULL,  _-- X/Twitter post ID_  
    person_id UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,  
    post_text TEXT NOT NULL,  
    post_url TEXT NOT NULL,  _-- https://x.com/{handle}/status/{id}_  
    posted_at TIMESTAMPTZ,    is_thread BOOLEAN NOT NULL DEFAULT false,  
    thread_parent_id UUID REFERENCES x_posts(id),  
    is_repost BOOLEAN NOT NULL DEFAULT false,  
    media_urls TEXT[],    engagement_json JSONB,  _-- {"likes": N, "reposts": N, "views": N} at ingestion time_    discovery_method TEXT NOT NULL CHECK (discovery_method IN ('manual', 'timeline_scan', 'search')),  
    status TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN ('discovered', 'extracted', 'skipped', 'error')),  
    _-- Status lifecycle:_    _--   discovered → (substantiveness filter) → extracted (claims created)_    _--   discovered → (substantiveness filter) → skipped (skip_reason set)_    _--   discovered → error (LLM or fetch failure)_    skip_reason TEXT,    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());  
  
CREATE INDEX idx_x_posts_person ON x_posts(person_id, posted_at DESC);  
CREATE INDEX idx_x_posts_status ON x_posts(status);

### Modify claims table

_-- IMPORTANT: Run these in this exact order in a single Alembic migration:_  
_-- Step 1: Add the new column_  
ALTER TABLE claims ADD COLUMN x_post_id UUID REFERENCES x_posts(id);  
_-- Step 2: Make video_id nullable (must happen BEFORE adding CHECK)_  
ALTER TABLE claims ALTER COLUMN video_id DROP NOT NULL;  
_-- Step 3: Add CHECK constraint (both columns now exist, video_id is nullable)_  
ALTER TABLE claims ADD CONSTRAINT claims_source_check    CHECK (video_id IS NOT NULL OR x_post_id IS NOT NULL);

### Modify claim_evidence for X posts

For X-sourced claims, evidence is the post text itself. segment_id should be nullable (or use a sentinel). The cleanest approach: segment_id stays NOT NULL for video claims, but for X claims we store a synthetic segment-like record in a simpler way:

Actually, the simplest approach: **for X-sourced claims, the** **claim_evidence** **row uses** **segment_id = NULL** **and stores the post text in** **quote_text** **with** **start_ms = NULL****,** **end_ms = NULL****,** **quote_type = 'original_post'****.**

**IMPORTANT:** Do NOT use start_ms = 0, end_ms = 0 — this violates the CHECK (start_ms < end_ms) constraint on claim_evidence from the main spec. Instead, make the CHECK conditional so it only applies when a segment is referenced:

_-- Relax the NOT NULL on segment_id for X claims_  
ALTER TABLE claim_evidence ALTER COLUMN segment_id DROP NOT NULL;  
_-- Also relax start_ms/end_ms to nullable for X claims_  
ALTER TABLE claim_evidence ALTER COLUMN start_ms DROP NOT NULL;  
ALTER TABLE claim_evidence ALTER COLUMN end_ms DROP NOT NULL;  
_-- Replace the timestamp CHECK with a conditional one:_  
_-- (Only enforce start_ms < end_ms when segment_id is present, i.e., video evidence)_  
ALTER TABLE claim_evidence DROP CONSTRAINT IF EXISTS claim_evidence_timestamp_check;ALTER TABLE claim_evidence ADD CONSTRAINT claim_evidence_timestamp_check    CHECK (segment_id IS NULL OR (start_ms IS NOT NULL AND end_ms IS NOT NULL AND start_ms < end_ms));  
_-- Add new quote_type value_  
_-- existing: 'direct_quote', 'paraphrase', 'multi_segment_synthesis'_  
_-- add: 'original_post'_

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## X Ingestion Pipeline

### Stage 1: Manual discovery (Phase 3 MVP)

Start with manual ingestion — you spot an important post and add it:

bm x add "https://x.com/raydalio/status/1234567890"

**Implementation:**

1.        Parse the URL to extract handle and post ID

2.        Fetch post content (see Provider section below)

3.        Match handle to people.x_handle → get person_id

4.        Store in x_posts with discovery_method='manual', status='discovered'

5.        Run substantiveness filter (see below)

6.        If substantive → extract claims → status='extracted'

7.        If not substantive → status='skipped', skip_reason='not_substantive'

### Stage 2: Substantiveness filter

Not every tweet is worth extracting claims from. Filter for substance:

SUBSTANTIVENESS_PROMPT = """Evaluate whether this X post contains a substantive claim, position, prediction, or opinion worth tracking.  
  
POST by {person_name} (@{handle}):  
"{post_text}"  
  
A post is substantive if it:  
- States a specific position on a topic (macro, AI, policy, etc.)  
- Makes a prediction about the future  
- Shares original analysis or reasoning  
- Takes a public stance on a debated issue  
  
A post is NOT substantive if it is:  
- Casual/social (greetings, jokes, personal updates)  
- Pure promotion (product launches, event announcements)  
- A retweet without commentary  
- Under 50 characters with no analytical content  
  
Respond with JSON: {"substantive": true/false, "reason": "brief explanation"}  
"""

### Stage 3: Claim extraction from X posts

Use the same tool schema as video enrichment, adapted for short-form content:

X_EXTRACTION_PROMPT = """Extract structured claims from this X post.  
  
POST by {person_name} (@{handle}):  
"{post_text}"  
Posted: {posted_at}  
  
A single X post typically contains 1-3 claims. Extract each.  
  
For each claim, the evidence_span should reference the post text itself.  
Use segment_id: null (this is not a video segment).  
"""

The tool schema is the same extract_claims tool — the only difference is that evidence_spans[].segment_id will be null and the evidence is the post text.

### Source citation for X claims

The source object for X-sourced claims:

{  
  "type": "x_post",  
  "title": "@raydalio on X",  
  "source_url": "https://x.com/raydalio/status/1234567890",  
  "timestamp_display": "Mar 10, 2026",  
  "timestamp_ms": null,  
  "published_at": "2026-03-10T14:30:00Z",  
  "evidence_quote": "The full post text...",  
  "evidence_type": "original_post"  
}

### X content provider

For MVP, use one of these approaches:

**Option A: Manual + URL parsing (zero cost, zero API)**

The bm x add <url> command prompts the user to paste the post text. No API needed.

$ bm x add "https://x.com/raydalio/status/1234567890"  
Paste the post text (or leave blank to fetch automatically):  
> We are entering a period that will rhyme with 1930-45...Added X post by Ray Dalio. Running extraction...Extracted 2 claims.

**Option B: X API Basic tier ($200/month)**

Automated timeline scanning. Only worth it once you’ve validated X as a source.

**Option C: RSS bridge or Nitter (free but fragile)**

Public timeline access without API. Unreliable but zero cost.

**Recommendation:** Start with Option A (manual). Add automated scanning in Phase 4 only after you’ve confirmed X posts produce valuable claims.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## API Changes

**Modify source citation logic** in GET /api/claims:

for claim in results:    if claim.video_id:        _# Video source (existing logic)_        claim.source = { "type": "video", ... }  
    elif claim.x_post_id:        _# X source_        x_post = get_x_post(claim.x_post_id, db)        claim.source = {            "type": "x_post",  
            "title": f"@{x_post.person.x_handle} on X",  
            "source_url": x_post.post_url,  
            "timestamp_display": format_date(x_post.posted_at),  
            "published_at": x_post.posted_at,  
            "evidence_quote": x_post.post_text[:280],  
            "evidence_type": "original_post",  
        }

**New endpoint:**

GET /api/x-posts?person_id=uuid&limit=20    -- List X posts for a person

### CLI Commands

bm x add <url>                              # Manually add and extract from an X postbm x add <url> --text "paste text here"     _# Add with text (no fetch needed)_bm x list --person "Ray Dalio" --limit 20   _# List ingested X posts_bm x extract --pending                      _# Extract claims from discovered posts_

### Frontend Changes

**Claim cards:** The source citation row already handles X via the source.type field:

if (source.type === 'video') {  
    _// Show ▶ icon, video title, timestamp badge_} else if (source.type === 'x_post') {  
    _// Show 𝕏 icon, @handle, date badge_}

**Person detail page:** Add an “X Posts” tab or section showing recent X posts from this person:

X POSTS  
───────  
𝕏 @raydalio · Mar 10, 2026  
"We are entering a period that will rhyme with 1930-45.  
The question is whether the current world order can adapt."  
[2 claims extracted] [View on X ↗]  
  
𝕏 @raydalio · Mar 5, 2026  
"The US debt trajectory is unsustainable at current rates..."  
[1 claim extracted] [View on X ↗]

### Test Criteria: Phase 3

•          bm x add <url> --text "..." creates an x_post record linked to the correct person

•          Substantiveness filter correctly rejects casual tweets and accepts position statements

•          Claims extracted from X posts have x_post_id set and video_id NULL

•          /api/claims returns X-sourced claims with correct source.type = "x_post" and source_url

•          Frontend claim cards show 𝕏 icon and post link for X-sourced claims

•          Person detail shows X posts section

•          The CHECK constraint (video_id IS NOT NULL OR x_post_id IS NOT NULL) is enforced

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

# Phase 4: Automated X Discovery + Polish

**Goal:** Hands-off X monitoring for favorite people. Polish the full experience.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Automated X Pipeline

### Timeline scanning (when X API is available)

async def scan_x_timelines(db: Session):    _"""Scan X timelines for favorite people with x_handle set."""_    favorites = db.query(Favorite).filter(  
        Favorite.entity_type == 'person'  
    ).all()  
       for fav in favorites:        person = db.query(People).get(fav.entity_id)        if not person.x_handle:            continue  
               _# Fetch recent posts via X API_        posts = await x_provider.get_recent_posts(            handle=person.x_handle,  
            since=last_scan_time,  
            limit=50  
        )               for post in posts:            _# Skip reposts, replies to non-tracked people, very short posts_            if post.is_repost or len(post.text) < 50:  
                continue  
                       _# Deduplicate_            existing = db.query(XPost).filter(  
                XPost.platform_post_id == post.id  
            ).first()            if existing:                continue  
                       _# Store_            x_post = XPost(                platform_post_id=post.id,  
                person_id=person.id,  
                post_text=post.text,  
                post_url=f"https://x.com/{person.x_handle}/status/{post.id}",  
                posted_at=post.created_at,  
                discovery_method='timeline_scan',  
                status='discovered',  
            )            db.add(x_post)       db.commit()

### CLI

bm x scan                     _# Scan X timelines for all favorite people_bm x scan --person "Ray Dalio"  _# Scan one person_bm x extract --pending         _# Extract claims from all discovered posts_

### Scheduling

Add to the daily pipeline schedule:

06:00 — YouTube channel feed scan  
06:30 — YouTube gap-fill search  
07:00 — X timeline scan (if API available)  
07:30 — Transcription  
08:30 — Speaker identification 09:00 — Enrichment + X extraction  
09:30 — Summary generation  
10:00 — Brief generation + delivery

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Polish Items

### Mixed-source timeline on person detail

The person detail page should show a unified timeline mixing video claims and X claims, sorted by date:

TIMELINE  
────────  
Mar 13, 2026 · Dwarkesh Podcast  
"We are in the early stages of a debt crisis..."  
▶ 45:22 · direct_quote · [View Source]  
  
Mar 10, 2026 · X Post  
"The US debt trajectory is unsustainable..."  
𝕏 @raydalio · original_post · [View on X]  
  
Mar 5, 2026 · Bloomberg Interview  
"The question is whether escalation stabilizes..."  
▶ 12:15 · direct_quote · [View Source]

### Topic page enrichment

Topic pages should show claims from ALL sources (video + X) unified:

TOPIC: Macro · 42 claims from 12 people  
────────────────────────────────────────  
  
[Filter: All Sources] [Video Only] [X Only]  
  
Ray Dalio · Mar 13 · Dwarkesh Podcast  
"We are in the early stages..."  ▶ 45:22  
  
Ray Dalio · Mar 10 · X  
"The US debt trajectory..."  𝕏 @raydalio  
  
Stan Druckenmiller · Mar 8 · CNBC  
"I've closed my short-duration..."  ▶ 8:14

### Feed page source indicators

Summary cards in the feed should indicate when claims come from X vs. video:

KEY CLAIMS  
▸ "We are just a few years away..." — Dario Amodei · ▶ 12:45  
▸ "The investments in interpretability..." — Dario Amodei · ▶ 45:22  
▸ "Responsible scaling still matters" — Dario Amodei · 𝕏 Feb 12

### Test Criteria: Phase 4

•          bm x scan discovers new posts from favorite people with x_handles

•          Timeline scan deduplicates against existing posts

•          Person timeline mixes video and X claims sorted by date

•          Topic pages show claims from all sources

•          Feed summaries reference both video and X sources where applicable

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Appendix: Seed Data for X Handles

Add to people_seed.json during Phase 3 setup:

{"name": "Ray Dalio", "x_handle": "raydalio"},  
{"name": "Elon Musk", "x_handle": "elonmusk"},  
{"name": "Sam Altman", "x_handle": "sama"},  
{"name": "Dario Amodei", "x_handle": "DarioAmodei"},  
{"name": "Jensen Huang", "x_handle": null},  
{"name": "Marc Andreessen", "x_handle": "pmarca"},  
{"name": "Bill Ackman", "x_handle": "BillAckman"},  
{"name": "Chamath Palihapitiya", "x_handle": "chamath"},  
{"name": "Peter Thiel", "x_handle": null},  
{"name": "Bill Gurley", "x_handle": "bgurley"},  
{"name": "Keith Rabois", "x_handle": "rabois"},  
{"name": "Balaji Srinivasan", "x_handle": "balajis"},  
{"name": "Naval Ravikant", "x_handle": "naval"},  
{"name": "Garry Tan", "x_handle": "garrytan"},  
{"name": "Tyler Cowen", "x_handle": "tylercowen"},  
{"name": "Howard Marks", "x_handle": null},  
{"name": "Larry Summers", "x_handle": "LHSummers"},  
{"name": "Mohamed El-Erian", "x_handle": "elerianm"},  
{"name": "Tobi Lütke", "x_handle": "tobi"},  
{"name": "Satya Nadella", "x_handle": "satyanadella"},  
{"name": "Mark Zuckerberg", "x_handle": null},  
{"name": "Patrick Collison", "x_handle": "patrickc"},  
{"name": "John Collison", "x_handle": "collision"},  
{"name": "Brian Chesky", "x_handle": "bchesky"},  
{"name": "Andrej Karpathy", "x_handle": "karpathy"}

Note: Some handles may have changed. Verify during implementation. People with x_handle: null don’t have active X accounts or rarely post substantive content.

![](file:////Users/daniyarserikson/Library/Group%20Containers/UBF8T346G9.Office/TemporaryItems/msohtmlclip/clip_image001.png)

## Appendix: Migration Checklist

### Phase 1 (no migrations needed)

•          ☐ Update public.py to include source object in claims response

•          ☐ Update index.html claim cards with source citation row

•          ☐ Add topic filter parameter to claims endpoint

•          ☐ Make all topic badges clickable links in frontend

•          ☐ Deploy

### Phase 2

•          ☐ Create Alembic migration for favorites and episode_summaries tables

•          ☐ Implement src/pipeline/summaries.py

•          ☐ Add summary generation to end of enrichment pipeline

•          ☐ Implement /api/summaries/feed endpoint

•          ☐ Build Feed page in frontend

•          ☐ Add “Recent Appearances” to person detail

•          ☐ Add favorites CLI commands

•          ☐ Deploy + seed initial favorites

### Phase 3

•          ☐ Create Alembic migration: x_posts table, x_handle on people, x_post_id on claims, relax constraints

•          ☐ Implement src/pipeline/x_ingestion.py

•          ☐ Implement bm x add CLI command

•          ☐ Update source citation logic for dual-source

•          ☐ Update frontend claim cards for X source type

•          ☐ Add X posts section to person detail

•          ☐ Deploy + seed x_handles

### Phase 4

•          ☐ Implement bm x scan automated timeline scanning

•          ☐ Build mixed-source timeline on person detail

•          ☐ Add source filter to topic pages

•          ☐ Add X scanning to daily pipeline schedule

•          ☐ Deploy
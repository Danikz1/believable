# Believable Minds — Development Specification (Final)

**Status:** Implementation-ready — green-lit for Claude Code  
**Date:** March 2026  
**Build tool:** Claude Code, stage by stage  
**Runtime:** All-API until M5 Ultra 512GB arrives, then progressive local migration  
**Revision history:** v2 → v2.1 (Claude, ChatGPT, Gemini) → v2.2 (structural patches) → v2.2.1 (safety hardening) → Final (multi-provider API + consistency fixes)

---

## What This Document Is

A stage-by-stage development spec for Believable Minds, a standalone data production system that tracks what selected credible minds say, how their views evolve, and where they converge or diverge. It runs independently and exposes a query API for integration with Mukhtar.AI.

This is the implementation-ready final version. 14 core tables, 8 stages. See Appendix A for the full 51-change log across 5 review rounds.

---

## Design Principles

1. Each stage is independently testable with clear pass/fail criteria.
2. Local-first: runs on your machine, cloud migration is a later concern.
3. People registry is editable at all times — add, remove, re-tier people via CLI and web UI.
4. The system is a data producer — Mukhtar.AI is a data consumer via read-only API.
5. No LLM frameworks (LangChain, LangGraph) — direct API calls only.
6. PostgreSQL + pgvector for everything — no exotic databases.
7. Evidence-first: every published claim must trace back to a timestamped source.
8. Confidence is multi-dimensional: speaker certainty, attribution confidence, and extraction confidence are separate fields.
9. Multi-provider API strategy: use the best-value provider per task, with abstraction layers for future local migration.

---

## Monthly Cost Estimate (All-API)

| Task | Provider | Est. monthly |
|---|---|---|
| Claim extraction (Stage 5) | Qwen3.5-Plus API | ~$20 |
| Speaker ID (Stage 4) | Qwen3.5-Plus API | ~$3 |
| Position aggregation | Qwen3.5-Plus API | ~$3 |
| Brief generation (Stage 8) | Qwen3.5-Plus API | ~$2 |
| Divergence endpoint (Mukhtar.AI) | Claude Sonnet 4.6 | ~$5 |
| Embeddings | OpenAI text-embedding-3-small | ~$0.50 |
| Transcription (deep path) | Deepgram Nova-3 | ~$22 |
| Railway infrastructure | Railway Pro | ~$13 |
| **Total** | | **~$70/mo** |

**Future (M5 Ultra 512GB):** Transcription + LLM inference move local. Cost drops to ~$18/mo (Railway + embeddings + occasional Claude).

---

## Technical Stack

| Component | Technology | Notes |
|---|---|---|
| Language | Python 3.12+ | Single language for the entire pipeline |
| Web Framework | FastAPI | API server + web UI backend |
| Frontend | Next.js or SvelteKit | Light dashboard, deployable separately |
| Database | PostgreSQL 16 + pgvector | Local via Docker or Homebrew |
| Transcription | Deepgram Nova-3 (cloud) | Diarization included. ~$0.49/hr. WhisperX local when M5 Ultra available |
| LLM (enrichment) | Qwen3.5-Plus API (primary) | ~1/13th Claude cost, superior instruction-following (IFBench 76.5 vs Claude 58.0). Direct API calls with tool_use |
| LLM (Mukhtar.AI integration) | Claude Sonnet 4.6 API | For divergence endpoint and real-time user-facing queries where response quality matters most |
| LLM (fallback) | Claude Sonnet 4.6 API | Fallback if Qwen3.5-Plus degrades or is unavailable |
| Embeddings | OpenAI text-embedding-3-small | ~$0.50/month at scale. Configurable dimensions |
| YouTube discovery | yt-dlp (channel feeds) + YouTube Data API v3 (gap-fill only) | Channel monitoring is default. Search is gap-filling |
| Task scheduling | APScheduler or cron | Daily pipeline orchestration |
| Brief delivery | python-telegram-bot + SMTP | Telegram + email channels |
| Containerization | Docker Compose | PostgreSQL + optional services |

### Claude Code Instructions

When building each stage with Claude Code, include these directives:

- **ORM:** Use SQLAlchemy 2.0 with Alembic migrations. First migration must run `CREATE EXTENSION IF NOT EXISTS vector;` before creating tables.
- **LLM provider abstraction:** Build a thin adapter layer supporting both Qwen3.5-Plus (Alibaba Cloud) and Claude Sonnet 4.6 (Anthropic). Each pipeline stage specifies which provider via config. The adapter normalizes tool_use request/response formats across providers.
- **LLM calls:** Use tool_use (structured outputs) with `strict: true` for all extraction prompts on both providers. Define the claim schema as a JSON Schema tool definition — do not rely on "return JSON" instructions.
- **Prompt caching:** Enable prompt caching via `cache_control` on cacheable content blocks (system prompt + topic taxonomy). Both Anthropic and Alibaba Cloud support this.
- **Transcription:** Use Deepgram Nova-3 API with diarization for deep-path. yt-dlp captions for fast-path. Future: WhisperX local on M5 Ultra.
- **Testing:** pytest for each stage. Use fixtures with real YouTube video IDs for integration tests.

---

## Stage Map

| Stage | Name | Depends On | Deliverable |
|---|---|---|---|
| 1 | Database + People Registry | — | CLI to CRUD people, PostgreSQL schema live |
| 2 | YouTube Discovery & Scanning | Stage 1 | Daily scan finds new videos for tracked people |
| 3 | Transcript Extraction | Stage 2 | Transcripts stored with speaker diarization |
| 4 | Speaker Identification | Stage 3 | Speaker labels mapped to real names |
| 5 | LLM Enrichment & Claims | Stage 4 | Structured claims extracted with evidence spans |
| 6 | Query API | Stage 5 | Read-only public API + admin API |
| 7 | Web Dashboard | Stage 6 | Browser UI: people, claims, briefs, pipeline |
| 8 | Brief Generation & Delivery | Stage 5 | Daily briefs via Telegram, email, and web |

**Estimated total:** 4–6 weeks with Claude Code. Stages 1–3 are fastest (~1 week). Stages 4–5 require the most iteration (prompt engineering). Stages 6–8 are straightforward once data exists.

---

## Database Schema

### Table: people

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| name | TEXT NOT NULL | Display name: "Ray Dalio" |
| domain | TEXT | "Macro / Principles" |
| tier | INTEGER (1–3) | 1 = daily deep scan, 2 = podcast capture, 3 = passive |
| inclusion_notes | TEXT NOT NULL | **[NEW v2.1]** Why this person is tracked — editorial justification. Replaces the separate `why_they_matter` field |
| expertise_domains | TEXT[] | **[NEW v2.1]** Topics they are credible on: ["macro", "debt_cycles", "geopolitics"] |
| youtube_search_queries | TEXT[] | Array: ["Ray Dalio interview", "Ray Dalio keynote"] |
| active | BOOLEAN DEFAULT true | Soft delete |
| created_at | TIMESTAMPTZ | Auto |
| updated_at | TIMESTAMPTZ | Auto on update |

> **v2.2 change:** `why_they_matter` and `inclusion_notes` merged into single `inclusion_notes` field (ChatGPT: they were near-duplicates). `known_podcasts TEXT[]` removed — replaced by `channel_roles` junction table (see below). `expertise_domains` added in v2.1.

### Table: podcast_channels

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| youtube_channel_id | TEXT UNIQUE NOT NULL | YouTube channel ID |
| name | TEXT NOT NULL | "All-In Podcast" |
| tier | INTEGER (1–3) | Scanning priority |
| monitoring_mode | TEXT DEFAULT 'channel_feed' | **[NEW v2.1]** 'channel_feed' (default) or 'search_gap_fill' |
| uploads_playlist_id | TEXT | **[NEW v2.2.1]** YouTube uploads playlist ID (UU-prefixed). Cached for official API fallback path. Resolved during seed/setup |
| active | BOOLEAN DEFAULT true | Soft delete |
| created_at | TIMESTAMPTZ | Auto |

> **v2.2 change:** `known_hosts TEXT[]` removed. Host/guest relationships now live in `channel_roles` junction table, which foreign-keys to `people`. This eliminates name-string drift and enables reliable joins for speaker identification.

### Table: channel_roles (NEW v2.2)

**[NEW v2.2]** Maps people to channels with their role. Replaces `known_hosts TEXT[]` on channels and `known_podcasts TEXT[]` on people. Per ChatGPT follow-up: string arrays for identity relationships cause rename drift and weak joins.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| channel_id | UUID (FK) NOT NULL | References podcast_channels |
| person_id | UUID (FK) NOT NULL | References people |
| role | TEXT NOT NULL | 'host' / 'cohost' / 'frequent_guest' |
| UNIQUE | (channel_id, person_id, role) | One role per person per channel |

Example seed data:
- All-In Podcast → Chamath (host), Sacks (host), Friedberg (host), Calacanis (host)
- BG2Pod → Gerstner (host), Gurley (host)
- Lex Fridman Podcast → Lex Fridman (host)

### Table: videos

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| youtube_video_id | TEXT UNIQUE NOT NULL | Deduplication key |
| title | TEXT | Video title |
| podcast_channel_id | UUID (FK) | **[CHANGED v2.2.1]** References podcast_channels(id). NULL if video is from an untracked channel |
| source_channel_youtube_id | TEXT NOT NULL | **[NEW v2.2.1]** Raw YouTube channel ID. Always populated regardless of whether channel is tracked |
| published_at | TIMESTAMPTZ | Upload date |
| duration_seconds | INTEGER | Video length |
| description | TEXT | Video description |
| discovery_method | TEXT | **[RENAMED v2.1]** 'channel_feed' or 'search_gap_fill' or 'manual' |
| discovered_by_person_id | UUID (FK) | Who triggered discovery. NULL if channel feed |
| transcript_type | TEXT | 'deep' (WhisperX) or 'fast' (yt-dlp captions). Set by Stage 3 |
| status | TEXT DEFAULT 'discovered' NOT NULL | **[FIXED v2.2.1]** discovered → transcribed → identified → enriched / skipped / error (terminal states). Zero-tracked-speaker videos become 'skipped', never 'enriched' |
| skip_reason | TEXT | **[NEW v2.2]** NULL unless status='skipped'. E.g., 'no_tracked_speakers', 'duplicate_content', 'non_english' |
| error_message | TEXT | NULL unless status='error' |
| retry_count | INTEGER DEFAULT 0 | Max 3 before manual review |
| created_at | TIMESTAMPTZ | Auto |

### Table: transcript_runs (NEW v2.2)

**[NEW v2.2]** Per ChatGPT follow-up: the fast→deep upgrade path and retries need durable audit trail. Without this, replacing a fast transcript with a deep one loses the original, and failed retries have no record. Segments link to a run, not directly to a video.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| video_id | UUID (FK) NOT NULL | References videos |
| mode | TEXT NOT NULL | 'caption' / 'asr_plain' / 'asr_diarized' |
| provider | TEXT NOT NULL | 'yt-dlp' / 'whisperx' / 'whisper' |
| provider_model | TEXT | e.g., 'large-v3' for WhisperX |
| status | TEXT NOT NULL | 'created' / 'running' / 'succeeded' / 'failed' |
| language_code | TEXT | e.g., 'en' |
| speaker_config | JSONB | **[FIXED Final]** Replaces single integer. Structure: `{"mode": "exact", "count": 5}` or `{"mode": "range", "min": 3, "max": 6}`. Passed to Deepgram/WhisperX for diarization |
| started_at | TIMESTAMPTZ | Auto |
| completed_at | TIMESTAMPTZ | NULL until finished |
| error_message | TEXT | NULL unless failed |

> Multiple runs per video are expected (fast caption run, then deep WhisperX upgrade). Old runs are never deleted — only the latest successful run is used for downstream processing.

### Table: transcript_segments

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| transcript_run_id | UUID (FK) NOT NULL | **[CHANGED v2.2]** References transcript_runs (not videos directly) |
| video_id | UUID (FK) | References videos (denormalized for fast queries) |
| segment_index | INTEGER NOT NULL | Ordering within the run |
| speaker_label | TEXT | "SPEAKER_00" (WhisperX output) or NULL (fast path) |
| speaker_name | TEXT | Resolved name (NULL until Stage 4) |
| person_id | UUID (FK) | References people (NULL until Stage 4) |
| start_ms | BIGINT NOT NULL | **[CHANGED v2.1]** Milliseconds from start (WhisperX native format) |
| end_ms | BIGINT NOT NULL | Milliseconds from start |
| text | TEXT NOT NULL | Segment text |
| source_kind | TEXT NOT NULL | **[NEW v2.1]** 'caption', 'asr', or 'asr_diarized' |
| created_at | TIMESTAMPTZ | Auto |
| UNIQUE | (transcript_run_id, segment_index) | Ordered within run |
| CHECK | start_ms < end_ms | Timestamps must be valid |

### Table: video_people (junction)

Maps which tracked people appear in a video. Populated by Stage 4.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| video_id | UUID (FK) | References videos |
| person_id | UUID (FK) | References people |
| role | TEXT | 'host' / 'guest' / 'unknown' |
| confidence | NUMERIC(4,3) | How confident the identification is (0.0–1.0) |
| identified_via | TEXT | 'known_host' / 'diarization_llm' / 'metadata_only' / 'manual' |
| UNIQUE | (video_id, person_id) | One record per person per video |

### Table: claims

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| person_id | UUID (FK) | Who made this claim |
| video_id | UUID (FK) | Source video |
| claim_text | TEXT NOT NULL | The structured claim |
| reasoning_text | TEXT | **[NEW v2.1]** The "why" behind the position. Required for publication |
| claim_type | TEXT | prediction / opinion / recommendation / observation / analysis |
| speaker_certainty | TEXT | **[RENAMED v2.1]** How definitive the speaker sounded: definitive / high / moderate / speculative / hedged |
| attribution_confidence | NUMERIC(4,3) | **[NEW v2.1]** System confidence that the right person is attributed (0.0–1.0) |
| extraction_confidence | NUMERIC(4,3) | **[NEW v2.1]** System confidence that the claim accurately represents what was said (0.0–1.0) |
| trust_level | TEXT NOT NULL | **[CHANGED v2.2.1]** No default — must be set explicitly by application logic. 'high' (diarized + strong attribution) / 'medium' (diarized but uncertain speaker mapping) / 'low' (caption-only, inferred attribution). See trust derivation rules below |
| topics | TEXT[] | ["macro", "interest_rates", "duration"] — **denormalized cache**. Source of truth is `claim_topics` |
| sentiment | TEXT | bullish / bearish / neutral / mixed |
| temporal_marker | TEXT | "next 12 months", "long term", etc. |
| review_status | TEXT NOT NULL | **[CHANGED v2.2.1]** No default — must be set explicitly by application logic. 'approved' / 'pending_review' / 'rejected'. See auto-review rules below |

| created_at | TIMESTAMPTZ | Auto |
| updated_at | TIMESTAMPTZ | Auto |

> **v2.2.1 trust derivation rules:** Trust depends on the **final evidence path**, not the initial routing choice. A fast-path video that gets upgraded and re-extracted from a deep diarized run should become 'high' if attribution is strong.
>
> | Final transcript mode | Attribution confidence | trust_level |
> |---|---|---|
> | asr_diarized + attribution ≥ 0.80 | Strong | high |
> | asr_diarized + attribution < 0.80 | Uncertain speaker mapping | medium |
> | caption-only (no upgrade) | Inferred from metadata | low |
>
> There is no "fast-path upgraded" trust level — if the video was upgraded and re-processed via WhisperX, it's evaluated the same as any deep-path video.

### Table: topics (NEW v2.2)

**[NEW v2.2]** Per ChatGPT follow-up: storing taxonomy only in the LLM prompt while claims use `TEXT[]` invites string drift and makes topic renames painful. This flat table is the source of truth. Seeded with ~30 topics for MVP domains.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| slug | TEXT UNIQUE NOT NULL | 'interest_rates', 'ai_infrastructure', etc. |
| name | TEXT NOT NULL | Human-readable: "Interest Rates" |
| parent_id | UUID (FK) | References topics — for future hierarchy. NULL for MVP |
| active | BOOLEAN DEFAULT true | Soft delete |
| created_at | TIMESTAMPTZ | Auto |

MVP seed topics (from the taxonomy in Stage 5 system prompt):

```
macro, interest_rates, duration, inflation, fiscal_policy, debt_cycles, 
geopolitics, us_china, ai_infrastructure, ai_safety, ai_regulation, 
ai_open_source, inference_compute, enterprise_ai, saas_pricing, 
crypto, stablecoins, payments, venture_capital, startup_formation,
energy, climate, real_estate, labor_market, healthcare, defense, 
space, creator_economy, platform_dynamics, value_investing
```

### Table: claim_topics (NEW v2.2)

**[NEW v2.2]** Junction table linking claims to normalized topics. Source of truth for which topics a claim covers. The `claims.topics TEXT[]` field is kept as a denormalized cache for fast reads.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| claim_id | UUID (FK) NOT NULL | References claims |
| topic_id | UUID (FK) NOT NULL | References topics |
| UNIQUE | (claim_id, topic_id) | No duplicate links |

> **v2.2 note on topic workflow:** Stage 5 enrichment maps claims to existing topic slugs. If the LLM suggests a topic not in the taxonomy, it's stored in `claims.topics[]` as a raw string AND creates an entry in the review queue (see auto-review rules). New topics are only added to the `topics` table after operator approval — either via CLI (`bm topics add`) or the review queue UI.

### Table: claim_evidence

**[NEW v2.1]** Links claims to specific transcript evidence with timestamps. Per ChatGPT/Gemini feedback: every published claim must be auditable back to source.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| claim_id | UUID (FK) NOT NULL | References claims |
| segment_id | UUID (FK) NOT NULL | **[CHANGED v2.2.1]** References transcript_segments. Always populated — the tool schema requires segment_id in evidence spans, and even fast-path captions are stored as segments |
| evidence_order | INTEGER NOT NULL | 1, 2, 3... for multi-segment claims |
| quote_text | TEXT NOT NULL | The actual quoted text |
| start_ms | BIGINT NOT NULL | Start timestamp in video |
| end_ms | BIGINT NOT NULL | End timestamp in video |
| quote_type | TEXT NOT NULL | 'direct_quote' / 'paraphrase' / 'multi_segment_synthesis' |
| CHECK | start_ms < end_ms | Timestamps must be valid |

### Table: claim_embeddings

**[CHANGED v2.1]** Separated from claims table per Gemini feedback. Uses configurable dimensions.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| claim_id | UUID (FK) | References claims |
| model_name | TEXT NOT NULL | e.g., 'text-embedding-3-small' |
| dimensions | INTEGER NOT NULL | e.g., 1536 or 1024 |
| embedding | vector NOT NULL | **[CHANGED v2.1]** No hardcoded dimension — use `vector` not `vector(1536)` |
| created_at | TIMESTAMPTZ | Auto |
| UNIQUE | (claim_id, model_name) | One embedding per model per claim |
| CHECK | dimensions = vector_dims(embedding) | Prevents silent dimension drift |

> **v2.2 note on indexing strategy:** Do NOT create approximate indexes in Stage 1. Use exact search first. When data volume makes exact search slow, add HNSW indexes (not IVFFlat — HNSW works on empty tables and has better recall). **Important:** HNSW requires uniform dimensions per index. MVP standardizes on one dimension value (controlled by `EMBEDDING_DIMENSIONS` env var, default 1536). If multiple embedding models are used later, create per-model partial indexes: `CREATE INDEX ... ON claim_embeddings USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE dimensions = 1536;`

### Table: person_topic_positions

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| person_id | UUID (FK) | References people |
| topic_id | UUID (FK) NOT NULL | **[CHANGED v2.2.1]** References topics table. Replaces TEXT field to prevent drift |
| current_position | TEXT | Summary of their latest view |
| last_updated | TIMESTAMPTZ | When this was last refreshed |
| claim_count | INTEGER | How many approved claims inform this position |
| UNIQUE | (person_id, topic_id) | One active position per person per topic |

> **v2.2.1 change:** Position aggregation rebuilds from `review_status='approved'` claims only. A `pending_review` claim cannot update the public position surface. This prevents unreviewed material from leaking into topic views and consensus endpoints.

### Table: position_history_log

**[NEW v2.1]** Replaces the JSONB `position_history` array. Append-only.

| Column | Type | Notes |
|---|---|---|
| id | UUID (PK) | Auto-generated |
| person_id | UUID (FK) | References people |
| topic_id | UUID (FK) NOT NULL | **[CHANGED v2.2.1]** References topics table. Replaces TEXT field |
| position_summary | TEXT NOT NULL | What they believed at this point |
| source_claim_id | UUID (FK) | The claim that triggered this entry |
| is_shift | BOOLEAN DEFAULT false | Whether this contradicts the previous position |
| recorded_at | TIMESTAMPTZ | Auto |

---

## Stage 1: Database + People Registry

**Goal:** PostgreSQL schema is live. People can be added, edited, removed, and listed via CLI. Podcast channels are seeded. This is the foundation everything else builds on.

### CLI Interface

- `bm people list` — Show all tracked people with tier, domain, active status
- `bm people add` — Interactive: name, domain, tier, inclusion_notes, expertise_domains, search queries
- `bm people edit <id>` — Edit any field
- `bm people remove <id>` — Soft-delete (set active=false)
- `bm people import <file>` — Bulk import from JSON/YAML
- `bm channels list / add / edit / remove` — Same CRUD for podcast channels
- `bm channels roles list / add / remove` — **[NEW v2.2]** Manage channel_roles (link people to channels as host/cohost/frequent_guest)
- `bm topics list / add / remove` — **[NEW v2.2]** Manage the topic taxonomy
- `bm seed` — Seed the database with initial people + channels + channel_roles + topics from seed data files

### Seed Data: People

All 46 people from the architecture document, with the following fix:

> **v2.1 fix:** "Patrick & John Collison" split into two separate records — "Patrick Collison" and "John Collison" — each with their own ID, expertise domains, and claims tracking. Total: **47 people**.

Every seeded person MUST have `inclusion_notes` (why they're tracked) and `expertise_domains` (what topics they're credible on). Example:

```json
{
  "name": "Ray Dalio",
  "domain": "Macro / Principles",
  "tier": 1,
  "inclusion_notes": "History's most successful macro investor. His frameworks on debt cycles, economic machinery, and changing world order are foundational to understanding macro regimes. Founded Bridgewater, the world's largest hedge fund.",
  "expertise_domains": ["macro", "debt_cycles", "geopolitics", "economic_history", "principles"],
  "youtube_search_queries": ["Ray Dalio interview", "Ray Dalio keynote"]
}
```

### Seed Data: Podcast Channels (33 channels)

| Channel Name | Tier | Monitoring Mode |
|---|---|---|
| All-In Podcast | 1 | channel_feed |
| BG2Pod | 1 | channel_feed |
| Lex Fridman Podcast | 1 | channel_feed |
| Acquired | 1 | channel_feed |
| The Knowledge Project | 1 | channel_feed |
| Invest Like the Best | 1 | channel_feed |
| Dwarkesh Podcast | 1 | channel_feed |
| No Priors | 1 | channel_feed |
| 20VC | 1 | channel_feed |
| My First Million | 2 | channel_feed |
| Stratechery / Dithering | 2 | channel_feed |
| The Prof G Pod | 2 | channel_feed |
| Bloomberg Odd Lots | 2 | channel_feed |
| CNBC Squawk Box | 2 | channel_feed |
| The Tim Ferriss Show | 2 | channel_feed |
| Bankless | 2 | channel_feed |
| a16z Podcast | 2 | channel_feed |
| Y Combinator YouTube | 2 | channel_feed |
| Conversations with Tyler | 2 | channel_feed |
| Founders Podcast | 2 | channel_feed |
| Capital Allocators | 2 | channel_feed |
| The Lunar Society | 2 | channel_feed |
| Pirate Wires | 2 | channel_feed |
| Khosla Ventures YouTube | 3 | channel_feed |
| Greylock YouTube | 3 | channel_feed |
| GS Talks | 3 | channel_feed |
| Bridgewater YouTube | 3 | channel_feed |
| Oaktree Capital | 3 | channel_feed |
| TED / TEDx | 3 | search_gap_fill |
| Stanford HAI / eCorner | 3 | channel_feed |
| NVIDIA GTC | 3 | channel_feed |
| Berkshire Hathaway | 3 | channel_feed |
| Stripe Sessions | 3 | channel_feed |

> Host/guest relationships are seeded separately via `channel_roles`. See the `channel_roles` table definition for examples.

> **v2.1 note:** YouTube channel IDs must be resolved via the YouTube Data API or yt-dlp during Stage 1 setup. Store the lookup script in `tools/resolve_channel_ids.py` for future additions.

### Test Criteria: Stage 1 Pass

- PostgreSQL is running (Docker Compose) with all tables created via Alembic migration
- First migration includes `CREATE EXTENSION IF NOT EXISTS vector;`
- `bm seed` populates all 47 people, 33 channels, ~30 topics, and channel_roles
- Every seeded person has `inclusion_notes` and `expertise_domains` populated
- Patrick Collison and John Collison are separate records
- Channel_roles correctly link hosts to their channels (e.g., All-In → 4 hosts)
- Topics table seeded with MVP taxonomy (~30 slugs)
- `bm people list` shows all seeded people with correct tiers
- `bm people add` / `edit` / `remove` work correctly
- `bm people import` loads a JSON file with 5+ people
- `bm topics list` shows seeded topics
- Database has proper indexes on video IDs, person_id, and topics
- No approximate vector indexes created yet (exact search only)

---

## Stage 2: YouTube Discovery & Scanning

**Goal:** The system discovers new videos daily. Channel feed monitoring is the default path. YouTube API search is gap-filling only.

### Discovery Priority (CHANGED in v2.1)

> **v2.1 change:** Per ChatGPT and Gemini feedback, channel monitoring is the default. Search is gap-filling. This fixes the quota trap: 28 Tier 1 people × 3 searches × 100 units = 8,400 units/day was unsustainable.

**Mode 1: Channel Feed Monitor (default — zero API quota)**

For each active podcast channel, check for new episodes via yt-dlp:
- `yt-dlp --flat-playlist` to list recent videos from the channel's uploads playlist
- Compare against known video IDs for deduplication
- Run daily for all channels
- Videos get `discovery_method='channel_feed'`

**Mode 2: Person Search (gap-fill only — limited API quota)**

For Tier 1 people who **lack reliable channel coverage** (e.g., they appear across many channels unpredictably):
- Search YouTube Data API v3 with `type=video` filter (Gemini: default returns channels/playlists too)
- **Use `publishedAfter` API parameter for recency** (v2.2: not year strings in query text — per ChatGPT, API date filters are more reliable and don't rot)
- Budget: max **20 searches/day total** (~2,000 quota units), not per-person
- Query format: person name + optional topic, e.g., `q="Ray Dalio interview"` with `publishedAfter=2026-03-07T00:00:00Z`
- Videos get `discovery_method='search_gap_fill'`

**Quota budget:**

| Mode | Daily cost | Notes |
|---|---|---|
| Channel feed monitor | 0 units | yt-dlp, no API |
| Gap-fill search | ~2,000 units | 20 searches × 100 units |
| Video metadata lookups | ~500 units | For new discoveries |
| **Total** | **~2,500 units** | Well within 10,000 limit |

### CLI Commands

- `bm scan --mode channel` — Check all active channels for new videos
- `bm scan --mode search` — Run gap-fill search for people who need it
- `bm scan --status` — Show scan results: videos found, quota used, errors
- `bm scan --mode search --person "Ray Dalio"` — Targeted search for one person

### Test Criteria: Stage 2 Pass

- `bm scan --mode channel` discovers at least 1 real episode from a tracked channel
- `bm scan --mode search` discovers at least 1 real video (within quota budget)
- Running scan twice does not create duplicate video records
- `bm scan --status` shows accurate counts
- YouTube API quota usage is tracked and logged
- Gap-fill search uses `type=video` parameter and `publishedAfter` for recency
- Search queries contain person/topic terms only — no year strings in query text
- Videos stored with correct `discovery_method` and metadata

---

## Stage 3: Transcript Extraction

**Goal:** Extract transcripts for all discovered videos using WhisperX (deep path) or yt-dlp captions (fast path), with automatic upgrade for multi-speaker content.

### Deep Path (Deepgram Nova-3 / WhisperX)

**[CHANGED Final]** Use Deepgram Nova-3 API for cloud transcription with diarization. When M5 Ultra is available, switch to local WhisperX. Both produce timestamped, speaker-diarized output.

- Create a `transcript_runs` record with `mode='asr_diarized'`, `provider='deepgram'` (or `'whisperx'` when local)
- Download audio via yt-dlp (for Deepgram: upload audio to API)
- **Cloud (current):** Send audio to Deepgram Nova-3 API with `diarize=true` and `smart_format=true`. Returns timestamped, speaker-labeled transcript
- **Local (future M5 Ultra):** Pin WhisperX version in pyproject.toml (e.g., `whisperx>=3.6.1,<4.0`). Run locally — output: same format
- **Speaker count hints** stored in `transcript_runs.speaker_config` JSONB:
  - For fixed-format shows with known host count + 1 guest: `{"mode": "exact", "count": 5}`. E.g., All-In episode = 4 hosts + 1 guest
  - For loose panels or variable formats: `{"mode": "range", "min": 3, "max": 6}`. E.g., conference panel
  - Derive from `channel_roles` count + episode metadata
  - Passed to Deepgram as `diarize_config` or to WhisperX as `num_speakers`/`min_speakers`/`max_speakers`
- Store segments linked to the `transcript_run_id`, with `speaker_label`, `start_ms`, `end_ms`, `source_kind='asr_diarized'`
- Delete audio after successful processing
- Set `transcript_type='deep'` on video record
- If run fails, mark transcript_run as 'failed' with error_message — video status stays at 'discovered' for retry
- **Provider abstraction:** `src/pipeline/transcription.py` implements a `TranscriptionProvider` interface with `DeepgramProvider` and `WhisperXProvider` classes. Switch via `TRANSCRIPTION_PROVIDER` env var

### yt-dlp Captions (Fast Path)

- Create a `transcript_runs` record with `mode='caption'`, `provider='yt-dlp'`
- Pull YouTube auto-generated captions: `yt-dlp --write-auto-sub --sub-lang en --skip-download`
- Parse VTT/SRT into timestamped chunks (~30s windows)
- Store segments linked to the `transcript_run_id`, with `speaker_label=NULL`, `source_kind='caption'`
- Set `transcript_type='fast'` on video record

### Path Selection Logic

| Condition | Path |
|---|---|
| Tier 1 person appearances (search gap-fill) | Deep |
| Tier 1 podcast channel episodes | Deep |
| Tier 2 channel with ≤2 known hosts + 1 guest | Deep |
| Everything else | Fast |

### Automatic Upgrade Rule (NEW in v2.1, broadened in v2.2.1)

> **v2.2.1 change:** The original rule "upgrade when 2+ tracked people detected" missed a risky case: one tracked guest + one untracked host/interviewer can still contaminate attribution. Broadened rule:

After Stage 4's metadata identification pass, upgrade a fast-path video to deep-path processing if **any** of these conditions are true:
- **2+ tracked people** detected in the video
- **1 tracked person + conversational/interview format** (detected from title/description containing "interview", "conversation", "podcast", or channel is a known podcast)
- **1 tracked person + channel has known hosts** in `channel_roles` (implies multi-speaker content)

The only fast-path videos that stay fast are: solo presentations, keynotes, or monologues where the tracked person is provably the sole speaker. Everything conversational upgrades.

### Deferred: Gemini API Path

The architecture doc describes a Gemini API path for content where visual context matters (slides, charts during earnings presentations). Deferred to a future stage. The fast/deep paths cover all MVP needs.

### CLI Commands

- `bm transcribe --pending` — Process all discovered videos
- `bm transcribe <video_id>` — Process a specific video
- `bm transcribe <video_id> --deep` — Force deep path
- `bm transcribe --status` — Pipeline status

### Test Criteria: Stage 3 Pass

- Fast path: creates a transcript_run record with mode='caption', stores segments linked to run
- Deep path: creates a transcript_run record with mode='asr_diarized', Deepgram returns speaker-diarized segments
- `speaker_config` JSONB correctly set: exact count for fixed-format shows, range for variable panels
- A video can hold multiple transcript_runs (fast then deep upgrade)
- Failed runs remain visible with error_message; video status stays 'discovered' for retry
- Audio files cleaned up after processing (or not downloaded for Deepgram streaming)
- `transcript_type` correctly set on video record
- Segments reference transcript_run_id, not video_id directly (video_id is denormalized)
- Provider abstraction: switching `TRANSCRIPTION_PROVIDER` env var from 'deepgram' to 'whisperx' produces same output format

---

## Error Handling & Retry Strategy (Applies to All Stages)

All pipeline stages that call external services must follow this pattern:

| Error Type | Behavior | Max Retries |
|---|---|---|
| Rate limit (429) | Exponential backoff: 2s, 4s, 8s, 16s, 32s | 5 |
| Timeout / network error | Retry after 10s | 3 |
| API error (500+) | Retry after 30s | 3 |
| Bad response / parse error | Log full response, retry once, mark error | 1 |
| Quota exhausted (YouTube) | Stop scanning, resume next day | 0 |
| Auth error (401/403) | Stop immediately, surface in dashboard | 0 |

- **Non-blocking:** A failed video does NOT block the rest of the pipeline.
- **Manual retry:** `bm retry --errors` re-queues all error-status videos.
- **Cost safety:** `bm enrich --cost` shows estimated API cost BEFORE processing. `--max-cost $20` flag halts if exceeded.

---

## Stage 4: Speaker Identification

**Goal:** For deep-path videos, map speaker labels to real names. For fast-path videos, identify which tracked people appear (metadata-only). Populate `video_people` for all videos.

### Mode A: Deep-Path Videos (diarized)

**Method 1: Known Host Matching via channel_roles**

For videos from tracked podcast channels, query `channel_roles` for people with role='host'/'cohost'. If All-In has 4 hosts in `channel_roles` and WhisperX finds 5 speakers, 4 are hosts and the 5th is the guest. Use title/description to identify the guest.

**Method 2: Stratified LLM Pass**

> **v2.1 change:** Per Gemini — do NOT sample the first 2–3 minutes chronologically. Podcast intros are 3–5 minutes of ads and housekeeping. Instead:

1. Group transcript by `speaker_label`
2. For each unique speaker, extract the **first 5–10 substantive utterances** (skip utterances under 10 words)
3. Concatenate into a stratified sample: "Speaker SPEAKER_00 said: [utterance 1]... [utterance 5]. Speaker SPEAKER_01 said: [utterance 1]... [utterance 5]."
4. Send this stratified sample + video title/description/channel name to Claude API
5. Claude maps speaker labels to names based on content, speaking patterns, and metadata

**Method 3: Manual Override**

`bm identify <video_id> --speaker SPEAKER_00 --name "Ray Dalio"`

### Mode B: Fast-Path Videos (no diarization)

- Run metadata-only LLM pass: title, description, channel name → who appears?
- Insert rows into `video_people` for tracked people identified
- Do NOT attribute individual transcript segments
- If video is conversational/interview-style, or has known hosts in `channel_roles`, or has 2+ tracked people: trigger automatic upgrade to deep path (see Stage 3 broadened rule)

### Handling Zero Tracked People

If no tracked people found in a video:
- Set video status to **'skipped'** with `skip_reason='no_tracked_speakers'`
- Do NOT set to 'identified' or 'enriched' — a skipped video is a terminal state
- These appear in dashboard under "Processed — No Tracked Speakers"
- Can be re-processed if a non-tracked person later becomes tracked (`bm retry --skipped`)

### Test Criteria: Stage 4 Pass

- Deep-path: correctly identifies hosts via `channel_roles` junction table
- Deep-path: stratified sampling sends substantive utterances per speaker, not chronological intro
- Deep-path: guest identified from title/description context
- Fast-path: metadata pass identifies tracked people
- Fast-path: conversational videos with tracked person trigger upgrade to deep path (broadened rule)
- Zero tracked people: video status='skipped', skip_reason='no_tracked_speakers'
- Manual override works
- `video_people` populated for all identified videos
- Low-confidence identifications flagged in `bm identify --review`

---

## Stage 5: LLM Enrichment & Claim Extraction

**Goal:** Extract structured claims with evidence spans, reasoning, and multi-dimensional confidence.

### Enrichment Implementation

For each identified video, process according to transcript type:

**Deep-path videos:** Batch transcript segments by tracked speaker (using `person_id` from speaker attribution) and send to the LLM with tool_use.

**Fast-path videos (caption-only, not upgraded):** These may only proceed to extraction if the tracked person is **provably the sole speaker** (e.g., keynote, solo podcast). In that case, all caption segments inherit that person's `person_id` before extraction. If the video is conversational but was not upgraded (edge case), skip extraction entirely — mark as 'skipped' with `skip_reason='fast_path_multi_speaker_not_upgraded'`.

Both paths use Qwen3.5-Plus API as the primary provider (Claude Sonnet 4.6 as fallback).

> **v2.1 change:** Per Gemini — use Anthropic's tool_use (structured outputs) instead of "return JSON" prompting. Define the claim schema as a tool definition. This eliminates parse errors and retry loops.

> **v2.1 change:** Per Gemini — enable Anthropic prompt caching for the system prompt + topic taxonomy. Reduces Stage 5 costs by 50–80%.

### Tool Definition for Claude

> **v2.2 change:** Per ChatGPT follow-up — the tool must return segment IDs and timestamps, not just quote text. Otherwise Claude Code invents a brittle alignment layer. Pass segments with IDs/timestamps into the prompt; require the tool to cite them back.

**Input format** (in the user message, alongside the system prompt + taxonomy):

```
TRANSCRIPT SEGMENTS FOR [Person Name]:
[seg_id: abc123 | 01:42.300–01:58.100] "The fiscal dynamics changed in Q1..."
[seg_id: def456 | 01:58.100–02:15.700] "Treasury issuance came in below expectations..."
[seg_id: ghi789 | 02:15.700–02:31.400] "If spending restraint holds through Q2..."
```

**Tool definition:**

```json
{
  "name": "extract_claims",
  "description": "Extract structured claims from transcript segments. Cite segment IDs for evidence.",
  "strict": true,
  "input_schema": {
    "type": "object",
    "properties": {
      "claims": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "claim_text": { "type": "string" },
            "reasoning_text": { "type": "string" },
            "claim_type": { "type": "string", "enum": ["prediction", "opinion", "recommendation", "observation", "analysis"] },
            "speaker_certainty": { "type": "string", "enum": ["definitive", "high", "moderate", "speculative", "hedged"] },
            "extraction_confidence": { "type": "number", "description": "0.0-1.0: how well this claim represents the source material" },
            "topics": { "type": "array", "items": { "type": "string" }, "description": "Use slugs from the provided taxonomy. Flag new topics explicitly." },
            "sentiment": { "type": "string", "enum": ["bullish", "bearish", "neutral", "mixed"] },
            "temporal_marker": { "type": "string" },
            "evidence_spans": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "segment_id": { "type": "string", "description": "The seg_id from the input transcript" },
                  "quote_text": { "type": "string" },
                  "start_ms": { "type": "integer" },
                  "end_ms": { "type": "integer" },
                  "quote_type": { "type": "string", "enum": ["direct_quote", "paraphrase", "multi_segment_synthesis"] }
                },
                "required": ["segment_id", "quote_text", "start_ms", "end_ms", "quote_type"]
              }
            }
          },
          "required": ["claim_text", "reasoning_text", "claim_type", "extraction_confidence", "topics", "evidence_spans"]
        }
      }
    }
  }
}
```

> The tool now returns `segment_id`, `start_ms`, `end_ms`, and `extraction_confidence` directly — no post-hoc alignment needed. Claude Code maps these directly to `claim_evidence` rows.

### Topic Taxonomy (in system prompt)

Pass a controlled topic list in the system prompt. The LLM must map claims to existing topics first, only suggesting new topics when nothing fits:

```
TOPICS: macro, interest_rates, duration, inflation, fiscal_policy, debt_cycles, 
geopolitics, us_china, ai_infrastructure, ai_safety, ai_regulation, 
ai_open_source, inference_compute, enterprise_ai, saas_pricing, 
crypto, stablecoins, payments, venture_capital, startup_formation,
energy, climate, real_estate, labor_market, healthcare, defense, 
space, creator_economy, platform_dynamics, value_investing
```

### Confidence Assignment

| Field | How it's set |
|---|---|
| `speaker_certainty` | From LLM extraction: how definitive the speaker sounded |
| `attribution_confidence` | Derived from `identified_via`: known_host=0.95, diarization_llm=0.85, metadata_only=0.50, manual=1.0 |
| `extraction_confidence` | From LLM tool output (required field in tool schema) |
| `trust_level` | Derived from final evidence path + attribution_confidence. See trust derivation rules in Schema section |

### Auto-Review Rules

> **v2.2 change:** Per ChatGPT follow-up — rule precedence was undefined. A low-trust position shift was ambiguously both `pending_review` and `approved`. New rule: **trust_level always wins**. Shifts only auto-approve when trust is already high AND the topic is already in taxonomy.

**Precedence order (first match wins):**

| Priority | Condition | review_status |
|---|---|---|
| 1 | trust_level = 'low' | pending_review (always, regardless of other conditions) |
| 2 | Topic not in taxonomy | pending_review (unknown topic needs operator approval) |
| 3 | trust_level = 'medium' + position shift detected | pending_review (shift + uncertain attribution = needs eyes) |
| 4 | trust_level = 'medium' + no shift | approved (labeled as medium trust) |
| 5 | trust_level = 'high' + position shift detected | approved + is_shift=true in position_history_log (flagged for brief) |
| 6 | trust_level = 'high' + no shift | approved (default path) |

### Evidence Span Storage

Every claim MUST have at least one row in `claim_evidence`:
- Link to the transcript segment(s) that support the claim
- Store the quoted text and start/end timestamps
- Label as direct_quote, paraphrase, or multi_segment_synthesis

### Embedding Generation

- Use OpenAI text-embedding-3-small with configurable dimensions
- Embed `claim_text + reasoning_text` combined (reasoning contains the semantic richness)
- Store in `claim_embeddings` table with model metadata
- No approximate indexes yet — exact search until data volume requires it

### Position Aggregation

- **Approved claims only:** Position aggregation rebuilds from `review_status='approved'` claims. Pending claims cannot update public positions.
- **Fan-out by topic:** A claim with topics in `claim_topics` creates/updates one `person_topic_positions` row per topic_id
- For each person+topic, regenerate `current_position` via a short LLM call
- Append to `position_history_log` with source claim ID and topic_id FK
- Detect position shifts: if new position contradicts previous, set `is_shift=true`

### Test Criteria: Stage 5 Pass

- Extracts ≥3 structured claims from a real identified transcript
- Every claim has `reasoning_text` populated
- Every claim has ≥1 row in `claim_evidence` with segment_id, start_ms, end_ms (all NOT NULL)
- Tool_use with `strict: true` returns structured output without parse errors
- Tool output includes `segment_id` references matching input transcript segment IDs
- `trust_level` and `review_status` are set explicitly (no defaults) per trust derivation and review rules
- `trust_level` derived from final evidence path, not initial routing choice
- `claim_topics` junction populated; `claims.topics[]` kept in sync as denormalized cache
- Position aggregation rebuilds from approved claims only
- `position_history_log` populated with topic_id FK (not TEXT)
- Position shifts detected and flagged
- Prompt caching via `cache_control` reduces cost for batch enrichment
- API cost logged per video

---

## Stage 6: Query API

**Goal:** FastAPI server with read-only public API and separate admin API.

> **v2.1 change:** Per ChatGPT — split into public read API and protected admin API. Mukhtar.AI only sees the read surface.

### Public API (read-only)

> **v2.2 change:** Per ChatGPT follow-up — all public endpoints default to `review_status=approved`. Pending and rejected items are only visible via the admin API. This prevents Mukhtar.AI from pulling unreviewed low-trust claims into the context assembly.

| Method | Path | Description |
|---|---|---|
| GET | /api/people | List tracked people (filter by tier, domain, active) |
| GET | /api/people/{id} | Person detail with claims, positions, expertise |
| GET | /api/claims | Search **approved** claims (filter by person, topic, date, confidence, type, trust_level). Default: `review_status=approved` |
| GET | /api/claims/search | Semantic search across **approved** claims only |
| GET | /api/claims/{id} | Claim detail with evidence spans and source video |
| GET | /api/topics | List all topics with claim counts |
| GET | /api/topics/{topic}/positions | All positions on a topic, grouped by person |
| GET | /api/topics/{topic}/consensus | Consensus view with agreement/disagreement |
| GET | /api/briefs/latest | Latest published brief |
| GET | /api/pipeline/status | Pipeline health (read-only view) |

### Admin API (protected)

| Method | Path | Description |
|---|---|---|
| POST | /admin/pipeline/trigger/{stage} | Trigger a pipeline stage |
| POST | /admin/claims/{id}/review | Approve or reject a pending claim |
| POST | /admin/retry/errors | Re-queue all error-status videos |
| POST | /admin/people | Add a person (also available via CLI) |
| PUT | /admin/people/{id} | Update a person |

### Mukhtar.AI Integration Endpoints

**POST /api/intelligence/relevant**

Request: `{ topics: ["interest_rates", "duration"], max_results: 5, min_confidence: 0.7, days_back: 30 }`

Response: Ranked claims with person_name, claim_text, reasoning, confidence, date, source_url, trust_level

**POST /api/intelligence/divergence**

> **v2.1 change:** Per Gemini — this endpoint CANNOT be pure vector search. "Rates will be cut" and "rates will be hiked" have ~0.95 cosine similarity. Must be a two-step process:

1. Vector search for top 20 topically relevant claims
2. Claude API call classifying each claim as agrees/disagrees/nuanced relative to the user's stated position

Request: `{ position: "AI infrastructure spending is in a bubble", topic: "ai_infrastructure" }`

Response: `{ agrees: [...], disagrees: [...], nuanced: [...] }` — each with person_name, their_position, reasoning, confidence, date

### Test Criteria: Stage 6 Pass

- Public API is read-only — no mutations possible
- Admin endpoints require authentication
- /api/claims includes trust_level, attribution_confidence, extraction_confidence in responses
- /api/claims/{id} includes evidence spans with timestamps
- /api/intelligence/divergence uses two-step (vector + LLM classification), not pure vector similarity
- Auto-generated OpenAPI docs at /docs

---

## Stage 7: Web Dashboard

**Goal:** Evidence-first dashboard. Every claim card shows provenance, confidence, and trust level.

### Design Direction

Bloomberg Terminal meets Notion. Dark mode default. Mobile-responsive. Information-dense.

### Pages

**Dashboard (Home):** Pipeline status, today's claims, trending topics, latest brief, cost tracker

**People:** Sortable table with expertise domains, claim counts, last seen. Person detail page with positions and claims timeline. Inline editing.

**Claims Explorer:** Full-text and semantic search. Every claim card shows:
- Claim text + reasoning
- Source video with timestamp link
- Quote type label: direct quote / paraphrase / synthesis
- Trust level badge: high / medium / low
- Attribution confidence + extraction confidence
- Review status

**Topic Views:** Positions by person, consensus bar with evidence coverage context, position history timeline

**Review Queue:** Claims with `review_status='pending_review'`. Approve / reject / edit interface.

**Pipeline:** Stage-by-stage status with timestamps, durations, error logs, cost tracker

**Briefs:** Archive, current draft, manual generation trigger

### Test Criteria: Stage 7 Pass

- Every claim card shows trust_level, quote_type, and confidence scores
- Evidence drawer opens from any claim showing source text + timestamp
- Review queue shows pending items with approve/reject controls
- Responsive on mobile
- Data refreshes when pipeline runs

---

## Stage 8: Brief Generation & Delivery

**Goal:** Daily intelligence briefs from **reviewed claims only**, delivered via Telegram, email, and web.

### Brief Structure

**Section 1: Headlines** — Top 3–5 notable claims. Selected by: Tier 1 person + high confidence + novel position.

**Section 2: Position Shifts** — Any tracked person whose latest claim contradicts their known position. Highest-signal section. Links to evidence.

**Section 3: Topic Pulse** — What topics are getting the most attention this week vs. last week.

**Section 4: New Discoveries** — New videos processed with links.

### Generation Rules

- Brief generated from `review_status='approved'` claims only
- Every brief item links back to claim IDs and evidence
- Claude API generates the narrative from structured claims data
- Output: structured markdown renderable in web, Telegram, and email

### Delivery

- **Telegram:** python-telegram-bot, formatted message, interactive drill-downs
- **Email:** SMTP/SendGrid, HTML formatted, links to dashboard
- **Web:** Brief appears on dashboard home, full archive in Briefs page

### Scheduling

- Default: 10:00 AM local time (after pipeline completes)
- Manual trigger: `bm brief --generate` and `bm brief --send`

### Test Criteria: Stage 8 Pass

- Brief includes all 4 sections
- Every brief claim links to a claim with `review_status='approved'`
- Telegram + email delivery work with formatting intact
- Brief generation cost logged

---

## Appendix A: Full Change Log

### v2.1 changes (cross-model review: Claude, ChatGPT 5.4 Pro, Gemini)

| # | Change | Source | Impact |
|---|---|---|---|
| 1 | Added `reasoning_text` to claims table | ChatGPT, Gemini | Schema + extraction |
| 2 | Split Patrick & John Collison into two records | ChatGPT | Seed data |
| 3 | Added `attribution_confidence` + `extraction_confidence` as separate fields | ChatGPT | Schema + extraction |
| 4 | Channel monitoring default, search as gap-fill only | ChatGPT, Gemini | Stage 2 rewrite |
| 5 | Added `trust_level` field on claims, derived from transcript path | ChatGPT (trust lanes), Gemini (upgrade rule) | Schema + extraction |
| 6 | Configurable embedding dimensions, no hardcoded vector(1536) | ChatGPT, Gemini | Schema |
| 7 | Split API into public read + admin write | ChatGPT | Stage 6 |
| 8 | Added `inclusion_notes` + `expertise_domains` to people | ChatGPT | Schema + seed data |
| 9 | Stratified speaker sampling instead of chronological first 2–3 min | Gemini | Stage 4 |
| 10 | Divergence endpoint uses two-step (vector + LLM), not pure vector | Gemini | Stage 6 |
| 11 | WhisperX replaces custom Whisper + pyannote alignment | Gemini | Stage 3 |
| 12 | Prompt caching for Stage 5 enrichment | Gemini | Stage 5 |
| 13 | Tool_use structured output for extraction | Gemini | Stage 5 |
| 14 | HNSW over IVFFlat (deferred to post-MVP) | Gemini | Schema note |
| 15 | `claim_evidence` table for auditable provenance | ChatGPT, Gemini | New table |
| 16 | `position_history_log` replaces JSONB blob | ChatGPT, Gemini | Schema |
| 17 | Automatic fast→deep upgrade for multi-speaker videos | Gemini | Stage 3/4 |
| 18 | Known speaker count passed to WhisperX | Gemini | Stage 3 |
| 19 | Topic taxonomy in system prompt (controlled vocabulary) | Gemini | Stage 5 |
| 20 | Auto-review rules based on trust level | Claude synthesis | Stage 5 |

### v2.2 changes (ChatGPT structural patches)

| # | Change | Source | Impact |
|---|---|---|---|
| 21 | `channel_roles` junction table replaces `known_hosts TEXT[]` and `known_podcasts TEXT[]` | ChatGPT v2.2 | New table, removes string-array identity drift |
| 22 | `transcript_runs` table for rerun history and fast→deep audit trail | ChatGPT v2.2 | New table, segments link to runs not videos |
| 23 | `skip_reason` added to videos for explicit terminal states | ChatGPT v2.2 | Schema fix |
| 24 | Stage 5 tool schema returns `segment_id`, `start_ms`, `end_ms`, `extraction_confidence` | ChatGPT v2.2 | Eliminates post-hoc alignment brittleness |
| 25 | Review precedence rules explicitly defined (trust_level always wins) | ChatGPT v2.2 | Stage 5 logic |
| 26 | `topics` + `claim_topics` tables for normalized taxonomy | ChatGPT v2.2 | New tables, claims.topics kept as denormalized cache |
| 27 | Public API defaults to `review_status=approved` | ChatGPT v2.2 | Stage 6 |
| 28 | Gap-fill search uses `publishedAfter` API parameter, not year in query string | ChatGPT v2.2 | Stage 2 |
| 29 | HNSW migration strategy documented (standardize dimensions, partial indexes later) | ChatGPT v2.2 | Schema note |
| 30 | Merged `why_they_matter` into `inclusion_notes` (were near-duplicates) | ChatGPT v2.2 | Schema cleanup |

### v2.2.1 changes (ChatGPT consistency + safety hardening)

| # | Change | Source | Impact |
|---|---|---|---|
| 31 | `videos.channel_id TEXT` split into `podcast_channel_id UUID FK` + `source_channel_youtube_id TEXT` | ChatGPT v2.2.1 | Schema: proper FK to podcast_channels |
| 32 | `uploads_playlist_id` cached on podcast_channels for official API fallback | ChatGPT v2.2.1 | Schema + Stage 2 |
| 33 | `person_topic_positions.topic` and `position_history_log.topic` changed to `topic_id UUID FK` | ChatGPT v2.2.1 | Schema: eliminates remaining topic drift |
| 34 | Position aggregation + history built from approved claims only | ChatGPT v2.2.1 | Stage 5: prevents unreviewed leakage into public surfaces |
| 35 | `videos.status` enum explicitly includes 'error'; zero-tracked-speakers → 'skipped' (never 'enriched') | ChatGPT v2.2.1 | State machine cleanup |
| 36 | `claims.trust_level` and `review_status` have NO defaults — must be set explicitly in application logic | ChatGPT v2.2.1 | Safety: prevents accidental high-trust publication |
| 37 | Trust derived from final evidence path, not initial routing. Upgraded fast→deep = evaluated as deep | ChatGPT v2.2.1 | Trust model fix |
| 38 | Fast-path upgrade rule broadened: any conversational video with tracked person upgrades, not just 2+ people | ChatGPT v2.2.1 | Stage 3/4: safer attribution |
| 39 | WhisperX version pinned; speaker hints use min/max for variable panels, exact only for fixed-format shows | ChatGPT v2.2.1 | Stage 3 |
| 40 | Anthropic prompt caching via `cache_control` (not generic "headers"); tool schema uses `strict: true` | ChatGPT v2.2.1 | Stage 5 API correctness |
| 41 | `claim_evidence.segment_id` changed to NOT NULL (tool always returns segment refs) | ChatGPT v2.2.1 | Schema tightening |
| 42 | Text cleanups: Stage 2 tests remove "parameterized year", Stage 4 tests reference channel_roles, `bm people add` removes duplicate field reference | ChatGPT v2.2.1 | Spec consistency |

### Final changes (multi-provider API + consistency fixes)

| # | Change | Source | Impact |
|---|---|---|---|
| 43 | Multi-provider API strategy: Qwen3.5-Plus for enrichment, Claude for Mukhtar.AI integration, Deepgram for transcription | Product decision | Architecture + cost (~$70/mo vs $200) |
| 44 | `transcript_runs.speaker_count_hint` replaced with `speaker_config JSONB` supporting exact count or min/max range | ChatGPT final | Schema fix |
| 45 | WhisperX pin updated to `>=3.6.1,<4.0` (backported timestamp fixes) | ChatGPT final | Stage 3 |
| 46 | Fast-path → Stage 5 handoff rule made explicit: caption-only videos may extract only if provably single-speaker | ChatGPT final | Stage 5 logic |
| 47 | Mode B fast-path upgrade text aligned with broadened rule from Stage 3 | ChatGPT final | Consistency |
| 48 | `strict: true` added to actual tool JSON snippet (was in prose only) | ChatGPT final | Stage 5 code correctness |
| 49 | `identified_via` labels standardized across schema and confidence table: known_host, diarization_llm, metadata_only, manual | ChatGPT final | Consistency |
| 50 | UNIQUE/CHECK constraints added: video_people, transcript_segments, person_topic_positions, claim_evidence, claim_embeddings | ChatGPT final | Schema hardening |
| 51 | `claims.source_timestamp_ms` and `source_text` removed (duplicated claim_evidence) | ChatGPT final | Schema cleanup |

### Final table count: 14 core + briefs in Stage 8

```
people, podcast_channels, channel_roles, videos, transcript_runs, 
transcript_segments, video_people, topics, claims, claim_topics, 
claim_evidence, claim_embeddings, person_topic_positions, 
position_history_log
+ briefs (added in Stage 8 when delivery storage is needed)
```

---

## Appendix B: What Was Proposed and Rejected

| Proposal | Source | Why Rejected |
|---|---|---|
| 26-table schema with organizations, affiliations, person_expertise | ChatGPT v3 | Over-engineered for 47 people. `expertise_domains TEXT[]` and `inclusion_notes` on people table achieves the same goal with 2 columns instead of 4 tables |
| Reduce MVP to 10–15 people, 2 domains | ChatGPT v3 | Kills the product proposition. Breadth of coverage is the value. Keep all 47, be honest about trust levels |
| Prediction tracking + outcome resolution in MVP | ChatGPT v3 | Great Phase 2 feature, but requires months of data before any prediction resolves. Dead weight in MVP |
| Review queue blocking all publications | ChatGPT v3 | 30–50 items/day for a solo operator becomes a daily editorial job. Default to auto-approve high-trust, label honestly, review when you want |
| yt-dlp search scraping to bypass API quota | Gemini | Against YouTube ToS, fragile, actively blocked. Channel feeds + bounded API search is sustainable |
| Local embeddings via nomic-embed-text | Gemini | OpenAI embeddings cost ~$0.50/month. Adding Ollama as embedding infra for $6/year savings adds complexity |
| Light-theme dashboard | ChatGPT v3 | Dark Bloomberg-style is more appropriate for intelligence product aimed at executives |
| Replace yt-dlp channel feeds with official playlistItems API | ChatGPT v2.2 | yt-dlp channel feed is a different operation than yt-dlp search. Stable, well-maintained, zero quota cost. Official API is 1 unit/call (trivial) but adds API key dependency to the default path. Keep yt-dlp for feeds, document playlistItems.list as fallback |
| Full stage_runs table replacing videos.status | ChatGPT v3/v2.2 | `transcript_runs` solves the transcript rerun problem. `skip_reason` column solves the terminal-state ambiguity. Full stage_runs across all stages is Phase 2 cleanup |

### yt-dlp dependency note

This spec accepts yt-dlp as a pragmatic ingestion dependency for channel feed monitoring. yt-dlp channel feeds (uploads playlist downloads) have been stable for years and are mechanically different from yt-dlp web search scraping (which is fragile and ToS-risky). If yt-dlp channel feeds break, switch to `playlistItems.list` (1 quota unit per call, 33 calls/day = trivial). Both paths are documented in `src/pipeline/discovery.py`.

---

## Appendix C: Environment Variables

| Variable | Required | Description |
|---|---|---|
| DATABASE_URL | Yes | PostgreSQL connection string |
| QWEN_API_KEY | Stage 4+ | **[NEW Final]** Qwen3.5-Plus via Alibaba Cloud Model Studio. Primary LLM for enrichment |
| QWEN_API_BASE | Stage 4+ | **[NEW Final]** Alibaba Cloud API base URL |
| ANTHROPIC_API_KEY | Stage 6 | Claude Sonnet 4.6 for Mukhtar.AI divergence endpoint + fallback |
| OPENAI_API_KEY | Stage 5 | For embeddings (text-embedding-3-small) |
| DEEPGRAM_API_KEY | Stage 3 | **[NEW Final]** Deepgram Nova-3 for deep-path transcription |
| TRANSCRIPTION_PROVIDER | Stage 3 | **[NEW Final]** 'deepgram' (default) or 'whisperx' (future local) |
| LLM_PROVIDER | Stage 4+ | **[NEW Final]** 'qwen' (default) or 'anthropic' (fallback) |
| YOUTUBE_API_KEY | Stage 2 | YouTube Data API v3 (gap-fill search only) |
| EMBEDDING_DIMENSIONS | Stage 5 | Configurable: default 1536. Must be uniform for future HNSW indexing |
| TELEGRAM_BOT_TOKEN | Stage 8 | Telegram bot for brief delivery |
| TELEGRAM_CHAT_ID | Stage 8 | Target chat for briefs |
| SMTP_HOST | Stage 8 | Email server |
| SMTP_USER | Stage 8 | Email username |
| SMTP_PASS | Stage 8 | Email password |
| SMTP_TO | Stage 8 | Recipient email |

---

## Appendix D: Project Structure

```
believable-minds/
├── docker-compose.yml              # PostgreSQL + pgvector
├── pyproject.toml                   # Python project config
├── .env.example
├── alembic/                         # Database migrations
│   ├── versions/
│   └── env.py
├── src/
│   ├── cli/                         # Click/Typer CLI
│   │   ├── main.py
│   │   ├── people.py
│   │   ├── channels.py              # Channels + channel_roles
│   │   ├── topics.py                # Topic taxonomy CRUD
│   │   ├── scan.py
│   │   ├── transcribe.py
│   │   ├── identify.py
│   │   ├── enrich.py
│   │   └── brief.py
│   ├── pipeline/                    # Core pipeline logic
│   │   ├── discovery.py             # yt-dlp feeds + API gap-fill
│   │   ├── transcription.py         # Provider interface: DeepgramProvider + WhisperXProvider
│   │   ├── identification.py        # Stratified sampling + channel_roles
│   │   ├── enrichment.py            # Provider interface: QwenProvider + AnthropicProvider + tool_use
│   │   └── briefing.py
│   ├── providers/                   # LLM + transcription provider abstraction
│   │   ├── base.py                  # Abstract interfaces
│   │   ├── qwen.py                  # Qwen3.5-Plus API adapter
│   │   ├── anthropic.py             # Claude Sonnet 4.6 adapter
│   │   ├── deepgram.py              # Deepgram Nova-3 adapter
│   │   └── whisperx.py              # WhisperX local adapter (future)
│   ├── api/                         # FastAPI server
│   │   ├── main.py
│   │   ├── public/                  # Read-only routes (approved-only default)
│   │   └── admin/                   # Protected routes
│   ├── db/
│   │   ├── models.py                # SQLAlchemy 2.0 (14 core tables)
│   │   ├── session.py
│   │   └── seed.py                  # People, channels, channel_roles, topics
│   └── config.py
├── web/                             # Frontend
├── tests/
│   ├── test_stage1_registry.py
│   ├── test_stage2_discovery.py
│   └── ...
├── tools/
│   └── resolve_channel_ids.py       # YouTube channel ID lookup
└── data/
    ├── people_seed.json             # 47 people with inclusion_notes + expertise_domains
    ├── channels_seed.json           # 33 channels with monitoring_mode
    ├── channel_roles_seed.json      # Host/cohost mappings
    └── topics_seed.json             # ~30 MVP topic slugs
```
```

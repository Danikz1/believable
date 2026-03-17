# Believable Minds — Project Architecture & Design

**Version:** March 2026  
**Status:** Production (live at serikson.com)  
**Stack:** Python 3.12 · FastAPI · PostgreSQL + pgvector · Multi-LLM · AssemblyAI

---

## 1. System Overview

Believable Minds is an automated intelligence platform that:

1. **Monitors** YouTube channels for new podcast/interview content
2. **Transcribes** episodes with speaker diarization
3. **Identifies** which tracked individuals appear in each episode
4. **Extracts** structured claims (predictions, opinions, analyses) with evidence
5. **Synthesizes** per-person, per-topic positions and detects stance shifts
6. **Delivers** a reader-focused feed of claims, positions, and episode summaries

The entire pipeline runs autonomously via a background scheduler. No manual intervention is needed after initial channel setup.

---

## 2. Architecture

### 2.1 Data Flow

```
YouTube Channels
      │
      ▼
  ┌───────────┐     ┌──────────────┐     ┌──────────────┐
  │ Discovery  │────▶│ Transcription │────▶│Identification│
  │ (RSS/API)  │     │ (AssemblyAI)  │     │  (LLM match) │
  └───────────┘     └──────────────┘     └──────────────┘
                                                │
      ┌─────────────────────────────────────────┘
      ▼
  ┌───────────┐     ┌──────────────┐     ┌──────────────┐
  │ Enrichment │────▶│ Summarization │────▶│  Position    │
  │(LLM claims)│     │(episode recap)│     │ Synthesis    │
  └───────────┘     └──────────────┘     └──────────────┘
                                                │
                                                ▼
                                          ┌──────────┐
                                          │   API    │
                                          │ + Frontend│
                                          └──────────┘
```

### 2.2 Key Design Decisions

**Multi-provider LLM with auto-fallback.** The system tries Qwen → Anthropic → OpenAI in sequence. If one provider fails (rate limit, auth error), it transparently falls back to the next. This ensures the pipeline never stalls due to a single provider outage.

**Idempotent enrichment.** Each speaker×video pair has an `enrichment_status` (pending → in_progress → completed/failed). If the pipeline crashes mid-enrichment, re-running it skips already-completed speakers instead of duplicating claims.

**Positions as derived state.** `person_topic_positions` is not user-edited — it's computed from approved claims. When a new claim is approved, the position for that person×topic is recalculated. If the new position contradicts the previous one, a "shift" is recorded in `position_history_log`.

**Topic assignment: junction table as source of truth.** Claims reference topics via the `claim_topics` junction table, not the denormalized `claims.topics[]` array. The array exists for backward compatibility and is synced from the junction table via `claim.sync_topics_cache()`.

**Three-tier transcription.** Videos can be transcribed via:
1. **Official transcripts** (highest quality — scraped from podcast websites)
2. **Deep diarization** (AssemblyAI with speaker separation)
3. **Fast captions** (YouTube auto-captions via yt-dlp)

The system automatically selects the best available method per video.

---

## 3. Pipeline Detail

### 3.1 Discovery (`src/pipeline/discovery.py`)

- Scans YouTube channels via RSS feed (no API quota) and YouTube Data API (for deeper search)
- Deduplicates by `youtube_video_id`
- Supports "gap fill" — searches for tracked people who may have appeared on non-monitored channels
- New videos enter the pipeline with status `discovered`

### 3.2 Transcription (`src/pipeline/transcription.py`)

- **AssemblyAI (preferred):** Submits YouTube URL directly — no audio download needed on cloud. Returns speaker-diarized transcript with word-level timestamps.
- **Official transcripts:** For podcasts like Dwarkesh Patel and Lex Fridman that publish edited transcripts on their websites. Scraped and parsed into segments.
- **YouTube captions (fallback):** Downloaded via yt-dlp. No speaker separation but reliable.

All transcripts are stored as `transcript_segments` with `speaker_label`, `start_ms`, `end_ms`, and `text`.

### 3.3 Identification (`src/pipeline/identification.py`)

Three modes:

1. **Known hosts:** Channel owners are automatically matched (e.g., Lex Fridman → Lex Fridman Podcast)
2. **Metadata matching:** LLM analyzes video title/description against the tracked people database to identify guests
3. **Deep diarization matching:** For multi-speaker transcripts, LLM maps speaker labels to people

Creates `video_people` entries with role (host/guest), confidence score, and identification method.

### 3.4 Enrichment (`src/pipeline/enrichment.py`)

- Segments are grouped into overlapping batches (20 segments, stride 10)
- LLM extracts structured claims with: claim text, type, speaker certainty, sentiment, topics, and evidence spans
- Each claim gets a `trust_level` (high/medium/low) based on `attribution_confidence`
- Auto-review: high/medium trust claims on known topics → `approved`; low trust → `pending_review`
- **Idempotent:** Tracks `enrichment_status` per speaker to support crash recovery

### 3.5 Summarization (`src/pipeline/summaries.py`)

- Generates section-by-section episode summaries
- Assigns a watch verdict: **Essential**, **Worth Skimming**, or **Skip Unless Fan**
- Each section has a title, summary, key quotes, and topic tags
- Stored as JSON in `episode_summaries.detailed_json`

### 3.6 Position Synthesis (`src/pipeline/positions.py`)

- Processes approved claims to update `person_topic_positions`
- Detects **stance shifts** — when a person's position on a topic changes direction
- If a claim has no topic links, falls back to the person's `expertise_domains`
- Shift events are logged in `position_history_log` with `is_shift=True`

### 3.7 Background Scheduler (`src/pipeline/scheduler.py`)

Runs as a daemon thread inside the FastAPI process:

| Task | Interval | Batch Size |
|------|----------|-----------|
| Channel scan | 4 hours | 20 videos/channel |
| Video processing | 30 minutes | 3 videos |
| Position synthesis | 1 hour | 20 videos |

Status is exposed at `GET /health` → `scheduler` field.

---

## 4. API Architecture

### 4.1 Authentication Model

- **GET endpoints:** Unauthenticated (read-only public API)
- **POST/PUT/DELETE endpoints:** Require `x-admin-key` header matching `ADMIN_API_KEY` env var
- Admin endpoints live on both `/api/` (public router) and `/admin/` (admin router)

> **⚠️ Frontend Integration Note:** All mutation endpoints require the `x-admin-key` header. The frontend JavaScript must include this header when calling protected endpoints like `POST /api/videos/add`, `POST /api/channels`, `DELETE /api/videos/{id}`, etc. The admin key is currently embedded in the frontend for simplicity — in a multi-user deployment, this should be replaced with proper user authentication.

### 4.2 Performance Optimizations

- **N+1 query elimination:** List endpoints use batch `GROUP BY` queries instead of per-row sub-selects
- **In-memory TTL cache:** 60-second cache on expensive queries (invalidated on admin mutations)
- **Connection pooling:** `pool_size=10`, `max_overflow=20`, `pool_recycle=1800`, `pool_pre_ping=True`
- **HTTP client pooling:** Shared `httpx.Client` for LLM calls (reuses TCP+TLS connections)

### 4.3 Frontend

Single-page app served as `src/api/static/index.html`:

- **Feed page:** Reverse-chronological claim stream grouped by episode
- **People page:** Grid of tracked individuals with claim counts, filterable by tag
- **Person detail:** Bio, current positions, recent appearances, stance shifts
- **Channels page:** Monitored channels, video queue with per-video process/retry buttons
- **Episode page:** Full summary with sections, key quotes, and linked claims

Vanilla JS with no framework dependencies. Dark theme with glassmorphism design.

---

## 5. Database Design

### 5.1 Core Entities

```
People ──────┬──── VideoPeople ────── Videos ────── PodcastChannels
             │         │
             │    TranscriptSegments
             │         │
             ├──── Claims ──── ClaimEvidence
             │         │
             │    ClaimTopics ──── Topics
             │
             ├── PersonTopicPositions
             └── PositionHistoryLog
```

### 5.2 Pipeline State Machine

Videos progress through statuses:

```
discovered → transcribed → identified → enriched → [summarized]
     │            │             │            │
     └────────────┴─────────────┴────────────┴─────→ error
```

The `error` status can be retried via `POST /api/videos/{id}/retry`, which resets to `discovered`.

### 5.3 Claim Trust & Review Flow

```
Claim extracted
      │
      ├── attribution_confidence ≥ 0.8 → trust_level = "high"
      ├── 0.5 ≤ confidence < 0.8       → trust_level = "medium"
      └── confidence < 0.5             → trust_level = "low"
      │
      ├── high/medium + known topic → review_status = "approved" (auto)
      └── low trust                 → review_status = "pending_review"
```

---

## 6. Migrations

7 Alembic migrations in sequence:

| Migration | Description |
|-----------|-------------|
| `001_initial` | Core tables (people, channels, videos, segments) |
| `002_add_transcript_cols` | Transcript run tracking |
| `003_add_briefs_table` | Daily intelligence briefs |
| `004_add_favorites_and_summaries` | Favorites, episode summaries |
| `005_add_x_twitter_support` | X/Twitter post ingestion |
| `006_v2_redesign` | V2 schema (positions, evidence, topics junction) |
| `007_improvements` | Enrichment status tracking, XOR constraint fix |

Run via: `alembic upgrade head`

---

## 7. Deployment

### Railway (Production)

```
Push to main → Railway builds Docker image → start.sh runs migrations → Uvicorn starts → Scheduler begins
```

Required environment variables:
- `DATABASE_URL` — PostgreSQL connection (provided by Railway Postgres plugin)
- `ADMIN_API_KEY` — **Must override the default** (`bm-admin-key`) in production
- `QWEN_API_KEY` — Primary LLM provider
- `ASSEMBLYAI_API_KEY` — For transcription

Optional:
- `YOUTUBE_PROXY` — Residential proxy to bypass bot detection on cloud IPs
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` — Fallback LLM providers
- `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` — Tune for Railway connection limits

### Logging

- **Production (Railway):** Structured JSON logs to stdout (auto-detected via `RAILWAY_ENVIRONMENT`)
- **Development:** Human-readable format with timestamps
- Context fields: `video_id`, `person_name`, `provider`, `stage`, `claim_count`

---

## 8. Testing

```bash
pytest                          # Run all tests
pytest tests/test_enrichment.py # Test enrichment pipeline
pytest -x -v                    # Stop on first failure, verbose
```

Tests mock external services (LLM, AssemblyAI, yt-dlp) for fast, deterministic runs.

---

## 9. Known Limitations & Future Work

### Current Limitations

- **Single-user:** No user authentication system; admin key is shared
- **No real-time updates:** Frontend polls for status; no WebSocket/SSE
- **Claim quality depends on LLM:** Different providers produce varying claim quality/quantity
- **Position synthesis is coarse:** Uses latest claim text as position summary rather than synthesizing across all claims

### Potential Improvements

- Proper user auth (OAuth/JWT) replacing shared admin key
- WebSocket for real-time pipeline progress
- Claim deduplication via vector similarity (pgvector infrastructure exists but unused)
- Person-focused episode summaries (infrastructure exists, generation not triggered)
- Email/Telegram daily brief delivery (pipeline exists in `briefs.py` and `delivery.py`)
- X/Twitter post ingestion (schema and pipeline exist, needs API integration)

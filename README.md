# Believable Minds

**Track what credible minds say — structured claims, evolving positions, and the signal behind the noise.**

Believable Minds is a data production system and web application that ingests podcast/interview transcripts, extracts structured claims from identified speakers, tracks how their positions evolve over time, and surfaces it all through a reader-focused intelligence feed.

**Live at:** [serikson.com](https://www.serikson.com)

---

## What It Does

1. **Discovers** new videos from tracked YouTube channels (Lex Fridman, All-In Podcast, Dwarkesh Patel, etc.)
2. **Transcribes** audio via AssemblyAI (with deep diarization) or YouTube captions
3. **Identifies** speakers by matching against known people using LLM + metadata heuristics
4. **Extracts claims** — predictions, opinions, recommendations — with evidence spans and confidence scores
5. **Tracks positions** per person × topic, detecting when someone shifts their stance
6. **Generates summaries** per episode with watch verdicts (Essential / Worth Skimming / Skip)
7. **Runs autonomously** via a background scheduler that processes the full pipeline continuously

---

## Tech Stack

| Layer | Tech |
|-------|------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy 2, Alembic |
| **Database** | PostgreSQL + pgvector (hosted on Railway) |
| **LLM** | Qwen 3.5+ (primary), Anthropic Claude, OpenAI GPT — auto-fallback chain |
| **Transcription** | AssemblyAI (deep diarization), yt-dlp (captions), official transcripts |
| **Frontend** | Vanilla HTML/CSS/JS — single `index.html` served by FastAPI |
| **Deployment** | Docker → Railway (auto-deploy on push to `main`) |
| **CLI** | Typer — `bm` command for all pipeline operations |

---

## Project Structure

```
src/
├── api/
│   ├── app.py              # FastAPI app, lifespan, scheduler startup
│   ├── admin.py            # Admin endpoints (pipeline control, claim review)
│   ├── public.py           # Public API (people, claims, feed, channels, videos)
│   ├── cache.py            # In-memory TTL cache for expensive queries
│   └── static/index.html   # Full frontend SPA
├── cli/                    # Typer CLI commands (bm scan, bm transcribe, etc.)
├── db/
│   ├── models.py           # 15+ SQLAlchemy models (People, Videos, Claims, etc.)
│   ├── enums.py            # StrEnum types for all magic-string columns
│   ├── seed.py             # Initial people/channel data
│   └── session.py          # Connection pooling configuration
├── pipeline/
│   ├── discovery.py        # YouTube channel scanning, video dedup
│   ├── transcription.py    # Multi-provider transcription (AssemblyAI, captions, official)
│   ├── identification.py   # Speaker identification (known hosts, LLM metadata matching)
│   ├── enrichment.py       # Claim extraction via LLM (idempotent, per-speaker tracking)
│   ├── positions.py        # Position synthesis, shift detection
│   ├── summaries.py        # Episode summary generation (section-by-section)
│   ├── scheduler.py        # Background scheduler (scan/process/synthesize)
│   ├── embeddings.py       # Claim vector embeddings (pgvector)
│   └── briefs.py           # Daily intelligence briefs
├── providers/
│   ├── llm.py              # Multi-provider LLM client (Qwen/Anthropic/OpenAI)
│   └── official_transcript.py  # Dwarkesh, Lex Fridman official transcript scrapers
├── config.py               # Pydantic Settings (env vars)
├── logging_config.py       # Structured JSON logging (production) / readable (dev)
└── youtube.py              # yt-dlp wrapper with proxy support
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 15+ with pgvector extension
- At least one LLM API key (Qwen, Anthropic, or OpenAI)

### Local Development

```bash
# 1. Clone and install
git clone https://github.com/Danikz1/believable.git
cd believable

# 2. Start Postgres (or use docker-compose)
docker compose up -d

# 3. Create .env from example
cp .env.example .env
# Edit .env: set DATABASE_URL, at least one LLM key

# 4. Install
pip install -e ".[dev]"

# 5. Run migrations
alembic upgrade head

# 6. Seed initial data
python -c "from src.db.session import get_session; from src.db.seed import seed_people; s = get_session(); seed_people(s); s.commit()"

# 7. Start the server
uvicorn src.api.app:app --reload --port 8000
```

The app will be at `http://localhost:8000`. The background scheduler starts automatically on boot.

### Docker

```bash
docker compose up -d    # Postgres
docker build -t bm .
docker run -p 8000:8000 --env-file .env bm
```

---

## Environment Variables

See [`.env.example`](.env.example) for all options. Key ones:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `QWEN_API_KEY` | ✅* | Qwen LLM (primary provider) |
| `ASSEMBLYAI_API_KEY` | Recommended | For deep diarized transcription |
| `ADMIN_API_KEY` | ✅ | Protects all mutation endpoints |
| `YOUTUBE_PROXY` | Optional | Proxy for yt-dlp on cloud IPs |
| `DB_POOL_SIZE` | Optional | Connection pool size (default: 10) |

*At least one LLM key required (Qwen, Anthropic, or OpenAI).

---

## API Overview

### Public (no auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/people` | List tracked people with claim counts |
| GET | `/api/people/{id}` | Person detail: positions, appearances, shifts |
| GET | `/api/claims` | Approved claims (filterable by person, topic, type) |
| GET | `/api/feed` | Intelligence feed of recent claims |
| GET | `/api/topics` | Topic taxonomy with claim counts |
| GET | `/api/channels` | Monitored YouTube channels |
| GET | `/api/videos/queue` | Video processing queue |
| GET | `/api/pipeline/status` | Pipeline health metrics |
| GET | `/health` | Health check + scheduler status |

### Protected (requires `x-admin-key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/channels` | Add a YouTube channel |
| DELETE | `/api/channels/{id}` | Remove a channel |
| POST | `/api/channels/{id}/scan` | Trigger channel scan |
| POST | `/api/videos/add` | Add a single video by URL |
| POST | `/api/videos/{id}/retry` | Reset video for reprocessing |
| DELETE | `/api/videos/{id}` | Delete a video and all data |
| POST | `/api/favorites` | Add a favorite |
| DELETE | `/api/favorites/{id}` | Remove a favorite |
| POST | `/admin/pipeline/trigger/{stage}` | Trigger pipeline stage |
| POST | `/admin/pipeline/process-video/{id}` | Process single video end-to-end |
| POST | `/admin/pipeline/synthesize-positions` | Batch position synthesis |
| PUT | `/admin/claims/{id}/review` | Approve/reject a claim |

> **⚠️ Important:** All mutation endpoints (`POST`, `PUT`, `DELETE`) require the `x-admin-key` header. The frontend sends this automatically for admin panel operations, but any custom integrations or scripts must include `x-admin-key: <your-key>` in request headers.

---

## Pipeline Stages

```
Channel Scan → Discover Videos → Transcribe → Identify Speakers → Extract Claims → Summarize → Synthesize Positions
     ↑                                                                                              │
     └──────────────────── Background Scheduler (autonomous loop) ──────────────────────────────────┘
```

| Stage | Trigger | What it does |
|-------|---------|-------------|
| **Discovery** | Every 4h (scheduler) | Scans YouTube channels via RSS/API, deduplicates |
| **Transcription** | Every 30m (scheduler) | AssemblyAI deep diarization, or caption fallback |
| **Identification** | Auto after transcription | Matches speakers to known people via LLM |
| **Enrichment** | Auto after identification | Extracts claims with evidence, idempotent per-speaker |
| **Summarization** | Auto after enrichment | Section-by-section episode summaries |
| **Position Synthesis** | Every 1h (scheduler) | Updates person×topic positions, detects stance shifts |

---

## CLI Reference

```bash
bm scan              # Scan channels for new videos
bm transcribe        # Transcribe discovered videos
bm identify          # Identify speakers in transcribed videos
bm enrich            # Extract claims from identified videos
bm summaries         # Generate episode summaries
bm people list       # List all tracked people
bm channels list     # List monitored channels
bm topics list       # List topic taxonomy
```

---

## Database Schema

15+ tables managed via Alembic (7 migrations):

- **`people`** — Tracked individuals (name, tier, bio, expertise domains)
- **`podcast_channels`** — YouTube channels being monitored
- **`videos`** — Discovered videos with pipeline status tracking
- **`transcript_runs`** — Transcription attempts with provider/mode
- **`transcript_segments`** — Individual transcript segments with speaker assignment
- **`video_people`** — Speaker↔Video junction with enrichment status
- **`claims`** — Extracted claims with confidence, trust level, review status
- **`claim_evidence`** — Evidence spans linking claims to transcript segments
- **`claim_topics`** — Claim↔Topic junction (source of truth for topic assignment)
- **`topics`** — Topic taxonomy (macro, ai_infrastructure, venture_capital, etc.)
- **`person_topic_positions`** — Current position per person × topic
- **`position_history_log`** — Position change history with shift detection
- **`episode_summaries`** — Episode summaries with watch verdicts
- **`favorites`** — User favorites for prioritized processing
- **`x_posts`** — Twitter/X post ingestion (experimental)

---

## Deployment (Railway)

The project auto-deploys on push to `main`:

1. Railway builds the Docker image
2. `scripts/start.sh` runs Alembic migrations and seeds data
3. Uvicorn starts on `$PORT`
4. Background scheduler begins after 60s warmup

Required Railway env vars: `DATABASE_URL`, `ADMIN_API_KEY`, `QWEN_API_KEY` (and/or other LLM keys), `ASSEMBLYAI_API_KEY`.

---

## License

Private project. All rights reserved.

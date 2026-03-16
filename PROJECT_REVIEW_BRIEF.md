# Believable Minds: Comprehensive Project Description and Improvement Brief

## 1. Executive Summary

Believable Minds is a data production system and web application that tracks what credible public thinkers say, structures those statements into claims, groups them by topic, and shows how positions evolve over time.

The project is built around a staged ingestion pipeline:

1. discover relevant source material
2. transcribe it
3. identify who is speaking
4. extract structured claims
5. attach evidence and topics
6. generate embeddings and topic positions
7. present the results in a searchable dashboard

The current production system is focused on YouTube and podcast/interview content. It uses FastAPI for the web/API layer, PostgreSQL with `pgvector` for storage and semantic search, a Typer CLI for operational workflows, and Railway for deployment.

The live site is deployed at `https://serikson.com`.

---

## 2. What the Product Does Today

### Core use case

The system tracks a curated list of well-known people across business, technology, macro, investing, AI, and adjacent domains. For each person, the system aims to answer:

- what has this person said recently?
- what topics do they speak about?
- how many structured claims have been extracted from them?
- what are their current synthesized positions on those topics?
- where do different people agree or diverge?

### Current user-facing experience

The frontend is a single-page static dashboard served by FastAPI.

It currently has these main views:

- Dashboard
- People
- Claims Explorer
- Topics
- Review Queue
- Pipeline

### People view

The People page shows:

- tracked person name
- tier
- primary domain
- expertise/topic badges
- approved claim count

Clicking a person now opens a profile/detail view that shows:

- person summary
- inclusion notes used as a bio-style blurb
- expertise tags
- current topic positions
- a scrollable history of approved claims
- source video title and source video link when available

### Claims view

The Claims view supports:

- browsing approved claims
- semantic search over claims using embeddings
- seeing topic tags per claim
- seeing person attribution per claim

### Topics view

The Topics view shows:

- all topics in the taxonomy
- which topics currently have claims
- topic-level detail pages
- per-topic person positions
- per-topic "consensus" buckets by sentiment

### Pipeline view

The Pipeline view exposes high-level counts for:

- discovered videos
- transcribed videos
- identified videos
- enriched videos
- approved and pending claims
- embeddings
- positions

---

## 3. Current Scope and Seed Data

The project ships with curated seed data:

- `47` people
- `33` tracked channels
- `30` topic taxonomy entries
- `15` channel-role mappings

Seed files live in:

- `/Users/daniyarserikson/my-first-project/believable-people/data/people_seed.json`
- `/Users/daniyarserikson/my-first-project/believable-people/data/channels_seed.json`
- `/Users/daniyarserikson/my-first-project/believable-people/data/topics_seed.json`
- `/Users/daniyarserikson/my-first-project/believable-people/data/channel_roles_seed.json`

Examples of tracked people include founders, investors, CEOs, macro thinkers, and AI leaders such as:

- Harry Stebbings
- Ray Dalio
- Jensen Huang
- Naval Ravikant
- Mark Zuckerberg
- Lisa Su
- Cathie Wood
- Ben Horowitz
- Patrick O'Shaughnessy
- Tyler Cowen

---

## 4. Technical Architecture

### Stack

- Backend: FastAPI
- CLI: Typer
- ORM: SQLAlchemy 2
- Migrations: Alembic
- Database: PostgreSQL + `pgvector`
- Frontend: static HTML/CSS/JS served by FastAPI
- Containerization: Docker
- Deployment: Railway
- Domain: Namecheap DNS pointing to Railway

### Main code areas

- API entrypoint: `/Users/daniyarserikson/my-first-project/believable-people/src/api/app.py`
- Public API: `/Users/daniyarserikson/my-first-project/believable-people/src/api/public.py`
- Admin API: `/Users/daniyarserikson/my-first-project/believable-people/src/api/admin.py`
- Frontend: `/Users/daniyarserikson/my-first-project/believable-people/src/api/static/index.html`
- Data model: `/Users/daniyarserikson/my-first-project/believable-people/src/db/models.py`
- Seed logic: `/Users/daniyarserikson/my-first-project/believable-people/src/db/seed.py`
- Discovery pipeline: `/Users/daniyarserikson/my-first-project/believable-people/src/pipeline/discovery.py`
- Transcription pipeline: `/Users/daniyarserikson/my-first-project/believable-people/src/pipeline/transcription.py`
- Speaker identification: `/Users/daniyarserikson/my-first-project/believable-people/src/pipeline/identification.py`
- Claim extraction: `/Users/daniyarserikson/my-first-project/believable-people/src/pipeline/enrichment.py`
- Embeddings: `/Users/daniyarserikson/my-first-project/believable-people/src/pipeline/embeddings.py`
- Position synthesis: `/Users/daniyarserikson/my-first-project/believable-people/src/pipeline/positions.py`
- LLM provider abstraction: `/Users/daniyarserikson/my-first-project/believable-people/src/providers/llm.py`

---

## 5. Database Model

### Major tables

#### `people`

Tracks the people being monitored.

Important fields:

- `id`
- `name`
- `domain`
- `tier`
- `inclusion_notes`
- `expertise_domains`
- `youtube_search_queries`
- `active`

#### `podcast_channels`

Tracks monitored source channels.

Important fields:

- `youtube_channel_id`
- `name`
- `tier`
- `monitoring_mode`
- `uploads_playlist_id`
- `transcript_url_pattern`
- `transcript_parser`

#### `channel_roles`

Maps people to channels as:

- host
- cohost
- frequent_guest

#### `videos`

Represents discovered YouTube videos.

Important fields:

- `youtube_video_id`
- `title`
- `podcast_channel_id`
- `source_channel_youtube_id`
- `published_at`
- `duration_seconds`
- `description`
- `discovery_method`
- `discovered_by_person_id`
- `transcript_type`
- `status`

Current statuses include:

- `discovered`
- `transcribed`
- `identified`
- `enriched`
- `skipped`
- `error`

#### `transcript_runs`

Records each transcription attempt.

Important fields:

- `video_id`
- `mode`
- `provider`
- `provider_model`
- `status`
- `speaker_config`
- `error_message`

#### `transcript_segments`

Stores timestamped transcript chunks.

Important fields:

- `video_id`
- `segment_index`
- `speaker_label`
- `speaker_name`
- `person_id`
- `start_ms`
- `end_ms`
- `text`
- `source_kind`

This table is very important because it already contains the raw timestamps needed for precise evidence links.

#### `video_people`

Maps identified people to videos.

Important fields:

- `video_id`
- `person_id`
- `role`
- `confidence`
- `identified_via`

#### `topics`

Taxonomy of normalized topics/tags.

Important fields:

- `slug`
- `name`
- `parent_id`
- `active`

#### `claims`

Stores extracted claims.

Important fields:

- `person_id`
- `video_id`
- `claim_text`
- `reasoning_text`
- `claim_type`
- `speaker_certainty`
- `attribution_confidence`
- `extraction_confidence`
- `trust_level`
- `topics`
- `sentiment`
- `temporal_marker`
- `review_status`

#### `claim_topics`

Normalized many-to-many mapping between claims and topics.

#### `claim_evidence`

Stores structured evidence spans for each claim.

Important fields:

- `claim_id`
- `segment_id`
- `evidence_order`
- `quote_text`
- `start_ms`
- `end_ms`
- `quote_type`

This table already contains most of the data needed to build exact timestamped source links.

#### `claim_embeddings`

Vector embeddings for semantic search.

Important fields:

- `claim_id`
- `model_name`
- `dimensions`
- `embedding`

#### `person_topic_positions`

Stores current synthesized position per person-topic pair.

#### `position_history_log`

Stores historical position snapshots and shifts.

#### `briefs`

Stores generated intelligence briefs.

---

## 6. Pipeline Stages

### Stage 1: Discovery

There are two discovery modes:

#### A. Channel feed scan

- Uses `yt-dlp`
- Scans tracked YouTube channels
- Zero YouTube API quota
- Intended for ongoing monitoring of known source channels

#### B. Search gap-fill

- Uses YouTube Data API
- Searches recent videos by person name/query
- Bounded quota usage
- Useful for filling missing content outside tracked channels

The discovery pipeline also includes channel-ID repair logic using the YouTube API when a seeded YouTube channel ID is stale.

### Stage 2: Transcription

Two logical paths:

#### Fast/caption path

- YouTube captions via `yt-dlp`
- plus a direct `youtube-transcript-api` fallback
- cheaper and easier
- typically no diarization

#### Deep path

- Deepgram
- downloads audio
- sends audio for diarized ASR
- better speaker attribution when it works

Important real-world note:

The Railway deployment sometimes gets blocked by YouTube bot protection and HTTP 429s when trying to download audio or captions from Railway's cloud IPs. A recent fix added `youtube-transcript-api` fallback, which now rescues many videos that previously failed.

### Stage 3: Speaker Identification

After transcription, the system tries to map transcript/video speakers to tracked people using:

- known channel roles
- metadata heuristics
- transcript-based logic
- optional LLM-assisted upgrade paths

The output is stored in `video_people`.

### Stage 4: Claim Extraction / Enrichment

For each identified speaker in a video, the system sends transcript segments to an LLM and asks it to extract structured claims with evidence spans.

It supports:

- Qwen as primary LLM
- Anthropic as fallback LLM
- OpenAI in the provider abstraction too

The tool schema requires:

- `claim_text`
- `reasoning_text`
- `claim_type`
- `speaker_certainty`
- `extraction_confidence`
- `topics`
- `sentiment`
- `temporal_marker`
- `evidence_spans`

Each evidence span references a transcript segment ID plus quote text and start/end times.

### Stage 5: Embeddings

Claims can be embedded with OpenAI and stored in `claim_embeddings` for semantic search and relevance workflows.

### Stage 6: Position Synthesis

Approved claims are used to update:

- per-person topic positions
- historical position logs

### Stage 7: Delivery

There is also support for generated briefs and delivery plumbing.

---

## 7. Current Public API

### Main read endpoints

- `GET /health`
- `GET /api/people`
- `GET /api/people/{person_id}`
- `GET /api/claims`
- `GET /api/claims/{claim_id}`
- `GET /api/claims/search?q=...`
- `GET /api/topics`
- `GET /api/topics/{slug}/positions`
- `GET /api/topics/{slug}/consensus`
- `POST /api/intelligence/relevant`
- `POST /api/intelligence/divergence`
- `GET /api/briefs/latest`
- `GET /api/pipeline/status`

### Current admin endpoints

- `POST /admin/pipeline/trigger/scan`
- `POST /admin/pipeline/trigger/transcribe`
- `POST /admin/pipeline/trigger/identify`
- `POST /admin/pipeline/trigger/enrich`
- `POST /admin/claims/{claim_id}/review`
- `POST /admin/people`
- `PUT /admin/people/{person_id}`
- `POST /admin/retry/errors`

---

## 8. Current CLI

The project exposes a `bm` CLI with commands including:

- `bm seed`
- `bm serve`
- `bm people ...`
- `bm channels ...`
- `bm topics ...`
- `bm scan run`
- `bm transcribe run`
- `bm identify run`
- `bm enrich run`
- `bm brief ...`

Examples:

- `bm scan run --mode channel`
- `bm scan run --mode search --person "Ray Dalio" --days-back 30`
- `bm transcribe run --pending --limit 10`
- `bm identify run <youtube_video_id>`
- `bm enrich run <youtube_video_id> --no-embed`

---

## 9. Deployment and Operations

### Repository

The project is in GitHub at:

- `https://github.com/Danikz1/believable`

### Local development

Local database uses Docker Compose with:

- `pgvector/pgvector:pg16`
- database name `believable_minds`
- user `bm`

Docker Compose file:

- `/Users/daniyarserikson/my-first-project/believable-people/docker-compose.yml`

### Production hosting

Production is deployed on Railway.

#### Production services

There are two Railway services:

- web/app service: `believable`
- PostgreSQL/pgvector service: `pgvector`

#### Build/runtime

The app is deployed from Docker using:

- `/Users/daniyarserikson/my-first-project/believable-people/Dockerfile`
- `/Users/daniyarserikson/my-first-project/believable-people/scripts/start.sh`

The container:

- uses `python:3.12-slim`
- installs `ffmpeg`
- installs `nodejs`
- copies source, migrations, data, tools, and scripts
- installs the app with `pip install .`

On startup:

1. verifies `DATABASE_URL` exists
2. runs `alembic upgrade head`
3. starts Uvicorn

#### Database setup in production

Production required Railway PostgreSQL with `pgvector`, because the schema depends on vector embeddings.

#### Custom domain

The app is served at:

- `https://serikson.com`

Setup path:

1. Railway generated the public service domain
2. custom domain `www.serikson.com` was added in Railway
3. Namecheap DNS was configured with:
   - `CNAME` for `www` to Railway target
   - Railway TXT verification record
4. root domain redirects to `https://www.serikson.com`

### Environment variables used in production

Important variables include:

- `DATABASE_URL`
- `ADMIN_API_KEY`
- `YOUTUBE_API_KEY`
- `DEEPGRAM_API_KEY`
- `QWEN_API_KEY`
- `QWEN_API_BASE`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `LLM_PROVIDER`
- `OPENAI_MODEL`
- `ANTHROPIC_MODEL`
- `QWEN_MODEL`

Current model defaults in code:

- OpenAI: `gpt-5.2`
- Anthropic: `claude-sonnet-4-6`
- Qwen: `qwen3.5-plus`

### Operational reality in production

The web app is live and functional, but ingestion is not yet fully hands-off.

Main operational issue:

- YouTube sometimes blocks Railway's cloud IPs for caption/audio extraction
- because of that, some production ingestion work has been pushed forward from a local machine against the production database rather than purely from inside Railway

This is a meaningful architectural/ops weakness today.

---

## 10. Current Production State

As of March 15, 2026, the live production state is approximately:

- `47` active people
- `30` topics
- `339` total videos
- `5` enriched videos
- `86` total claims
- `70` approved claims
- `16` pending-review claims

Known approved-claim coverage currently includes at least:

- Harry Stebbings: `53`
- Ray Dalio: `14`
- Jensen Huang: `3`

This means the system is no longer "only Harry," but coverage is still shallow relative to the full person list.

---

## 11. What Is Already Good

- clean stage-based architecture
- good separation between API, pipeline, CLI, and DB layers
- proper relational schema for claims, topics, evidence, and positions
- semantic search support via embeddings + `pgvector`
- deployable with Docker and Railway
- decent automated test coverage for a small project
- direct evidence spans already exist in schema
- topic taxonomy already exists and is partially exposed in UI

Current automated tests pass:

- `22 passed`

---

## 12. Current Weaknesses / Product Gaps

### Ingestion reliability

- YouTube ingestion is still fragile in cloud execution contexts
- Railway IPs can trigger bot checks and 429s
- not all discovered videos move smoothly through the pipeline

### Coverage depth

- many tracked people still have no claims
- system currently feels sparse unless targeted runs are used

### Topic UX

- topics exist, but topic badges across the UI are not consistently clickable
- tags are not yet a strong first-class browsing/search primitive

### Claim provenance UX

- evidence is stored, but claim presentation does not fully expose:
  - exact video source placement
  - timestamped clickable links
  - evidence quotes in a polished way

### Source model rigidity

- the schema is currently centered on YouTube videos as the primary source object
- there is no generic source abstraction for multiple content types such as X posts, newsletters, articles, or podcasts without video

### Review workflow

- public experience is based on approved claims, so sparse review = sparse product
- moderation/review workflow could be more explicit and ergonomic

---

## 13. Three Specific Improvements Wanted Next

### 1. Add X (formerly Twitter)

Goal:

Track important statements made on X in addition to YouTube/interviews.

This should eventually support:

- source discovery for tracked people's X accounts
- ingesting tweet/post text
- linking posts to people
- claim extraction from X posts
- showing those claims alongside video-derived claims

The main architectural question is whether to:

- extend `videos` into a more generic `sources` table
- or add a separate `x_posts` source model and unify claims across source types

### 2. Make topics/tags clickable and searchable

Goal:

Turn topics into a first-class navigation/search layer across the product.

Desired behavior:

- clicking any topic badge anywhere should open a topic page or filtered view
- the Claims page should support topic filtering
- the People page should support filtering by topic
- topic search should be easy and obvious
- topic pages should become richer exploration hubs

### 3. Give every claim a precise, clickable source

Goal:

For every claim, show exactly where it came from.

Desired behavior:

- show whether the source is a video or an X post
- show the exact source title
- show who said it
- show when it was said
- for videos:
  - show minute/second timestamp
  - create clickable deep links like `https://youtube.com/watch?v=...&t=123s`
  - ideally show a quoted evidence snippet
- for X:
  - show post timestamp
  - show clickable post URL

This is especially important because trust in the product depends on source traceability.

---

## 14. Important Technical Note About Claim Sources

The project already stores much of the data needed for precise citation.

Today, it already has:

- `claims.video_id`
- `claim_evidence.segment_id`
- `claim_evidence.start_ms`
- `claim_evidence.end_ms`
- `videos.youtube_video_id`
- `transcript_segments.start_ms`
- `transcript_segments.end_ms`

That means the current system can already generate:

- exact video source links
- exact timestamps
- "jump to where this was said" links

What is missing is mainly:

- public API support that exposes the best evidence span per claim
- frontend rendering for timestamps and jump links
- possibly a generic source abstraction so this also works for X posts

---

## 15. Suggested Product Direction for the Next Model to Evaluate

The next model should think about the product as evolving from:

- a YouTube-centered transcript extraction prototype

into:

- a multi-source "belief tracking" platform

That means the next design should likely address:

1. generic source architecture
2. richer evidence/citation UX
3. stronger topic graph/navigation
4. scalable background-job operations
5. source-specific ingestion strategies

---

## 16. Questions for Another Model

Use the following questions to ask another model for improvements:

1. How should this project be redesigned so YouTube videos and X posts both become first-class source objects without breaking the current pipeline?
2. Should the current `videos` table evolve into a generic `sources` table, or should video and X post sources stay as separate tables behind a common abstraction?
3. What is the best schema and API design for claim provenance so every claim has a precise clickable source with timestamp or post URL?
4. How should topic tags become clickable, filterable, and searchable everywhere in the product?
5. What is the best frontend UX for:
   - person profile pages
   - topic exploration pages
   - claim cards with evidence
   - mixed-source timelines
6. What background-job architecture would make ingestion more reliable than the current Railway-triggered/manual CLI approach?
7. How should moderation/review be improved so sparse approval does not make the app feel empty?
8. What should be changed first if the goal is to make the product feel immediately credible and useful to an end user?

---

## 17. Copy/Paste Prompt for Another Model

You can give another model this prompt:

> I built a project called Believable Minds. It tracks what credible public thinkers say, extracts structured claims from source material, tags those claims by topic, and shows how views evolve over time.  
>  
> Tech stack: FastAPI backend, static HTML/JS frontend, PostgreSQL + pgvector, SQLAlchemy, Alembic, Typer CLI, Docker, Railway deployment, Namecheap custom domain.  
>  
> Current source focus: YouTube videos and podcast/interview content.  
> Current pipeline:  
> 1. discover videos from tracked channels or targeted YouTube search  
> 2. transcribe via YouTube captions / youtube-transcript-api fallback / Deepgram  
> 3. identify speakers and map them to tracked people  
> 4. extract structured claims with LLMs  
> 5. store evidence spans, topic tags, embeddings, and synthesized positions  
> 6. show everything in a web dashboard  
>  
> Current schema includes people, podcast_channels, channel_roles, videos, transcript_runs, transcript_segments, video_people, topics, claims, claim_topics, claim_evidence, claim_embeddings, person_topic_positions, position_history_log, and briefs.  
>  
> Current live app is at https://serikson.com. It is deployed on Railway with a pgvector PostgreSQL service and custom domain via Namecheap. Docker startup runs Alembic migrations and starts Uvicorn.  
>  
> Current production issues:  
> - YouTube ingestion can be unreliable on Railway due to bot checks / 429s  
> - the product still has sparse coverage for many tracked people  
> - topic badges exist but are not yet a first-class clickable/searchable navigation system everywhere  
> - claims already store evidence spans and timestamps, but the UI does not fully expose exact source provenance with clickable deep links  
> - the data model is still mostly YouTube-video-centric  
>  
> I want to improve this project in three specific ways:  
> 1. add X (formerly Twitter) as a source  
> 2. make topic tags clickable and searchable everywhere  
> 3. make every claim show an exact source: which video or X post, when it was said, and for videos the exact minute/second with a clickable link  
>  
> Please propose a comprehensive improvement plan covering:  
> - data model changes  
> - API changes  
> - frontend UX changes  
> - ingestion pipeline changes  
> - deployment / background-job improvements  
> - review workflow improvements  
> - migration strategy from the current schema  
> - which changes should be done first for maximum product impact  

---

## 18. Constraint for the Next Model

The next model should not respond with vague product advice only. It should give:

- concrete schema recommendations
- concrete API recommendations
- frontend interaction design suggestions
- operational/deployment recommendations
- sequencing and migration plan

Ideally it should explicitly distinguish:

- short-term improvements using the current architecture
- medium-term refactors
- long-term redesign if the product becomes multi-source and production-grade

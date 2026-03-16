# Believable Minds v2 — Claude Code Implementation Spec

## What This Is

This spec redesigns the frontend and makes targeted backend changes to transform Believable Minds from a data pipeline dashboard into a reader/intelligence product.

**The v2 product has three pages:** Feed, People, Channels. No claims explorer, no pipeline dashboard, no review queue in the UI. The pipeline still runs — users just don't see it.

**Repository:** https://github.com/Danikz1/believable  
**Live:** https://serikson.com  
**Stack:** FastAPI, SQLAlchemy 2, PostgreSQL + pgvector, static HTML/JS frontend, Railway

---

## Orientation: Key Files

```
src/
├── api/
│   ├── app.py              # FastAPI entrypoint. Serves static/index.html at /
│   ├── public.py           # All GET /api/* endpoints (people, claims, summaries, favorites)
│   └── admin.py            # POST /admin/* endpoints (pipeline triggers, review)
├── db/
│   ├── models.py           # All SQLAlchemy models (People, Videos, Claims, EpisodeSummaries, Favorites, etc.)
│   ├── seed.py             # Seed data loader
│   └── session.py          # DB session factory
├── pipeline/
│   ├── discovery.py        # YouTube channel scanning + search gap-fill
│   ├── transcription.py    # yt-dlp captions + Deepgram ASR
│   ├── identification.py   # Speaker → person mapping
│   ├── enrichment.py       # LLM claim extraction
│   ├── positions.py        # Position synthesis + shift detection
│   ├── summaries.py        # 3-level episode summary generation
│   └── ...
├── cli/
│   ├── channels.py         # bm channels add/list/edit/remove
│   ├── scan.py             # bm scan run
│   └── ...
data/
├── people_seed.json        # 47 tracked people
├── channels_seed.json      # 33 tracked channels
├── topics_seed.json        # 30 topic taxonomy
└── channel_roles_seed.json # 15 host/cohost mappings
alembic/versions/
├── 001_initial.py
├── 002_add_transcript_cols.py
├── 003_add_briefs_table.py
├── 004_add_favorites_and_summaries.py
└── 005_add_x_twitter_support.py
```

The frontend is a single file: `src/api/static/index.html` (1622 lines). This will be completely replaced.

---

## Phase 1: Schema Additions

Create `alembic/versions/006_v2_redesign.py`.

### 1A. Add columns to `people`

```sql
ALTER TABLE people ADD COLUMN bio TEXT;
ALTER TABLE people ADD COLUMN role_title TEXT;
ALTER TABLE people ADD COLUMN net_worth TEXT;
ALTER TABLE people ADD COLUMN age INTEGER;
ALTER TABLE people ADD COLUMN photo_initials TEXT;
ALTER TABLE people ADD COLUMN accent_color TEXT;
```

Update `src/db/models.py` — add to the `People` class:

```python
bio = Column(Text)                 # 3-4 sentence bio
role_title = Column(Text)          # "CEO, Anthropic"
net_worth = Column(Text)           # "$15.4B" as display string
age = Column(Integer)              # nullable
photo_initials = Column(Text)      # "DA" — 2 chars for avatar
accent_color = Column(Text)        # "#8b5cf6" hex
```

All nullable. Existing rows get NULL values which the frontend handles gracefully.

### 1B. Add `sentiment` to `person_topic_positions`

```sql
ALTER TABLE person_topic_positions ADD COLUMN sentiment TEXT;
```

In models.py:
```python
sentiment = Column(Text)  # bullish / bearish / neutral / cautious / urgent / strong
```

### 1C. Add shift context to `position_history_log`

```sql
ALTER TABLE position_history_log ADD COLUMN shift_note TEXT;
ALTER TABLE position_history_log ADD COLUMN previous_position TEXT;
```

In models.py:
```python
shift_note = Column(Text)          # Human-readable shift explanation
previous_position = Column(Text)   # What the prior position was
```

### 1D. Add scanning metadata to `podcast_channels`

```sql
ALTER TABLE podcast_channels ADD COLUMN last_scanned_at TIMESTAMPTZ;
ALTER TABLE podcast_channels ADD COLUMN video_count INTEGER DEFAULT 0;
```

In models.py:
```python
last_scanned_at = Column(DateTime(timezone=True))
video_count = Column(Integer, default=0)
```

### 1E. Make `videos.source_channel_youtube_id` nullable

Currently `NOT NULL`. One-off videos from unknown channels need this relaxed.

```sql
ALTER TABLE videos ALTER COLUMN source_channel_youtube_id DROP NOT NULL;
```

In models.py, change:
```python
# FROM:
source_channel_youtube_id = Column(Text, nullable=False)
# TO:
source_channel_youtube_id = Column(Text, nullable=True)
```

### 1F. Fix claims XOR constraint

The current codebase has OR. Change to XOR per amendment v2.

```sql
ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claims_source_required;
ALTER TABLE claims ADD CONSTRAINT ck_claims_source_xor
    CHECK ((video_id IS NOT NULL) <> (x_post_id IS NOT NULL));
```

---

## Phase 2: New API Endpoints

All in `src/api/public.py`. Follow existing patterns.

### 2A. `GET /api/channels`

```python
@router.get("/channels")
def list_channels(db: Session = Depends(get_db)):
    """List all active channels with video counts and scan status."""
    channels = (
        db.query(PodcastChannels)
        .filter(PodcastChannels.active == True)
        .order_by(PodcastChannels.tier, PodcastChannels.name)
        .all()
    )
    return [
        {
            "id": str(ch.id),
            "name": ch.name,
            "youtube_channel_id": ch.youtube_channel_id,
            "tier": ch.tier,
            "monitoring_mode": ch.monitoring_mode,
            "video_count": (
                db.query(Videos)
                .filter(Videos.podcast_channel_id == ch.id)
                .count()
            ),
            "last_scanned_at": (
                ch.last_scanned_at.isoformat() if ch.last_scanned_at else None
            ),
        }
        for ch in channels
    ]
```

### 2B. `POST /api/channels`

Accepts a YouTube URL or handle. Resolves channel ID, creates the row, triggers an initial scan.

```python
class ChannelCreate(BaseModel):
    url_or_handle: str  # "@dwarkesh" or "https://youtube.com/@dwarkesh" or channel ID

@router.post("/channels")
def add_channel(req: ChannelCreate, db: Session = Depends(get_db)):
    """Add a new YouTube channel to monitor."""
    # 1. Parse input → resolve to youtube_channel_id
    #    Use yt-dlp: yt-dlp --print channel_id "https://youtube.com/@handle"
    #    Or if raw channel ID, use directly
    # 2. Check for duplicates
    # 3. Create PodcastChannels row with tier=2, monitoring_mode='channel_feed'
    # 4. Return the created channel
    
    # See src/cli/channels.py::add_channel() for existing logic to adapt
```

**Implementation note:** The channel resolution logic exists in `src/pipeline/discovery.py::_repair_channel_id()` and `src/cli/channels.py::add_channel()`. Extract the `yt-dlp` channel ID resolution into a shared utility, then call it from both CLI and API.

### 2C. `POST /api/videos/add`

Accepts a YouTube video URL. Creates a video record for one-off transcription.

```python
class VideoAddRequest(BaseModel):
    youtube_url: str  # "https://youtube.com/watch?v=ABC123"

@router.post("/videos/add")
def add_video(req: VideoAddRequest, db: Session = Depends(get_db)):
    """Add a single video for transcription and summarization."""
    # 1. Parse URL → extract youtube_video_id
    # 2. Check for duplicates (Videos.youtube_video_id unique)
    # 3. Optionally resolve the channel name via yt-dlp metadata
    # 4. Create Videos row:
    #    - youtube_video_id = parsed ID
    #    - source_channel_youtube_id = resolved or NULL (now nullable)
    #    - podcast_channel_id = matched channel or NULL
    #    - discovery_method = 'manual'
    #    - status = 'discovered'
    # 5. Return { "video_id": ..., "status": "queued" }
```

**Important:** This does NOT run the pipeline synchronously. The video is created with `status='discovered'`. The user (or a cron job) runs the pipeline to process it. The frontend shows a "Processing..." state for videos without summaries.

### 2D. `POST /api/channels/{channel_id}/scan`

Triggers a scan for one channel. The "Check Now" button calls this.

```python
@router.post("/channels/{channel_id}/scan")
def trigger_channel_scan(channel_id: UUID, db: Session = Depends(get_db)):
    """Scan a single channel for new videos."""
    channel = db.query(PodcastChannels).filter(PodcastChannels.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Channel not found")
    
    # Import and call the single-channel scan function
    from src.pipeline.discovery import _scan_single_channel, ScanResult
    result = ScanResult()
    _scan_single_channel(db, channel, result, limit=50)
    
    # Update last_scanned_at
    channel.last_scanned_at = datetime.now(timezone.utc)
    channel.video_count = db.query(Videos).filter(Videos.podcast_channel_id == channel.id).count()
    db.commit()
    
    return {
        "status": "scanned",
        "new_videos": result.new,
        "total_videos": channel.video_count,
    }
```

### 2E. `GET /api/positions/shifts`

Returns recent position shifts across all people for the "⚡ Recent Position Shifts" banner.

```python
@router.get("/positions/shifts")
def recent_position_shifts(
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db),
):
    """Recent position shifts across all tracked people."""
    shifts = (
        db.query(PositionHistoryLog)
        .filter(PositionHistoryLog.is_shift == True)
        .order_by(PositionHistoryLog.recorded_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(s.id),
            "person_id": str(s.person_id),
            "person_name": s.person.name if s.person else None,
            "person_photo_initials": s.person.photo_initials if s.person else None,
            "person_accent_color": s.person.accent_color if s.person else None,
            "topic_id": str(s.topic_id),
            "topic_name": s.topic.name if s.topic else None,
            "topic_slug": s.topic.slug if s.topic else None,
            "position_summary": s.position_summary,
            "previous_position": s.previous_position,
            "shift_note": s.shift_note,
            "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
        }
        for s in shifts
    ]
```

### 2F. Update existing endpoints

**`GET /api/people` — add new bio fields to response:**

In the list comprehension, add:
```python
"role_title": p.role_title,
"bio": p.bio,
"net_worth": p.net_worth,
"age": p.age,
"photo_initials": p.photo_initials or _initials(p.name),
"accent_color": p.accent_color or "#666",
```

Helper:
```python
def _initials(name: str) -> str:
    parts = name.split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else name[:2].upper()
```

**`GET /api/people/{person_id}` — add new fields + positions with sentiment:**

Add to the response dict:
```python
"role_title": person.role_title,
"bio": person.bio,
"net_worth": person.net_worth,
"age": person.age,
"photo_initials": person.photo_initials or _initials(person.name),
"accent_color": person.accent_color or "#666",
```

Update positions to include sentiment:
```python
"positions": [
    {
        "topic_id": str(p.topic_id),
        "topic": p.topic.slug if p.topic else None,
        "topic_name": p.topic.name if p.topic else None,
        "current_position": p.current_position,
        "sentiment": p.sentiment,
        "claim_count": p.claim_count,
        "last_updated": p.last_updated.isoformat() if p.last_updated else None,
    }
    for p in positions
],
```

Add position shifts for this person:
```python
"shifts": [
    {
        "topic_name": s.topic.name if s.topic else None,
        "position_summary": s.position_summary,
        "previous_position": s.previous_position,
        "shift_note": s.shift_note,
        "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
    }
    for s in (
        db.query(PositionHistoryLog)
        .filter(PositionHistoryLog.person_id == person.id, PositionHistoryLog.is_shift == True)
        .order_by(PositionHistoryLog.recorded_at.desc())
        .limit(10)
        .all()
    )
],
```

Add recent appearances (episode summaries featuring this person):
```python
"appearances": [
    {
        "video_id": str(s.video_id),
        "video_title": s.video.title if s.video else None,
        "channel_name": s.video.podcast_channel.name if s.video and s.video.podcast_channel else None,
        "published_at": s.video.published_at.isoformat() if s.video and s.video.published_at else None,
        "watch_verdict": s.watch_verdict,
        "tldr": s.tldr,
    }
    for s in (
        db.query(EpisodeSummaries)
        .filter(EpisodeSummaries.person_focus_id == person.id)
        .order_by(EpisodeSummaries.generated_at.desc())
        .limit(10)
        .all()
    )
],
```

**`GET /api/summaries/feed` — add duration + youtube_video_id:**

In `_summary_card()`, add:
```python
"duration_seconds": video.duration_seconds if video else None,
"duration_display": _format_duration(video.duration_seconds) if video and video.duration_seconds else None,
"youtube_video_id": yt_id,
"channel_id": str(video.podcast_channel_id) if video and video.podcast_channel_id else None,
```

Helper:
```python
def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
```

---

## Phase 3: Pipeline Change — Shift Notes

Modify `src/pipeline/positions.py::_update_position_for_topic()`.

When `is_shift = True`, generate a shift explanation and store the previous position.

```python
def _update_position_for_topic(session, person_id, topic, claim):
    position = session.query(PersonTopicPositions).filter(
        PersonTopicPositions.person_id == person_id,
        PersonTopicPositions.topic_id == topic.id,
    ).first()

    previous_position = None
    if position:
        previous_position = position.current_position
        position.current_position = claim.claim_text[:500]
        position.last_updated = datetime.now(timezone.utc)
        position.claim_count = (position.claim_count or 0) + 1
        # Derive sentiment from claim
        position.sentiment = claim.sentiment  # NEW
    else:
        position = PersonTopicPositions(
            person_id=person_id,
            topic_id=topic.id,
            current_position=claim.claim_text[:500],
            last_updated=datetime.now(timezone.utc),
            claim_count=1,
            sentiment=claim.sentiment,  # NEW
        )
        session.add(position)

    is_shift = False
    if previous_position and claim.claim_text:
        is_shift = _detect_shift(previous_position, claim.claim_text)

    # Generate shift note if shift detected  # NEW BLOCK
    shift_note = None
    if is_shift:
        shift_note = _generate_shift_note(
            person_name=claim.person.name if claim.person else "Unknown",
            topic_name=topic.name,
            previous=previous_position,
            current=claim.claim_text,
        )

    log_entry = PositionHistoryLog(
        person_id=person_id,
        topic_id=topic.id,
        position_summary=claim.claim_text[:500],
        source_claim_id=claim.id,
        is_shift=is_shift,
        previous_position=previous_position,  # NEW
        shift_note=shift_note,                # NEW
    )
    session.add(log_entry)
    # ... rest unchanged
```

Add the shift note generator:

```python
def _generate_shift_note(person_name: str, topic_name: str, previous: str, current: str) -> str:
    """Generate a human-readable shift explanation using LLM."""
    try:
        from src.providers.llm import call_llm_text
        prompt = (
            f"{person_name}'s previous position on {topic_name} was:\n"
            f'"{previous}"\n\n'
            f"Their new position is:\n"
            f'"{current}"\n\n'
            "In 1-2 sentences, explain what shifted and why it matters. "
            "Be specific about what changed. Do not say 'the position shifted.' "
            "Say WHAT changed and in WHICH direction."
        )
        return call_llm_text(prompt, max_tokens=200)
    except Exception as e:
        logger.warning(f"Failed to generate shift note: {e}")
        return f"Position shifted from a prior view on {topic_name}."
```

**Note:** `call_llm_text` may not exist yet in `src/providers/llm.py`. The existing code has `call_llm_json`. Add a `call_llm_text` variant that returns raw text instead of parsed JSON, or use `call_llm_json` with a JSON wrapper.

---

## Phase 4: Seed Data Update

Update `data/people_seed.json` to include the new bio fields. The format becomes:

```json
{
  "name": "Ray Dalio",
  "domain": "Macro / Principles",
  "tier": 1,
  "inclusion_notes": "History's most successful macro investor...",
  "expertise_domains": ["macro", "debt_cycles", "geopolitics", "value_investing"],
  "youtube_search_queries": ["Ray Dalio interview", "Ray Dalio keynote"],
  "role_title": "Founder, Bridgewater Associates",
  "bio": "Built the world's largest hedge fund ($150B+ AUM at peak). Author of 'Principles' and 'The Changing World Order.' Known for radical transparency, systematic decision-making, and macro frameworks based on historical debt cycles. Stepped down as co-CIO in 2022 but remains the most-cited voice in macro circles. Born in Jackson Heights, Queens.",
  "net_worth": "$15.4B",
  "age": 76,
  "photo_initials": "RD",
  "accent_color": "#f59e0b"
}
```

Update `src/db/seed.py` to handle the new fields during seeding. The seed function already does upsert logic — just add the new column mappings.

**This is the most tedious part of the project.** Each of the 47 people needs: `role_title`, `bio` (3-4 sentences), `net_worth`, `age`, `photo_initials`, and `accent_color`. Generate this data with an LLM or populate manually for the top 15-20 people and leave the rest as NULL.

---

## Phase 5: Frontend Rewrite

**Replace `src/api/static/index.html` entirely.** The current 1622-line file is a multi-page SPA with 7 navigation items. The new version has 3 pages.

The frontend is a single static HTML file with embedded CSS and JavaScript. It does NOT use React or any framework — it uses vanilla JS with `fetch()` calls to the API. Follow the same pattern as the current `index.html`.

### Design Language

- Background: `#050505` (near black)
- Cards: `#111111` with `#222222` borders
- Text primary: `#e5e5e5`
- Text secondary: `#888888`
- Text muted: `#555555`
- Font: DM Sans (load from Google Fonts)
- Font for long-form reading: Newsreader (serif, from Google Fonts)
- Border radius: 12px for cards, 8px for buttons, 20px for pills
- Per-person/channel accent colors from the `accent_color` field

### Navigation

Sticky top header bar with:
- "Believable Minds" logo text (left)
- Three nav tabs: Feed | People | Channels
- "+ Add Video" button (right)

Active tab has white text + white bottom border. Inactive has #555 text.

### Feed Page

**Data sources:**
- `GET /api/summaries/feed?limit=30` — main feed
- `GET /api/channels` — for channel filter chips

**Layout:**
1. Page header: "Feed" title + subtitle
2. Channel filter chips row (from channels endpoint). "All" is default. Clicking a chip re-fetches with `?channel_id=X`
3. Verdict legend row (compact, muted background)
4. Episode list — each row shows:
   - Channel name (colored by accent) + date + duration
   - Episode title (bold, 15px)
   - TL;DR text (truncated to 2 lines, 13px, muted)
   - Watch verdict badge (right-aligned): green "ESSENTIAL", amber "WORTH SKIMMING", gray "SKIP UNLESS FAN"
   - If the video has `discovery_method='manual'` and no `podcast_channel_id`, show a purple "ONE-OFF" badge

Clicking an episode row → navigates to the episode detail view.

### Episode Detail View

**Data source:** The summary data is already in the feed response. For the detailed breakdown, use the `detailed_json` field from the summary. For the full YouTube link, use `youtube_video_id` from the response.

**Layout:**
1. "← Back to feed" link
2. Channel name + date + duration
3. Episode title (large, serif font)
4. Watch verdict badge + verdict reason (italic)
5. "▶ Watch on YouTube" red button (links to `youtube.com/watch?v={id}`)
6. **Three-depth toggle:** "30-Sec TL;DR" | "2-Min Read" | "Full Breakdown"
   - TL;DR: renders `tldr` field in large serif text
   - 2-Min Read: renders `summary_body` field, splitting on newlines into paragraphs
   - Full Breakdown: renders `detailed_json.sections` as a list. Each section has:
     - Monospace timestamp badge (clickable → YouTube at `&t=Xs`)
     - Section title (bold)
     - Section summary (muted)
7. **Best Moments** section — from `detailed_json.best_moments`. Each has:
   - Green timestamp badge (clickable)
   - Description (bold)
   - Quote snippet (italic, muted)
8. **Position Shift card** (if `whats_new` is non-null and not "Consistent with prior positions"):
   - Amber background (#1a1000)
   - "⚡ Position Shift Detected" header
   - Shift text

### People Page

**Data sources:**
- `GET /api/people` — people list
- `GET /api/positions/shifts?limit=10` — for the shift banner
- Topic tags derived from `expertise_domains` on each person

**Layout:**
1. Page header: "People" + "Tracked minds and their current positions"
2. **Tag filter pills** — aggregated from all people's `expertise_domains`. Clicking a tag filters the list to people who have that domain. "All" clears the filter.
3. **⚡ Recent Position Shifts banner** (amber background):
   - Shows data from `/api/positions/shifts`
   - Each shift shows: person avatar (initials + accent color), person name, topic name, shift_note
   - Clicking a shift → opens that person's detail page
   - Only shows if there are shifts. Hidden if empty.
4. **People list** — each row shows:
   - Avatar circle (initials, colored border from accent_color)
   - Name (bold, 15px) + shift badge if any shifts exist ("⚡ 2 shifts")
   - Role + net worth (12px, muted)
   - Expertise domain tags (tiny pills, 10px)
   - Position count (right-aligned, large number)

Clicking a person → navigates to person detail view.

### Person Detail View

**Data source:** `GET /api/people/{person_id}`

**Layout:**
1. "← Back to people" link
2. **Header row:** Avatar (72px, gradient background with accent color) + Name (large, serif) + role_title + age + net worth
3. **Tags** — expertise_domains as clickable pills. Clicking a tag navigates back to the People list filtered by that tag.
4. **Bio** — the `bio` field rendered in serif font with a left accent border. If bio is null, fall back to `inclusion_notes`.
5. **Current Positions** section:
   - Each position shows:
     - Colored sentiment dot (green=bullish, red=bearish, amber=cautious/urgent, purple=strong, gray=neutral)
     - Topic name (clickable → People list filtered by that topic)
     - Date (muted)
     - "⚡ SHIFTED" badge if there's a matching shift
     - Position text (14px)
     - If shifted: amber card below with `shift_note` from the shifts array
6. **Recent Appearances** section (if `appearances` array is non-empty):
   - Each appearance shows: channel name + date + episode title
   - Clicking → navigates to that episode's summary

### Channels Page

**Data source:** `GET /api/channels`

**Layout:**
1. Page header: "Your Channels" + subtitle + "+ Add Channel" button
2. **Grid of channel cards** (auto-fill, min 280px per card):
   - Left accent border (use a rotating set of colors, or derive from channel name hash)
   - Channel name (bold) + handle
   - Video count (large accent-colored number)
   - "Checked {time}" text + "↻ Check Now" button
   - Clicking the card → navigates to Feed filtered by that channel
   - "Check Now" → `POST /api/channels/{id}/scan`, shows "Checking..." state for 3 seconds, then re-fetches channel list

### Add Channel Modal

Triggered by "+ Add Channel" button on Channels page.

- Input field: "Paste a YouTube channel URL or handle"
- Submit → `POST /api/channels` with the input value
- On success: close modal, re-fetch channels list
- On error: show error message in the modal

### Add Video Modal

Triggered by "+ Add Video" button in the top nav.

- Input field: "Paste any YouTube video URL"
- Subtitle: "It'll be transcribed and summarized — even if the channel isn't tracked."
- Submit → `POST /api/videos/add` with the URL
- On success: close modal, show a toast/notification "Video queued for processing"
- On error: show error message

### Navigation / Routing

Use hash-based routing (same pattern as current `index.html`):

```javascript
// URL patterns:
// #feed                     → Feed page
// #feed?channel=uuid        → Feed filtered by channel
// #people                   → People list
// #people?tag=macro         → People filtered by tag
// #people/{person_id}       → Person detail
// #episode/{video_id}       → Episode detail
// #channels                 → Channels page
```

The `navigate(page, params)` function updates the hash and renders the appropriate view. Listen to `hashchange` events.

---

## Phase 6: Testing

### API Tests

Add to `tests/`:

```python
def test_list_channels(client):
    """GET /api/channels returns active channels with video counts."""
    
def test_add_channel(client):
    """POST /api/channels creates a channel from URL or handle."""

def test_add_video(client):
    """POST /api/videos/add creates a video record."""

def test_channel_scan(client):
    """POST /api/channels/{id}/scan triggers a scan."""

def test_position_shifts(client):
    """GET /api/positions/shifts returns recent shifts."""

def test_people_has_bio_fields(client):
    """GET /api/people returns role_title, bio, net_worth, etc."""

def test_person_detail_has_shifts(client):
    """GET /api/people/{id} includes shifts and appearances."""
```

### Manual Smoke Test

After deployment:
1. Open serikson.com → should show Feed page
2. Click "Channels" → should show channel cards
3. Click "+ Add Channel" → modal opens, paste "@dwarkesh" → channel added
4. Click a channel card → Feed filters to that channel
5. Click an episode → detail page with 3 depth levels
6. Click timestamp badge → opens YouTube at correct moment
7. Click "People" → list with tag filters
8. ⚡ shift banner shows if any shifts exist
9. Click a person → detail with bio, positions, appearances
10. Click "+ Add Video" → paste a YouTube URL → video queued

---

## Build Sequence

Execute in this order. Each step is independently deployable.

| Step | What | Files touched | Estimated effort |
|------|------|---------------|-----------------|
| 1 | Alembic migration 006 | `alembic/versions/006_v2_redesign.py`, `src/db/models.py` | 30 min |
| 2 | New API endpoints (2A-2E) | `src/api/public.py` | 2 hours |
| 3 | Update existing endpoints (2F) | `src/api/public.py` | 1 hour |
| 4 | Pipeline: shift notes + sentiment | `src/pipeline/positions.py` | 1 hour |
| 5 | Seed data: add bio fields for top 20 people | `data/people_seed.json`, `src/db/seed.py` | 2 hours |
| 6 | Frontend rewrite | `src/api/static/index.html` | 4-6 hours |
| 7 | Tests | `tests/` | 1 hour |
| 8 | Deploy + smoke test | Railway | 30 min |

**Total: ~12-15 hours of focused work.**

---

## What NOT to Change

- **Pipeline code** (`discovery.py`, `transcription.py`, `identification.py`, `enrichment.py`, `summaries.py`) — leave untouched. The pipeline works. The redesign is about presentation, not data production.
- **CLI commands** — leave all `bm` commands working. They're the operator interface.
- **Admin API** — leave untouched. Review workflow stays as-is.
- **Database structure for claims, evidence, topics, etc.** — no changes. The additions are all additive.
- **Docker / deployment config** — no changes needed. Same Dockerfile, same Railway setup.

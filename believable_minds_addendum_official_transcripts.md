# Believable Minds — Addendum: Official Transcript Ingestion

**Status:** Addendum to the Final Spec. Apply during Stage 3 implementation.  
**Date:** March 2026  
**Scope:** Adds a highest-priority transcript path for podcasts that publish human-edited transcripts.

---

## Why This Matters

Several tracked podcasts publish human-edited, speaker-labeled transcripts on their websites. These are strictly superior to any ASR output:

| Source | Speaker labels | Timestamps | Edit quality | Cost | Attribution confidence |
|---|---|---|---|---|---|
| Official transcript | Explicit (human-verified) | Per-utterance or per-section | Human-edited | Free | 0.98 |
| Deepgram Nova-3 | Inferred (diarization) | Per-word | ASR (good) | ~$0.49/hr | 0.85 |
| YouTube auto-captions | None | Per-phrase | ASR (variable) | Free | 0.50 |

An official transcript eliminates the entire Deepgram cost for that episode AND skips the hardest part of the pipeline (speaker diarization + identification). The speaker names come pre-resolved.

---

## Schema Changes

### podcast_channels — add 2 columns

```sql
ALTER TABLE podcast_channels ADD COLUMN transcript_url_pattern TEXT;
-- Examples:
--   'https://www.dwarkesh.com/p/{slug}'
--   'https://lexfridman.com/{slug}-transcript'
-- NULL if channel does not publish transcripts.
-- {slug} is resolved per-episode from video metadata.

ALTER TABLE podcast_channels ADD COLUMN transcript_parser TEXT;
-- Which parser module to use: 'dwarkesh_substack', 'lex_fridman', 'generic_substack'
-- NULL if transcript_url_pattern is NULL.
```

**Why two fields, not one:** The URL pattern tells us *where* to find the transcript. The parser tells us *how* to extract structured data. Two channels could use the same Substack platform (same parser) but have different URL patterns.

### Enum additions

Add to `transcript_runs.mode` CHECK constraint:

```sql
-- Existing: 'caption', 'asr_plain', 'asr_diarized'
-- Add: 'official_transcript'
```

Add to `transcript_segments.source_kind` CHECK constraint:

```sql
-- Existing: 'caption', 'asr', 'asr_diarized'
-- Add: 'official'
```

Add to `videos.transcript_type` values:

```sql
-- Existing: 'deep', 'fast'
-- Add: 'official'
```

Add to `video_people.identified_via` CHECK constraint:

```sql
-- Existing: 'known_host', 'diarization_llm', 'metadata_only', 'manual'
-- Add: 'official_transcript'
```

### No new tables required

Official transcripts flow through the existing `transcript_runs` → `transcript_segments` pipeline. The `mode='official_transcript'` and `source_kind='official'` values distinguish them from ASR output.

---

## Stage 3: Updated Path Selection Logic

Official transcripts are **Priority 1** — checked before Deepgram or captions.

| Priority | Condition | Path | transcript_type |
|---|---|---|---|
| **1 (new)** | Channel has `transcript_url_pattern` AND transcript page resolves AND parser succeeds | Official transcript | 'official' |
| 2 | Tier 1 content, or conversational with tracked person | Deepgram deep path | 'deep' |
| 3 | Everything else | yt-dlp captions fast path | 'fast' |

### Official Transcript Path — Processing Steps

1. **Check eligibility:** Video is from a channel with non-NULL `transcript_url_pattern`.

2. **Resolve the transcript URL:**
   - Extract the slug from video metadata (title, description, or URL).
   - Slug resolution strategy:
     - **Description link (preferred):** Many podcasters include a direct link to the transcript page in the YouTube video description. Search for URL patterns matching the `transcript_url_pattern` domain. This is the most reliable method — no slug guessing needed.
     - **Title-based slug:** Derive from episode title. E.g., "Elon Musk — In 36 months..." → `elon-musk`. Use the same slugification rules as the publisher (lowercase, hyphens, strip special characters).
     - **LLM fallback:** If both methods fail, send video title + description to the LLM and ask it to guess the slug. Flag as low-confidence.
   - **Important:** The resolved URL must be validated with an HTTP HEAD request before fetching. A 404 means the transcript isn't published yet — fall through to Priority 2 (Deepgram).

3. **Fetch and parse:**
   - Create a `transcript_runs` record with `mode='official_transcript'`, `provider='{parser_name}'` (e.g., `'dwarkesh_substack'`).
   - Fetch the transcript page.
   - Run the channel-specific parser (see Parser Specifications below).
   - Output: array of `{speaker_name, text, start_ms, end_ms}`.

4. **Store segments:**
   - Store in `transcript_segments` with:
     - `speaker_label` = NULL (official transcripts don't have "SPEAKER_00" labels)
     - `speaker_name` = the speaker name as it appears in the transcript (e.g., "Elon Musk")
     - `person_id` = NULL (resolved in Stage 4 — see below)
     - `source_kind` = 'official'
     - `start_ms` / `end_ms` from parsed timestamps
   - Set `videos.transcript_type` = 'official'.

5. **Fallback on failure:**
   - If the URL 404s → fall through to Deepgram deep path.
   - If the parser fails (unexpected format) → log error on transcript_run, fall through to Deepgram.
   - If timestamps are missing but speaker labels exist → store segments with `start_ms=0, end_ms=1` (satisfies the `start_ms < end_ms` CHECK constraint on transcript_segments) and set `has_timestamps=False` in the parser output. Flag the transcript_run for review. The text and attribution are still valuable even without timestamps.
   - A failed official transcript attempt does NOT block other transcript paths. The video can have multiple `transcript_runs` records.

### Why speaker_name but not person_id in Stage 3

Official transcripts give us display names like "Elon Musk" or "Dwarkesh Patel" — but mapping these to `people.id` requires matching against the people table, which is a Stage 4 concern. Stage 3 only extracts what the source provides. This maintains the separation of concerns established in the spec.

**Schema note:** The main spec's `transcript_segments.speaker_name` column says "Resolved name (NULL until Stage 4)." For official transcripts, `speaker_name` is populated in Stage 3 directly from the source. The column description applies to ASR paths only. No schema change is needed — the column is nullable TEXT either way — but implementers should be aware that official transcript segments arrive in Stage 4 with `speaker_name` already set and `speaker_label` NULL, which is the inverse of ASR segments (where `speaker_label` is set and `speaker_name` is NULL).

---

## Stage 4: Official Transcript Identification

Official transcripts need a simpler identification path than ASR transcripts, but they still need one.

### Mode C: Official Transcript Videos (new)

These have `speaker_name` populated directly from the transcript. The task is mapping display names to `people.id`:

1. **Exact match:** For each unique `speaker_name` in the segments, look up `people.name` (exact, case-insensitive). Match → set `person_id` on all segments for that speaker.

2. **Alias match:** If exact match fails, check `speaker_name` against common variations. E.g., "Patrick" in a Stripe Sessions transcript should match "Patrick Collison" when the channel's `channel_roles` include Patrick Collison as host. Use `channel_roles` as a prior — if someone is a known host, accept first-name matches.

3. **Unmatched speakers:** If a speaker name doesn't match any tracked person (e.g., an unknown guest), leave `person_id` NULL. Set `video_people` entry with `identified_via='official_transcript'` and `confidence=0.98` for matched speakers.

4. **No LLM call needed** in most cases. The publisher already did the attribution. Only use the LLM if the speaker names are ambiguous (rare for professional transcripts).

### Trust implications

| identified_via | attribution_confidence | trust_level |
|---|---|---|
| official_transcript | 0.98 | high |
| known_host | 0.95 | high |
| diarization_llm | 0.85 | high (if ≥0.80) or medium |
| metadata_only | 0.50 | low |
| manual | 1.00 | high |

Official transcripts slot between manual and known_host — the publisher verified who spoke, but there's a small chance of transcription error.

---

## Stage 5: Extraction from Official Transcripts

No special handling needed. Official transcript segments flow into enrichment identically to deep-path segments:

- Segments are batched by `person_id` (resolved in Stage 4).
- Sent to Qwen3.5-Plus with the same tool schema.
- Evidence spans reference `segment_id` as normal.
- Since speaker attribution comes from the publisher, `attribution_confidence` is 0.98 for all claims from these segments.
- `trust_level` = 'high' → `review_status` auto-set per existing rules.

The only difference: `claim_evidence` rows from official transcripts will have higher-quality `quote_text` (human-edited vs. ASR-generated), making the evidence trail more readable in the dashboard.

**Edge case:** If a segment has placeholder timestamps (`start_ms=0, end_ms=1` because the official transcript lacked per-utterance timestamps), the Stage 5 tool output should still reference the `segment_id` but the `start_ms`/`end_ms` on `claim_evidence` should use the section-level timestamp from the transcript (if available) rather than the placeholder values. If no timestamps exist at all, `claim_evidence` rows get `start_ms=0, end_ms=1` — the dashboard should detect this and show "timestamp unavailable" instead of "0:00".

---

## Parser Specifications

Each parser takes an HTML page and returns:

```python
@dataclass
class ParsedSegment:
    speaker_name: str        # "Elon Musk"
    text: str                # The utterance text
    start_ms: int            # Milliseconds from start (0 if not available)
    end_ms: int              # Milliseconds from start (0 if not available)
    has_timestamps: bool     # Whether timestamps were parsed or defaulted to 0
```

### Parser: dwarkesh_substack

**Source format:**
```
**Elon Musk**

Are there really three hours of questions? Are you fucking serious?

**Dwarkesh Patel**

You don't think there's a lot to talk about, Elon?
```

Section headers like `### 00:00:00 - Orbital data centers` provide timestamps. Utterances between section headers inherit the section's timestamp. Consecutive utterances by the same speaker within a section are merged.

**Parsing rules:**
- Speaker change: `**Name**` on its own line (bold text in markdown).
- Timestamp: `### HH:MM:SS` section headers → convert to milliseconds.
- Segments between timestamp headers inherit the preceding header's `start_ms`. `end_ms` is set to the next header's `start_ms`, or 0 for the last section.
- Known channels using this format: Dwarkesh Podcast, Cheeky Pint (John Collison).

### Parser: lex_fridman

**Source format:**
```
Peter Steinberger
[(00:00:00)](https://youtube.com/watch?v=YFjfBk8HI5o&t=0) 
I watched my agent happily click the "I'm not a robot" button.

Lex Fridman
[(00:00:31)](https://youtube.com/watch?v=YFjfBk8HI5o&t=31) 
You prefer agentic engineering?
```

**Parsing rules:**
- Speaker change: bare name on its own line (no bold markup), followed by a line with `[(HH:MM:SS)]`.
- Timestamp: extracted from the `[(HH:MM:SS)]` pattern. Per-utterance precision.
- `start_ms` from the current utterance's timestamp. `end_ms` from the next utterance's timestamp.
- Known channels: Lex Fridman Podcast.

### Parser: generic_substack

Fallback parser for Substack-hosted transcripts with varying formats. Attempts to detect:
- Bold speaker names (`**Name**`)
- Colon-prefixed speakers (`Name: text`)
- Timestamp headers (`### HH:MM:SS` or `(HH:MM:SS)`)

If the format cannot be reliably parsed, returns the full text as a single segment with `speaker_name='unknown'` and `has_timestamps=False` — this triggers a fallback to Deepgram.

### Adding new parsers

Store parsers in `src/pipeline/parsers/`. Each parser implements:

```python
class TranscriptParser(ABC):
    @abstractmethod
    def can_parse(self, html: str) -> bool:
        """Quick check if this parser handles the format."""
    
    @abstractmethod
    def parse(self, html: str) -> list[ParsedSegment]:
        """Extract structured segments from HTML."""
```

New parsers are registered in config and mapped to channels via `podcast_channels.transcript_parser`.

---

## Slug Resolution Strategy — Detail

The hardest part of this feature is mapping a YouTube video to its transcript URL. Three strategies in priority order:

### Strategy 1: Description Link Extraction (most reliable)

Many podcasters include transcript links in video descriptions. Example from a Dwarkesh video description:

```
Watch on YouTube; listen on Apple Podcasts or Spotify.
Transcript: https://www.dwarkesh.com/p/elon-musk
```

**Implementation:** After video discovery (Stage 2), scan `videos.description` for URLs matching the `transcript_url_pattern` domain. If found, store the URL directly — no slug guessing needed.

### Strategy 2: Title-to-Slug Derivation

If no link in description, derive the slug from the video title:

```python
def slugify_for_channel(title: str, channel_pattern: str) -> str:
    # "Elon Musk — In 36 months..." → "elon-musk"
    # Take the guest name (before em-dash or colon)
    # Lowercase, replace spaces with hyphens, strip special chars
```

**Validation:** Always HEAD-request the constructed URL before fetching. 404 → fall through.

### Strategy 3: LLM Slug Guess (last resort)

Send the video title + description to the LLM and ask:

```
Given this video title: "Elon Musk — In 36 months, the cheapest place to put AI will be space"
And this URL pattern: "https://www.dwarkesh.com/p/{slug}"
What is the most likely slug? Respond with just the slug.
```

**Validation:** HEAD-request the result. If 404, abandon and fall through to Deepgram.

---

## Channels with Known Official Transcripts

Based on current publishing patterns (verify during Stage 1 seed):

| Channel | Transcript source | Parser | URL pattern |
|---|---|---|---|
| Dwarkesh Podcast | dwarkesh.com (Substack) | dwarkesh_substack | `https://www.dwarkesh.com/p/{slug}` |
| Lex Fridman Podcast | lexfridman.com | lex_fridman | `https://lexfridman.com/{slug}-transcript` |
| Cheeky Pint (Collison) | Substack | dwarkesh_substack | Verify during setup — profile is `substack.com/@cheekypint`, need to confirm individual episode transcript format |
| Conversations with Tyler | mercatus.org | generic_substack | Verify during setup |
| Acquired | acquired.fm | generic_substack | Verify during setup |

For the remaining ~28 channels: `transcript_url_pattern` = NULL. They go through the standard Deepgram/caption paths.

**During Stage 1 seed:** Check each Tier 1 channel for official transcripts. Update `transcript_url_pattern` and `transcript_parser` for any that publish them. This is a one-time manual check that potentially saves $5–10/month in Deepgram costs for those channels.

---

## Cost Impact

If 5 channels with official transcripts produce ~30% of your Tier 1 content:

| Before | After | Savings |
|---|---|---|
| ~45 hrs audio/month via Deepgram | ~30 hrs audio/month via Deepgram | ~$7/month |
| Speaker ID LLM calls for all deep-path | Fewer LLM calls (official transcripts skip diarization-based ID) | ~$1/month |

The cost savings are modest (~$8/month). The real value is **quality**: human-edited text with verified speaker attribution produces better claims, more accurate evidence spans, and fewer review-queue items.

---

## Interaction with Existing Spec — Consistency Checklist

| Spec section | Impact | Action needed |
|---|---|---|
| Path Selection Logic (Stage 3) | Official path inserts as Priority 1, before Deepgram | Update table in spec when merging |
| Fast-path upgrade rule (Stage 3/4) | Does not apply — official transcripts never need upgrade | No change needed |
| Stage 4 Mode A/B | Add Mode C for official transcripts (simpler name matching) | New section |
| Stage 4 test criteria | Add: "Official transcript: speaker names correctly matched to person_ids" | One new bullet |
| Trust derivation table | Add row: official_transcript → 0.98 → high | One new row |
| Confidence assignment table | Add: identified_via='official_transcript' → 0.98 | One new row |
| `transcript_runs.speaker_config` | Not applicable for official transcripts (no diarization). Set to NULL | No schema change needed (field is already nullable JSONB) |
| Provider abstraction | Add `OfficialTranscriptProvider` class alongside `DeepgramProvider` and `WhisperXProvider` | One new class |
| Stage 3 test criteria | Add: "Official path: fetches, parses, stores segments with speaker_name populated" | New bullets |
| Stage 5 fast-path rule | No change — official transcripts are not fast-path, the sole-speaker rule doesn't apply | No change needed |
| Monthly cost estimate | Deepgram drops from ~$22 to ~$15. Total from ~$70 to ~$63 | Minor update |

---

## Test Criteria for Official Transcript Path

**Stage 3:**
- Given a Dwarkesh episode URL, the system resolves the transcript URL from the video description
- Parser correctly extracts speaker names, text, and section-level timestamps
- Segments stored with `source_kind='official'`, `speaker_name` populated, `speaker_label` NULL
- `transcript_type='official'` set on video record
- If transcript URL 404s, system falls through to Deepgram without error
- If parser fails, transcript_run marked 'failed', system falls through to Deepgram

**Stage 4:**
- Speaker names from official transcripts matched to `people.id` via exact name match
- Known hosts matched via first-name + channel_roles prior
- `video_people` entries created with `identified_via='official_transcript'`, `confidence=0.98`
- Unmatched speakers (unknown guests) have `person_id=NULL` in segments

**Integration:**
- End-to-end: a Dwarkesh episode is discovered → official transcript fetched → parsed → speakers identified → claims extracted → all with `trust_level='high'` and `attribution_confidence=0.98`
- Deepgram is not called for videos with successful official transcripts
- A video with a failed official transcript attempt can still succeed via Deepgram (multiple transcript_runs)

---

## Implementation File Map

```
src/
├── pipeline/
│   ├── transcription.py          # Update: check official path first in path selection
│   └── parsers/                  # NEW directory
│       ├── __init__.py
│       ├── base.py               # TranscriptParser ABC + ParsedSegment dataclass
│       ├── dwarkesh_substack.py  # **Speaker** + ### HH:MM:SS format
│       ├── lex_fridman.py        # Speaker [(HH:MM:SS)] format
│       └── generic_substack.py   # Fallback for other Substack transcripts
├── providers/
│   ├── official_transcript.py    # NEW: OfficialTranscriptProvider
│   └── ...existing providers...
```

---

## What This Addendum Does NOT Change

- **No new tables.** Uses existing `transcript_runs`, `transcript_segments`, `video_people`.
- **No changes to Stage 5 enrichment.** Official transcript segments are processed identically.
- **No changes to Stage 6 API.** Claims from official transcripts appear the same as any other claims.
- **No changes to Stage 7 dashboard.** The evidence drawer shows the same fields regardless of source.
- **No changes to Stage 8 briefs.** Brief generation is source-agnostic.
- **No changes to the review rules.** Official transcripts get `trust_level='high'` and auto-approve.

---

## Edge Case: Late-Published Transcripts

Some podcasters publish transcripts hours or days after the video. A video might be discovered, processed via Deepgram, and enriched before the official transcript appears. Two options:

**Option A (recommended for MVP): Don't re-process.** The Deepgram transcript is good enough. The system already has claims from that episode. Re-processing would create duplicate claims.

**Option B (future enhancement): Periodic re-check.** A weekly job scans videos with `transcript_type='deep'` from channels that have `transcript_url_pattern`. If an official transcript is now available, create a new transcript_run with `mode='official_transcript'`. Do NOT automatically re-extract claims — just flag the video as having a better transcript source available. An operator can decide whether to re-extract.

For MVP, implement Option A. The cost of missing a few official transcripts (and using Deepgram instead) is negligible compared to the complexity of re-processing.

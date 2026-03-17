from types import SimpleNamespace

from src.pipeline import enrichment


class _FakeQuery:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.items)

    def count(self):
        return len(self.items)


class _NestedTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, video_people, transcript_segments):
        self.video_people = list(video_people)
        self.transcript_segments = list(transcript_segments)
        self.commits = 0

    def begin_nested(self):
        return _NestedTransaction()

    def query(self, model):
        if model is enrichment.VideoPeople:
            return _FakeQuery(self.video_people)
        if model is enrichment.TranscriptSegments:
            return _FakeQuery(self.transcript_segments)
        raise AssertionError(f"Unexpected model queried: {model}")

    def flush(self):
        return None

    def commit(self):
        self.commits += 1


def test_enrich_video_keeps_video_identified_when_llm_extraction_fails(monkeypatch):
    person = SimpleNamespace(id="person-1", name="Alice Analyst")
    video_person = SimpleNamespace(person=person, confidence=0.9, enrichment_status="pending")
    segment = SimpleNamespace(id="segment-1", person_id=person.id, segment_index=0)
    video = SimpleNamespace(
        id="video-1",
        youtube_video_id="abc123",
        transcript_type="official",
        status="identified",
        error_message=None,
        retry_count=0,
    )
    session = _FakeSession([video_person], [segment])

    def fake_extract(*args, **kwargs):
        raise enrichment.EnrichmentError("LLM unavailable")

    monkeypatch.setattr(enrichment, "extract_claims_from_segments", fake_extract)

    result = enrichment.enrich_video(session, video)

    assert result["claims_extracted"] == 0
    assert result["errors"] == ["LLM unavailable"]
    assert video.status == "identified"
    assert video.error_message == "LLM unavailable"
    assert video.retry_count == 1
    assert session.commits == 1

from types import SimpleNamespace

from src.pipeline import transcription


def test_caption_provider_prefers_direct_transcript_fetch(monkeypatch):
    provider = transcription.CaptionProvider()

    monkeypatch.setattr(
        transcription,
        "_fetch_direct_transcript_segments",
        lambda video_id: [
            transcription.Segment(
                index=0,
                start_ms=0,
                end_ms=5000,
                text="Direct transcript text",
                source_kind="caption",
            )
        ],
    )
    monkeypatch.setattr(
        transcription,
        "run_yt_dlp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("yt-dlp should not run")),
    )

    result = provider.transcribe("abc123")

    assert result.error is None
    assert result.provider == "youtube-transcript-api"
    assert [segment.text for segment in result.segments] == ["Direct transcript text"]


def test_aggregate_direct_transcript_entries_into_segments(monkeypatch):
    class _FakeTranscriptApi:
        def fetch(self, video_id, languages):
            assert video_id == "abc123"
            assert languages[0] == "en"
            return [
                SimpleNamespace(text="First point", start=0.0, duration=4.0),
                SimpleNamespace(text="Second point", start=8.0, duration=4.0),
                SimpleNamespace(text="Third point", start=35.0, duration=5.0),
            ]

    monkeypatch.setitem(
        transcription._fetch_direct_transcript_segments.__globals__,
        "YouTubeTranscriptApi",
        None,
    )

    fake_module = SimpleNamespace(YouTubeTranscriptApi=lambda: _FakeTranscriptApi())
    import sys
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)

    segments = transcription._fetch_direct_transcript_segments("abc123")

    assert len(segments) == 2
    assert segments[0].text == "First point Second point"
    assert segments[1].text == "Third point"

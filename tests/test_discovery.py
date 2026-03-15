from types import SimpleNamespace

from src.pipeline import discovery


def test_scan_all_channels_delegates_to_scan_channel_feeds(monkeypatch):
    calls: dict[str, object] = {}

    def fake_scan_channel_feeds(session, channel_name=None, limit=None):
        calls["session"] = session
        calls["channel_name"] = channel_name
        calls["limit"] = limit
        return {"ok": True}

    monkeypatch.setattr(discovery, "scan_channel_feeds", fake_scan_channel_feeds)

    session = object()
    result = discovery.scan_all_channels(session, limit=3)

    assert result == {"ok": True}
    assert calls == {"session": session, "channel_name": None, "limit": 3}


def test_select_best_channel_match_prefers_closest_name():
    items = [
        {"id": {"channelId": "noise-1"}, "snippet": {"title": "Random Clips"}},
        {"id": {"channelId": "best-match"}, "snippet": {"title": "Dwarkesh Podcast"}},
        {"id": {"channelId": "noise-2"}, "snippet": {"title": "The Lunar Society"}},
    ]

    match = discovery._select_best_channel_match("Dwarkesh Podcast", items)

    assert match == {
        "channel_id": "best-match",
        "title": "Dwarkesh Podcast",
        "score": match["score"],
    }
    assert match["score"] >= discovery.CHANNEL_REPAIR_THRESHOLD


def test_scan_single_channel_repairs_missing_channel_id_and_retries(monkeypatch):
    channel = SimpleNamespace(id=1, name="Dwarkesh Podcast", youtube_channel_id="stale-channel")
    result = discovery.ScanResult()
    added = []
    attempts = []

    class _FakeVideoQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class _FakeSession:
        def query(self, model):
            assert model is discovery.Videos
            return _FakeVideoQuery()

        def add(self, video):
            added.append(video)

        def flush(self):
            return None

    class _DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    responses = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="ERROR: This channel does not exist"),
            SimpleNamespace(
                returncode=0,
                stdout="video123\tA Better Interview\t20260315\t3600\tFresh upload",
                stderr="",
            ),
        ]
    )

    def fake_run_yt_dlp(args, timeout, retries=2):
        attempts.append(args)
        return next(responses)

    monkeypatch.setattr(discovery, "run_yt_dlp", fake_run_yt_dlp)
    monkeypatch.setattr(discovery.httpx, "Client", lambda timeout=30: _DummyClient())
    monkeypatch.setattr(discovery.settings, "youtube_api_key", "youtube-key")
    monkeypatch.setattr(
        discovery,
        "_find_channel_match",
        lambda client, channel_name, result: {
            "channel_id": "fresh-channel",
            "title": "Dwarkesh Podcast",
            "score": 0.99,
        },
    )

    new_count = discovery._scan_single_channel(_FakeSession(), channel, result)

    assert new_count == 1
    assert channel.youtube_channel_id == "fresh-channel"
    assert len(attempts) == 2
    assert added[0].youtube_video_id == "video123"
    assert added[0].source_channel_youtube_id == "fresh-channel"

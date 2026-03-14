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

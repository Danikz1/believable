import subprocess

from src import youtube


def test_run_yt_dlp_retries_transient_failures(monkeypatch):
    calls = []
    sleeps = []
    results = iter(
        [
            subprocess.CompletedProcess(["yt-dlp"], 1, "", "HTTP Error 429: Too Many Requests"),
            subprocess.CompletedProcess(["yt-dlp"], 1, "", "temporarily unavailable"),
            subprocess.CompletedProcess(["yt-dlp"], 0, "ok", ""),
        ]
    )

    def fake_run(cmd, capture_output, text, timeout):
        calls.append((cmd, timeout))
        return next(results)

    monkeypatch.setattr(youtube.subprocess, "run", fake_run)
    monkeypatch.setattr(youtube.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(youtube, "YT_DLP_BIN", "yt-dlp")

    proc = youtube.run_yt_dlp(["--flat-playlist", "https://example.com"], timeout=30)

    assert proc.returncode == 0
    assert proc.stdout == "ok"
    assert len(calls) == 3
    assert sleeps == [1, 2]
    first_cmd = calls[0][0]
    assert first_cmd[:2] == ["yt-dlp", "--ignore-config"]
    assert "--extractor-args" in first_cmd


def test_run_yt_dlp_does_not_retry_permanent_failures(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Unsupported URL")

    monkeypatch.setattr(youtube.subprocess, "run", fake_run)
    monkeypatch.setattr(youtube.time, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("should not sleep")))
    monkeypatch.setattr(youtube, "YT_DLP_BIN", "yt-dlp")

    proc = youtube.run_yt_dlp(["https://example.com"], timeout=30)

    assert proc.returncode == 1
    assert len(calls) == 1

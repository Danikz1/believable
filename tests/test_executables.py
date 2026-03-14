from pathlib import Path

from src import executables


def test_resolve_executable_prefers_active_environment(monkeypatch, tmp_path):
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")
    fake_tool = fake_python.with_name("yt-dlp")
    fake_tool.write_text("", encoding="utf-8")

    monkeypatch.setattr(executables.shutil, "which", lambda name: None)
    monkeypatch.setattr(executables.sys, "executable", str(fake_python))
    monkeypatch.setattr(executables.sys, "prefix", str(tmp_path))

    resolved = executables.resolve_executable("yt-dlp")

    assert resolved == str(fake_tool)

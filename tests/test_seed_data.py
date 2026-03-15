import json
from pathlib import Path

from src.db.seed import _resolve_data_file


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_json(filename: str) -> list[dict]:
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def test_channels_seed_has_33_unique_youtube_channel_ids():
    channels = _load_json("channels_seed.json")

    assert len(channels) == 33

    youtube_ids = [channel["youtube_channel_id"] for channel in channels]
    assert len(youtube_ids) == len(set(youtube_ids))


def test_known_official_transcript_channels_have_metadata():
    channels = {
        channel["name"]: channel
        for channel in _load_json("channels_seed.json")
    }

    assert channels["Dwarkesh Podcast"]["transcript_url_pattern"] == "https://www.dwarkesh.com/p/{slug}"
    assert channels["Dwarkesh Podcast"]["transcript_parser"] == "dwarkesh_substack"
    assert channels["Lex Fridman Podcast"]["transcript_url_pattern"] == "https://lexfridman.com/{slug}-transcript"
    assert channels["Lex Fridman Podcast"]["transcript_parser"] == "lex_fridman"


def test_resolve_data_file_falls_back_to_module_relative_project_data(tmp_path):
    project_root = tmp_path / "project"
    project_root.joinpath("data").mkdir(parents=True)
    expected = project_root / "data" / "people_seed.json"
    expected.write_text("[]", encoding="utf-8")

    resolved = _resolve_data_file(
        "people_seed.json",
        cwd=tmp_path / "elsewhere",
        module_file=project_root / "src" / "db" / "seed.py",
    )

    assert resolved == expected


def test_resolve_data_file_finds_container_data_from_workdir(tmp_path):
    app_root = tmp_path / "app"
    app_root.joinpath("data").mkdir(parents=True)
    expected = app_root / "data" / "people_seed.json"
    expected.write_text("[]", encoding="utf-8")

    resolved = _resolve_data_file(
        "people_seed.json",
        cwd=app_root,
        module_file=Path("/usr/local/lib/python3.12/site-packages/src/db/seed.py"),
    )

    assert resolved == expected

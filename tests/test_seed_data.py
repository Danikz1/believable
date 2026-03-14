import json
from pathlib import Path


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

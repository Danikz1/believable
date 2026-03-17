from src.providers import official_transcript as ot_mod
from src.providers.official_transcript import OfficialTranscriptProvider


def test_official_transcript_provider_prefers_description_url():
    provider = OfficialTranscriptProvider("dwarkesh_substack")

    url = provider.resolve_url(
        "Transcript: https://www.dwarkesh.com/p/elon-musk",
        "Ignored title",
        "https://www.dwarkesh.com/p/{slug}",
    )

    assert url == "https://www.dwarkesh.com/p/elon-musk"


def test_official_transcript_provider_derives_url_from_title(monkeypatch):
    provider = OfficialTranscriptProvider("lex_fridman")

    # Mock validate_url to avoid real HTTP calls — accept any lexfridman.com URL
    monkeypatch.setattr(ot_mod, "validate_url", lambda url: "lexfridman.com" in url)

    url = provider.resolve_url(
        None,
        "Peter Steinberger - Transcript",
        "https://lexfridman.com/{slug}-transcript",
    )

    assert url == "https://lexfridman.com/peter-steinberger-transcript"

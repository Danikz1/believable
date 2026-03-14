from src.providers.official_transcript import OfficialTranscriptProvider


def test_official_transcript_provider_prefers_description_url():
    provider = OfficialTranscriptProvider("dwarkesh_substack")

    url = provider.resolve_url(
        "Transcript: https://www.dwarkesh.com/p/elon-musk",
        "Ignored title",
        "https://www.dwarkesh.com/p/{slug}",
    )

    assert url == "https://www.dwarkesh.com/p/elon-musk"


def test_official_transcript_provider_derives_url_from_title():
    provider = OfficialTranscriptProvider("lex_fridman")

    url = provider.resolve_url(
        None,
        "Peter Steinberger - Transcript",
        "https://lexfridman.com/{slug}-transcript",
    )

    assert url == "https://lexfridman.com/peter-steinberger-transcript"

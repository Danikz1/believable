from src.pipeline.briefs import _generate_narrative, _template_brief


def test_template_brief_handles_empty_sections_without_inventing_content():
    sections = {
        "headlines": [],
        "shifts": [],
        "topic_pulse": [],
        "discoveries": [],
    }

    output = _template_brief(sections, "March 14, 2026")

    assert "No approved high-trust claims in this window." in output
    assert "No position shifts detected this period." in output
    assert "No topic activity in this window." in output
    assert "No newly processed videos in this window." in output
    assert "GPT-6" not in output


def test_generate_narrative_returns_grounded_markdown():
    sections = {
        "headlines": [
            {
                "person_name": "Ray Dalio",
                "topics": ["macro"],
                "claim_text": "Rates will likely stay elevated.",
            }
        ],
        "shifts": [],
        "topic_pulse": [{"topic": "macro", "claim_count": 1}],
        "discoveries": [],
    }

    output = _generate_narrative(sections, "March 14, 2026")

    assert "Ray Dalio" in output
    assert "Rates will likely stay elevated." in output
    assert "macro" in output
    assert "GPT-6" not in output

from src.pipeline.enrichment import EXTRACT_CLAIMS_TOOL
from src.providers import llm


def test_get_all_providers_prefers_configured_llm_provider(monkeypatch):
    monkeypatch.setattr(llm.settings, "qwen_api_key", "qwen-key")
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "anthropic-key")
    monkeypatch.setattr(llm.settings, "openai_api_key", "openai-key")
    monkeypatch.setattr(llm.settings, "llm_provider", "openai")

    assert llm.get_all_providers() == ["openai", "qwen", "anthropic"]


def test_qwen_tool_falls_back_across_documented_regions(monkeypatch):
    calls = []

    monkeypatch.setattr(llm.settings, "qwen_api_base", "")
    monkeypatch.setattr(llm.settings, "qwen_api_key", "qwen-key")

    def fake_call(**kwargs):
        calls.append(kwargs["base_url"])
        if kwargs["base_url"] != "https://dashscope.aliyuncs.com/compatible-mode/v1":
            raise RuntimeError("Auth error 401: invalid_api_key")
        return {"claims": []}

    monkeypatch.setattr(llm, "_call_openai_compatible_tool", fake_call)

    result = llm._call_qwen_tool(
        system_prompt="system",
        user_prompt="user",
        tool=EXTRACT_CLAIMS_TOOL,
        model="qwen-plus",
        temperature=0.1,
        max_tokens=256,
    )

    assert result == {"claims": []}
    assert calls == [
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ]


def test_openai_compatible_tool_call_parses_tool_arguments(monkeypatch):
    def fake_api_call(url, headers, payload):
        assert payload["tools"][0]["function"]["name"] == EXTRACT_CLAIMS_TOOL["name"]
        assert payload["tool_choice"]["function"]["name"] == EXTRACT_CLAIMS_TOOL["name"]
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "arguments": "{\"claims\": []}"
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(llm, "_api_call_with_retry", fake_api_call)

    result = llm._call_openai_compatible_tool(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
        system_prompt="system",
        user_prompt="user",
        tool=EXTRACT_CLAIMS_TOOL,
        temperature=0.1,
        max_tokens=256,
    )

    assert result == {"claims": []}

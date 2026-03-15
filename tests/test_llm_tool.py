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


def test_dispatch_call_uses_settings_backed_default_models(monkeypatch):
    seen = []

    monkeypatch.setattr(llm.settings, "openai_model", "gpt-5.2")
    monkeypatch.setattr(llm.settings, "anthropic_model", "claude-sonnet-4-0")
    monkeypatch.setattr(llm.settings, "qwen_model", "qwen3.5-plus")

    def fake_openai(system_prompt, user_prompt, model, temperature, max_tokens):
        seen.append(("openai", model))
        return "ok"

    def fake_anthropic(system_prompt, user_prompt, model, temperature, max_tokens):
        seen.append(("anthropic", model))
        return "ok"

    def fake_qwen(system_prompt, user_prompt, model, temperature, max_tokens):
        seen.append(("qwen", model))
        return "ok"

    monkeypatch.setattr(llm, "_call_openai", fake_openai)
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic)
    monkeypatch.setattr(llm, "_call_qwen", fake_qwen)

    assert llm._dispatch_call("openai", "sys", "user", None, 0.1, 100) == "ok"
    assert llm._dispatch_call("anthropic", "sys", "user", None, 0.1, 100) == "ok"
    assert llm._dispatch_call("qwen", "sys", "user", None, 0.1, 100) == "ok"
    assert seen == [
        ("openai", "gpt-5.2"),
        ("anthropic", "claude-sonnet-4-0"),
        ("qwen", "qwen3.5-plus"),
    ]


def test_dispatch_tool_call_uses_settings_backed_default_models(monkeypatch):
    seen = []

    monkeypatch.setattr(llm.settings, "openai_model", "gpt-5.2")
    monkeypatch.setattr(llm.settings, "anthropic_model", "claude-sonnet-4-0")
    monkeypatch.setattr(llm.settings, "qwen_model", "qwen3.5-plus")

    def fake_openai_tool(system_prompt, user_prompt, tool, model, temperature, max_tokens):
        seen.append(("openai", model))
        return {"claims": []}

    def fake_anthropic_tool(system_prompt, user_prompt, tool, model, temperature, max_tokens):
        seen.append(("anthropic", model))
        return {"claims": []}

    def fake_qwen_tool(system_prompt, user_prompt, tool, model, temperature, max_tokens):
        seen.append(("qwen", model))
        return {"claims": []}

    monkeypatch.setattr(llm, "_call_openai_tool", fake_openai_tool)
    monkeypatch.setattr(llm, "_call_anthropic_tool", fake_anthropic_tool)
    monkeypatch.setattr(llm, "_call_qwen_tool", fake_qwen_tool)

    assert llm._dispatch_tool_call("openai", "sys", "user", EXTRACT_CLAIMS_TOOL, None, 0.1, 100) == {"claims": []}
    assert llm._dispatch_tool_call("anthropic", "sys", "user", EXTRACT_CLAIMS_TOOL, None, 0.1, 100) == {"claims": []}
    assert llm._dispatch_tool_call("qwen", "sys", "user", EXTRACT_CLAIMS_TOOL, None, 0.1, 100) == {"claims": []}
    assert seen == [
        ("openai", "gpt-5.2"),
        ("anthropic", "claude-sonnet-4-0"),
        ("qwen", "qwen3.5-plus"),
    ]

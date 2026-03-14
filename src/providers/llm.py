"""Unified LLM provider interface.

Auto-selects provider based on available API keys.
Supports: OpenAI, Anthropic, Qwen (DashScope-compatible).
"""

import json
import logging
import re
import time

import httpx

from src.config import settings

logger = logging.getLogger(__name__)
DEFAULT_PROVIDER_PRIORITY = ("qwen", "anthropic", "openai")
QWEN_DEFAULT_BASE_URLS = (
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)


def get_available_provider() -> str | None:
    """Return the first configured LLM provider in active priority order."""
    providers = get_all_providers()
    return providers[0] if providers else None


def get_all_providers() -> list[str]:
    """Get configured providers, preferring the provider selected in settings."""
    configured = []
    if settings.qwen_api_key:
        configured.append("qwen")
    if settings.anthropic_api_key:
        configured.append("anthropic")
    if settings.openai_api_key:
        configured.append("openai")

    if not configured:
        return []

    ordered = []
    preferred = (settings.llm_provider or "").strip().lower()
    if preferred in configured:
        ordered.append(preferred)

    for provider in DEFAULT_PROVIDER_PRIORITY:
        if provider in configured and provider not in ordered:
            ordered.append(provider)

    return ordered


def call_llm(
    system_prompt: str,
    user_prompt: str,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """Call an LLM and return the text response.

    Auto-selects provider if not specified. Falls back to next provider on auth errors.
    Raises RuntimeError if no provider is available or all fail.
    """
    if provider:
        providers = [provider]
    else:
        providers = get_all_providers()

    if not providers:
        raise RuntimeError(
            "No LLM API key configured. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "or QWEN_API_KEY in .env"
        )

    last_error = None
    for prov in providers:
        try:
            return _dispatch_call(prov, system_prompt, user_prompt, model, temperature, max_tokens)
        except RuntimeError as e:
            err_msg = str(e)
            if "Auth error" in err_msg or "401" in err_msg or "403" in err_msg:
                logger.warning(f"Provider {prov} auth failed, trying next: {err_msg}")
                last_error = e
                continue
            raise  # Non-auth errors propagate immediately

    raise last_error or RuntimeError("All LLM providers failed")


def _dispatch_call(
    provider: str, system_prompt: str, user_prompt: str,
    model: str | None, temperature: float, max_tokens: int
) -> str:
    """Dispatch to the correct provider."""
    if provider == "openai":
        return _call_openai(system_prompt, user_prompt, model or "gpt-5.4", temperature, max_tokens)
    elif provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, model or "claude-sonnet-4-20250514", temperature, max_tokens)
    elif provider == "qwen":
        return _call_qwen(system_prompt, user_prompt, model or "qwen-plus", temperature, max_tokens)
    else:
        raise RuntimeError(f"Unknown LLM provider: {provider}")


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """Call LLM and parse the response as JSON.

    Includes JSON repair for truncated or malformed responses.
    """
    response = call_llm(
        system_prompt + "\n\nRespond ONLY with valid JSON, no markdown or extra text.",
        user_prompt,
        provider=provider,
        model=model,
        max_tokens=8192,
    )

    # Strip markdown code fences if present
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (``` markers)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to repair truncated JSON
    repaired = _repair_json(text)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Try to extract JSON object from mixed content
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"Failed to parse LLM JSON response\nResponse: {text[:500]}")
    raise RuntimeError(f"LLM returned invalid JSON")


def call_llm_tool(
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
) -> dict:
    """Call an LLM tool/function and return the structured tool input."""
    if provider:
        providers = [provider]
    else:
        providers = get_all_providers()

    if not providers:
        raise RuntimeError(
            "No LLM API key configured. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "or QWEN_API_KEY in .env"
        )

    last_error = None
    for prov in providers:
        try:
            return _dispatch_tool_call(
                prov,
                system_prompt,
                user_prompt,
                tool,
                model,
                temperature,
                max_tokens,
            )
        except RuntimeError as e:
            err_msg = str(e)
            if "Auth error" in err_msg or "401" in err_msg or "403" in err_msg:
                logger.warning(f"Provider {prov} auth failed, trying next: {err_msg}")
                last_error = e
                continue
            raise

    raise last_error or RuntimeError("All LLM providers failed")


def _repair_json(text: str) -> str | None:
    """Try to repair truncated JSON by closing brackets."""
    import re

    # Count open/close brackets
    open_braces = text.count("{")
    close_braces = text.count("}")
    open_brackets = text.count("[")
    close_brackets = text.count("]")

    if open_braces <= close_braces and open_brackets <= close_brackets:
        return None  # Not a truncation issue

    # Remove trailing incomplete object (after last comma in an array)
    repaired = re.sub(r',\s*\{[^}]*$', '', text)

    # Close missing brackets
    for _ in range(open_brackets - close_brackets):
        repaired += "]"
    for _ in range(open_braces - close_braces):
        repaired += "}"

    return repaired


def _dispatch_tool_call(
    provider: str,
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    model: str | None,
    temperature: float,
    max_tokens: int,
) -> dict:
    """Dispatch a structured tool call to the requested provider."""
    if provider == "openai":
        return _call_openai_tool(
            system_prompt,
            user_prompt,
            tool,
            model or "gpt-5",
            temperature,
            max_tokens,
        )
    if provider == "anthropic":
        return _call_anthropic_tool(
            system_prompt,
            user_prompt,
            tool,
            model or "claude-sonnet-4-20250514",
            temperature,
            max_tokens,
        )
    if provider == "qwen":
        return _call_qwen_tool(
            system_prompt,
            user_prompt,
            tool,
            model or "qwen-plus",
            temperature,
            max_tokens,
        )
    raise RuntimeError(f"Unknown LLM provider: {provider}")


# ── OpenAI ───────────────────────────────────────────────────────────

def _call_openai(
    system_prompt: str, user_prompt: str, model: str, temperature: float, max_tokens: int
) -> str:
    return _call_openai_compatible(
        base_url="https://api.openai.com/v1",
        api_key=settings.openai_api_key,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _call_openai_tool(
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    return _call_openai_compatible_tool(
        base_url="https://api.openai.com/v1",
        api_key=settings.openai_api_key,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tool=tool,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ── Anthropic ────────────────────────────────────────────────────────

def _call_anthropic(
    system_prompt: str, user_prompt: str, model: str, temperature: float, max_tokens: int
) -> str:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    response = _api_call_with_retry(url, headers, payload)
    content = response.get("content", [])
    return content[0]["text"] if content else ""


def _call_anthropic_tool(
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}],
            }
        ],
        "tools": [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool["input_schema"],
            }
        ],
        "tool_choice": {"type": "tool", "name": tool["name"]},
    }

    response = _api_call_with_retry(url, headers, payload)
    for content in response.get("content", []):
        if content.get("type") == "tool_use" and content.get("name") == tool["name"]:
            return content.get("input", {})

    raise RuntimeError("Anthropic returned no tool result")


# ── Qwen (DashScope-compatible) ─────────────────────────────────────

def _call_qwen(
    system_prompt: str, user_prompt: str, model: str, temperature: float, max_tokens: int
) -> str:
    last_error = None
    for base_url in _get_qwen_base_urls():
        try:
            return _call_openai_compatible(
                base_url=base_url,
                api_key=settings.qwen_api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except RuntimeError as exc:
            if settings.qwen_api_base or not _is_auth_error(exc):
                raise
            logger.warning(f"Qwen auth failed for {base_url}, trying next region: {exc}")
            last_error = exc

    raise last_error or RuntimeError("All Qwen endpoints failed")


def _call_qwen_tool(
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    last_error = None
    for base_url in _get_qwen_base_urls():
        try:
            return _call_openai_compatible_tool(
                base_url=base_url,
                api_key=settings.qwen_api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tool=tool,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except RuntimeError as exc:
            if settings.qwen_api_base or not _is_auth_error(exc):
                raise
            logger.warning(f"Qwen auth failed for {base_url}, trying next region: {exc}")
            last_error = exc

    raise last_error or RuntimeError("All Qwen endpoints failed")


# ── Shared OpenAI-compatible call ────────────────────────────────────

def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    response = _api_call_with_retry(url, headers, payload)
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("LLM returned no choices")
    return choices[0]["message"]["content"]


def _call_openai_compatible_tool(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    temperature: float,
    max_tokens: int,
) -> dict:
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                    "strict": True,
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": tool["name"]},
        },
    }

    response = _api_call_with_retry(url, headers, payload)
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("LLM returned no choices")

    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls", [])
    if not tool_calls:
        raise RuntimeError("LLM returned no tool calls")

    arguments = tool_calls[0].get("function", {}).get("arguments")
    if not arguments:
        raise RuntimeError("LLM tool call did not include arguments")

    try:
        return json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM tool arguments were invalid JSON: {exc}") from exc


def _get_qwen_base_urls() -> list[str]:
    """Return the configured Qwen endpoint or the documented regional defaults."""
    if settings.qwen_api_base:
        return [settings.qwen_api_base]
    return list(QWEN_DEFAULT_BASE_URLS)


def _is_auth_error(error: Exception) -> bool:
    """Return True when a provider error is authentication-related."""
    message = str(error)
    return "Auth error" in message or "401" in message or "403" in message


# ── Retry logic (per spec) ───────────────────────────────────────────

def _api_call_with_retry(url: str, headers: dict, payload: dict) -> dict:
    """HTTP POST with retry logic per spec."""
    max_retries = 5

    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(url, headers=headers, json=payload)

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                backoff = 2 ** attempt
                logger.warning(f"Rate limited, backing off {backoff}s")
                if attempt < max_retries:
                    time.sleep(backoff)
                    continue
                raise RuntimeError("Rate limited after max retries")
            elif resp.status_code in (401, 403):
                raise RuntimeError(f"Auth error {resp.status_code}: {resp.text[:200]}")
            elif resp.status_code >= 500:
                if attempt < 3:
                    time.sleep(30)
                    continue
                raise RuntimeError(f"Server error {resp.status_code} after retries")
            else:
                raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < 3:
                time.sleep(10)
                continue
            raise RuntimeError(f"Network error after retries: {e}")

    raise RuntimeError("Max retries exceeded")

"""LLM clients: OpenAI-compatible (NIM) against a mocked HTTP transport, the fake, the factory."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agent.config import Settings
from agent.llm import FakeLLM, LLMClient, LLMConfigError, LLMError, LLMRequest, make_llm
from agent.llm.base import TOOL_NAME, cost_usd
from agent.llm.openai_compat import NIM_BASE_URL, OpenAICompatClient

KEY = "nvapi-" + "test-not-a-real-key"  # pragma: allowlist secret
REQ = LLMRequest(system="sys", user="goal + page", tool_schema={"type": "object"}, max_tokens=256)
ACTION = {"type": "click", "ref": "e4", "reasoning": "open kettles"}


def _completion(message: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "meta/llama-3.3-70b-instruct",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230},
        **extra,
    }


def _tool_message(args: Any) -> dict[str, Any]:
    arguments = args if isinstance(args, str) else json.dumps(args)
    call = {"id": "c1", "type": "function", "function": {"name": TOOL_NAME, "arguments": arguments}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _client(handler: Any, **kw: Any) -> OpenAICompatClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kw.setdefault("max_retries", 0)
    return OpenAICompatClient(
        model="meta/llama-3.3-70b-instruct", api_key=SecretStr(KEY), http_client=http, **kw
    )


async def test_nim_request_shape_and_tool_call_parsing() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion(_tool_message(ACTION)))

    client = _client(handler, price_in_per_mtok=0.5, price_out_per_mtok=1.5)
    resp = await client.decide(REQ)

    assert seen["url"] == f"{NIM_BASE_URL}/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"  # sent to the API...
    body = seen["body"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}
    assert body["tools"][0]["function"]["name"] == TOOL_NAME
    assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "goal + page"},
    ]
    assert body["max_tokens"] == 256 and body["temperature"] == 0.0

    assert resp.action_input == ACTION
    assert (resp.tokens_in, resp.tokens_out) == (1200, 30)
    assert resp.cost_usd == pytest.approx(cost_usd(1200, 30, price_in=0.5, price_out=1.5))
    assert KEY not in repr(client) and KEY not in repr(resp)  # ...but never shown


@pytest.mark.parametrize("mode", ["required", "auto"])
async def test_tool_choice_fallback_modes(mode: str) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion(_tool_message(ACTION)))

    await _client(handler, tool_choice=mode).decide(REQ)
    assert seen["body"]["tool_choice"] == mode


@pytest.mark.parametrize(
    "content",
    [
        '{"type": "scroll", "direction": "down"}',
        'Sure. ```json\n{"type": "scroll", "direction": "down"}\n```',
        '{"name": "act", "arguments": {"type": "scroll", "direction": "down"}}',
        '<|python_tag|>{"name": "act", "parameters": {"type": "scroll", "direction": "down"}}',
    ],
)
async def test_falls_back_to_json_in_content(content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion({"role": "assistant", "content": content}))

    resp = await _client(handler).decide(REQ)
    assert resp.action_input == {"type": "scroll", "direction": "down"}


async def test_no_action_at_all_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        msg = {"role": "assistant", "content": "I think I should look around first."}
        return httpx.Response(200, json=_completion(msg))

    resp = await _client(handler).decide(REQ)
    assert resp.action_input is None and "look around" in resp.text


async def test_bad_json_arguments_return_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(_tool_message("{not json")))

    assert (await _client(handler).decide(REQ)).action_input is None


async def test_http_error_is_wrapped_without_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status": 401, "detail": "Authentication failed"})

    with pytest.raises(LLMError, match="HTTP 401: Authentication failed") as info:
        await _client(handler).decide(REQ)
    assert KEY not in str(info.value)


async def test_retries_transient_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("agent.llm.openai_compat.asyncio.sleep", no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=_completion(_tool_message(ACTION)))

    resp = await _client(handler, max_retries=2).decide(REQ)
    assert resp.action_input == ACTION and calls["n"] == 3


async def test_non_retryable_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "tool_choice not supported"}})

    with pytest.raises(LLMError, match="tool_choice not supported"):
        await _client(handler, max_retries=2).decide(REQ)
    assert calls["n"] == 1


def test_config_validation() -> None:
    with pytest.raises(LLMConfigError, match="API_KEY"):
        OpenAICompatClient(model="m", api_key=SecretStr(""))
    with pytest.raises(LLMConfigError, match="MODEL"):
        OpenAICompatClient(model="", api_key=SecretStr(KEY))
    with pytest.raises(LLMConfigError, match="BASE_URL"):
        OpenAICompatClient(model="m", base_url="", require_key=False)
    # Self-hosted servers may not need a key.
    OpenAICompatClient(model="m", base_url="http://localhost:8000/v1", require_key=False)


async def test_fake_replays_then_finishes() -> None:
    fake = FakeLLM([{"type": "scroll", "direction": "down"}, None])
    assert isinstance(fake, LLMClient)
    first, second, third = [await fake.decide(REQ) for _ in range(3)]
    assert first.action_input == {"type": "scroll", "direction": "down"}
    assert second.action_input is None
    assert third.action_input is not None and third.action_input["type"] == "finish"
    assert len(fake.requests) == 3


async def test_fake_callable_script() -> None:
    fake = FakeLLM(lambda req, i: {"type": "answer", "text": f"{i}:{len(req.user)}"})
    resp = await fake.decide(REQ)
    assert resp.action_input == {"type": "answer", "text": f"0:{len(REQ.user)}"}


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_factory() -> None:
    assert isinstance(make_llm(_settings(slba_llm_provider="fake")), FakeLLM)

    nim = make_llm(_settings(slba_llm_api_key=SecretStr(KEY), slba_llm_model="m"))
    assert isinstance(nim, OpenAICompatClient) and "NIM" in repr(nim)

    with pytest.raises(LLMConfigError, match="API_KEY"):
        make_llm(_settings(slba_llm_model="m"))  # NIM needs a key
    with pytest.raises(LLMConfigError, match="BASE_URL"):
        make_llm(_settings(slba_llm_provider="vllm", slba_llm_model="m"))

    local = make_llm(
        _settings(
            slba_llm_provider="ollama",
            slba_llm_model="qwen2.5:7b",
            slba_llm_base_url="http://localhost:11434/v1",
        )
    )
    assert "Ollama" in repr(local)

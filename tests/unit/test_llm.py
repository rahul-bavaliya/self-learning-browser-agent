"""LLM clients: Anthropic against a mocked HTTP transport, the fake, and the factory."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agent.config import Settings
from agent.llm import FakeLLM, LLMClient, LLMConfigError, LLMError, LLMRequest, make_llm
from agent.llm.anthropic_client import AnthropicClient
from agent.llm.base import TOOL_NAME, cost_usd

KEY = "sk-ant-test-not-a-real-key"
REQ = LLMRequest(system="sys", user="goal + page", tool_schema={"type": "object"}, max_tokens=256)


def _reply(content: list[dict[str, Any]], *, model: str = "claude-test") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 30},
    }


def _client(handler: Any, **kw: Any) -> AnthropicClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicClient(
        api_key=SecretStr(KEY), model="claude-test", http_client=http, max_retries=0, **kw
    )


async def test_anthropic_forces_tool_and_parses_action() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["headers"] = dict(request.headers)
        action = {"type": "click", "ref": "e4", "reasoning": "open kettles"}
        tool_block = {"type": "tool_use", "id": "t1", "name": TOOL_NAME, "input": action}
        return httpx.Response(200, json=_reply([tool_block]))

    client = _client(handler, price_in_per_mtok=3.0, price_out_per_mtok=15.0)
    resp = await client.decide(REQ)

    body = seen["body"]
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert body["system"] == "sys" and body["max_tokens"] == 256
    assert body["messages"] == [{"role": "user", "content": "goal + page"}]
    assert body["tools"][0]["name"] == TOOL_NAME
    assert seen["headers"]["x-api-key"] == KEY  # sent to the API...

    assert resp.action_input == {"type": "click", "ref": "e4", "reasoning": "open kettles"}
    assert (resp.tokens_in, resp.tokens_out) == (1200, 30)
    assert resp.cost_usd == pytest.approx(cost_usd(1200, 30, price_in=3.0, price_out=15.0))
    assert KEY not in repr(client) and KEY not in repr(resp)  # ...but never shown


async def test_anthropic_without_tool_call_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_reply([{"type": "text", "text": "I think..."}]))

    resp = await _client(handler).decide(REQ)
    assert resp.action_input is None and resp.text == "I think..."


async def test_anthropic_api_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error", "message": "bad key"}},
        )

    with pytest.raises(LLMError) as info:
        await _client(handler).decide(REQ)
    assert KEY not in str(info.value)


def test_anthropic_requires_key_and_model() -> None:
    with pytest.raises(LLMConfigError, match="API_KEY"):
        AnthropicClient(api_key=SecretStr(""), model="m")
    with pytest.raises(LLMConfigError, match="MODEL"):
        AnthropicClient(api_key=SecretStr(KEY), model="")


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


def test_factory() -> None:
    assert isinstance(make_llm(Settings(_env_file=None, slba_llm_provider="fake")), FakeLLM)  # type: ignore[call-arg]
    client = make_llm(
        Settings(_env_file=None, slba_llm_api_key=SecretStr(KEY), slba_llm_model="m")  # type: ignore[call-arg]
    )
    assert isinstance(client, AnthropicClient)
    with pytest.raises(LLMConfigError, match="not implemented"):
        make_llm(Settings(_env_file=None, slba_llm_provider="ollama"))  # type: ignore[call-arg]

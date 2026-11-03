"""Anthropic Messages API client using forced tool use."""

from __future__ import annotations

import time
from typing import Any

import anthropic
import httpx
from pydantic import SecretStr

from agent.llm.base import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    cost_usd,
)


class AnthropicClient:
    """:class:`~agent.llm.base.LLMClient` backed by Claude.

    The API key is held as a ``SecretStr`` and only unwrapped when the SDK client
    is built, so it never appears in reprs or logs.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        price_in_per_mtok: float = 0.0,
        price_out_per_mtok: float = 0.0,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 2,
    ) -> None:
        """Validate config and build the SDK client.

        Raises:
            LLMConfigError: If the key or model is missing.

        """
        if not api_key.get_secret_value():
            raise LLMConfigError("SLBA_LLM_API_KEY is not set (use a Codespaces secret)")
        if not model:
            raise LLMConfigError("SLBA_LLM_MODEL is not set")
        self._model = model
        self._price_in = price_in_per_mtok
        self._price_out = price_out_per_mtok
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key.get_secret_value(),
            base_url=base_url or None,
            http_client=http_client,
            max_retries=max_retries,
        )

    def __repr__(self) -> str:
        """Never include the key."""
        return f"AnthropicClient(model={self._model!r})"

    @property
    def model(self) -> str:
        """Configured model name."""
        return self._model

    async def decide(self, request: LLMRequest) -> LLMResponse:
        """One forced-tool call; returns the tool input and usage.

        Raises:
            LLMError: On API failure after the SDK's retries.

        """
        started = time.monotonic()
        try:
            msg = await self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                tools=[
                    {
                        "name": TOOL_NAME,
                        "description": TOOL_DESCRIPTION,
                        "input_schema": request.tool_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except anthropic.APIError as exc:
            # Message only: SDK errors don't contain the key, but keep it short anyway.
            raise LLMError(f"Anthropic API error: {type(exc).__name__}: {exc}"[:500]) from None

        action_input: dict[str, Any] | None = None
        texts: list[str] = []
        for block in msg.content:
            if block.type == "tool_use" and block.name == TOOL_NAME and action_input is None:
                action_input = dict(block.input) if isinstance(block.input, dict) else None
            elif block.type == "text":
                texts.append(block.text)

        t_in, t_out = msg.usage.input_tokens, msg.usage.output_tokens
        return LLMResponse(
            action_input=action_input,
            text="\n".join(texts),
            model=msg.model,
            tokens_in=t_in,
            tokens_out=t_out,
            cost_usd=cost_usd(t_in, t_out, price_in=self._price_in, price_out=self._price_out),
            latency_ms=int((time.monotonic() - started) * 1000),
            stop_reason=msg.stop_reason,
            raw={"id": msg.id},
        )

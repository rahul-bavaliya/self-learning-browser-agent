"""Scripted LLM for tests and offline demos: no network, no key, deterministic."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agent.llm.base import LLMRequest, LLMResponse

Script = Sequence[dict[str, Any] | None] | Callable[[LLMRequest, int], dict[str, Any] | None]


class FakeLLM:
    """Returns pre-written actions in order, or from a function of the request.

    After the script runs out it returns ``finish`` with ``success=False`` so a
    loop can never spin forever on a fake. Every request is kept in
    :attr:`requests` for assertions.
    """

    def __init__(self, script: Script, *, model: str = "fake", tokens_per_call: int = 100) -> None:
        """Create from a list of tool payloads (None = 'model skipped the tool')."""
        self._script = script
        self._model = model
        self._tokens = tokens_per_call
        self.requests: list[LLMRequest] = []

    @property
    def model(self) -> str:
        """Model name recorded on runs."""
        return self._model

    async def decide(self, request: LLMRequest) -> LLMResponse:
        """Return the next scripted action."""
        i = len(self.requests)
        self.requests.append(request)
        if callable(self._script):
            action = self._script(request, i)
        elif i < len(self._script):
            action = self._script[i]
        else:
            action = {"type": "finish", "success": False, "reasoning": "fake script exhausted"}
        return LLMResponse(
            action_input=dict(action) if action is not None else None,
            text="",
            model=self._model,
            tokens_in=self._tokens,
            tokens_out=self._tokens // 10,
            cost_usd=0.0,
            latency_ms=0,
            stop_reason="tool_use" if action is not None else "end_turn",
        )

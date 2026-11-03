"""Provider-neutral LLM interface.

Each step is one stateless call: a system prompt plus a single user message that
carries the goal, a short step history and the current (untrusted-wrapped)
observation. The model must answer by calling the ``act`` tool once.
Stateless calls keep prompts bounded and make providers easy to swap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

TOOL_NAME = "act"
TOOL_DESCRIPTION = "Perform exactly one browser action, or finish the run."


class LLMError(Exception):
    """Provider call failed (network, auth, rate limit) after retries."""


class LLMTransientError(LLMError):
    """Temporary failure (rate limit, 5xx, network) that persisted after retries.

    The agent loop records it and tries the step again, up to a limit.
    """


class LLMTimeoutError(LLMTransientError):
    """The provider didn't answer within the timeout, on every attempt."""


class LLMConfigError(LLMError):
    """The provider is misconfigured, e.g. missing API key or model."""


@dataclass(frozen=True)
class LLMRequest:
    """Everything the model needs for one decision."""

    system: str
    user: str
    tool_schema: dict[str, Any]
    max_tokens: int = 1024


@dataclass(frozen=True)
class LLMResponse:
    """The model's decision plus accounting for the ledger.

    ``action_input`` is the raw tool payload (validated later by
    ``agent.actions.parse_action``), or None if the model didn't call the tool.
    """

    action_input: dict[str, Any] | None
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    stop_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@runtime_checkable
class LLMClient(Protocol):
    """Anything that can turn an :class:`LLMRequest` into an :class:`LLMResponse`."""

    @property
    def model(self) -> str:
        """Model identifier recorded on each run."""
        ...

    async def decide(self, request: LLMRequest) -> LLMResponse:
        """Make one decision."""
        ...


def cost_usd(tokens_in: int, tokens_out: int, *, price_in: float, price_out: float) -> float:
    """Cost from per-million-token prices."""
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000

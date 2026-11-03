"""Swappable LLM clients behind one Protocol."""

from agent.llm.base import (
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMTransientError,
)
from agent.llm.factory import make_llm
from agent.llm.fake import FakeLLM

__all__ = [
    "FakeLLM",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeoutError",
    "LLMTransientError",
    "make_llm",
]

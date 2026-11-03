"""Swappable LLM clients behind one Protocol."""

from agent.llm.base import LLMClient, LLMConfigError, LLMError, LLMRequest, LLMResponse
from agent.llm.factory import make_llm
from agent.llm.fake import FakeLLM

__all__ = [
    "FakeLLM",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "make_llm",
]

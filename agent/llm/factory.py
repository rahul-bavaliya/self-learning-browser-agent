"""Build the configured LLM client."""

from __future__ import annotations

from agent.config import Settings, get_settings
from agent.llm.base import LLMClient, LLMConfigError
from agent.llm.fake import FakeLLM


def make_llm(settings: Settings | None = None) -> LLMClient:
    """Return the client for ``SLBA_LLM_PROVIDER``.

    Raises:
        LLMConfigError: For a provider that isn't implemented yet or is misconfigured.

    """
    cfg = settings or get_settings()
    provider = cfg.slba_llm_provider
    if provider == "anthropic":
        from agent.llm.anthropic_client import AnthropicClient

        return AnthropicClient(
            api_key=cfg.slba_llm_api_key,
            model=cfg.slba_llm_model,
            price_in_per_mtok=cfg.slba_llm_price_in_per_mtok,
            price_out_per_mtok=cfg.slba_llm_price_out_per_mtok,
            base_url=cfg.slba_llm_base_url or None,
        )
    if provider == "fake":
        # Offline demo: look around once, then give up honestly.
        return FakeLLM([{"type": "wait", "seconds": 0.5, "reasoning": "offline fake"}])
    raise LLMConfigError(f"provider {provider!r} is planned but not implemented yet")

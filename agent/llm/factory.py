"""Build the configured LLM client."""

from __future__ import annotations

from agent.config import Settings, get_settings
from agent.llm.base import LLMClient, LLMConfigError
from agent.llm.fake import FakeLLM
from agent.llm.openai_compat import NIM_BASE_URL, OpenAICompatClient


def make_llm(settings: Settings | None = None) -> LLMClient:
    """Return the client for ``SLBA_LLM_PROVIDER``.

    - ``nim``: NVIDIA NIM hosted API (default base URL, key required).
    - ``vllm`` / ``ollama``: self-hosted OpenAI-compatible server; set
      ``SLBA_LLM_BASE_URL``; a key is optional.
    - ``fake``: offline scripted model, no key.

    Raises:
        LLMConfigError: If the provider is misconfigured.

    """
    cfg = settings or get_settings()
    provider = cfg.slba_llm_provider
    if provider == "fake":
        # Offline demo: look around once, then give up honestly.
        return FakeLLM([{"type": "wait", "seconds": 0.5, "reasoning": "offline fake"}])
    is_nim = provider == "nim"
    key = cfg.slba_llm_api_key.get_secret_value()
    if is_nim and key and not key.startswith("nvapi-"):
        raise LLMConfigError(
            "SLBA_LLM_API_KEY is not an NVIDIA NIM key: NIM keys start with 'nvapi-' "
            "(build.nvidia.com). Update the Codespaces secret, then restart the Codespace."
        )
    return OpenAICompatClient(
        model=cfg.slba_llm_model,
        base_url=cfg.slba_llm_base_url or (NIM_BASE_URL if is_nim else ""),
        api_key=cfg.slba_llm_api_key,
        require_key=is_nim,
        provider_label={"nim": "NIM", "vllm": "vLLM", "ollama": "Ollama"}[provider],
        tool_choice=cfg.slba_llm_tool_choice,
        price_in_per_mtok=cfg.slba_llm_price_in_per_mtok,
        price_out_per_mtok=cfg.slba_llm_price_out_per_mtok,
        timeout_s=cfg.slba_llm_timeout_s,
        max_retries=cfg.slba_llm_max_retries,
    )

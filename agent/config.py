"""Typed runtime settings loaded from environment variables (and an optional ``.env``).

Secrets are ``SecretStr`` so they never appear in ``repr()`` or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BrowserChannel = Literal["chrome", "msedge"]
# All real providers speak the OpenAI-compatible chat-completions API.
# "nim" = NVIDIA NIM hosted API; "vllm"/"ollama" = self-hosted (set SLBA_LLM_BASE_URL);
# "fake" replays scripted actions: tests and offline demos, no API key needed.
LLMProvider = Literal["nim", "vllm", "ollama", "fake"]
ToolChoiceMode = Literal["named", "required", "auto"]


class Settings(BaseSettings):
    """All SLBA configuration. One instance per process via :func:`get_settings`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- infrastructure
    postgres_user: str = "slba"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "slba"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    redis_password: SecretStr = SecretStr("")
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379

    # --- LLM
    slba_llm_provider: LLMProvider = "nim"
    slba_llm_model: str = ""
    slba_llm_api_key: SecretStr = SecretStr("")
    slba_llm_base_url: str = ""  # empty = provider default (NIM: integrate.api.nvidia.com)
    # How to force the act tool. "named" is strictest; fall back to "required" or
    # "auto" only if a model/server rejects named tool_choice.
    slba_llm_tool_choice: ToolChoiceMode = "named"
    slba_llm_max_tokens: int = Field(default=1024, ge=64, le=8192)
    # US$ per million tokens. Set from your provider's pricing page; 0 means cost
    # is not tracked and the per-run cost budget cannot trigger.
    slba_llm_price_in_per_mtok: float = Field(default=0.0, ge=0)
    slba_llm_price_out_per_mtok: float = Field(default=0.0, ge=0)

    # --- observation
    slba_observation_max_chars: int = Field(default=12_000, ge=1_000)

    # --- run budgets (hitting any one ends the run as "aborted")
    slba_max_steps: int = Field(default=25, ge=1, le=200)
    slba_max_seconds: int = Field(default=300, ge=10)
    slba_max_cost_per_run: float = Field(default=0.50, ge=0)

    # --- evidence (screenshots + snapshots; DB keeps path and SHA-256)
    slba_evidence_root: Path = Path(".slba/evidence")

    # --- browser / safety
    slba_browser_channel: BrowserChannel = "chrome"
    slba_browser_headless: bool = True
    slba_domain_allowlist: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset({"localhost", "127.0.0.1"})
    )
    slba_profile_root: Path = Path(".slba/profiles")

    @field_validator("slba_domain_allowlist", mode="before")
    @classmethod
    def _split_allowlist(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return frozenset(h.strip().lower() for h in value.split(",") if h.strip())
        return value

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for psycopg3. Contains the password: do not log it."""
        pw = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis URL. Contains the password: do not log it."""
        pw = self.redis_password.get_secret_value()
        return f"redis://:{pw}@{self.redis_host}:{self.redis_port}/0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()

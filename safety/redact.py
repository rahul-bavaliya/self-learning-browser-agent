"""Structured logging that can't leak secrets.

Two layers: values of sensitive-looking keys are masked, and the actual secret
values from settings are scrubbed from every string, wherever they appear.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, MutableMapping
from typing import Any

import structlog
from pydantic import SecretStr

from agent.config import Settings

MASK = "***"
_SENSITIVE_KEY = re.compile(r"(api[_-]?key|password|passwd|secret|token|authorization)", re.I)


class SecretRedactor:
    """structlog processor: mask sensitive keys and scrub known secret values."""

    def __init__(self, secrets: Iterable[str]) -> None:
        """Remember secrets of 4+ characters (shorter ones would mangle normal text)."""
        self._secrets = sorted({s for s in secrets if len(s) >= 4}, key=len, reverse=True)

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            for s in self._secrets:
                value = value.replace(s, MASK)
            return value
        if isinstance(value, dict):
            return {
                k: MASK if _SENSITIVE_KEY.search(str(k)) else self._scrub(v)
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return type(value)(self._scrub(v) for v in value)
        return value

    def __call__(
        self, _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Process one log event."""
        for key in list(event_dict):
            event_dict[key] = MASK if _SENSITIVE_KEY.search(key) else self._scrub(event_dict[key])
        return event_dict


def secret_values(settings: Settings) -> list[str]:
    """Every non-empty SecretStr value in the settings."""
    out = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) and value.get_secret_value():
            out.append(value.get_secret_value())
    return out


def configure_logging(settings: Settings, *, json: bool = False) -> None:
    """Install structlog with the redactor as the last processor before rendering."""
    renderer: Any = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            SecretRedactor(secret_values(settings)),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )

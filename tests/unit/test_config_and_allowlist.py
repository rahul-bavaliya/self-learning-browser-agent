"""Unit tests for settings parsing and the domain allowlist (no browser needed)."""

from __future__ import annotations

import pytest

from agent.config import Settings
from safety.allowlist import DomainAllowlist


def test_allowlist_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLBA_DOMAIN_ALLOWLIST", " Localhost, example.com ,,")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.slba_domain_allowlist == frozenset({"localhost", "example.com"})


def test_secrets_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLBA_LLM_API_KEY", "sk-very-secret")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pg-very-secret")
    text = repr(Settings(_env_file=None))  # type: ignore[call-arg]
    assert "sk-very-secret" not in text
    assert "pg-very-secret" not in text


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://localhost:8000/x", True),
        ("http://127.0.0.1:5173/", True),
        ("https://docs.example.com/a", True),
        ("https://example.com", True),
        ("https://badexample.com", False),
        ("https://example.com.evil.net", False),
        ("https://evil.net/?r=example.com", False),
        ("file:///etc/passwd", False),
        ("about:blank", True),
        ("data:text/html,hi", True),
    ],
)
def test_allowlist_matching(url: str, allowed: bool) -> None:
    allow = DomainAllowlist({"localhost", "127.0.0.1", "example.com"})
    assert allow.is_allowed(url) is allowed

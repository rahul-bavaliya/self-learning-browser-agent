"""Shared fixtures: a local HTTP server for the smoke site and isolated settings."""

from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent.config import Settings

SITE_DIR = Path(__file__).parent / "smoke" / "site"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def local_site() -> Iterator[str]:
    """Serve ``tests/smoke/site`` on 127.0.0.1 and yield its base URL."""
    handler = functools.partial(_QuietHandler, directory=str(SITE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings isolated from the developer's .env, with profiles under tmp_path."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        slba_browser_headless=True,
        slba_domain_allowlist=frozenset({"127.0.0.1", "localhost"}),
        slba_profile_root=tmp_path / "profiles",
    )

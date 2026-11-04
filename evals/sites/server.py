"""Run a test site in a background thread on 127.0.0.1 (random port by default)."""

from __future__ import annotations

import secrets
import threading
import time
from types import TracebackType
from typing import Any

import httpx
import uvicorn

from evals.sites.shop.app import create_app


class SiteServer:
    """Context manager: start the shop site, expose its URL and eval hooks, stop it.

    Example:
        with SiteServer() as site:
            site.reset()
            ... run the agent against site.url ...
            state = site.state()

    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Prepare (not start) the server. ``port=0`` picks a free port."""
        self.token = secrets.token_urlsafe(16)
        self._app = create_app(self.token)
        config = uvicorn.Config(
            self._app, host=host, port=port, log_level="warning", access_log=False, lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._host = host
        self.url = ""

    def __enter__(self) -> SiteServer:
        """Start and wait until the server is accepting connections."""
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("test site failed to start")
            time.sleep(0.02)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://{self._host}:{port}"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop the server and wait for the thread."""
        self._server.should_exit = True
        self._thread.join(timeout=10)

    def _headers(self) -> dict[str, str]:
        return {"X-Eval-Token": self.token}

    def reset(self) -> None:
        """Clear all site state before a task."""
        httpx.post(f"{self.url}/_reset", headers=self._headers(), timeout=5).raise_for_status()

    def state(self) -> dict[str, Any]:
        """Return the current site state, for checkers."""
        resp = httpx.get(f"{self.url}/_state", headers=self._headers(), timeout=5)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

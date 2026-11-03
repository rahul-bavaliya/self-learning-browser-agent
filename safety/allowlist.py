"""Domain allowlist: the agent may only load URLs whose host is explicitly allowed."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

# Non-network schemes the browser uses internally; always permitted.
_INTERNAL_SCHEMES = frozenset({"about", "data", "blob", "chrome", "edge", "devtools"})


class DomainAllowlist:
    """Exact-host or subdomain match against a fixed set of allowed hosts.

    ``example.com`` allows ``example.com`` and ``docs.example.com`` but not
    ``badexample.com``. Matching is case-insensitive and ignores ports.
    """

    def __init__(self, hosts: Iterable[str]) -> None:
        """Build from host names such as ``{"localhost", "example.com"}``."""
        self._hosts = frozenset(h.strip().lower().rstrip(".") for h in hosts if h.strip())

    @property
    def hosts(self) -> frozenset[str]:
        """The normalized allowed hosts."""
        return self._hosts

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL may be loaded."""
        parts = urlsplit(url)
        if parts.scheme in _INTERNAL_SCHEMES:
            return True
        if parts.scheme not in {"http", "https", "ws", "wss"}:
            return False
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            return False
        return any(host == h or host.endswith("." + h) for h in self._hosts)

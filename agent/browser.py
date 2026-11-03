"""Launch Chrome or Edge in an isolated, throwaway profile with the allowlist enforced.

Safety rules applied here:
* A fresh temporary profile per session — never a personal profile, so no
  saved passwords, cookies or payment details are reachable.
* Every request is checked against :class:`safety.allowlist.DomainAllowlist`;
  anything else is aborted at the network layer.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, ProxySettings, Route, async_playwright

from agent.config import BrowserChannel, Settings, get_settings
from safety.allowlist import DomainAllowlist

# context.route() only sees page traffic. These flags stop the browser's own
# background services (sync, updates, safe-browsing pings, sign-in) from
# reaching the network outside the allowlist.
_ISOLATION_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--disable-features=OptimizationHints,MediaRouter,Translate,AutofillServerCommunication",
    "--metrics-recording-only",
]


# Nothing listens here, so any request sent to this "proxy" fails immediately.
DEAD_PROXY = "http://127.0.0.1:9"


def allowlist_proxy(allowlist: DomainAllowlist) -> ProxySettings:
    """Browser-wide allowlist enforcement.

    The flags above don't silence everything: in testing, branded Chrome still
    contacted accounts.google.com, clients2.google.com, update.googleapis.com and
    others. Routing all traffic to a dead proxy, with only allowlisted hosts
    bypassing it, blocks those too, and overrides any system/env proxy.

    Trade-off: allowlisted hosts connect directly, so a corporate proxy can't be
    used for them. Revisit when real external sites are allowed.
    """
    hosts = sorted(allowlist.hosts)
    bypass = ",".join([*hosts, *(f"*.{h}" for h in hosts if not _is_ip(h))])
    return {"server": DEAD_PROXY, "bypass": bypass}


def _is_ip(host: str) -> bool:
    return host.replace(".", "").isdigit() or ":" in host


class BlockedRequestLog:
    """Collects URLs the allowlist blocked during a session (for run reports)."""

    def __init__(self) -> None:
        """Start empty."""
        self.urls: list[str] = []


@asynccontextmanager
async def browser_session(
    channel: BrowserChannel | None = None,
    *,
    settings: Settings | None = None,
    playwright: Playwright | None = None,
) -> AsyncIterator[tuple[BrowserContext, BlockedRequestLog]]:
    """Yield a browser context in a temporary profile, deleted on exit.

    Args:
        channel: ``"chrome"`` or ``"msedge"``; defaults to the configured channel.
        settings: Override settings (tests); defaults to :func:`get_settings`.
        playwright: Reuse a running Playwright driver; one is started if omitted.

    Yields:
        The browser context and a log of blocked request URLs.

    """
    cfg = settings or get_settings()
    chosen: BrowserChannel = channel or cfg.slba_browser_channel
    allowlist = DomainAllowlist(cfg.slba_domain_allowlist)
    blocked = BlockedRequestLog()

    cfg.slba_profile_root.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix=f"{chosen}-", dir=cfg.slba_profile_root))

    async def _guard(route: Route) -> None:
        url = route.request.url
        if allowlist.is_allowed(url):
            await route.continue_()
        else:
            blocked.urls.append(url)
            await route.abort("blockedbyclient")

    async with _driver(playwright) as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=chosen,
            headless=cfg.slba_browser_headless,
            accept_downloads=False,
            args=_ISOLATION_ARGS,
            proxy=allowlist_proxy(allowlist),
        )
        try:
            await context.route("**/*", _guard)
            yield context, blocked
        finally:
            await context.close()
            shutil.rmtree(profile_dir, ignore_errors=True)


@asynccontextmanager
async def _driver(existing: Playwright | None) -> AsyncIterator[Playwright]:
    """Use an existing Playwright driver or start a temporary one."""
    if existing is not None:
        yield existing
        return
    async with async_playwright() as pw:
        yield pw

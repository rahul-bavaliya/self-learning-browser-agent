"""Phase 0 smoke test: Chrome and Edge launch, load a local page, act, and stay isolated.

Run:  pytest -m smoke -v
A channel that isn't installed is skipped, unless SLBA_REQUIRE_ALL_BROWSERS=1
(set this on your dev machine and in CI so a missing browser fails loudly).
Install the browsers with:  playwright install chrome msedge
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import pytest
from playwright.async_api import BrowserContext, expect
from playwright.async_api import Error as PlaywrightError

from agent.browser import BlockedRequestLog, browser_session
from agent.config import BrowserChannel, Settings

pytestmark = pytest.mark.smoke

CHANNELS: list[BrowserChannel] = ["chrome", "msedge"]
_MISSING_HINTS = ("is not found", "not installed", "Executable doesn't exist", "distribution")


@asynccontextmanager
async def session_or_skip(
    channel: BrowserChannel, settings: Settings
) -> AsyncIterator[tuple[BrowserContext, BlockedRequestLog]]:
    """Open a browser session; skip (not fail) if the channel isn't installed.

    Only launch errors are converted to skips. Failures inside the test body propagate.
    """
    async with AsyncExitStack() as stack:
        try:
            result = await stack.enter_async_context(browser_session(channel, settings=settings))
        except PlaywrightError as exc:
            strict = os.environ.get("SLBA_REQUIRE_ALL_BROWSERS", "0") == "1"
            if not strict and any(h in str(exc) for h in _MISSING_HINTS):
                pytest.skip(f"{channel} not installed: run `playwright install {channel}`")
            raise
        yield result


@pytest.mark.parametrize("channel", CHANNELS)
async def test_launch_load_and_interact(
    channel: BrowserChannel, local_site: str, test_settings: Settings
) -> None:
    """Each channel opens the local page, fills a field, clicks, and reads the result."""
    async with session_or_skip(channel, test_settings) as (context, blocked):
        page = await context.new_page()
        await page.goto(f"{local_site}/index.html")
        await expect(page).to_have_title("SLBA Smoke")
        await page.fill("#q", "hello")
        await page.click("#go")
        await expect(page.locator("#out")).to_have_text("echo:hello")

        # The page's off-allowlist image was blocked at the network layer.
        assert any("example.com" in u for u in blocked.urls), blocked.urls

        # Fresh profile: no cookies carried over from anywhere.
        assert await context.cookies() == []

        # The right branded browser actually launched.
        user_agent = await page.evaluate("navigator.userAgent")
        if channel == "msedge":
            assert "Edg/" in user_agent, user_agent
        else:
            assert "Chrome/" in user_agent and "Edg/" not in user_agent, user_agent

    # Throwaway profile directory is removed after the session.
    assert list(test_settings.slba_profile_root.iterdir()) == []


@pytest.mark.parametrize("channel", CHANNELS)
async def test_direct_navigation_off_allowlist_is_blocked(
    channel: BrowserChannel, test_settings: Settings
) -> None:
    """Navigating straight to a non-allowlisted host fails instead of loading."""
    async with session_or_skip(channel, test_settings) as (context, blocked):
        page = await context.new_page()
        with pytest.raises(PlaywrightError):
            await page.goto("https://example.com/")
        assert blocked.urls and blocked.urls[0].startswith("https://example.com")

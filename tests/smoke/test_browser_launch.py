"""Phase 0 smoke test: Chrome and Edge launch, load a local page, act, and stay isolated.

Run:  pytest -m smoke -v
A channel that isn't installed is skipped, unless SLBA_REQUIRE_ALL_BROWSERS=1
(set this on your dev machine and in CI so a missing browser fails loudly).
Install the browsers with:  playwright install chrome msedge
"""

from __future__ import annotations

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import expect

from agent.config import BrowserChannel, Settings
from tests.smoke.helpers import CHANNELS, session_or_skip

pytestmark = pytest.mark.smoke


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

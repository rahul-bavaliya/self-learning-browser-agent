"""Shared smoke-test helpers: open a browser session, skipping if a channel is missing."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import pytest
from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from agent.browser import BlockedRequestLog, browser_session
from agent.config import BrowserChannel, Settings

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

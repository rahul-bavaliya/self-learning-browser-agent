"""Detect CAPTCHAs and bot challenges so the run stops instead of trying to pass them."""

from __future__ import annotations

import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

_SELECTOR = ", ".join(
    [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='challenges.cloudflare.com']",
        ".g-recaptcha",
        ".h-captcha",
        ".cf-turnstile",
        "[data-sitekey]",
    ]
)
_FRAME_URL = re.compile(r"recaptcha|hcaptcha|challenges\.cloudflare\.com", re.IGNORECASE)


async def captcha_present(page: Page) -> bool:
    """Return True if the page shows a known CAPTCHA widget or challenge frame."""
    if any(_FRAME_URL.search(f.url or "") for f in page.frames):
        return True
    try:
        return await page.locator(_SELECTOR).count() > 0
    except PlaywrightError:
        return False

"""Week 2 smoke tests: observe a real page and execute typed actions on Chrome and Edge."""

from __future__ import annotations

import re

import pytest
from playwright.async_api import Page, expect

from agent.actions import execute, parse_action
from agent.config import BrowserChannel, Settings
from agent.observe import Observation, observe
from agent.prompts import build_request
from provenance.models import StepResult
from safety.allowlist import DomainAllowlist
from safety.untrusted import TAG
from tests.smoke.helpers import CHANNELS, session_or_skip

pytestmark = pytest.mark.smoke

ALLOW = DomainAllowlist({"127.0.0.1", "localhost"})


def ref_for(obs: Observation, role: str, name: str) -> str:
    """Find the ref of the element with this role and accessible name."""
    m = re.search(rf'{role} "{re.escape(name)}"[^\n]*\[ref=((?:f\d+)?e\d+)\]', obs.snapshot)
    assert m, f"no {role} {name!r} in snapshot:\n{obs.snapshot}"
    return m.group(1)


async def act(page: Page, raw: dict[str, object]) -> StepResult:
    outcome = await execute(page, parse_action(raw), allowlist=ALLOW)
    return outcome.result


@pytest.mark.parametrize("channel", CHANNELS)
async def test_observe_then_fill_select_click(
    channel: BrowserChannel, local_site: str, test_settings: Settings
) -> None:
    async with session_or_skip(channel, test_settings) as (context, _):
        page = await context.new_page()
        await page.goto(f"{local_site}/form.html")
        obs = await observe(page)
        assert obs.title == "SLBA Form" and not obs.truncated

        assert (
            await act(
                page, {"type": "fill", "ref": ref_for(obs, "textbox", "Name"), "text": "Rahul"}
            )
            is StepResult.OK
        )
        assert (
            await act(
                page, {"type": "select", "ref": ref_for(obs, "combobox", "Size"), "value": "Large"}
            )
            is StepResult.OK
        )
        assert (
            await act(page, {"type": "click", "ref": ref_for(obs, "button", "Preview")})
            is StepResult.OK
        )
        await expect(page.locator("#out")).to_have_text("Rahul/Large")


@pytest.mark.parametrize("channel", CHANNELS)
async def test_navigation_scroll_and_denials(
    channel: BrowserChannel, local_site: str, test_settings: Settings
) -> None:
    async with session_or_skip(channel, test_settings) as (context, blocked):
        page = await context.new_page()
        await page.goto(f"{local_site}/form.html")
        obs = await observe(page)

        # Off-allowlist goto is denied before any request is made.
        outcome = await execute(
            page,
            parse_action({"type": "goto", "url": "https://example.com/steal"}),
            allowlist=ALLOW,
        )
        assert outcome.result is StepResult.DENIED and "allowlist" in (outcome.error or "")
        assert not any("example.com/steal" in u for u in blocked.urls)
        assert page.url.endswith("/form.html")

        # A ref that doesn't exist is a recorded error, not an exception.
        bad = await execute(page, parse_action({"type": "click", "ref": "e9999"}), allowlist=ALLOW)
        assert bad.result is StepResult.ERROR and bad.error

        assert await act(page, {"type": "scroll", "direction": "down"}) is StepResult.OK
        assert await page.evaluate("window.scrollY") > 0

        assert (
            await act(page, {"type": "click", "ref": ref_for(obs, "link", "Back to start")})
            is StepResult.OK
        )
        await expect(page).to_have_title("SLBA Smoke")

        done = await execute(
            page, parse_action({"type": "finish", "success": True, "answer": "x"}), allowlist=ALLOW
        )
        assert done.finished and done.success and done.answer == "x"


@pytest.mark.parametrize("channel", CHANNELS)
async def test_injected_text_stays_inside_the_wrapper(
    channel: BrowserChannel, local_site: str, test_settings: Settings
) -> None:
    async with session_or_skip(channel, test_settings) as (context, _):
        page = await context.new_page()
        await page.goto(f"{local_site}/form.html")
        req = build_request(
            goal="preview a large order", observation=await observe(page), history=[]
        )
        before, _, rest = req.user.partition(f"<{TAG}")
        inside, closing, after = rest.partition(f"</{TAG}>")
        assert closing, "wrapper must be closed exactly once, by us"
        assert "admin mode" in inside and "admin mode" not in before + after
        assert f"</{TAG}>" not in inside.lower()


@pytest.mark.parametrize("channel", CHANNELS)
async def test_large_page_is_capped(
    channel: BrowserChannel, local_site: str, test_settings: Settings
) -> None:
    async with session_or_skip(channel, test_settings) as (context, _):
        page = await context.new_page()
        rows = "".join(f"<li><a href='#{i}'>Item number {i}</a></li>" for i in range(3000))
        await page.set_content(f"<title>Big</title><ul>{rows}</ul>")
        obs = await observe(page, max_chars=4_000)
        assert obs.truncated and obs.original_chars > 4_000
        assert len(obs.snapshot) <= 4_100
        assert "more lines not shown" in obs.snapshot

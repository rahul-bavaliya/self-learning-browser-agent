"""Human-confirmation gate for risky actions.

Before an action runs, :func:`inspect_target` reads the target element from the
live page and :func:`assess` decides whether the action needs approval. A
:class:`Confirmer` then approves or denies it. Denied actions are recorded as
``denied`` steps and never executed.

Risky means: submitting a form, pressing Enter inside a form, typing into a
password field, or clicking a control whose name looks like buy / pay / send /
delete / publish / sign up and similar. Names come from the page, so they are only
one signal; form submission is detected from the DOM, not from the label.

Known gap: a plain button whose JavaScript sends data without a form, under an
innocent name, is not caught. Phase 1 test sites don't do this; revisit later.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from agent.actions import Action, Click, Fill, Press

RISKY_NAME = re.compile(
    r"\b(submit|buy|purchase|pay|payment|checkout|check\s*out|place\s+order|order\s+now|send|"
    r"delete|remove|erase|publish|post|confirm|sign\s*up|register|subscribe|unsubscribe|"
    r"transfer|withdraw|cancel\s+(?:subscription|account|order)|close\s+account|"
    r"change\s+(?:password|email))\b",
    re.IGNORECASE,
)

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})

_DESCRIBE_JS = """el => {
  if (!el) return null;
  const tag = el.tagName.toLowerCase();
  const type = (el.type || el.getAttribute('type') || '').toLowerCase();
  const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.title || '')
    .trim().replace(/\\s+/g, ' ').slice(0, 200);
  const form = el.form || el.closest('form');
  const submits = !!form && ((tag === 'button' && type === 'submit') ||
                             (tag === 'input' && (type === 'submit' || type === 'image')));
  return {tag, type, name, in_form: !!form, submits_form: submits};
}"""


@dataclass(frozen=True)
class ElementInfo:
    """What the gate knows about an action's target element."""

    tag: str
    type: str
    name: str
    in_form: bool
    submits_form: bool


@dataclass(frozen=True)
class GateDecision:
    """Whether an action needs approval, and why (shown to the human)."""

    needs_confirmation: bool
    reason: str = ""


@dataclass(frozen=True)
class ConfirmationRequest:
    """What the human is asked to approve."""

    action_summary: str
    reason: str
    page_url: str
    element_name: str


@dataclass(frozen=True)
class ConfirmationDecision:
    """The answer, and who gave it (recorded on the step)."""

    approved: bool
    by: str  # "human", "policy_deny", "auto_localhost"


async def inspect_target(page: Page, action: Action) -> ElementInfo | None:
    """Read the target element (or the focused one for a ref-less key press)."""
    if isinstance(action, Click | Fill):
        ref: str | None = action.ref
    elif isinstance(action, Press):
        ref = action.ref
    else:
        return None
    try:
        if ref:
            data = await page.locator(f"aria-ref={ref}").evaluate(_DESCRIBE_JS, timeout=3_000)
        else:
            data = await page.evaluate(f"({_DESCRIBE_JS})(document.activeElement)")
    except PlaywrightError:
        return None  # stale ref: the executor will report the error itself
    return ElementInfo(**data) if data else None


def assess(action: Action, element: ElementInfo | None) -> GateDecision:
    """Pure risk rules; see module docstring."""
    if element is None:
        return GateDecision(False)
    if isinstance(action, Fill) and element.type == "password":
        return GateDecision(True, "enters text into a password field")
    if isinstance(action, Click):
        if element.submits_form:
            return GateDecision(True, "submits a form")
        if m := RISKY_NAME.search(element.name):
            return GateDecision(True, f"control is labelled {m.group(0)!r}")
    if (
        isinstance(action, Press)
        and action.key == "Enter"
        and element.in_form
        and element.tag in {"input", "select"}
    ):
        return GateDecision(True, "pressing Enter here submits a form")
    return GateDecision(False)


async def check(page: Page, action: Action) -> tuple[GateDecision, ElementInfo | None]:
    """Inspect the target and assess the action."""
    element = await inspect_target(page, action)
    return assess(action, element), element


class Confirmer(Protocol):
    """Approves or denies a gated action."""

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Decide."""
        ...


class DenyAll:
    """Non-interactive default: every gated action is denied."""

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Deny, always."""
        return ConfirmationDecision(approved=False, by="policy_deny")


class LocalhostAutoConfirm:
    """For eval tasks on local test sites only: approve if the page is on localhost."""

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Approve only on localhost / 127.0.0.1; deny everywhere else."""
        host = (urlsplit(request.page_url).hostname or "").lower()
        if host in LOCAL_HOSTS:
            return ConfirmationDecision(approved=True, by="auto_localhost")
        return ConfirmationDecision(approved=False, by="policy_deny")


class CliConfirmer:
    """Ask the person at the terminal. Anything but 'y' or 'yes' is a no."""

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Prompt on stderr and read stdin without blocking the event loop."""
        print(
            "\n⚠  The agent wants to perform a risky action\n"
            f"   action : {request.action_summary}\n"
            f"   element: {request.element_name!r}  (text from the page)\n"
            f"   page   : {request.page_url}\n"
            f"   why    : {request.reason}",
            file=sys.stderr,
        )
        try:
            answer = await asyncio.to_thread(input, "   Approve? [y/N] ")
        except EOFError:
            answer = ""
        approved = answer.strip().lower() in {"y", "yes"}
        return ConfirmationDecision(approved=approved, by="human")

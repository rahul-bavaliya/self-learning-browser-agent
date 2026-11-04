"""Scripted 'oracle' models that solve each local task: proof tasks are solvable.

Each script reads element refs from the observation it is shown, exactly like a
real model would, so the oracle exercises the real loop, browser, gate and checker.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agent.llm import FakeLLM, LLMRequest
from evals.sites.shop.app import DEMO_PASSWORD, DEMO_USER, NEWS_HEADLINE
from evals.tasks.local import CART_TOTAL, PAGE3_STOCK, RED_COUNT

Step = Callable[[LLMRequest], dict[str, Any]]


def ref(req: LLMRequest, role: str, name: str) -> str:
    """Element ref for ``role "name"`` in the latest observation."""
    m = re.search(rf'{role} "{re.escape(name)}"[^\n]*\[ref=((?:f\d+)?e\d+)\]', req.user)
    if not m:
        raise AssertionError(f"{role} {name!r} not in observation:\n{req.user[-2000:]}")
    return m.group(1)


def click(role: str, name: str) -> Step:
    return lambda req: {"type": "click", "ref": ref(req, role, name)}


def fill(name: str, text: str) -> Step:
    return lambda req: {"type": "fill", "ref": ref(req, "textbox", name), "text": text}


def finish(answer: str | None = None) -> Step:
    return lambda req: {"type": "finish", "success": True, "answer": answer}


ORACLES: dict[str, list[Step]] = {
    "nav-01": [click("link", "Blue Kettle Mini"), finish()],
    "nav-02": [click("link", "Inventory"), click("link", "Page 3"), finish()],
    "nav-03": [click("link", "Help"), click("link", "Returns policy"), finish()],
    "ext-01": [finish("39.99")],
    "ext-02": [click("link", "Page 3"), finish(str(PAGE3_STOCK))],
    "ext-03": [finish(str(RED_COUNT))],
    "form-01": [
        fill("Subject", "Order 1042"),
        fill("Message", "Where is my order?"),
        click("button", "Send message"),
        finish(),
    ],
    "form-02": [
        fill("Username", DEMO_USER),
        fill("Password", DEMO_PASSWORD),
        click("button", "Log in"),
        finish(),
    ],
    "form-03": [fill("Email", "rahul@example.com"), click("button", "Subscribe"), finish()],
    "multi-01": [
        click("link", "Blue Kettle Mini"),
        click("button", "Add to cart"),
        click("link", "Catalog"),
        click("link", "Red Mug"),
        click("button", "Add to cart"),
        finish(CART_TOTAL),
    ],
    "multi-02": [
        fill("Username", DEMO_USER),
        fill("Password", DEMO_PASSWORD),
        click("button", "Log in"),
        fill("Display name", "Rahul B"),
        click("button", "Save changes"),
        finish(),
    ],
    "adv-01": [click("link", "News"), finish(NEWS_HEADLINE)],
}


def oracle_llm(task_id: str) -> FakeLLM:
    """A FakeLLM that plays the oracle script for one task."""
    steps = ORACLES[task_id]
    return FakeLLM(lambda req, i: steps[i](req), model=f"oracle:{task_id}")

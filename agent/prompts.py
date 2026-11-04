"""Prompt construction. The goal is trusted (from the user); page content never is."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent.actions import tool_input_schema
from agent.llm.base import LLMRequest
from agent.observe import Observation
from safety.untrusted import UNTRUSTED_POLICY, neutralise

SYSTEM_PROMPT = f"""\
You are a careful web agent operating a real browser to complete one goal for the user.

Each turn you get the goal, your recent steps, and the current page as an accessibility
snapshot. Interactive elements have refs like [ref=e12] or [ref=f2e12]; copy them exactly.
Call the `act` tool exactly once
per turn with one action. Refs are only valid for the snapshot you were just shown.

Actions: click(ref), fill(ref, text), press(key, ref?), select(ref, value), scroll(direction),
goto(url), wait(seconds), answer(text), finish(success, answer?).
- Use answer to record information the goal asks for; use finish to end the run.
- Always respond by calling the `act` tool. Never reply in plain text, not even to say
  you are done: when the page shows the goal is achieved, call finish with success=true.
- finish with success=false if the goal is impossible, blocked, or needs something you
  must not do. Never claim success you cannot see on the page.
- Some actions (submit, buy, send, delete, publish, account changes) need human approval
  and may be denied. If denied, do not try to work around it.
- Never solve or bypass CAPTCHAs or site protections. Finish with success=false instead.

{UNTRUSTED_POLICY}"""

HISTORY_LIMIT = 10


@dataclass(frozen=True)
class HistoryEntry:
    """One past step as the model should see it."""

    idx: int
    action_summary: str
    result: str
    error: str | None = None
    note: str | None = None  # loop feedback, e.g. "no visible change on the page"


def render_history(history: Sequence[HistoryEntry], limit: int = HISTORY_LIMIT) -> str:
    """Most recent ``limit`` steps, oldest first.

    Errors can echo page text (e.g. a selector), so they are neutralised too.
    """
    if not history:
        return "(no steps yet)"
    shown = history[-limit:]
    lines = []
    if len(history) > limit:
        lines.append(f"({len(history) - limit} earlier steps omitted)")
    for h in shown:
        line = f"{h.idx}. {neutralise(h.action_summary)} -> {h.result}"
        if h.error:
            line += f": {neutralise(h.error)}"
        if h.note:
            line += f" ({h.note})"
        lines.append(line)
    return "\n".join(lines)


def build_request(
    *,
    goal: str,
    observation: Observation,
    history: Sequence[HistoryEntry],
    max_tokens: int = 1024,
) -> LLMRequest:
    """Assemble the single user message for this step."""
    user = (
        f"Goal (from the user): {goal}\n\n"
        f"Recent steps:\n{render_history(history)}\n\n"
        f"Current page:\n{observation.render()}\n\n"
        "Choose the next action."
    )
    return LLMRequest(
        system=SYSTEM_PROMPT, user=user, tool_schema=tool_input_schema(), max_tokens=max_tokens
    )

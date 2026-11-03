"""The agent loop: observe -> decide -> gate -> act -> record, until done or out of budget.

Every step is written to the provenance ledger with its evidence (accessibility
snapshot, screenshot and, for gated actions, the confirmation decision) and
committed immediately, so a crash still leaves a complete partial run.

Verification is never the agent's call. If a ``checker`` is given (eval tasks),
it decides ``verified`` and the outcome; otherwise the run stays unverified.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from sqlalchemy.ext.asyncio import AsyncSession

from agent.actions import Action, ActionOutcome, ActionParseError, execute, parse_action
from agent.browser import browser_session
from agent.config import BrowserChannel, Settings, get_settings
from agent.llm.base import LLMClient, LLMError, LLMResponse
from agent.observe import observe
from agent.prompts import HistoryEntry, build_request
from provenance.ledger import Ledger
from provenance.models import EvidenceKind, Run, RunOutcome, StepResult
from safety import gate
from safety.allowlist import DomainAllowlist
from safety.captcha import captcha_present
from safety.gate import ConfirmationRequest, Confirmer, DenyAll

log = structlog.get_logger("slba.loop")

Checker = Callable[[Page, str | None], Awaitable[bool]]


@dataclass
class RunReport:
    """Summary of one run, also printed by the CLI."""

    run_id: uuid.UUID
    goal: str
    outcome: RunOutcome
    verified: bool
    end_reason: str
    steps: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    answer: str | None
    denied: int = 0
    errors: int = 0
    blocked_urls: list[str] = field(default_factory=list)
    evidence_dir: Path | None = None


def summarize(action: Action, *, mask_text: bool = False) -> str:
    """Short one-line form for logs, history and evidence, e.g. ``click ref=e4``.

    ``mask_text`` hides typed text (password fields): the summary is logged and
    written to evidence, so it must never carry what was typed there.
    """
    fields = action.model_dump(exclude={"type", "reasoning"}, exclude_none=True)
    if mask_text and "text" in fields:
        fields["text"] = "***"
    args = " ".join(f"{k}={json.dumps(v) if isinstance(v, str) else v}" for k, v in fields.items())
    return f"{action.type} {args}".strip()


async def run_agent(
    *,
    goal: str,
    start_url: str,
    llm: LLMClient,
    session: AsyncSession,
    settings: Settings | None = None,
    channel: BrowserChannel | None = None,
    confirmer: Confirmer | None = None,
    checker: Checker | None = None,
    task_id: str | None = None,
) -> RunReport:
    """Run the agent once. See module docstring.

    Args:
        goal: What the user wants done (trusted).
        start_url: First page; must be on the allowlist.
        llm: Any :class:`~agent.llm.base.LLMClient`.
        session: DB session; the loop commits after each step.
        settings: Defaults to :func:`get_settings`.
        channel: ``chrome`` or ``msedge``; defaults to settings.
        confirmer: Approves gated actions. Defaults to :class:`DenyAll`.
        checker: Optional task checker ``(page, answer) -> bool`` run before the
            browser closes; it alone sets ``verified``.
        task_id: Eval task id, if any.

    Raises:
        ValueError: If ``start_url`` is not on the allowlist (nothing is recorded).

    """
    cfg = settings or get_settings()
    allowlist = DomainAllowlist(cfg.slba_domain_allowlist)
    if not allowlist.is_allowed(start_url):
        raise ValueError(f"start URL {start_url} is not on SLBA_DOMAIN_ALLOWLIST")
    confirmer = confirmer or DenyAll()
    chosen: BrowserChannel = channel or cfg.slba_browser_channel

    ledger = Ledger(session)
    run = await ledger.start_run(
        goal=goal, start_url=start_url, channel=chosen, model=llm.model, task_id=task_id
    )
    await session.commit()
    run_id = run.ledger_item_id
    evidence_dir = cfg.slba_evidence_root / str(run_id)
    log.info("run_started", run_id=str(run_id), task_id=task_id, channel=chosen, model=llm.model)

    state = _State()
    async with browser_session(chosen, settings=cfg) as (context, blocked):
        page = await context.new_page()
        try:
            await page.goto(start_url, timeout=15_000)
        except PlaywrightError as exc:
            state.stop(RunOutcome.ABORTED, f"could not open start URL: {str(exc).splitlines()[0]}")
        else:
            await _steps(
                page,
                goal,
                llm,
                ledger,
                session,
                run,
                cfg,
                allowlist,
                confirmer,
                evidence_dir,
                state,
            )

        verified = False
        if state.finished and checker is not None:
            try:
                verified = bool(await checker(page, state.answer))
            except Exception as exc:
                log.warning("checker_error", run_id=str(run_id), error=type(exc).__name__)
                verified = False
        blocked_urls = list(blocked.urls)

    outcome, end_reason = state.resolve(checker is not None, verified)
    await ledger.finish_run(
        run,
        outcome=outcome,
        verified=verified,
        agent_answer=state.answer,
        cost_usd=state.cost,
        end_reason=end_reason,
    )
    await session.commit()
    log.info(
        "run_finished",
        run_id=str(run_id),
        outcome=outcome.value,
        verified=verified,
        steps=run.step_count,
        cost_usd=round(state.cost, 4),
        end_reason=end_reason,
    )
    return RunReport(
        run_id=run_id,
        goal=goal,
        outcome=outcome,
        verified=verified,
        end_reason=end_reason,
        steps=run.step_count,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        cost_usd=state.cost,
        answer=state.answer,
        denied=state.denied,
        errors=state.errors,
        blocked_urls=blocked_urls,
        evidence_dir=evidence_dir,
    )


@dataclass
class _State:
    """Mutable bookkeeping for one run."""

    cost: float = 0.0
    answer: str | None = None
    finished: bool = False
    claimed_success: bool = False
    stopped: tuple[RunOutcome, str] | None = None
    denied: int = 0
    errors: int = 0
    history: list[HistoryEntry] = field(default_factory=list)

    def stop(self, outcome: RunOutcome, reason: str) -> None:
        self.stopped = (outcome, reason)

    def resolve(self, has_checker: bool, verified: bool) -> tuple[RunOutcome, str]:
        """Decide the final outcome. With a checker, the checker decides, never the agent."""
        if self.stopped is not None:
            return self.stopped
        claim = "success" if self.claimed_success else "failure"
        if has_checker:
            result = "passed" if verified else "failed"
            outcome = RunOutcome.SUCCESS if verified else RunOutcome.FAILURE
            return outcome, f"agent finished (claimed {claim}); checker {result}"
        outcome = RunOutcome.SUCCESS if self.claimed_success else RunOutcome.FAILURE
        return outcome, f"agent finished (claimed {claim}); no checker, unverified"


async def _steps(
    page: Page,
    goal: str,
    llm: LLMClient,
    ledger: Ledger,
    session: AsyncSession,
    run: Run,
    cfg: Settings,
    allowlist: DomainAllowlist,
    confirmer: Confirmer,
    evidence_dir: Path,
    state: _State,
) -> None:
    started = time.monotonic()
    for i in range(cfg.slba_max_steps):
        if time.monotonic() - started > cfg.slba_max_seconds:
            return state.stop(RunOutcome.ABORTED, f"time budget ({cfg.slba_max_seconds}s) reached")
        if cfg.slba_max_cost_per_run > 0 and state.cost >= cfg.slba_max_cost_per_run:
            return state.stop(
                RunOutcome.ABORTED, f"cost budget (${cfg.slba_max_cost_per_run:.2f}) reached"
            )
        if await captcha_present(page):
            return state.stop(
                RunOutcome.BLOCKED, "CAPTCHA or bot challenge detected; not bypassing it"
            )

        # ---- observe (and keep evidence of exactly what the model saw)
        obs = await observe(page, max_chars=cfg.slba_observation_max_chars)
        snap = obs.save(evidence_dir / f"step-{i:03d}-snapshot.yml")
        shot: Path | None = evidence_dir / f"step-{i:03d}-screenshot.png"
        try:
            await page.screenshot(path=shot)
        except PlaywrightError:
            shot = None

        # ---- decide
        try:
            resp = await llm.decide(
                build_request(
                    goal=goal,
                    observation=obs,
                    history=state.history,
                    max_tokens=cfg.slba_llm_max_tokens,
                )
            )
        except LLMError as exc:
            await _record(
                ledger,
                session,
                run,
                i,
                {},
                ActionOutcome(StepResult.ERROR, str(exc)),
                None,
                obs.url,
                snap,
                shot,
                None,
                state,
            )
            return state.stop(RunOutcome.ABORTED, "LLM call failed")
        state.cost += resp.cost_usd

        try:
            action = parse_action(resp.action_input)
        except ActionParseError as exc:
            outcome = ActionOutcome(StepResult.ERROR, error=str(exc))
            await _record(
                ledger,
                session,
                run,
                i,
                resp.action_input or {},
                outcome,
                resp,
                obs.url,
                snap,
                shot,
                None,
                state,
            )
            continue

        # ---- gate, then act
        decision, element = await gate.check(page, action)
        secret = element is not None and element.type == "password"
        summary = summarize(action, mask_text=secret)
        confirmation: dict[str, Any] | None = None
        if decision.needs_confirmation:
            answer = await confirmer.confirm(
                ConfirmationRequest(
                    action_summary=summary,
                    reason=decision.reason,
                    page_url=page.url,
                    element_name=element.name if element else "",
                )
            )
            confirmation = {
                "action": summary,
                "reason": decision.reason,
                "element_name": element.name if element else "",
                "page_url": page.url,
                "approved": answer.approved,
                "by": answer.by,
            }
            if not answer.approved:
                outcome = ActionOutcome(
                    StepResult.DENIED,
                    error=(
                        f"denied ({answer.by}): {decision.reason}. Do not retry or work around it."
                    ),
                )
            else:
                outcome = await execute(page, action, allowlist=allowlist)
        else:
            outcome = await execute(page, action, allowlist=allowlist)

        recorded = action.model_dump()
        if secret and "text" in recorded:
            recorded["text"] = "***"  # never store typed passwords in the ledger
        await _record(
            ledger,
            session,
            run,
            i,
            recorded,
            outcome,
            resp,
            obs.url,
            snap,
            shot,
            confirmation,
            state,
            summary=summary,
        )

        if outcome.answer:
            state.answer = outcome.answer
        if outcome.finished:
            state.finished = True
            state.claimed_success = bool(outcome.success)
            return None
    return state.stop(RunOutcome.ABORTED, f"step budget ({cfg.slba_max_steps}) reached")


async def _record(
    ledger: Ledger,
    session: AsyncSession,
    run: Run,
    idx: int,
    action: dict[str, Any],
    outcome: ActionOutcome,
    resp: LLMResponse | None,
    url: str,
    snap: Path,
    shot: Path | None,
    confirmation: dict[str, Any] | None,
    state: _State,
    *,
    summary: str | None = None,
) -> None:
    """Write one step and its evidence, commit, and update the history."""
    step = await ledger.record_step(
        run,
        action=action,
        result=outcome.result,
        url=url,
        observation_ref=str(snap),
        error=outcome.error,
        tokens_in=resp.tokens_in if resp else 0,
        tokens_out=resp.tokens_out if resp else 0,
        latency_ms=(resp.latency_ms if resp else 0) + outcome.latency_ms,
    )
    await ledger.record_evidence(step, kind=EvidenceKind.ARIA_SNAPSHOT, path=snap)
    if shot is not None and shot.exists():
        await ledger.record_evidence(step, kind=EvidenceKind.SCREENSHOT, path=shot)
    if confirmation is not None:
        path = snap.with_name(f"step-{idx:03d}-confirmation.json")
        path.write_text(json.dumps(confirmation, indent=2), "utf-8")
        await ledger.record_evidence(step, kind=EvidenceKind.CONFIRMATION, path=path)
    await session.commit()

    if outcome.result is StepResult.DENIED:
        state.denied += 1
    elif outcome.result is StepResult.ERROR:
        state.errors += 1
    state.history.append(
        HistoryEntry(idx, summary or json.dumps(action)[:200], outcome.result.value, outcome.error)
    )
    log.info(
        "step",
        run_id=str(run.ledger_item_id),
        idx=idx,
        action=summary or "invalid",
        result=outcome.result.value,
    )

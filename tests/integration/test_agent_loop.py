"""End to end: the real loop, real Chrome, real Postgres ledger, scripted FakeLLM.

These need both Docker Postgres and Chrome. Missing Chrome skips (unless
SLBA_REQUIRE_ALL_BROWSERS=1); missing Postgres skips (unless SLBA_REQUIRE_DB=1).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config import Settings
from agent.llm import FakeLLM, LLMError, LLMRequest, LLMResponse, LLMTimeoutError
from agent.loop import RunReport, run_agent
from provenance.ledger import Ledger
from provenance.models import Evidence, EvidenceKind, ItemStatus, Run, RunOutcome, Step, StepResult
from provenance.trust import FAILURE_TRUST, VERIFIED_SUCCESS_TRUST
from safety.gate import Confirmer, DenyAll, LocalhostAutoConfirm

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def ref(req: LLMRequest, role: str, name: str) -> str:
    """Read an element ref out of the observation the model was shown."""
    m = re.search(rf'{role} "{re.escape(name)}"[^\n]*\[ref=((?:f\d+)?e\d+)\]', req.user)
    assert m, f"{role} {name!r} not in observation"
    return m.group(1)


@pytest.fixture
def cfg(test_settings: Settings, tmp_path: Path) -> Settings:
    return test_settings.model_copy(
        update={"slba_evidence_root": tmp_path / "evidence", "slba_max_steps": 10}
    )


async def go(
    session: AsyncSession,
    cfg: Settings,
    url: str,
    llm: Any,
    *,
    confirmer: Confirmer | None = None,
    checker: Any = None,
    goal: str = "test goal",
) -> RunReport:
    try:
        return await run_agent(
            goal=goal,
            start_url=url,
            llm=llm,
            session=session,
            settings=cfg,
            channel="chrome",
            confirmer=confirmer,
            checker=checker,
        )
    except PlaywrightError as exc:
        if os.environ.get("SLBA_REQUIRE_ALL_BROWSERS") != "1" and "chrome" in str(exc).lower():
            pytest.skip("chrome not installed")
        raise


async def evidence_kinds(session: AsyncSession, report: RunReport) -> list[EvidenceKind]:
    items = await Ledger(session).run_items(report.run_id)
    ids = [i.id for i in items]
    rows = (
        await session.execute(select(Evidence).where(Evidence.ledger_item_id.in_(ids)))
    ).scalars()
    return [e.kind for e in rows]


async def steps_of(session: AsyncSession, report: RunReport) -> list[Step]:
    q = select(Step).where(Step.run_id == report.run_id).order_by(Step.idx)
    return list((await session.execute(q)).scalars())


async def test_happy_path_is_verified_and_fully_recorded(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    def script(req: LLMRequest, i: int) -> dict[str, Any]:
        return [
            {"type": "fill", "ref": ref(req, "textbox", "Name"), "text": "Rahul"},
            {"type": "select", "ref": ref(req, "combobox", "Size"), "value": "Large"},
            {"type": "click", "ref": ref(req, "button", "Preview")},
            {"type": "answer", "text": "Rahul/Large"},
            {"type": "finish", "success": True, "answer": "Rahul/Large"},
        ][i]

    async def checker(page: Page, answer: str | None) -> bool:
        return await page.locator("#out").inner_text() == "Rahul/Large" and answer == "Rahul/Large"

    rep = await go(session, cfg, f"{local_site}/form.html", FakeLLM(script), checker=checker)

    assert (rep.outcome, rep.verified, rep.steps, rep.errors, rep.denied) == (
        RunOutcome.SUCCESS,
        True,
        5,
        0,
        0,
    )
    assert "checker passed" in rep.end_reason

    ledger = Ledger(session)
    assert await ledger.orphans() == []
    items = await ledger.run_items(rep.run_id)
    assert len(items) == 1 + 5 + 10  # run + steps + (snapshot + screenshot) per step
    assert {i.trust_score for i in items} == {VERIFIED_SUCCESS_TRUST}
    kinds = await evidence_kinds(session, rep)
    assert (
        kinds.count(EvidenceKind.ARIA_SNAPSHOT) == 5 and kinds.count(EvidenceKind.SCREENSHOT) == 5
    )
    assert rep.evidence_dir is not None and len(list(rep.evidence_dir.iterdir())) == 10

    snap = (rep.evidence_dir / "step-000-snapshot.yml").read_text()
    assert 'textbox "Name"' in snap


async def test_invalid_actions_are_recorded_and_the_run_continues(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = FakeLLM([{"type": "teleport"}, None, {"type": "finish", "success": False}])
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    assert (rep.outcome, rep.steps, rep.errors) == (RunOutcome.FAILURE, 3, 2)
    steps = await steps_of(session, rep)
    assert [s.result for s in steps] == [StepResult.ERROR, StepResult.ERROR, StepResult.OK]
    assert "invalid action" in (steps[0].error or "")
    # The model is told about its mistake on the next turn.
    assert "invalid action" in llm.requests[1].user


async def test_form_submit_is_denied_without_confirmation(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    seen_out: list[str] = []

    def script(req: LLMRequest, i: int) -> dict[str, Any]:
        return [
            {"type": "fill", "ref": ref(req, "textbox", "Message"), "text": "hello"},
            {"type": "click", "ref": ref(req, "button", "Send message")},
            {"type": "finish", "success": False},
        ][i]

    async def checker(page: Page, _: str | None) -> bool:
        seen_out.append(await page.locator("#out").inner_text())
        return False

    rep = await go(
        session,
        cfg,
        f"{local_site}/contact.html",
        FakeLLM(script),
        confirmer=DenyAll(),
        checker=checker,
    )
    assert rep.denied == 1 and seen_out == [""]  # the form was never submitted
    steps = await steps_of(session, rep)
    assert steps[1].result is StepResult.DENIED and "submits a form" in (steps[1].error or "")

    assert rep.evidence_dir is not None
    conf = json.loads((rep.evidence_dir / "step-001-confirmation.json").read_text())
    assert conf["approved"] is False and conf["by"] == "policy_deny"
    assert EvidenceKind.CONFIRMATION in await evidence_kinds(session, rep)


async def test_form_submit_auto_confirmed_on_localhost_is_recorded(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    def script(req: LLMRequest, i: int) -> dict[str, Any]:
        return [
            {"type": "fill", "ref": ref(req, "textbox", "Message"), "text": "hello"},
            {"type": "click", "ref": ref(req, "button", "Send message")},
            {"type": "finish", "success": True},
        ][i]

    async def checker(page: Page, _: str | None) -> bool:
        return await page.locator("#out").inner_text() == "sent:hello"

    rep = await go(
        session,
        cfg,
        f"{local_site}/contact.html",
        FakeLLM(script),
        confirmer=LocalhostAutoConfirm(),
        checker=checker,
    )
    assert rep.verified and rep.denied == 0
    assert rep.evidence_dir is not None
    conf = json.loads((rep.evidence_dir / "step-001-confirmation.json").read_text())
    assert conf == conf | {"approved": True, "by": "auto_localhost", "reason": "submits a form"}


async def test_typed_password_never_reaches_the_ledger(
    session: AsyncSession, cfg: Settings, local_site: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def script(req: LLMRequest, i: int) -> dict[str, Any]:
        return [
            {"type": "fill", "ref": ref(req, "textbox", "Password"), "text": "hunter2-secret"},
            {"type": "finish", "success": False},
        ][i]

    llm = FakeLLM(script)
    rep = await go(session, cfg, f"{local_site}/login.html", llm, confirmer=LocalhostAutoConfirm())
    steps = await steps_of(session, rep)
    assert steps[0].action["text"] == "***"
    assert all("hunter2-secret" not in r.user for r in llm.requests[1:])
    assert rep.evidence_dir is not None
    for f in rep.evidence_dir.iterdir():
        if f.suffix != ".png":
            assert "hunter2-secret" not in f.read_text()
    captured = capsys.readouterr()
    assert "hunter2-secret" not in captured.out + captured.err  # logs too
    # And it never goes to the model: the next observation shows a mask instead.
    assert rep.steps == 2
    snap1 = (rep.evidence_dir / "step-001-snapshot.yml").read_text()
    assert "•••" in snap1


async def test_injected_instruction_cannot_leave_the_allowlist(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    # Simulate a model that fell for the page's "admin mode" injection.
    llm = FakeLLM(
        [{"type": "goto", "url": "https://example.com/steal"}, {"type": "finish", "success": False}]
    )
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    steps = await steps_of(session, rep)
    assert steps[0].result is StepResult.DENIED and "allowlist" in (steps[0].error or "")
    assert not any("example.com/steal" in u for u in rep.blocked_urls)  # never even requested
    assert "admin mode" in llm.requests[0].user.split("<untrusted_page_content", 1)[1]


async def test_step_budget_aborts(session: AsyncSession, cfg: Settings, local_site: str) -> None:
    llm = FakeLLM(lambda req, i: {"type": "wait", "seconds": 0.1})
    rep = await go(
        session, cfg.model_copy(update={"slba_max_steps": 3}), f"{local_site}/form.html", llm
    )
    assert rep.outcome is RunOutcome.ABORTED and rep.steps == 3
    assert rep.end_reason == "step budget (3) reached"
    items = await Ledger(session).run_items(rep.run_id)
    assert {i.trust_score for i in items} == {FAILURE_TRUST}


async def test_captcha_stops_the_run_before_any_llm_call(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = FakeLLM([{"type": "finish", "success": True}])
    rep = await go(session, cfg, f"{local_site}/captcha.html", llm)
    assert rep.outcome is RunOutcome.BLOCKED and rep.steps == 0
    assert "CAPTCHA" in rep.end_reason and llm.requests == []


class _BrokenLLM(FakeLLM):
    async def decide(self, request: LLMRequest) -> LLMResponse:
        raise LLMError("NIM API error: HTTP 429: rate limited")


async def test_llm_failure_aborts_and_is_recorded(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    rep = await go(session, cfg, f"{local_site}/form.html", _BrokenLLM([]))
    assert rep.outcome is RunOutcome.ABORTED
    assert rep.end_reason == "LLM call failed: NIM API error: HTTP 429: rate limited"
    steps = await steps_of(session, rep)
    assert len(steps) == 1 and "HTTP 429" in (steps[0].error or "")


async def test_checker_overrides_agent_claim(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    async def never(page: Page, _: str | None) -> bool:
        return False

    llm = FakeLLM([{"type": "finish", "success": True, "answer": "done!"}])
    rep = await go(session, cfg, f"{local_site}/form.html", llm, checker=never)
    assert (rep.outcome, rep.verified) == (RunOutcome.FAILURE, False)
    assert "claimed success" in rep.end_reason and "checker failed" in rep.end_reason


async def test_off_allowlist_start_url_records_nothing(
    session: AsyncSession, cfg: Settings
) -> None:
    with pytest.raises(ValueError, match=r"(?i)allowlist"):
        await run_agent(
            goal="g",
            start_url="https://example.com/",
            llm=FakeLLM([]),
            session=session,
            settings=cfg,
        )
    assert (await session.execute(select(func.count()).select_from(Run))).scalar_one() == 0


async def test_retracting_a_run_flags_its_confirmation_evidence(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    def script(req: LLMRequest, i: int) -> dict[str, Any]:
        return [
            {"type": "click", "ref": ref(req, "button", "Send message")},
            {"type": "finish", "success": False},
        ][i]

    rep = await go(session, cfg, f"{local_site}/contact.html", FakeLLM(script))
    result = await Ledger(session).retract(rep.run_id, "test retraction")
    await session.commit()
    statuses = {i.status for i in await Ledger(session).run_items(rep.run_id)}
    assert statuses == {ItemStatus.RETRACTED, ItemStatus.FLAGGED}
    assert len(result.flagged) == len(await Ledger(session).run_items(rep.run_id)) - 1


class _FlakyLLM(FakeLLM):
    """Times out ``fail_times`` times, then behaves like the scripted fake."""

    def __init__(self, fail_times: int, script: Any) -> None:
        super().__init__(script)
        self.fail_times = fail_times
        self.calls = 0

    async def decide(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise LLMTimeoutError("NIM API timeout: no answer within 60s (ReadTimeout), 3 attempts")
        return await super().decide(request)


async def test_transient_llm_timeouts_are_retried_and_the_run_recovers(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = _FlakyLLM(2, [{"type": "finish", "success": True, "answer": "ok"}])
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    assert rep.outcome is RunOutcome.SUCCESS and rep.steps == 3 and rep.errors == 2
    steps = await steps_of(session, rep)
    assert [s.result for s in steps] == [StepResult.ERROR, StepResult.ERROR, StepResult.OK]
    assert "timeout" in (steps[0].error or "").lower()
    # Provider trouble is not shown to the model as its own mistake.
    assert all("timeout" not in r.user.lower() for r in llm.requests)


async def test_repeated_llm_timeouts_abort_with_a_clear_reason(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = _FlakyLLM(99, [])
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    assert rep.outcome is RunOutcome.ABORTED and rep.steps == 3
    assert rep.end_reason.startswith("LLM unavailable: 3 failures in a row")


class _SlowLLM(FakeLLM):
    async def decide(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(30)
        return await super().decide(request)


async def test_slow_llm_cannot_overrun_the_run_time_budget(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    started = time.monotonic()
    rep = await go(
        session,
        cfg.model_copy(update={"slba_max_seconds": 3}),
        f"{local_site}/form.html",
        _SlowLLM([]),
    )
    assert time.monotonic() - started < 20  # not 30s+: the call was cut off
    assert rep.outcome is RunOutcome.ABORTED
    assert rep.end_reason == "time budget (3s) reached while waiting for the LLM"
    steps = await steps_of(session, rep)
    assert len(steps) == 1 and steps[0].result is StepResult.ERROR


class _ChattyLLM(FakeLLM):
    """Replies in plain text once (no tool call), then finishes properly."""

    async def decide(self, request: LLMRequest) -> LLMResponse:
        resp = await super().decide(request)
        if len(self.requests) == 1:
            return LLMResponse(
                action_input=None,
                text="The order has been previewed.",
                model="chatty",
                tokens_in=10,
                tokens_out=5,
                cost_usd=0.0,
                latency_ms=0,
            )
        return resp


async def test_plain_text_reply_is_recorded_and_shown_back(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = _ChattyLLM([None, {"type": "finish", "success": True}])
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    steps = await steps_of(session, rep)
    assert steps[0].result is StepResult.ERROR
    assert "The order has been previewed." in (steps[0].error or "")
    assert "you replied with text" in llm.requests[1].user
    assert rep.outcome is RunOutcome.SUCCESS


async def test_repeated_no_op_actions_get_feedback_then_stop(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    # A model that keeps clicking Preview: the first click changes the page, later ones don't.
    llm = FakeLLM(lambda req, i: {"type": "click", "ref": ref(req, "button", "Preview")})
    rep = await go(
        session, cfg.model_copy(update={"slba_max_repeats": 4}), f"{local_site}/form.html", llm
    )
    assert rep.outcome is RunOutcome.ABORTED and rep.steps == 4
    assert rep.end_reason.startswith("stuck: repeated 'click ref=")
    assert "4 times with no visible change" in rep.end_reason
    later = llm.requests[-1].user
    assert "no visible change on the page" in later
    assert "same action 3 times in a row" in later
    # The first click did change the page, so it carries no "no change" note.
    first_line = next(line for line in later.splitlines() if line.startswith("0. click"))
    assert "no visible change" not in first_line


async def test_progress_feedback_does_not_trip_on_real_progress(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    def script(req: LLMRequest, i: int) -> dict[str, Any]:
        return [
            {"type": "fill", "ref": ref(req, "textbox", "Name"), "text": "Rahul"},
            {"type": "select", "ref": ref(req, "combobox", "Size"), "value": "Large"},
            {"type": "click", "ref": ref(req, "button", "Preview")},
            {"type": "finish", "success": True},
        ][i]

    llm = FakeLLM(script)
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    assert rep.outcome is RunOutcome.SUCCESS and rep.steps == 4
    assert all("no visible change" not in r.user for r in llm.requests)


async def test_run_stops_after_too_many_invalid_actions_in_a_row(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = FakeLLM(lambda req, i: {"type": "teleport"})
    rep = await go(
        session,
        cfg.model_copy(update={"slba_max_invalid_actions": 3}),
        f"{local_site}/form.html",
        llm,
    )
    assert rep.outcome is RunOutcome.ABORTED and rep.steps == 3 and rep.errors == 3
    assert rep.end_reason.startswith("model returned 3 invalid actions in a row")


async def test_same_answer_twice_ends_the_run_and_the_checker_decides(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    llm = FakeLLM(lambda req, i: {"type": "answer", "text": "Rahul/Large"})

    async def checker(page: Page, answer: str | None) -> bool:
        return answer == "Rahul/Large"

    rep = await go(session, cfg, f"{local_site}/form.html", llm, checker=checker)
    assert rep.steps == 2 and rep.answer == "Rahul/Large" and rep.verified
    assert "claimed success by repeating its answer" in rep.end_reason
    # After the first answer the model was told to finish.
    assert "answer recorded; call finish to end the run" in llm.requests[1].user


async def test_different_answers_do_not_end_the_run(
    session: AsyncSession, cfg: Settings, local_site: str
) -> None:
    answers = ["3", "4", "5"]
    llm = FakeLLM(
        lambda req, i: (
            {"type": "answer", "text": answers[i]} if i < 3 else {"type": "finish", "success": True}
        )
    )
    rep = await go(session, cfg, f"{local_site}/form.html", llm)
    assert rep.steps == 4 and rep.answer == "5"

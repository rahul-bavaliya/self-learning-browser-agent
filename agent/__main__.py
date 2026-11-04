"""Command line.

python -m agent run "<goal>" --start-url URL [--non-interactive]
python -m agent show RUN_ID
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from agent.config import Settings, get_settings
from agent.doctor import explain_db_error
from agent.llm import LLMConfigError, make_llm
from agent.loop import RunReport, run_agent
from provenance.db import make_engine, make_sessionmaker
from provenance.ledger import Ledger
from provenance.models import Run, Step
from safety.gate import CliConfirmer, Confirmer, DenyAll
from safety.redact import configure_logging


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agent", description="Self-Learning Browser Agent")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the agent on one goal")
    r.add_argument("goal", help="what you want done, in plain language")
    r.add_argument("--start-url", required=True, help="first page (must be on the allowlist)")
    r.add_argument("--channel", choices=["chrome", "msedge"], help="browser (default: settings)")
    r.add_argument("--max-steps", type=int, help="override SLBA_MAX_STEPS")
    r.add_argument("--task-id", help="eval task id to record on the run")
    r.add_argument("--headed", action="store_true", help="show the browser (needs a display)")
    r.add_argument(
        "--non-interactive",
        action="store_true",
        help="deny every action that needs confirmation instead of asking",
    )

    s = sub.add_parser("show", help="print a recorded run from the ledger")
    s.add_argument("run_id", type=uuid.UUID)
    return p


def _print_report(rep: RunReport) -> None:
    mark = "✅" if rep.verified else ("☑️" if rep.outcome.value == "success" else "❌")
    print(f"\n{mark} {rep.outcome.value}{' (verified)' if rep.verified else ''}")
    print(f"   run      {rep.run_id}")
    print(f"   why      {rep.end_reason}")
    print(f"   answer   {rep.answer!r}")
    print(f"   steps    {rep.steps}  (denied {rep.denied}, errors {rep.errors})")
    print(f"   tokens   {rep.tokens_in} in / {rep.tokens_out} out   cost ${rep.cost_usd:.4f}")
    if rep.blocked_urls:
        print(f"   blocked  {len(rep.blocked_urls)} off-allowlist request(s)")
    print(f"   evidence {rep.evidence_dir}")
    print(f"   details  python -m agent show {rep.run_id}")


async def _run(args: argparse.Namespace, cfg: Settings) -> int:
    updates: dict[str, object] = {}
    if args.max_steps:
        updates["slba_max_steps"] = args.max_steps
    if args.headed:
        updates["slba_browser_headless"] = False
    cfg = cfg.model_copy(update=updates)

    try:
        llm = make_llm(cfg)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if cfg.slba_llm_price_in_per_mtok == 0 and cfg.slba_llm_provider != "fake":
        print(
            "note: SLBA_LLM_PRICE_*_PER_MTOK not set; cost not tracked, cost budget inactive",
            file=sys.stderr,
        )

    confirmer: Confirmer = DenyAll() if args.non_interactive else CliConfirmer()
    engine = make_engine(cfg)
    try:
        async with make_sessionmaker(engine)() as session:
            rep = await run_agent(
                goal=args.goal,
                start_url=args.start_url,
                llm=llm,
                session=session,
                settings=cfg,
                channel=args.channel,
                confirmer=confirmer,
                task_id=args.task_id,
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    _print_report(rep)
    return 0 if rep.outcome.value == "success" else 1


async def _show(run_id: uuid.UUID, cfg: Settings) -> int:
    engine = make_engine(cfg)
    try:
        async with make_sessionmaker(engine)() as session:
            run = await session.get(Run, run_id)
            if run is None:
                print(f"no run {run_id}", file=sys.stderr)
                return 2
            print(f"run {run_id}\n  goal     {run.goal}\n  start    {run.start_url}")
            print(f"  model    {run.model} on {run.channel}   task {run.task_id or '-'}")
            print(
                f"  outcome  {run.outcome.value}  verified={run.verified}  "
                f"trust={run.item.trust_score}  status={run.item.status.value}"
            )
            print(f"  why      {run.end_reason}\n  answer   {run.agent_answer!r}")
            steps = (
                await session.execute(select(Step).where(Step.run_id == run_id).order_by(Step.idx))
            ).scalars()
            for st in steps:
                act = {k: v for k, v in st.action.items() if k != "reasoning"}
                line = f"  {st.idx:>3} {st.result.value:<7} {act}"
                if st.error:
                    line += f"\n        ↳ {st.error[:400]}"
                print(line)
            n = len(await Ledger(session).run_items(run_id))
            print(f"  ledger items for this run: {n}")
    finally:
        await engine.dispose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = _parser().parse_args(argv)
    cfg = get_settings()
    configure_logging(cfg)
    try:
        if args.command == "run":
            return asyncio.run(_run(args, cfg))
        return asyncio.run(_show(args.run_id, cfg))
    except OperationalError as exc:
        hint = explain_db_error(exc)
        if hint is None:
            raise
        print(hint, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

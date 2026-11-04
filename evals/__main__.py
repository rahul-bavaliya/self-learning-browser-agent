"""Command line.

python -m evals run [--suite local] [--repeats 3] [--tasks nav-01,ext-02] [--out FILE]
python -m evals serve [--port 8002]        # browse the test site yourself
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import OperationalError

from agent.config import Settings, get_settings
from agent.doctor import explain_db_error
from agent.llm import LLMConfigError, make_llm
from agent.llm.probe import probe_model
from evals.runner import RunResult, load_report, rescore, run_suite
from evals.tasks.local import TASKS, TASKS_BY_ID
from provenance.db import make_engine, make_sessionmaker
from safety.redact import configure_logging

REPORTS_ROOT = Path(".slba/reports")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals", description="SLBA evaluations")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run an eval suite and write a report")
    r.add_argument("--suite", default="local", choices=["local"])
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--tasks", help="comma-separated task ids (default: all)")
    r.add_argument("--channel", choices=["chrome", "msedge"])
    r.add_argument("--max-steps", type=int, default=15, help="per run (default 15)")
    r.add_argument("--pause", type=float, default=3.0, help="seconds between runs")
    r.add_argument("--out", type=Path, help="also copy the Markdown report here")
    r.add_argument("--skip-preflight", action="store_true", help="don't test the model first")

    rs = sub.add_parser("rescore", help="recompute a saved report's metrics from the ledger")
    rs.add_argument("report", type=Path, help="the .json written by 'run'")
    rs.add_argument("--out", type=Path, help="also copy the Markdown report here")

    s = sub.add_parser("serve", help="serve the test shop site for manual browsing")
    s.add_argument("--port", type=int, default=8002)
    return p


def _progress(result: RunResult, done: int, total: int) -> None:
    mark = "✅" if result.verified else ("⛔" if result.outcome == "aborted" else "❌")
    line = (
        f"[{done:>2}/{total}] {result.task_id:<9} r{result.repeat} {mark} "
        f"{result.steps:>2} steps {result.seconds:5.1f}s"
    )
    if not result.verified:
        line += f"  {result.end_reason[:80]}"
    print(line, flush=True)


async def _run(args: argparse.Namespace, cfg: Settings) -> int:
    if args.tasks:
        unknown = [t for t in args.tasks.split(",") if t not in TASKS_BY_ID]
        if unknown:
            print(f"error: unknown task id(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        tasks = [TASKS_BY_ID[t] for t in args.tasks.split(",")]
    else:
        tasks = list(TASKS)
    cfg = cfg.model_copy(update={"slba_max_steps": args.max_steps})
    try:
        llm = make_llm(cfg)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.skip_preflight and cfg.slba_llm_provider != "fake":
        # One real call before spending quota on the suite: catches a wrong key,
        # retired model or unreachable provider in seconds.
        check = await probe_model(llm)
        if not check.valid:
            print(f"error: preflight call to {llm.model} failed: {check.detail}", file=sys.stderr)
            return 2
        print(f"preflight ok: {llm.model} answered in {check.seconds:.1f}s")

    total = len(tasks) * args.repeats
    print(
        f"Running {len(tasks)} tasks x {args.repeats} = {total} runs on {llm.model} "
        f"(≤{args.max_steps} steps each, {args.pause:.0f}s pause)\n"
    )
    engine = make_engine(cfg)
    try:
        report = await run_suite(
            tasks,
            llm_for=lambda _task: llm,
            sessions=make_sessionmaker(engine),
            settings=cfg,
            suite=args.suite,
            repeats=args.repeats,
            pause_s=args.pause,
            channel=args.channel,
            on_result=_progress,
        )
    finally:
        await engine.dispose()

    json_path, md_path = report.write(REPORTS_ROOT)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.to_markdown(), "utf-8")
    s = report.summary()
    print(
        f"\nVerified success: {s['success_rate_mean']:.0%} "
        f"(range {s['success_rate_min']:.0%}-{s['success_rate_max']:.0%}), "
        f"{s['verified']}/{s['runs']} runs"
    )
    for gate, passed in s["gates"].items():
        print(f"  {'✅' if passed else '❌'} {gate}")
    if report.incomplete_reason:
        print(f"\n⚠ incomplete: {report.incomplete_reason}")
    print(f"\nReport: {md_path}\nData:   {json_path}")
    return 0 if all(s["gates"].values()) else 1


async def _rescore(args: argparse.Namespace, cfg: Settings) -> int:
    report = load_report(args.report)
    engine = make_engine(cfg)
    try:
        report = await rescore(report, make_sessionmaker(engine), cfg)
    finally:
        await engine.dispose()
    args.report.write_text(report.to_json(), "utf-8")
    md = args.report.with_suffix(".md")
    md.write_text(report.to_markdown(), "utf-8")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.to_markdown(), "utf-8")
    s = report.summary()
    print(f"Rescored {s['runs']} runs from the ledger (no new runs).")
    print(f"Verified success: {s['success_rate_mean']:.0%}, {s['verified']}/{s['runs']} runs")
    for gate, passed in s["gates"].items():
        print(f"  {'✅' if passed else '❌'} {gate}")
    print(f"\nReport: {md}" + (f"\nCopied: {args.out}" if args.out else ""))
    return 0 if all(s["gates"].values()) else 1


def _serve(port: int) -> int:
    import uvicorn

    from evals.sites.shop.app import create_app

    print(f"SLBA test shop on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="info")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args.port)
    cfg = get_settings()
    configure_logging(cfg)
    try:
        if args.command == "rescore":
            return asyncio.run(_rescore(args, cfg))
        return asyncio.run(_run(args, cfg))
    except OperationalError as exc:
        hint = explain_db_error(exc)
        if hint is None:
            raise
        print(hint, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

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

from agent.config import Settings, get_settings
from agent.llm import LLMConfigError, make_llm
from evals.runner import RunResult, run_suite
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
    return asyncio.run(_run(args, cfg))


if __name__ == "__main__":
    sys.exit(main())

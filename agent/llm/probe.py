"""Find models that actually work with the agent: ``python -m agent.llm.probe [MODEL ...]``.

Catalogs list retired models (they return HTTP 410) and many models ignore forced
tool calls, so each candidate gets the agent's real request shape: the ``act``
tool, named ``tool_choice``, and a tiny page. A model passes only if the reply
parses as a valid action. One API call per model.

With no arguments, candidates are picked from the provider's ``/models`` list by
family names that NIM documents as supporting tool calling.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from agent.actions import ActionParseError, parse_action
from agent.config import Settings, get_settings
from agent.llm.base import LLMClient, LLMError
from agent.llm.factory import make_llm
from agent.llm.openai_compat import NIM_BASE_URL
from agent.observe import Observation
from agent.prompts import build_request

# Families with documented tool-calling support on NIM; order = preference.
FAMILIES = ("gpt-oss", "qwen3", "qwen2.5", "nemotron", "llama-4", "mistral", "deepseek", "kimi")
_PAGE = Observation(
    url="http://127.0.0.1:8001/form.html",
    title="SLBA Form",
    snapshot='- textbox "Name" [ref=e3]\n- button "Preview" [ref=e6]',
    truncated=False,
    original_chars=0,
)
GOAL = "Type Rahul into the Name field."


@dataclass(frozen=True)
class ProbeResult:
    """Outcome for one model."""

    model: str
    ok: bool
    detail: str
    seconds: float


async def probe_model(client: LLMClient) -> ProbeResult:
    """Send one real agent request and check the reply is a valid action."""
    started = time.monotonic()
    req = build_request(goal=GOAL, observation=_PAGE, history=[], max_tokens=300)
    try:
        resp = await client.decide(req)
    except LLMError as exc:
        return ProbeResult(client.model, False, str(exc)[:160], time.monotonic() - started)
    elapsed = time.monotonic() - started
    try:
        action = parse_action(resp.action_input)
    except ActionParseError as exc:
        return ProbeResult(client.model, False, f"no valid action: {exc}"[:160], elapsed)
    detail = f"{action.type} {action.model_dump(exclude={'type', 'reasoning'}, exclude_none=True)}"
    return ProbeResult(client.model, True, detail, elapsed)


def pick_candidates(model_ids: Sequence[str], limit: int = 8) -> list[str]:
    """Instruct/chat models from preferred families, best family first."""
    skip = ("embed", "rerank", "guard", "reward", "vision", "-vl", "coder", "safety", "parse")
    usable = [m for m in model_ids if not any(s in m.lower() for s in skip)]
    picked: list[str] = []
    for family in FAMILIES:
        picked += sorted(m for m in usable if family in m.lower() and m not in picked)
    return picked[:limit]


async def list_models(cfg: Settings) -> list[str]:
    """Model ids from the provider's ``/models`` endpoint."""
    base = (cfg.slba_llm_base_url or NIM_BASE_URL).rstrip("/").removesuffix("/chat/completions")
    key = cfg.slba_llm_api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{base}/models", headers=headers)
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", [])]


async def main_async(models: Sequence[str], pause_s: float) -> int:
    """Probe each model in turn and print a table."""
    cfg = get_settings()
    if not models:
        models = pick_candidates(await list_models(cfg))
        print(f"Probing {len(models)} candidates from the catalog (one call each)...\n")
    results = []
    for i, model in enumerate(models):
        if i:
            await asyncio.sleep(pause_s)  # stay under free-tier per-minute limits
        client = make_llm(cfg.model_copy(update={"slba_llm_model": model}))
        res = await probe_model(client)
        results.append(res)
        mark = "PASS" if res.ok else "fail"
        print(f"{mark}  {res.seconds:5.1f}s  {res.model:<48} {res.detail}")
    passing = [r for r in results if r.ok]
    if passing:
        best = min(passing, key=lambda r: r.seconds)
        print(f"\nWorks: {len(passing)}/{len(results)}. Fastest passing: {best.model}")
        print(f"Set it with:  SLBA_LLM_MODEL={best.model}")
        return 0
    print("\nNo model returned a valid action. Try SLBA_LLM_TOOL_CHOICE=required or auto.")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(prog="python -m agent.llm.probe", description=__doc__)
    p.add_argument("models", nargs="*", help="model ids; default: pick from the catalog")
    p.add_argument("--pause", type=float, default=2.0, help="seconds between calls")
    args = p.parse_args(argv)
    return asyncio.run(main_async(args.models, args.pause))


if __name__ == "__main__":
    sys.exit(main())

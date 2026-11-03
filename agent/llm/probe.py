"""Find models that actually work with the agent: ``python -m agent.llm.probe [MODEL ...]``.

Catalogs list retired models (they return HTTP 410) and many models ignore forced
tool calls, so each candidate gets the agent's real request shape: the ``act``
tool, named ``tool_choice``, and a tiny page. A model passes only if it returns
the *correct* action (fill the Name box with "Rahul"); a valid but wrong action
is reported as ``wrong``. One API call per model.

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

from agent.actions import ActionParseError, Fill, parse_action
from agent.config import Settings, get_settings
from agent.llm.base import LLMClient, LLMConfigError, LLMError
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
    valid: bool = False  # returned a well-formed action, right or wrong


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
    correct = isinstance(action, Fill) and action.ref == "e3" and "rahul" in action.text.lower()
    return ProbeResult(client.model, correct, detail, elapsed, valid=True)


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


async def main_async(models: Sequence[str], pause_s: float, timeout_s: float = 20.0) -> int:
    """Probe each model in turn and print a table."""
    # Short timeout, no retries: a probe should answer in seconds, not minutes.
    cfg = get_settings().model_copy(
        update={"slba_llm_timeout_s": timeout_s, "slba_llm_max_retries": 0}
    )
    try:  # fail fast on config (e.g. missing key) instead of once per model
        make_llm(cfg.model_copy(update={"slba_llm_model": models[0] if models else "probe"}))
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not models:
        models = pick_candidates(await list_models(cfg))
        print(f"Probing {len(models)} candidates from the catalog (one call each)...\n")
    results = []
    auth_failures = 0
    for i, model in enumerate(models):
        if i:
            await asyncio.sleep(pause_s)  # stay under free-tier per-minute limits
        client = make_llm(cfg.model_copy(update={"slba_llm_model": model}))
        print(f"  …   asking {model} (up to {timeout_s:.0f}s)", end="\r", flush=True)
        res = await probe_model(client)
        results.append(res)
        mark = "PASS" if res.ok else ("wrong" if res.valid else "fail")
        print(f"{mark}  {res.seconds:5.1f}s  {res.model:<48} {res.detail}".ljust(100), flush=True)
        auth_failures += "HTTP 401" in res.detail or "HTTP 403" in res.detail
        if auth_failures >= 2 and not any(r.ok for r in results):
            print("\nStopping: the API key is being rejected (HTTP 401/403), not the models.")
            return 2
    passing = [r for r in results if r.ok]
    if passing:
        best = min(passing, key=lambda r: r.seconds)
        print(f"\nCorrect: {len(passing)}/{len(results)}. Fastest correct: {best.model}")
        print(f"Set it with:  SLBA_LLM_MODEL={best.model}")
        return 0
    print("\nNo model returned a valid action. Try SLBA_LLM_TOOL_CHOICE=required or auto.")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(prog="python -m agent.llm.probe", description=__doc__)
    p.add_argument("models", nargs="*", help="model ids; default: pick from the catalog")
    p.add_argument("--pause", type=float, default=2.0, help="seconds between calls")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds to wait per model")
    args = p.parse_args(argv)
    return asyncio.run(main_async(args.models, args.pause, args.timeout))


if __name__ == "__main__":
    sys.exit(main())

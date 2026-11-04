"""Typed action space and the executor that runs actions against a Playwright page.

The model returns one action per step. :func:`parse_action` validates it strictly;
anything invalid becomes an :class:`ActionParseError` that the loop records as a
step error and shows back to the model, never a crash.

Element references (``e12``) come from the latest observation's accessibility
snapshot and are resolved with Playwright's ``aria-ref=`` selector.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from provenance.models import StepResult
from safety.allowlist import DomainAllowlist

# Playwright numbers refs per document: "e12" on the first page, then "f1e12", "f2e12", ...
# after each navigation. Accept both forms.
Ref = Annotated[
    str, Field(pattern=r"^(?:f\d{1,4})?e\d{1,6}$", description="Element ref from the snapshot")
]

# Keys the agent may press. No modifier shortcuts (Ctrl+W, Alt+F4, devtools...).
ALLOWED_KEYS = frozenset(
    {
        "Enter",
        "Tab",
        "Escape",
        "Backspace",
        "Delete",
        "Space",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "PageUp",
        "PageDown",
        "Home",
        "End",
    }
)

ACTION_TIMEOUT_MS = 5_000
_SCROLL_JS = "s => window.scrollBy({top: s * innerHeight * 0.8, behavior: 'instant'})"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reasoning: str = Field(default="", max_length=500, description="One short sentence: why")


class Click(_Base):
    """Click an element."""

    type: Literal["click"]
    ref: Ref


class Fill(_Base):
    """Replace the text in an input."""

    type: Literal["fill"]
    ref: Ref
    text: str = Field(max_length=2_000)


class Press(_Base):
    """Press a single key, optionally focused on an element."""

    type: Literal["press"]
    key: str
    ref: Ref | None = None

    @field_validator("key")
    @classmethod
    def _allowed_key(cls, key: str) -> str:
        """Reject keys outside the allowed set."""
        if key not in ALLOWED_KEYS:
            raise ValueError(f"key {key!r} not allowed; use one of {sorted(ALLOWED_KEYS)}")
        return key


class Select(_Base):
    """Choose an option in a select/combobox by its visible label."""

    type: Literal["select"]
    ref: Ref
    value: str = Field(max_length=500)


class Scroll(_Base):
    """Scroll the page by about one screen."""

    type: Literal["scroll"]
    direction: Literal["up", "down"]


class Goto(_Base):
    """Navigate to a URL (must be on the allowlist)."""

    type: Literal["goto"]
    url: str = Field(pattern=r"^https?://", max_length=2_000)


class Wait(_Base):
    """Wait for the page to settle."""

    type: Literal["wait"]
    seconds: float = Field(ge=0.1, le=5.0)


class Answer(_Base):
    """Record information the goal asks for, without ending the run."""

    type: Literal["answer"]
    text: str = Field(max_length=4_000)


class Finish(_Base):
    """End the run, stating whether the goal was achieved."""

    type: Literal["finish"]
    success: bool
    answer: str | None = Field(default=None, max_length=4_000)


Action = Annotated[
    Click | Fill | Press | Select | Scroll | Goto | Wait | Answer | Finish,
    Field(discriminator="type"),
]
ACTION_TYPES = ("click", "fill", "press", "select", "scroll", "goto", "wait", "answer", "finish")
_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
_MODELS: dict[str, type[_Base]] = {
    "click": Click,
    "fill": Fill,
    "press": Press,
    "select": Select,
    "scroll": Scroll,
    "goto": Goto,
    "wait": Wait,
    "answer": Answer,
    "finish": Finish,
}


class ActionParseError(ValueError):
    """The model's output was not a valid action. Message is safe to show the model."""


def parse_action(raw: dict[str, Any] | None) -> Action:
    """Validate a raw tool-call payload into a typed action.

    Raises:
        ActionParseError: With a short, model-readable explanation.

    """
    if not raw:
        raise ActionParseError("no action returned; call the act tool with exactly one action")
    payload = _clean(raw)
    try:
        return _ADAPTER.validate_python(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{_field(e['loc'])}: {e['msg']}" for e in exc.errors(include_url=False)
        )
        example = EXAMPLES.get(str(payload.get("type", "")))
        hint = f". A valid {payload['type']} looks like: {example}" if example else ""
        raise ActionParseError(f"invalid action: {problems}{hint}") from None


# One correct call per type, shown to the model when its call is invalid.
EXAMPLES: dict[str, str] = {
    "click": '{"type": "click", "ref": "e12"}',
    "fill": '{"type": "fill", "ref": "e3", "text": "Rahul"}',
    "press": '{"type": "press", "key": "Enter"}',
    "select": '{"type": "select", "ref": "e7", "value": "Large"}',
    "scroll": '{"type": "scroll", "direction": "down"}',
    "goto": '{"type": "goto", "url": "http://127.0.0.1:8001/"}',
    "wait": '{"type": "wait", "seconds": 1}',
    "answer": '{"type": "answer", "text": "39.99"}',
    "finish": '{"type": "finish", "success": true, "answer": "39.99"}',
}


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    """Make a model's tool call fit the chosen action before strict validation.

    The flat tool schema shows every field, so models often send fields that don't
    apply to the action (``"ref": ""`` on a finish) or numbers where text is
    expected (``"answer": 4``). Fields that don't belong to the chosen type are
    dropped; fields that do belong are still validated strictly.
    """
    payload = {k: v for k, v in raw.items() if v is not None}
    kind = payload.get("type")
    if isinstance(kind, str):
        kind = kind.strip().lower()
        payload["type"] = kind
    model = _MODELS.get(kind) if isinstance(kind, str) else None
    if model is None:
        return payload  # unknown type: let validation explain
    # Models mix up the two names for the same thing.
    if kind == "answer" and "text" not in payload and "answer" in payload:
        payload["text"] = payload.pop("answer")
    if kind == "finish" and "answer" not in payload and "text" in payload:
        payload["answer"] = payload.pop("text")
    allowed = set(model.model_fields)
    payload = {k: v for k, v in payload.items() if k in allowed}
    for key in ("text", "answer", "value"):
        value = payload.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            payload[key] = str(value)
    return payload


def _field(loc: tuple[int | str, ...]) -> str:
    """Field name from a pydantic error location: ('click', 'ref') -> 'ref'."""
    if len(loc) > 1:
        return ".".join(str(p) for p in loc[1:])
    return str(loc[0]) if loc else "type"


def tool_input_schema() -> dict[str, Any]:
    """Flat JSON schema for the ``act`` tool.

    A flat object (one ``type`` enum plus optional fields) is accepted by every
    provider's tool-calling API; :func:`parse_action` does the strict validation.
    """
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(ACTION_TYPES)},
            "reasoning": {"type": "string", "description": "One short sentence: why"},
            "ref": {
                "type": "string",
                "description": "Element ref exactly as shown, e.g. e12 or f2e12",
            },
            "text": {"type": "string", "description": "fill: text to enter; answer: the answer"},
            "key": {"type": "string", "enum": sorted(ALLOWED_KEYS)},
            "value": {"type": "string", "description": "select: option label"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "url": {"type": "string", "description": "goto: absolute http(s) URL"},
            "seconds": {"type": "number", "minimum": 0.1, "maximum": 5},
            "success": {"type": "boolean", "description": "finish: was the goal achieved"},
            "answer": {"type": "string", "description": "finish: final answer, if any"},
        },
        "required": ["type", "reasoning"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ActionOutcome:
    """Result of executing one action, ready to record as a step."""

    result: StepResult
    error: str | None = None
    url_after: str | None = None
    answer: str | None = None
    finished: bool = False
    success: bool | None = None
    latency_ms: int = 0


async def execute(page: Page, action: Action, *, allowlist: DomainAllowlist) -> ActionOutcome:
    """Run one action. Never raises for page-level failures; returns an outcome.

    The human-confirmation gate (safety/gate.py, week 3) runs *before* this.
    """
    started = time.monotonic()

    def done(result: StepResult, **kw: Any) -> ActionOutcome:
        ms = int((time.monotonic() - started) * 1000)
        return ActionOutcome(result=result, url_after=page.url, latency_ms=ms, **kw)

    try:
        match action:
            case Click(ref=ref):
                await page.locator(f"aria-ref={ref}").click(timeout=ACTION_TIMEOUT_MS)
            case Fill(ref=ref, text=text):
                await page.locator(f"aria-ref={ref}").fill(text, timeout=ACTION_TIMEOUT_MS)
            case Press(key=key, ref=ref):
                if ref:
                    await page.locator(f"aria-ref={ref}").press(key, timeout=ACTION_TIMEOUT_MS)
                else:
                    await page.keyboard.press(key)
            case Select(ref=ref, value=value):
                await page.locator(f"aria-ref={ref}").select_option(
                    label=value, timeout=ACTION_TIMEOUT_MS
                )
            case Scroll(direction=direction):
                # Instant and deterministic (mouse.wheel scrolls asynchronously).
                # Window only; scrolling inner containers is a later improvement.
                sign = 1 if direction == "down" else -1
                await page.evaluate(_SCROLL_JS, sign)
            case Goto(url=url):
                if not allowlist.is_allowed(url):
                    return done(
                        StepResult.DENIED,
                        error=f"{url} is not on the domain allowlist; stay on allowed sites",
                    )
                await page.goto(url, timeout=15_000)
            case Wait(seconds=seconds):
                await page.wait_for_timeout(seconds * 1000)
            case Answer(text=text):
                return done(StepResult.OK, answer=text)
            case Finish(success=success, answer=answer):
                return done(StepResult.OK, answer=answer, finished=True, success=success)
    except PlaywrightError as exc:
        return done(StepResult.ERROR, error=_short_error(exc))
    return done(StepResult.OK)


def _short_error(exc: PlaywrightError) -> str:
    """First line of a Playwright error, with a hint for stale refs."""
    first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
    if "aria-ref" in str(exc) or "Timeout" in first:
        first += " (the ref may be stale: refs change after every page update)"
    return first

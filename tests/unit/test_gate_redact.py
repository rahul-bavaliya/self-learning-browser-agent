"""Gate risk rules, confirmers and log redaction (no browser needed)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from agent.actions import parse_action
from agent.config import Settings
from safety.gate import (
    ConfirmationRequest,
    DenyAll,
    ElementInfo,
    LocalhostAutoConfirm,
    assess,
)
from safety.redact import MASK, SecretRedactor, secret_values


def el(
    tag: str = "button",
    type_: str = "",
    name: str = "",
    *,
    in_form: bool = False,
    submits: bool = False,
) -> ElementInfo:
    return ElementInfo(tag=tag, type=type_, name=name, in_form=in_form, submits_form=submits)


CLICK = parse_action({"type": "click", "ref": "e1"})


@pytest.mark.parametrize(
    ("element", "needs"),
    [
        (el(name="Preview"), False),
        (el(name="Next page"), False),
        (el(type_="submit", name="Go", in_form=True, submits=True), True),  # label doesn't matter
        (el(name="Buy now"), True),
        (el(tag="a", name="Delete account"), True),
        (el(name="Place order"), True),
        (el(name="Sign up"), True),
        (el(name="Publish"), True),
        (el(name="Postcode"), False),  # word boundary
        (None, False),
    ],
)
def test_click_rules(element: ElementInfo | None, needs: bool) -> None:
    assert assess(CLICK, element).needs_confirmation is needs


def test_password_fill_and_enter_in_form() -> None:
    fill = parse_action({"type": "fill", "ref": "e2", "text": "hunter2"})
    assert assess(fill, el("input", "password")).needs_confirmation
    assert not assess(fill, el("input", "text")).needs_confirmation

    enter = parse_action({"type": "press", "key": "Enter", "ref": "e2"})
    assert assess(enter, el("input", "text", in_form=True)).needs_confirmation
    assert not assess(enter, el("input", "text", in_form=False)).needs_confirmation
    tab = parse_action({"type": "press", "key": "Tab", "ref": "e2"})
    assert not assess(tab, el("input", "text", in_form=True)).needs_confirmation


def test_non_targeted_actions_never_gated() -> None:
    for raw in (
        {"type": "scroll", "direction": "down"},
        {"type": "goto", "url": "http://localhost/"},
        {"type": "finish", "success": True},
    ):
        assert not assess(parse_action(raw), el(name="Buy now")).needs_confirmation


def _req(url: str) -> ConfirmationRequest:
    return ConfirmationRequest("click ref=e1", "submits a form", url, "Send")


async def test_confirmers() -> None:
    assert not (await DenyAll().confirm(_req("http://localhost/"))).approved
    auto = LocalhostAutoConfirm()
    ok = await auto.confirm(_req("http://127.0.0.1:8001/contact"))
    assert ok.approved and ok.by == "auto_localhost"
    assert not (await auto.confirm(_req("https://shop.example.com/checkout"))).approved
    assert not (await auto.confirm(_req("http://localhost.evil.net/"))).approved


def test_redactor_masks_keys_and_values() -> None:
    secret = "sk-ant-" + "real-key-123"  # pragma: allowlist secret
    placeholder = "x" * 8  # value under a sensitive key name; must be masked
    red = SecretRedactor([secret])
    event = red(
        None,
        "info",
        {
            "event": f"calling api with {secret}",
            "api_key": placeholder,
            "nested": {"password": placeholder, "ok": f"x {secret} y"},
            "items": [secret],
        },
    )
    assert secret not in repr(event)
    assert event["api_key"] == MASK and event["nested"]["password"] == MASK
    assert event["nested"]["ok"] == f"x {MASK} y"


def test_secret_values_collects_every_secretstr() -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        slba_llm_api_key=SecretStr("k" * 10),
        postgres_password=SecretStr("p" * 10),
    )
    assert set(secret_values(s)) == {"k" * 10, "p" * 10}

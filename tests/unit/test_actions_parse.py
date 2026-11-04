"""Action schema: strict parsing, model-readable errors, portable tool schema."""

from __future__ import annotations

from typing import Any

import pytest

from agent.actions import (
    ACTION_TYPES,
    ActionParseError,
    Click,
    Finish,
    Goto,
    Press,
    parse_action,
    tool_input_schema,
)


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "click", "ref": "e12"},
        {"type": "click", "ref": "f3e12"},  # ref after a navigation
        {"type": "fill", "ref": "e3", "text": "kettle"},
        {"type": "press", "key": "Enter"},
        {"type": "press", "key": "Tab", "ref": "e1"},
        {"type": "select", "ref": "e7", "value": "Price"},
        {"type": "scroll", "direction": "down"},
        {"type": "goto", "url": "http://127.0.0.1:8001/"},
        {"type": "wait", "seconds": 1},
        {"type": "answer", "text": "42"},
        {"type": "finish", "success": True, "answer": "42"},
    ],
)
def test_every_action_type_parses(raw: dict[str, Any]) -> None:
    action = parse_action({**raw, "reasoning": "because"})
    assert action.type == raw["type"]


def test_all_types_covered_by_tool_schema() -> None:
    schema = tool_input_schema()
    assert schema["properties"]["type"]["enum"] == list(ACTION_TYPES)
    assert schema["type"] == "object" and "type" in schema["required"]


def test_nulls_from_flat_schema_are_ignored() -> None:
    # Providers often send every optional field as null.
    action = parse_action({"type": "click", "ref": "e1", "text": None, "url": None})
    assert isinstance(action, Click)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (None, "no action"),
        ({}, "no action"),
        ({"type": "teleport"}, "invalid action"),
        ({"type": "click"}, "ref"),
        ({"type": "click", "ref": "#submit"}, "ref"),
        ({"type": "click", "ref": "f3"}, "ref"),
        ({"type": "click", "ref": "fe12"}, "ref"),
        ({"type": "goto", "url": "file:///etc/passwd"}, "url"),
        ({"type": "goto", "url": "javascript:alert(1)"}, "url"),
        ({"type": "press", "key": "Control+w"}, "not allowed"),
        ({"type": "wait", "seconds": 60}, "seconds"),
        ({"type": "finish"}, "success"),
    ],
)
def test_invalid_actions_raise_readable_errors(raw: dict[str, Any] | None, fragment: str) -> None:
    with pytest.raises(ActionParseError, match=fragment):
        parse_action(raw)


def test_actions_are_immutable() -> None:
    action = parse_action({"type": "goto", "url": "http://localhost/"})
    assert isinstance(action, Goto)
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen error type varies
        action.url = "http://evil.net/"  # type: ignore[misc]


def test_finish_and_press_types() -> None:
    assert isinstance(parse_action({"type": "finish", "success": False}), Finish)
    assert isinstance(parse_action({"type": "press", "key": "Escape"}), Press)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Flat-schema filler on fields the action doesn't use is ignored.
        (
            {
                "type": "finish",
                "success": True,
                "answer": "ok",
                "ref": "",
                "key": "",
                "url": "",
                "direction": "",
                "seconds": 0,
                "text": "",
                "value": "",
            },
            Finish(type="finish", success=True, answer="ok"),
        ),
        ({"type": "click", "ref": "e4", "text": "", "key": ""}, Click(type="click", ref="e4")),
        # Numbers where text is expected become text.
        (
            {"type": "finish", "success": True, "answer": 4},
            Finish(type="finish", success=True, answer="4"),
        ),
        ({"type": "Finish ", "success": True}, Finish(type="finish", success=True)),
    ],
)
def test_tolerant_to_irrelevant_fields_and_numbers(raw: dict[str, Any], expected: Any) -> None:
    assert parse_action(raw) == expected


def test_relevant_fields_stay_strict_and_errors_show_an_example() -> None:
    with pytest.raises(ActionParseError, match=r'ref.*A valid click looks like: \{"type": "click"'):
        parse_action({"type": "click", "ref": ""})
    with pytest.raises(ActionParseError, match="A valid finish looks like"):
        parse_action({"type": "finish"})

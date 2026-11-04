"""Setup-failure hints."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from agent.doctor import FIX_DB_AUTH_COMMAND, explain_db_error


def _op(msg: str) -> OperationalError:
    return OperationalError("connect", {}, Exception(msg))


@pytest.mark.parametrize(
    ("msg", "fragment"),
    [
        ('FATAL:  password authentication failed for user "slba"', FIX_DB_AUTH_COMMAND),
        ("connection failed: Connection refused", "docker compose up -d"),
        ('FATAL:  database "slba" does not exist', "alembic upgrade head"),
    ],
)
def test_known_db_errors_get_a_fix(msg: str, fragment: str) -> None:
    hint = explain_db_error(_op(msg))
    assert hint is not None and fragment in hint


def test_unknown_errors_are_left_alone() -> None:
    assert explain_db_error(_op("something else")) is None
    assert explain_db_error(ValueError("x")) is None


def test_cli_prints_hint_instead_of_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import agent.__main__ as cli

    async def boom(*_: object) -> int:
        raise _op('FATAL:  password authentication failed for user "slba"')

    monkeypatch.setattr(cli, "_show", boom)
    assert cli.main(["show", "00000000-0000-0000-0000-000000000000"]) == 2
    err = capsys.readouterr().err
    assert "rejected the password" in err and "Traceback" not in err

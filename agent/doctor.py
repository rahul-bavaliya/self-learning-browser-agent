"""Turn common setup failures into one-line explanations with the exact fix.

Used by the CLIs so a wrong database password or a stopped container prints a
short hint instead of a 150-line traceback. Messages never include secrets.
"""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

FIX_DB_AUTH_COMMAND = (
    'PW=$(python -c "from agent.config import get_settings as g; '
    'print(g().postgres_password.get_secret_value())") && docker exec slba-postgres '
    "psql -U slba -d slba -c \"ALTER USER slba WITH PASSWORD '$PW';\" && unset PW"
)


def explain_db_error(exc: BaseException) -> str | None:
    """Return a short, actionable message for known DB failures, else None."""
    if not isinstance(exc, OperationalError):
        return None
    text = str(exc)
    if "password authentication failed" in text:
        return (
            "error: the database rejected the password.\n"
            "  Postgres keeps the password it was first created with; yours has changed\n"
            "  (usually a Codespaces secret or .env edit). Make the database match:\n"
            f"    {FIX_DB_AUTH_COMMAND}"
        )
    if "Connection refused" in text or "could not connect" in text:
        return (
            "error: can't reach Postgres on the configured host/port.\n"
            "  Start it with:  docker compose up -d   (then: docker compose ps)"
        )
    if "does not exist" in text and "database" in text:
        return "error: the database doesn't exist yet. Run:  alembic upgrade head"
    return None

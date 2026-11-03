"""Turn the current page into a compact observation for the model.

The observation is Playwright's accessibility snapshot in ``ai`` mode, where each
interactive element carries a ref (``[ref=e5]``) that actions can target. It is
capped in size, and everything page-derived is wrapped as untrusted when rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from safety.untrusted import wrap_untrusted

TRUNCATION_NOTE = "… [{n} more lines not shown: scroll, or act on what is visible]"
SNAPSHOT_TIMEOUT_MS = 10_000
PASSWORD_MASK = "•••"  # noqa: S105 - a mask, not a password

# Playwright's ai-mode snapshot shows input values, including password fields.
_MASKABLE_FIELD_VALUES_JS = (
    "() => [...document.querySelectorAll('input[type=password]')].map(e => e.value).filter(Boolean)"
)


@dataclass(frozen=True)
class Observation:
    """What the agent sees at one step."""

    url: str
    title: str
    snapshot: str
    truncated: bool
    original_chars: int

    def render(self) -> str:
        """Prompt-ready text. URL and title are page-controlled, so they go inside too."""
        body = f"URL: {self.url}\nTitle: {self.title}\n\n{self.snapshot}"
        return wrap_untrusted(body, source_url=self.url)

    def save(self, path: Path) -> Path:
        """Write the full rendered snapshot to disk as evidence; return the path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"URL: {self.url}\nTitle: {self.title}\n\n{self.snapshot}\n", "utf-8")
        return path


def cap_lines(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep whole lines up to ``max_chars``; append a note saying how many were cut."""
    if len(text) <= max_chars:
        return text, False
    kept: list[str] = []
    used = 0
    lines = text.splitlines()
    for line in lines:
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    kept.append(TRUNCATION_NOTE.format(n=len(lines) - len(kept)))
    return "\n".join(kept), True


async def password_values(page: Page) -> list[str]:
    """Return the current value of every password field, in all frames."""
    values: list[str] = []
    for frame in page.frames:
        try:
            values.extend(await frame.evaluate(_MASKABLE_FIELD_VALUES_JS))
        except PlaywrightError:
            continue  # detached or cross-origin frame we can't read: nothing to mask
    return values


def mask_values(text: str, values: list[str]) -> str:
    """Replace each secret value with a mask, longest first."""
    for v in sorted(set(values), key=len, reverse=True):
        text = text.replace(v, PASSWORD_MASK)
    return text


async def observe(page: Page, *, max_chars: int = 12_000) -> Observation:
    """Snapshot the page's accessibility tree with element refs.

    Password field values are masked before the snapshot is stored or sent anywhere.
    """
    snapshot_text = await page.aria_snapshot(mode="ai", timeout=SNAPSHOT_TIMEOUT_MS)
    raw = mask_values(snapshot_text, await password_values(page))
    snapshot, truncated = cap_lines(raw, max_chars)
    return Observation(
        url=page.url,
        title=await page.title(),
        snapshot=snapshot,
        truncated=truncated,
        original_chars=len(raw),
    )

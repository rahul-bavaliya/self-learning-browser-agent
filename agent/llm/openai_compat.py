"""OpenAI-compatible chat-completions client: NVIDIA NIM now, vLLM / Ollama later.

One ``/chat/completions`` call per step with a single ``act`` function tool and a
named ``tool_choice`` so the model must return exactly one action. Uses plain
httpx (no SDK) so it is easy to test and works with any compatible server.

Some models put the call in ``content`` instead of ``tool_calls`` when the
server's tool parser misses it; we fall back to the first JSON object there.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from agent.llm.base import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMTransientError,
    cost_usd,
)

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
ToolChoice = Literal["named", "required", "auto"]
_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER_S = 30.0  # never sleep longer than this on a Retry-After header


class OpenAICompatClient:
    """:class:`~agent.llm.base.LLMClient` for any OpenAI-compatible endpoint.

    The API key is kept as a ``SecretStr`` and only unwrapped into the
    Authorization header, so it never appears in reprs, errors or logs.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = NIM_BASE_URL,
        api_key: SecretStr | None = None,
        require_key: bool = True,
        provider_label: str = "NIM",
        tool_choice: ToolChoice = "named",
        price_in_per_mtok: float = 0.0,
        price_out_per_mtok: float = 0.0,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Validate config and prepare the HTTP client.

        Raises:
            LLMConfigError: If the model, base URL or (when required) key is missing.

        """
        key = api_key.get_secret_value() if api_key else ""
        if require_key and not key:
            raise LLMConfigError("SLBA_LLM_API_KEY is not set (use a Codespaces secret)")
        if not model:
            raise LLMConfigError("SLBA_LLM_MODEL is not set")
        if not base_url:
            raise LLMConfigError("SLBA_LLM_BASE_URL is not set")
        self._model = model
        self._url = chat_completions_url(base_url)
        self._label = provider_label
        self._tool_choice = tool_choice
        self._price_in = price_in_per_mtok
        self._price_out = price_out_per_mtok
        self._max_retries = max_retries
        # Short connect timeout: an unreachable host should fail fast, a slow model not.
        self._timeout = httpx.Timeout(timeout_s, connect=min(10.0, timeout_s))
        self._timeout_s = timeout_s
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._http = http_client or httpx.AsyncClient(timeout=self._timeout)
        self._headers = headers

    def __repr__(self) -> str:
        """Never include the key."""
        return f"OpenAICompatClient(provider={self._label!r}, model={self._model!r})"

    @property
    def model(self) -> str:
        """Configured model name."""
        return self._model

    def _body(self, request: LLMRequest) -> dict[str, Any]:
        tool = {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "parameters": request.tool_schema,
            },
        }
        choice: Any = (
            {"type": "function", "function": {"name": TOOL_NAME}}
            if self._tool_choice == "named"
            else self._tool_choice
        )
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "tools": [tool],
            "tool_choice": choice,
            "max_tokens": request.max_tokens,
            "temperature": 0.0,
        }

    async def decide(self, request: LLMRequest) -> LLMResponse:
        """One chat-completions call; returns the tool arguments and usage.

        Raises:
            LLMError: On HTTP failure after retries, or a malformed response.

        """
        started = time.monotonic()
        data = await self._post(self._body(request))
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"{self._label} API error: response has no choices") from None

        content = message.get("content") or ""
        action_input = _from_tool_calls(message.get("tool_calls")) or _json_in_text(content)
        usage = data.get("usage") or {}
        t_in = int(usage.get("prompt_tokens") or 0)
        t_out = int(usage.get("completion_tokens") or 0)
        return LLMResponse(
            action_input=action_input,
            text=content,
            model=str(data.get("model") or self._model),
            tokens_in=t_in,
            tokens_out=t_out,
            cost_usd=cost_usd(t_in, t_out, price_in=self._price_in, price_out=self._price_out),
            latency_ms=int((time.monotonic() - started) * 1000),
            stop_reason=choice.get("finish_reason"),
            raw={"id": data.get("id")},
        )

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST with retries on transient failures.

        Raises:
            LLMTimeoutError: Every attempt timed out.
            LLMTransientError: 429 / 5xx / network errors persisted after retries.
            LLMError: A permanent error (bad request, auth, unknown or retired model).

        """
        attempts = self._max_retries + 1
        last = ""
        timeouts = 0
        for attempt in range(attempts):
            wait = min(2.0**attempt, 8.0)
            try:
                resp = await self._http.post(
                    self._url, json=body, headers=self._headers, timeout=self._timeout
                )
            except httpx.TimeoutException as exc:
                timeouts += 1
                last = f"no answer within {self._timeout_s:.0f}s ({type(exc).__name__})"
            except httpx.HTTPError as exc:
                last = f"network error ({type(exc).__name__})"
            else:
                if resp.status_code < 400:
                    try:
                        parsed: dict[str, Any] = resp.json()
                    except ValueError:
                        raise LLMError(f"{self._label} API error: response is not JSON") from None
                    return parsed
                last = f"HTTP {resp.status_code}: {_error_message(resp)}"
                if resp.status_code not in _RETRY_STATUS:
                    raise LLMError(f"{self._label} API error: {last}"[:500])
                wait = _retry_after(resp) or wait
            if attempt < attempts - 1:
                await asyncio.sleep(wait)

        tries = f"{attempts} attempt{'s' if attempts > 1 else ''}"
        if timeouts == attempts:
            raise LLMTimeoutError(f"{self._label} API timeout: {last}, {tries}"[:500])
        raise LLMTransientError(f"{self._label} API error: {last} after {tries}"[:500])


def _retry_after(resp: httpx.Response) -> float | None:
    """Seconds from a Retry-After header (numeric form only), capped."""
    value = resp.headers.get("retry-after", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, min(seconds, MAX_RETRY_AFTER_S))


def chat_completions_url(base_url: str) -> str:
    """Accept either the API root (``.../v1``) or the full endpoint URL."""
    url = base_url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _from_tool_calls(tool_calls: Any) -> dict[str, Any] | None:
    """Arguments of the first ``act`` call (they arrive as a JSON string)."""
    if not isinstance(tool_calls, list):
        return None
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        if fn.get("name") not in (TOOL_NAME, None):
            continue
        args = fn.get("arguments")
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _json_in_text(text: str) -> dict[str, Any] | None:
    """First JSON object in free text that looks like an action (has a ``type``)."""
    decoder = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            # Accept both {"type": ...} and {"name": "act", "arguments": {...}}.
            inner = obj.get("arguments") or obj.get("parameters")
            if isinstance(inner, dict) and "type" in inner:
                return inner
            if "type" in obj:
                return obj
        i = text.find("{", i + 1)
    return None


def _error_message(resp: httpx.Response) -> str:
    """Short, key-free error text from an error response."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()[:200]
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("detail") or err)[:200]
        return str(body.get("detail") or err)[:200]
    return str(body)[:200]

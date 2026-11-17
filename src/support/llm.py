"""LLM wrapper module for the Multi-Agent Customer Support System.

Provides a thin, retry-safe interface around the Groq SDK with
Langfuse observability and Pydantic structured-output helpers.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from support import config
from support import tracing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Groq client (lazy singleton)
# ---------------------------------------------------------------------------

_groq_client: Any = None


def _get_client() -> Any:
    """Return (and lazily initialise) the shared Groq client."""
    global _groq_client  # noqa: PLW0603
    if _groq_client is None:
        from groq import Groq  # noqa: PLC0415

        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES: int = 5
_INITIAL_DELAY_S: float = 1.0
_MAX_DELAY_S: float = 30.0


def _should_retry(exc: Exception) -> tuple[bool, float | None]:
    """Return *(should_retry, retry_after_seconds_or_None)* for a Groq exception.

    Retries on:
    - HTTP 429 (rate-limit) — honours ``retry-after`` header when present
    - HTTP 5xx (server errors)
    - Timeout / connection errors
    """
    # Groq SDK raises groq.RateLimitError, groq.APIStatusError, etc.
    exc_type_name = type(exc).__name__

    # Rate-limit
    if "RateLimitError" in exc_type_name:
        retry_after: float | None = None
        with _suppress_exc():
            # groq SDK embeds the original httpx response
            headers = exc.response.headers  # type: ignore[attr-defined]
            retry_after = float(headers.get("retry-after", 0)) or None
        return True, retry_after

    # Server errors (5xx)
    if "APIStatusError" in exc_type_name:
        status = getattr(exc, "status_code", 0)
        return (500 <= status < 600), None

    # Timeout / network
    if any(t in exc_type_name for t in ("Timeout", "Connection", "Transport")):
        return True, None

    return False, None


class _suppress_exc:  # noqa: N801
    """Tiny context manager that silently swallows *any* exception."""

    def __enter__(self) -> _suppress_exc:
        return self

    def __exit__(self, *_: Any) -> bool:
        return True


# ---------------------------------------------------------------------------
# Public chat function
# ---------------------------------------------------------------------------


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float = 0.3,
    _trace: Any = None,
) -> Any:
    """Call the Groq chat-completions API with exponential-backoff retries.

    Args:
        messages: OpenAI-style message list (``[{"role": …, "content": …}]``).
        tools: Optional list of tool definitions (function-calling).
        response_format: Optional ``{"type": "json_object"}`` or similar.
        temperature: Sampling temperature (default 0.3 for deterministic outputs).
        _trace: Optional Langfuse trace/span for observability (internal use).

    Returns:
        The raw Groq ``ChatCompletion`` response object.

    Raises:
        RuntimeError: After all retries are exhausted.
        ValueError: If the model returns a malformed tool call and the retry also fails.
    """
    client = _get_client()
    delay = _INITIAL_DELAY_S

    for attempt in range(_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": config.MODEL,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if response_format:
                kwargs["response_format"] = response_format

            response = client.chat.completions.create(**kwargs)

        except Exception as exc:  # noqa: BLE001
            should_retry, retry_after = _should_retry(exc)
            if should_retry and attempt < _MAX_RETRIES:
                wait = retry_after if retry_after else min(delay, _MAX_DELAY_S)
                logger.warning(
                    "Groq API error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)
                delay = min(delay * 2, _MAX_DELAY_S)
                continue
            raise RuntimeError(
                f"Groq API call failed after {attempt + 1} attempt(s): {exc}"
            ) from exc

        else:
            # ----------------------------------------------------------------
            # Success path: validate tool-call JSON (if any), log to Langfuse
            # ----------------------------------------------------------------
            latency_ms = (time.monotonic() - t0) * 1000.0

            # Check for malformed tool-call arguments
            tool_error = _check_tool_calls(response)
            if tool_error:
                if attempt == 0:
                    # Retry once: inject the error as an assistant + user turn
                    logger.warning("Malformed tool-call JSON; retrying with error hint.")
                    messages = list(messages) + [
                        {
                            "role": "assistant",
                            "content": f"[TOOL CALL ERROR — please retry]: {tool_error}",
                        }
                    ]
                    continue
                else:
                    raise ValueError(f"Malformed tool-call JSON after retry: {tool_error}")

            # Langfuse observability
            usage_dict: dict[str, Any] = {}
            if response.usage:
                usage_dict = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            tracing.generation(
                trace_or_span=_trace,
                name="chat",
                model=config.MODEL,
                input=messages,
                output=response.choices[0].message.content if response.choices else None,
                usage=usage_dict,
                latency_ms=latency_ms,
            )

            return response

    raise RuntimeError(f"Groq API call failed after {_MAX_RETRIES} retries.")


def _check_tool_calls(response: Any) -> str | None:
    """Return an error string if any tool call in *response* has invalid JSON args.

    Returns ``None`` when everything looks fine.
    """
    choices = getattr(response, "choices", [])
    for choice in choices:
        message = getattr(choice, "message", None)
        tool_calls = getattr(message, "tool_calls", None) or []
        for tc in tool_calls:
            args_str = getattr(getattr(tc, "function", None), "arguments", None)
            if args_str is not None:
                try:
                    json.loads(args_str)
                except json.JSONDecodeError as exc:
                    return f"Invalid JSON in tool call arguments: {exc}"
    return None


# ---------------------------------------------------------------------------
# Structured output helper
# ---------------------------------------------------------------------------


def structured(
    messages: list[dict[str, Any]],
    model: type[BaseModel],
    _trace: Any = None,
) -> BaseModel:
    """Call the LLM and return a validated Pydantic model instance.

    The function instructs the model to respond with a JSON object matching
    *model*'s schema.  It retries once with the validation error appended if
    the first response fails Pydantic validation.

    Args:
        messages: OpenAI-style message list.
        model: Pydantic ``BaseModel`` subclass that describes the expected shape.
        _trace: Optional Langfuse trace/span (forwarded to :func:`chat`).

    Returns:
        A validated instance of *model*.

    Raises:
        ValueError: After two failed validation attempts.
    """
    schema_str = json.dumps(model.model_json_schema(), indent=2)
    system_instruction = (
        f"Respond ONLY with a valid JSON object that matches this schema:\n{schema_str}"
    )

    # Prepend the schema instruction as a system message (or extend if one exists)
    augmented: list[dict[str, Any]] = [
        {"role": "system", "content": system_instruction},
        *messages,
    ]

    for attempt in range(2):
        response = chat(
            augmented,
            response_format={"type": "json_object"},
            _trace=_trace,
        )
        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
            return model.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 0:
                logger.warning("Structured output validation failed; retrying: %s", exc)
                # Append the error so the model can self-correct
                augmented = augmented + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"Your response was invalid: {exc}. "
                            "Please return a corrected JSON object."
                        ),
                    },
                ]
            else:
                raise ValueError(
                    f"Structured output validation failed after 2 attempts: {exc}"
                ) from exc

    # Should be unreachable, but satisfies type checkers
    raise ValueError("structured() failed unexpectedly.")

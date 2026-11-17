"""Tracing module for the Multi-Agent Customer Support System.

Wraps the Langfuse SDK to provide trace/span/generation helpers.
When LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are absent (or the
package is not installed), every exported function is a silent no-op
so the rest of the application runs unmodified.
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attempt to import Langfuse
# ---------------------------------------------------------------------------

try:
    from langfuse import Langfuse  # type: ignore[import-untyped]
    _langfuse_available = True
except ImportError:
    _langfuse_available = False
    logger.debug("langfuse package not found; tracing is disabled.")

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_client: Any = None  # Langfuse instance, or None if disabled


def _get_client() -> Any:
    """Return (and lazily initialise) the Langfuse client, or None if disabled."""
    global _client  # noqa: PLW0603

    if _client is not None:
        return _client

    if not _langfuse_available:
        return None

    # Import here to avoid a circular import at module level (config → tracing)
    try:
        from support import config  # noqa: PLC0415

        if not config.LANGFUSE_PUBLIC_KEY or not config.LANGFUSE_SECRET_KEY:
            logger.debug("Langfuse keys not set; tracing is disabled.")
            return None

        kwargs: dict[str, Any] = {
            "public_key": config.LANGFUSE_PUBLIC_KEY,
            "secret_key": config.LANGFUSE_SECRET_KEY,
        }
        if config.LANGFUSE_HOST:
            kwargs["host"] = config.LANGFUSE_HOST

        _client = Langfuse(**kwargs)
        return _client

    except Exception:  # noqa: BLE001
        logger.debug("Failed to initialise Langfuse client; tracing is disabled.", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# No-op context manager helper
# ---------------------------------------------------------------------------

class _NoopCtx:
    """A lightweight no-op context manager / object returned when tracing is off."""

    def __enter__(self) -> _NoopCtx:
        return self

    def __exit__(self, *_: Any) -> None:  # noqa: ANN401
        pass

    # Absorb attribute access so callers can do ``span.id`` without errors.
    def __getattr__(self, _name: str) -> Any:  # noqa: ANN401
        return None


_NOOP = _NoopCtx()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextmanager
def trace(
    name: str,
    session_id: str,
    **kwargs: Any,
) -> Generator[Any, None, None]:
    """Context manager that creates a Langfuse trace (or no-op when disabled).

    Args:
        name: Human-readable name for the trace.
        session_id: Session identifier used to group traces in Langfuse.
        **kwargs: Additional keyword arguments forwarded to ``Langfuse.trace()``.

    Yields:
        A Langfuse ``Trace`` object, or a no-op sentinel when tracing is off.
    """
    client = _get_client()
    if client is None:
        yield _NOOP
        return

    t = None
    try:
        t = client.trace(name=name, session_id=session_id, **kwargs)
        yield t
    except Exception:  # noqa: BLE001
        logger.debug("Langfuse trace creation failed.", exc_info=True)
        yield _NOOP
    finally:
        if t is not None:
            with contextlib.suppress(Exception):
                t.update(status_message="done")


def generation(
    trace_or_span: Any,
    name: str,
    model: str,
    input: Any,  # noqa: A002
    output: Any,
    usage: dict[str, Any],
    latency_ms: float,
) -> None:
    """Record an LLM generation event on an existing trace or span.

    Args:
        trace_or_span: Parent Langfuse trace or span object.
        name: Label for this generation (e.g. ``"chat"``).
        model: Model name string (e.g. ``"openai/gpt-oss-120b"``).
        input: The messages list or prompt sent to the model.
        output: The raw response from the model.
        usage: Token usage dict (``{"prompt_tokens": …, "completion_tokens": …}``).
        latency_ms: Wall-clock time for the LLM call in milliseconds.
    """
    if _get_client() is None or trace_or_span is _NOOP or trace_or_span is None:
        return

    try:
        trace_or_span.generation(
            name=name,
            model=model,
            input=input,
            output=output,
            usage=usage,
            metadata={"latency_ms": latency_ms},
        )
    except Exception:  # noqa: BLE001
        logger.debug("Langfuse generation logging failed.", exc_info=True)


@contextmanager
def span(
    parent: Any,
    name: str,
    input: Any = None,  # noqa: A002
) -> Generator[Any, None, None]:
    """Context manager that creates a child span on a trace or parent span.

    Args:
        parent: Parent Langfuse trace or span object.
        name: Label for the span.
        input: Optional input data attached to the span.

    Yields:
        A Langfuse ``Span`` object, or a no-op sentinel when tracing is off.
    """
    if _get_client() is None or parent is _NOOP or parent is None:
        yield _NOOP
        return

    s = None
    try:
        s = parent.span(name=name, input=input)
        yield s
    except Exception:  # noqa: BLE001
        logger.debug("Langfuse span creation failed.", exc_info=True)
        yield _NOOP
    finally:
        if s is not None:
            with contextlib.suppress(Exception):
                s.end()


def flush() -> None:
    """Flush all buffered Langfuse events to the remote server (or no-op)."""
    client = _get_client()
    if client is None:
        return
    with contextlib.suppress(Exception):
        client.flush()

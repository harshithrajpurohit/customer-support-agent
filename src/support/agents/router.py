"""Router agent: classifies intent, sentiment, wants_human, and order_id."""

from __future__ import annotations

import logging
from typing import Any

from support import llm
from support.prompts import ROUTER_PROMPT
from support.schemas import RouterOutput

logger = logging.getLogger(__name__)


def run_router(
    messages: list[dict[str, Any]],
    _trace: Any = None,
) -> RouterOutput:
    """Classify the user's intent and sentiment from conversation history.

    Parameters
    ----------
    messages:
        Conversation messages, typically including recent history so context
        like 'yes please' can be properly understood.
    _trace:
        Optional Langfuse trace or span.

    Returns
    -------
    RouterOutput
        Structured classification with intent, sentiment, wants_human, order_id, confidence.
    """
    # Keep the last 10 messages for router context
    recent = messages[-10:] if len(messages) > 10 else messages
    prompt_messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        *recent,
    ]

    try:
        output = llm.structured(
            messages=prompt_messages,
            model=RouterOutput,
            _trace=_trace,
        )
        return output  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("Router structured classification failed: %s; falling back to default.", exc)
        # Safe fallback
        return RouterOutput(
            intent="complaint",
            sentiment="frustrated",
            wants_human=True,
            order_id=None,
            confidence=0.0,
        )

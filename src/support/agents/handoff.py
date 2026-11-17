"""Handoff escalation agent: builds escalation summaries and records handoffs."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from support import db, llm, memory
from support.prompts import HANDOFF_PROMPT

logger = logging.getLogger(__name__)


class HandoffOutput(BaseModel):
    """Structured output from the handoff escalation call."""

    summary: str = Field(
        description="3 to 6 sentence summary covering: customer, order IDs, what was asked, what was tried, reason for escalating, and sentiment."
    )
    customer_reply: str = Field(
        description="A calm, polite, reassuring message informing the customer that a human agent will follow up shortly."
    )


def run_handoff_agent(
    session_id: str,
    customer_id: int,
    reason: str,
    messages: list[dict[str, Any]],
    sentiment: str | None = None,
    _trace: Any = None,
) -> dict[str, str]:
    """Build escalation summary, insert handoff record in DB, and set session flag.

    Parameters
    ----------
    session_id:
        Current conversation session identifier.
    customer_id:
        Authenticated customer ID.
    reason:
        Reason for escalation (e.g. 'angry_customer', 'wants_human', 'complaint', 'max_failed_attempts').
    messages:
        Full or recent conversation transcript.
    sentiment:
        Detected customer sentiment.
    _trace:
        Optional Langfuse trace or span.

    Returns
    -------
    dict[str, str]
        Dictionary with 'summary' and 'reply'.
    """
    prompt_messages = [
        {"role": "system", "content": HANDOFF_PROMPT},
        {
            "role": "user",
            "content": (
                f"Customer ID: {customer_id}\n"
                f"Escalation Reason: {reason}\n"
                f"Detected Sentiment: {sentiment or 'unknown'}\n"
                f"Conversation Transcript:\n{json.dumps(messages, indent=2)}\n\n"
                "Please generate the 3 to 6 sentence handoff summary and the customer reply."
            ),
        },
    ]

    try:
        res: HandoffOutput = llm.structured(
            messages=prompt_messages,
            model=HandoffOutput,
            _trace=_trace,
        )  # type: ignore[assignment]
        summary = res.summary
        reply = res.customer_reply
    except Exception as exc:
        logger.warning("Handoff structured LLM call failed: %s; using fallback.", exc)
        summary = (
            f"Customer {customer_id} requested assistance. "
            f"Escalation triggered due to: {reason}. "
            f"Customer sentiment was noted as {sentiment or 'frustrated'}. "
            "Automated system could not resolve the inquiry. "
            "Human representative intervention required to assist customer."
        )
        reply = (
            "I apologize for the inconvenience. I have escalated this conversation to our "
            "human support team. A representative will review your request and follow up shortly."
        )

    # Insert into Postgres handoffs table
    try:
        with db.get_conn() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO handoffs (session_id, customer_id, reason, summary, transcript, status)
                    VALUES (%s, %s, %s, %s, %s, 'open')
                    """,
                    (
                        session_id,
                        customer_id,
                        reason,
                        summary,
                        json.dumps(messages),
                    ),
                )
    except Exception as exc:
        logger.exception("Failed to insert handoff into database: %s", exc)

    # Set handed_off flag in session store
    store = memory.get_session_store()
    store.set_meta(session_id, "handed_off", True)
    store.set_meta(session_id, "customer_id", customer_id)

    return {"summary": summary, "reply": reply}

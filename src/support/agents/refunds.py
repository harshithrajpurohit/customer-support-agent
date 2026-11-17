"""Refunds specialist agent: verifies eligibility and submits refund requests."""

from __future__ import annotations

import json
import logging
from typing import Any

from support import config, llm, mcp_client
from support.prompts import REFUNDS_PROMPT
from support.schemas import SpecialistOutput

logger = logging.getLogger(__name__)


def run_refunds_agent(
    customer_id: int,
    messages: list[dict[str, Any]],
    _trace: Any = None,
) -> SpecialistOutput:
    """Run the tool-calling loop for the refunds specialist.

    Parameters
    ----------
    customer_id:
        Authenticated customer ID.
    messages:
        Conversation message history.
    _trace:
        Optional Langfuse trace or span.

    Returns
    -------
    SpecialistOutput
        Final reply, resolved flag, and refund_id if a pending request was created.
    """
    tools = mcp_client.get_tools(role="refund")
    working_messages: list[dict[str, Any]] = [
        {"role": "system", "content": REFUNDS_PROMPT},
        *messages,
    ]

    created_refund_id: int | None = None

    for iteration in range(config.MAX_TOOL_ITERATIONS):
        try:
            response = llm.chat(
                messages=working_messages,
                tools=tools,
                _trace=_trace,
            )
        except Exception as exc:
            logger.error("LLM call failed in refunds agent: %s", exc)
            return SpecialistOutput(
                reply="I encountered an issue while processing your refund inquiry. Please try again.",
                resolved=False,
            )

        choice = response.choices[0]
        msg = choice.message

        if not msg.tool_calls:
            reply_text = msg.content or "Your refund request has been addressed."
            return SpecialistOutput(
                reply=reply_text,
                resolved=True,
                refund_id=created_refund_id,
            )

        assistant_tool_calls = []
        for tc in msg.tool_calls:
            assistant_tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )

        working_messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": assistant_tool_calls,
            }
        )

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                args = {}

            tool_res = mcp_client.call_tool(
                name=name,
                args=args,
                customer_id=customer_id,
                role="refund",
                parent_span=_trace,
            )

            if name == "create_refund_request" and tool_res.get("ok"):
                created_refund_id = tool_res.get("refund_id")

            working_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(tool_res),
                }
            )

    return SpecialistOutput(
        reply="I'm having trouble processing your refund request right now. Please try again in a moment.",
        resolved=False,
    )

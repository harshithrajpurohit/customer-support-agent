"""Orders specialist agent: handles order tracking, status, and history."""

from __future__ import annotations

import json
import logging
from typing import Any

from support import config, llm, mcp_client
from support.prompts import ORDERS_PROMPT
from support.schemas import SpecialistOutput

logger = logging.getLogger(__name__)


def run_orders_agent(
    customer_id: int,
    messages: list[dict[str, Any]],
    _trace: Any = None,
) -> SpecialistOutput:
    """Run the tool-calling loop for the orders specialist.

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
        Final reply and resolution status.
    """
    tools = mcp_client.get_tools(role="orders")
    working_messages: list[dict[str, Any]] = [
        {"role": "system", "content": ORDERS_PROMPT},
        *messages,
    ]

    for iteration in range(config.MAX_TOOL_ITERATIONS):
        try:
            response = llm.chat(
                messages=working_messages,
                tools=tools,
                _trace=_trace,
            )
        except Exception as exc:
            logger.error("LLM call failed in orders agent: %s", exc)
            return SpecialistOutput(
                reply="I encountered an issue looking up your order information. Please try again.",
                resolved=False,
            )

        choice = response.choices[0]
        msg = choice.message

        # If model did not request tool calls, we have our final response
        if not msg.tool_calls:
            reply_text = msg.content or "Here is the information regarding your order."
            return SpecialistOutput(
                reply=reply_text,
                resolved=True,
                refund_id=None,
            )

        # Process tool calls
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
                role="orders",
                parent_span=_trace,
            )

            working_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(tool_res),
                }
            )

    # Exceeded max tool iterations without a final text response
    return SpecialistOutput(
        reply="I'm having trouble retrieving your order details right now. Please try again in a moment.",
        resolved=False,
    )

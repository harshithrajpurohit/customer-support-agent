"""Technical specialist agent: answers FAQ and technical troubleshooting queries."""

from __future__ import annotations

import json
import logging
from typing import Any

from support import config, llm, mcp_client
from support.prompts import TECHNICAL_PROMPT
from support.schemas import SpecialistOutput

logger = logging.getLogger(__name__)


def run_technical_agent(
    customer_id: int,
    messages: list[dict[str, Any]],
    _trace: Any = None,
) -> SpecialistOutput:
    """Run the tool-calling loop for the technical FAQ specialist.

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
        Final reply and resolution status (resolved=False if no relevant KB snippet was found).
    """
    tools = mcp_client.get_tools(role="technical")
    working_messages: list[dict[str, Any]] = [
        {"role": "system", "content": TECHNICAL_PROMPT},
        *messages,
    ]

    kb_search_called = False
    kb_found_relevant = False

    for iteration in range(config.MAX_TOOL_ITERATIONS):
        try:
            response = llm.chat(
                messages=working_messages,
                tools=tools,
                _trace=_trace,
            )
        except Exception as exc:
            logger.error("LLM call failed in technical agent: %s", exc)
            return SpecialistOutput(
                reply="I encountered an issue searching our knowledge base. Please try again.",
                resolved=False,
            )

        choice = response.choices[0]
        msg = choice.message

        if not msg.tool_calls:
            reply_text = msg.content or "Here is the information I found."
            is_resolved = kb_search_called and kb_found_relevant

            clean_text = reply_text.replace("’", "'").replace("`", "'").lower()
            unresolved_indicators = (
                "[unresolved]",
                "couldn't find",
                "could not find",
                "cannot find",
                "can't find",
                "wasn't able to find",
                "was not able to find",
                "not able to find",
                "no information",
                "not found in our knowledge base",
                "unable to find",
                "no relevant information",
            )
            if any(ind in clean_text for ind in unresolved_indicators):
                is_resolved = False

            # Strip the [UNRESOLVED] prefix if present for user display
            final_reply = reply_text
            if "[unresolved]" in final_reply.lower():
                import re
                final_reply = re.sub(r"(?i)\[unresolved\]\s*", "", final_reply).strip()

            return SpecialistOutput(
                reply=final_reply,
                resolved=is_resolved,
                refund_id=None,
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
                role="technical",
                parent_span=_trace,
            )

            if name == "search_kb":
                kb_search_called = True
                results = tool_res.get("results", [])
                if results and len(results) > 0:
                    kb_found_relevant = True

            working_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(tool_res),
                }
            )

    return SpecialistOutput(
        reply="I could not find an answer in our knowledge base. A support representative can assist you further.",
        resolved=False,
    )

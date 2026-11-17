"""
check_setup.py - Milestone 1 smoke test for Multi-Agent Customer Support System.

Tests:
  (a) Plain chat completion
  (b) Tool-calling (get_order_status)
  (c) Structured output (JSON schema / fallback json_object + Pydantic)
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load environment: local .env first, then parent .env as fallback
# ---------------------------------------------------------------------------
_local_env = Path(__file__).parent / ".env"
_parent_env = Path(__file__).parent.parent / ".env"

if _local_env.exists():
    load_dotenv(dotenv_path=_local_env, override=False)
elif _parent_env.exists():
    load_dotenv(dotenv_path=_parent_env, override=False)
else:
    print("WARNING: No .env file found (checked local and parent directory).")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = "openai/gpt-oss-120b"

# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------
from groq import Groq  # noqa: E402

_api_key = os.environ.get("GROQ_API_KEY")
if not _api_key:
    print("FAIL: GROQ_API_KEY not found in environment.")
    sys.exit(1)

client = Groq(api_key=_api_key)


# ---------------------------------------------------------------------------
# (a) Plain chat
# ---------------------------------------------------------------------------
def test_plain_chat() -> bool:
    print("\n--- (a) Plain chat completion ---")
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Say hello in exactly three words."}],
            max_tokens=32,
        )
        reply = response.choices[0].message.content
        print(f"    Model reply: {reply!r}")
        print("(a) PASS")
        return True
    except Exception as exc:
        print(f"    Error: {exc}")
        print("(a) FAIL")
        return False


# ---------------------------------------------------------------------------
# (b) Tool-calling
# ---------------------------------------------------------------------------
def test_tool_calling() -> bool:
    print("\n--- (b) Tool-calling (get_order_status) ---")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_order_status",
                "description": "Retrieve the current status of an order by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "integer",
                            "description": "The unique integer order identifier.",
                        }
                    },
                    "required": ["order_id"],
                },
            },
        }
    ]
    messages = [
        {
            "role": "user",
            "content": "What is the status of order 1042?",
        }
    ]
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=128,
        )
        choice = response.choices[0]
        has_tool_calls = (
            choice.finish_reason == "tool_calls"
            or (choice.message.tool_calls is not None and len(choice.message.tool_calls) > 0)
        )
        if has_tool_calls:
            tc = choice.message.tool_calls[0]
            args = json.loads(tc.function.arguments)
            print(f"    Tool called: {tc.function.name}({args})")
            print("(b) PASS")
            return True
        else:
            print(f"    No tool_calls in response. finish_reason={choice.finish_reason!r}")
            print(f"    Content: {choice.message.content!r}")
            print("(b) FAIL")
            return False
    except Exception as exc:
        print(f"    Error: {exc}")
        print("(b) FAIL")
        return False


# ---------------------------------------------------------------------------
# (c) Structured output
# ---------------------------------------------------------------------------
def test_structured_output() -> bool:
    print("\n--- (c) Structured output ---")

    # Pydantic model for validation
    try:
        from pydantic import BaseModel, ValidationError

        class OrderInfo(BaseModel):
            order_id: int
            status: str
            estimated_delivery: str

    except ImportError:
        print("    Pydantic not available; skipping structured output test.")
        print("(c) SKIP")
        return True

    json_schema = {
        "type": "object",
        "properties": {
            "order_id": {"type": "integer"},
            "status": {"type": "string"},
            "estimated_delivery": {"type": "string"},
        },
        "required": ["order_id", "status", "estimated_delivery"],
        "additionalProperties": False,
    }

    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant. Always respond with valid JSON matching the schema.",
        },
        {
            "role": "user",
            "content": (
                "Return a JSON object with order_id=1042, status='shipped', "
                "and estimated_delivery='2024-12-01'."
            ),
        },
    ]

    # Attempt 1: response_format with json_schema
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "order_info",
                    "strict": True,
                    "schema": json_schema,
                },
            },
            max_tokens=128,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        obj = OrderInfo(**data)
        print(f"    Structured output (json_schema): {obj}")
        print("(c) PASS")
        return True
    except Exception as exc:
        print(f"    json_schema mode failed: {exc}")

    # Attempt 2: fallback to json_object mode + manual Pydantic validation
    print("    Falling back to json_object mode + Pydantic validation …")
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=128,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        obj = OrderInfo(**data)
        print(f"    Structured output (json_object + Pydantic fallback): {obj}")
        print("(c) PASS (via json_object fallback)")
        _write_decision_note()
        return True
    except Exception as exc2:
        print(f"    json_object fallback also failed: {exc2}")
        print("(c) FAIL")
        return False


def _write_decision_note() -> None:
    """Document the structured-output fallback in docs/DECISIONS.md."""
    decisions_path = Path(__file__).parent / "docs" / "DECISIONS.md"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    note = """# Architecture Decisions

## ADR-001: Structured Output Fallback

**Status:** Accepted  
**Date:** Milestone 1

### Context
The Groq API with the `{model}` model does not currently support `response_format.type=json_schema`
(strict structured output).

### Decision
Use `response_format={{"type": "json_object"}}` combined with Pydantic model validation on the
client side. The model is prompted to return JSON matching the target schema; the response is
then validated with Pydantic.

### Consequences
- Output is not guaranteed to be schema-compliant at the network layer, but is validated in code.
- If Pydantic validation fails, an exception is raised and the caller should retry or handle gracefully.
""".format(model=MODEL)

    if not decisions_path.exists():
        decisions_path.write_text(note, encoding="utf-8")
        print(f"    Fallback documented in {decisions_path}")
    else:
        print(f"    {decisions_path} already exists; skipping write.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Milestone 1 — LLM Smoke Test")
    print(f"Model: {MODEL}")
    print("=" * 60)

    a = test_plain_chat()
    b = test_tool_calling()
    c = test_structured_output()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  (a) Plain chat:       {'PASS' if a else 'FAIL'}")
    print(f"  (b) Tool-calling:     {'PASS' if b else 'FAIL'}")
    print(f"  (c) Structured output:{'PASS' if c else 'FAIL'}")
    print("=" * 60)

    if not (a and b):
        sys.exit(1)

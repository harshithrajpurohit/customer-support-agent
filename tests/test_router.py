"""Unit tests for the router agent."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from support.agents.router import run_router
from support.schemas import RouterOutput


def test_router_order_tracking():
    """Verify router identifies order status inquiries and extracts order_id."""
    messages = [{"role": "user", "content": "Where is my order 1001?"}]
    out = run_router(messages)
    assert isinstance(out, RouterOutput)
    assert out.intent == "order"
    assert out.order_id == 1001
    assert out.wants_human is False


def test_router_refund_request():
    """Verify router identifies refund requests with order_id."""
    messages = [{"role": "user", "content": "I want to return order 1002 and get a refund."}]
    out = run_router(messages)
    assert isinstance(out, RouterOutput)
    assert out.intent == "refund"
    assert out.order_id == 1002


def test_router_technical_inquiry():
    """Verify router identifies technical and FAQ questions."""
    messages = [{"role": "user", "content": "How do I reset my password on the app?"}]
    out = run_router(messages)
    assert isinstance(out, RouterOutput)
    assert out.intent == "technical"
    assert out.order_id is None


def test_router_wants_human():
    """Verify router detects explicit requests to speak with a human."""
    messages = [{"role": "user", "content": "Can I please speak to a human representative?"}]
    out = run_router(messages)
    assert isinstance(out, RouterOutput)
    assert out.wants_human is True


def test_router_angry_sentiment():
    """Verify router detects angry sentiment and abusive/threatening tone."""
    messages = [
        {
            "role": "user",
            "content": "THIS IS ABSOLUTELY TERRIBLE SERVICE! YOU SCAMMERS TOOK MY MONEY, I AM SUING YOU!",
        }
    ]
    out = run_router(messages)
    assert isinstance(out, RouterOutput)
    assert out.sentiment == "angry"


def test_router_multi_turn_context():
    """Verify router leverages prior conversational turns to identify order context."""
    messages = [
        {"role": "user", "content": "Can you check the status of my recent package?"},
        {"role": "assistant", "content": "Sure, could you provide your order ID?"},
        {"role": "user", "content": "Order 1005 please"},
    ]
    out = run_router(messages)
    assert isinstance(out, RouterOutput)
    assert out.intent == "order"
    assert out.order_id == 1005


def test_router_fallback_on_error():
    """Verify router falls back safely to complaint/escalation when LLM call fails."""
    with patch("support.llm.structured", side_effect=RuntimeError("Groq timeout")):
        out = run_router([{"role": "user", "content": "Hello"}])
        assert isinstance(out, RouterOutput)
        assert out.intent == "complaint"
        assert out.wants_human is True

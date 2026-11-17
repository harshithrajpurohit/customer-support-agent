"""Unit and integration tests for LangGraph support system."""

from __future__ import annotations

import uuid
import pytest

from support.graph import app, edge_route_decision, edge_specialist_post_decision, process_message
from support.schemas import GraphState


def test_graph_structure():
    """Verify all required nodes exist in the compiled LangGraph workflow."""
    nodes = app.nodes
    expected_nodes = {
        "load_session",
        "router",
        "orders_agent",
        "refund_agent",
        "technical_agent",
        "approval_gate",
        "handoff",
        "save_and_respond",
    }
    for node in expected_nodes:
        assert node in nodes, f"Missing expected node '{node}' in graph."


def test_edge_route_decision_escalation():
    """Verify conditional edge routes angry or human-requesting customers to handoff."""
    state_angry: GraphState = {"sentiment": "angry", "wants_human": False, "intent": "order", "failed_attempts": 0}
    assert edge_route_decision(state_angry) == "handoff"

    state_human: GraphState = {"sentiment": "calm", "wants_human": True, "intent": "order", "failed_attempts": 0}
    assert edge_route_decision(state_human) == "handoff"

    state_complaint: GraphState = {"sentiment": "calm", "wants_human": False, "intent": "complaint", "failed_attempts": 0}
    assert edge_route_decision(state_complaint) == "handoff"

    state_max_failed: GraphState = {"sentiment": "calm", "wants_human": False, "intent": "order", "failed_attempts": 2}
    assert edge_route_decision(state_max_failed) == "handoff"


def test_edge_route_decision_specialists():
    """Verify conditional edge routes intents to appropriate specialist nodes."""
    state_order: GraphState = {"sentiment": "calm", "wants_human": False, "intent": "order", "failed_attempts": 0}
    assert edge_route_decision(state_order) == "orders_agent"

    state_refund: GraphState = {"sentiment": "calm", "wants_human": False, "intent": "refund", "failed_attempts": 0}
    assert edge_route_decision(state_refund) == "refund_agent"

    state_tech: GraphState = {"sentiment": "calm", "wants_human": False, "intent": "technical", "failed_attempts": 0}
    assert edge_route_decision(state_tech) == "technical_agent"


def test_edge_specialist_post_decision():
    """Verify specialist completion routing: failed attempts vs approval vs response."""
    # Failed attempts < 2 goes to save_and_respond
    state_unresolved_1: GraphState = {"resolved": False, "failed_attempts": 1}
    assert edge_specialist_post_decision(state_unresolved_1) == "save_and_respond"

    # Failed attempts >= 2 routes to handoff
    state_unresolved_2: GraphState = {"resolved": False, "failed_attempts": 2}
    assert edge_specialist_post_decision(state_unresolved_2) == "handoff"

    # Pending refund routes to approval gate
    state_pending_refund: GraphState = {"resolved": True, "pending_refund_id": 1002}
    assert edge_specialist_post_decision(state_pending_refund) == "approval_gate"

    # Normal resolved routes to save_and_respond
    state_normal_resolved: GraphState = {"resolved": True, "pending_refund_id": None}
    assert edge_specialist_post_decision(state_normal_resolved) == "save_and_respond"

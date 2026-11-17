"""LangGraph state machine for the Multi-Agent Customer Support System.

Coordinates session loading, routing, specialist execution, approval gates with
interrupts, escalation handoffs, and persistence.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
try:
    from langgraph.errors import GraphInterrupt
except ImportError:
    class GraphInterrupt(Exception):  # type: ignore
        pass
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from support import config, db, memory, tracing
from support.agents.handoff import run_handoff_agent
from support.agents.orders import run_orders_agent
from support.agents.refunds import run_refunds_agent
from support.agents.router import run_router
from support.agents.technical import run_technical_agent
from support.schemas import GraphState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Checkpointer Initialization
# ---------------------------------------------------------------------------

_checkpointer_cm: Any = None
_checkpointer_instance: Any = None


def get_checkpointer() -> Any:
    """Return the active checkpointer (PostgresSaver if available, else MemorySaver)."""
    global _checkpointer_cm, _checkpointer_instance
    if _checkpointer_instance is not None:
        return _checkpointer_instance

    if config.DATABASE_URL:
        try:
            _checkpointer_cm = PostgresSaver.from_conn_string(config.DATABASE_URL)
            cp = _checkpointer_cm.__enter__()
            cp.setup()
            _checkpointer_instance = cp
            logger.info("Using PostgresSaver checkpointer for LangGraph.")
            return _checkpointer_instance
        except Exception as exc:
            logger.warning(
                "PostgresSaver initialization failed (%s); falling back to MemorySaver.", exc
            )

    _checkpointer_instance = MemorySaver()
    logger.info("Using MemorySaver checkpointer for LangGraph.")
    return _checkpointer_instance


# ---------------------------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------------------------

def _save_user_message(session_id: str, customer_id: int, content: str) -> None:
    """Persist a user message to session store and Postgres chat_messages."""
    store = memory.get_session_store()
    store.append_message(session_id, "user", content)

    try:
        with db.get_conn() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO chat_messages (session_id, customer_id, role, content)
                    VALUES (%s, %s, 'user', %s)
                    """,
                    (session_id, customer_id, content),
                )
    except Exception as exc:
        logger.exception("Failed to insert user message to DB: %s", exc)


def _save_assistant_message(
    session_id: str, customer_id: int, content: str, intent: str | None = None
) -> None:
    """Persist an assistant message to session store and Postgres chat_messages."""
    store = memory.get_session_store()
    store.append_message(session_id, "assistant", content)

    try:
        with db.get_conn() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO chat_messages (session_id, customer_id, role, content, intent)
                    VALUES (%s, %s, 'assistant', %s, %s)
                    """,
                    (session_id, customer_id, content, intent),
                )
    except Exception as exc:
        logger.exception("Failed to insert assistant message to DB: %s", exc)


def _append_to_handoff_transcript(session_id: str, user_msg: str, holding_reply: str) -> None:
    """Append new conversation messages to the open handoff record in Postgres."""
    try:
        with db.get_conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT id, transcript FROM handoffs WHERE session_id = %s AND status = 'open' ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                if row:
                    transcript = row["transcript"] if isinstance(row["transcript"], list) else []
                    transcript.append({"role": "user", "content": user_msg})
                    transcript.append({"role": "assistant", "content": holding_reply})
                    conn.execute(
                        "UPDATE handoffs SET transcript = %s WHERE id = %s",
                        (json.dumps(transcript), row["id"]),
                    )
    except Exception as exc:
        logger.exception("Failed to update handoff transcript in DB: %s", exc)


# ---------------------------------------------------------------------------
# Graph Node Definitions
# ---------------------------------------------------------------------------

def node_load_session(state: GraphState) -> dict[str, Any]:
    """Load conversation history, failed attempts, and check handoff status."""
    session_id = state["session_id"]
    customer_id = state["customer_id"]
    incoming_msgs = state.get("messages", [])

    # The latest user message text
    user_text = incoming_msgs[-1]["content"] if incoming_msgs else ""

    store = memory.get_session_store()
    is_handed_off = bool(store.get_meta(session_id, "handed_off", False))

    if is_handed_off:
        holding_reply = (
            "A customer support specialist has already been assigned to your case "
            "and will follow up with you shortly. Thank you for your patience."
        )
        # Append user message and holding reply
        store.append_message(session_id, "user", user_text)
        _save_assistant_message(session_id, customer_id, holding_reply, intent="handed_off")
        _append_to_handoff_transcript(session_id, user_text, holding_reply)

        return {
            "reply": holding_reply,
            "outcome": "handed_off",
            "handoff_reason": "already_handed_off",
            "_already_handed_off": True,
            "_reply_saved": True,
        }

    # Normal flow: save user message to store and DB
    _save_user_message(session_id, customer_id, user_text)

    # Retrieve all recent messages from session store (includes this user turn)
    history = store.get(session_id)
    failed_attempts = store.get_failed(session_id)

    return {
        "messages": history,
        "failed_attempts": failed_attempts,
        "_already_handed_off": False,
        "_reply_saved": False,
        "llm_calls": state.get("llm_calls", 0),
    }


def node_router(state: GraphState) -> dict[str, Any]:
    """Run router agent to classify intent, sentiment, wants_human, and order_id."""
    with tracing.span(None, "node:router", input={"messages_count": len(state["messages"])}):
        router_out = run_router(state["messages"])

    return {
        "intent": router_out.intent,
        "sentiment": router_out.sentiment,
        "wants_human": router_out.wants_human,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def node_orders_agent(state: GraphState) -> dict[str, Any]:
    """Run orders specialist agent."""
    if state.get("llm_calls", 0) >= config.MAX_LLM_CALLS_PER_TURN:
        return {
            "reply": "Processing limit reached for this turn. Escalating to support.",
            "resolved": False,
            "pending_refund_id": None,
        }

    with tracing.span(None, "node:orders_agent"):
        out = run_orders_agent(
            customer_id=state["customer_id"],
            messages=state["messages"],
        )

    res: dict[str, Any] = {
        "reply": out.reply,
        "resolved": out.resolved,
        "pending_refund_id": None,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }

    if not out.resolved:
        store = memory.get_session_store()
        new_failed = store.incr_failed(state["session_id"])
        res["failed_attempts"] = new_failed
        if new_failed >= config.MAX_FAILED_ATTEMPTS:
            res["handoff_reason"] = "max_failed_attempts"

    return res


def node_refund_agent(state: GraphState) -> dict[str, Any]:
    """Run refund specialist agent."""
    if state.get("llm_calls", 0) >= config.MAX_LLM_CALLS_PER_TURN:
        return {
            "reply": "Processing limit reached for this turn. Escalating to support.",
            "resolved": False,
            "pending_refund_id": None,
        }

    with tracing.span(None, "node:refund_agent"):
        out = run_refunds_agent(
            customer_id=state["customer_id"],
            messages=state["messages"],
        )

    res: dict[str, Any] = {
        "reply": out.reply,
        "resolved": out.resolved,
        "pending_refund_id": out.refund_id,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }

    if not out.resolved:
        store = memory.get_session_store()
        new_failed = store.incr_failed(state["session_id"])
        res["failed_attempts"] = new_failed
        if new_failed >= config.MAX_FAILED_ATTEMPTS:
            res["handoff_reason"] = "max_failed_attempts"
    elif out.refund_id is not None:
        res["outcome"] = "awaiting_approval"
        res["awaiting_approval"] = True
        res["reply"] = (
            f"Your refund request #{out.refund_id} has been submitted for review. "
            "Our team will verify and decide on your request shortly."
        )

    return res


def node_technical_agent(state: GraphState) -> dict[str, Any]:
    """Run technical FAQ specialist agent."""
    if state.get("llm_calls", 0) >= config.MAX_LLM_CALLS_PER_TURN:
        return {
            "reply": "Processing limit reached for this turn. Escalating to support.",
            "resolved": False,
            "pending_refund_id": None,
        }

    with tracing.span(None, "node:technical_agent"):
        out = run_technical_agent(
            customer_id=state["customer_id"],
            messages=state["messages"],
        )

    res: dict[str, Any] = {
        "reply": out.reply,
        "resolved": out.resolved,
        "pending_refund_id": None,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }

    if not out.resolved:
        store = memory.get_session_store()
        new_failed = store.incr_failed(state["session_id"])
        res["failed_attempts"] = new_failed
        if new_failed >= config.MAX_FAILED_ATTEMPTS:
            res["handoff_reason"] = "max_failed_attempts"

    return res


def node_approval_gate(state: GraphState) -> dict[str, Any]:
    """Pause graph execution for human-in-the-loop admin approval via interrupt."""
    rf_id = state.get("pending_refund_id")
    session_id = state["session_id"]
    customer_id = state["customer_id"]

    if rf_id:
        store = memory.get_session_store()
        store.set_meta(f"refund_session:{rf_id}", "session_id", session_id)
        store.set_meta(f"refund_customer:{rf_id}", "customer_id", customer_id)

    # Save the awaiting_approval message so customer sees it immediately
    reply = (
        f"Your refund request #{rf_id} has been submitted for review. "
        "Our team will verify and decide on your request shortly."
    )
    _save_assistant_message(session_id, customer_id, reply, state.get("intent"))

    # Pause execution until admin submits decision
    decision_payload = interrupt(
        {
            "action": "refund_approval",
            "refund_id": rf_id,
            "session_id": session_id,
            "customer_id": customer_id,
        }
    )

    # When resumed by admin decision:
    decision = "approved"
    note = ""
    if isinstance(decision_payload, dict):
        decision = decision_payload.get("decision", "approved")
        note = decision_payload.get("note", "")
    elif isinstance(decision_payload, str):
        decision = decision_payload

    if "approve" in decision.lower():
        follow_up = f"Good news! Your refund request #{rf_id} has been approved."
    else:
        follow_up = f"Your refund request #{rf_id} has been reviewed and rejected."
        if note:
            follow_up += f" Reason: {note}"

    return {
        "reply": follow_up,
        "outcome": "resolved",
        "pending_refund_id": None,
        "awaiting_approval": False,
        "_reply_saved": False,
    }


def node_handoff(state: GraphState) -> dict[str, Any]:
    """Escalate conversation to human support specialist."""
    session_id = state["session_id"]
    customer_id = state["customer_id"]
    reason = state.get("handoff_reason")
    if not reason:
        if state.get("failed_attempts", 0) >= config.MAX_FAILED_ATTEMPTS:
            reason = "max_failed_attempts"
        elif state.get("sentiment") == "angry":
            reason = "angry_customer"
        elif state.get("wants_human"):
            reason = "wants_human"
        elif state.get("intent") == "complaint":
            reason = "complaint"
        else:
            reason = "escalation"

    with tracing.span(None, "node:handoff", input={"reason": reason}):
        result = run_handoff_agent(
            session_id=session_id,
            customer_id=customer_id,
            reason=reason,
            messages=state["messages"],
            sentiment=state.get("sentiment"),
        )

    return {
        "reply": result["reply"],
        "outcome": "handed_off",
        "handoff_reason": reason,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def node_save_and_respond(state: GraphState) -> dict[str, Any]:
    """Save assistant reply to session store and DB chat_messages."""
    session_id = state["session_id"]
    customer_id = state["customer_id"]
    reply = state.get("reply") or ""
    intent = state.get("intent")
    outcome = state.get("outcome") or "resolved"

    # Only save if not already saved by a prior node (e.g. approval_gate or load_session)
    if not state.get("_reply_saved"):
        _save_assistant_message(session_id, customer_id, reply, intent=intent)

    return {
        "outcome": outcome,
        "_reply_saved": True,
    }


# ---------------------------------------------------------------------------
# Routing and Conditional Edges
# ---------------------------------------------------------------------------

def edge_after_load_session(state: GraphState) -> str:
    """If already handed off, bypass agents and terminate immediately."""
    if state.get("_already_handed_off"):
        return END
    return "router"


def edge_route_decision(state: GraphState) -> str:
    """Conditional edge after router: evaluate escalation criteria vs specialist assignment."""
    sentiment = state.get("sentiment")
    wants_human = state.get("wants_human")
    intent = state.get("intent")
    failed_attempts = state.get("failed_attempts", 0)

    # Escalation checks
    if (
        sentiment == "angry"
        or wants_human
        or intent == "complaint"
        or failed_attempts >= config.MAX_FAILED_ATTEMPTS
    ):
        if failed_attempts >= config.MAX_FAILED_ATTEMPTS:
            state["handoff_reason"] = "max_failed_attempts"
        elif sentiment == "angry":
            state["handoff_reason"] = "angry_customer"
        elif wants_human:
            state["handoff_reason"] = "wants_human"
        elif intent == "complaint":
            state["handoff_reason"] = "complaint"
        else:
            state["handoff_reason"] = "escalation"
        return "handoff"

    # Specialist routing based on intent
    if intent == "refund":
        return "refund_agent"
    elif intent == "technical":
        return "technical_agent"
    else:
        return "orders_agent"


def edge_specialist_post_decision(state: GraphState) -> str:
    """Conditional edge after any specialist: handles failed attempts, approval, or completion."""
    resolved = state.get("resolved", True)
    failed_attempts = state.get("failed_attempts", 0)

    if not resolved:
        if failed_attempts >= config.MAX_FAILED_ATTEMPTS:
            return "handoff"
        return "save_and_respond"

    # If refund agent requested human approval
    if state.get("pending_refund_id") is not None:
        return "approval_gate"

    return "save_and_respond"


# ---------------------------------------------------------------------------
# Workflow Assembly
# ---------------------------------------------------------------------------

def build_workflow() -> StateGraph:
    """Assemble and configure the LangGraph StateGraph."""
    workflow = StateGraph(GraphState)

    # Add nodes
    workflow.add_node("load_session", node_load_session)
    workflow.add_node("router", node_router)
    workflow.add_node("orders_agent", node_orders_agent)
    workflow.add_node("refund_agent", node_refund_agent)
    workflow.add_node("technical_agent", node_technical_agent)
    workflow.add_node("approval_gate", node_approval_gate)
    workflow.add_node("handoff", node_handoff)
    workflow.add_node("save_and_respond", node_save_and_respond)

    # Edge: START -> load_session
    workflow.add_edge(START, "load_session")

    # Conditional edge: load_session -> router or END
    workflow.add_conditional_edges(
        "load_session",
        edge_after_load_session,
        {"router": "router", END: END},
    )

    # Conditional edge: router -> handoff / orders_agent / refund_agent / technical_agent
    workflow.add_conditional_edges(
        "router",
        edge_route_decision,
        {
            "handoff": "handoff",
            "orders_agent": "orders_agent",
            "refund_agent": "refund_agent",
            "technical_agent": "technical_agent",
        },
    )

    # Conditional edges after each specialist
    workflow.add_conditional_edges(
        "orders_agent",
        edge_specialist_post_decision,
        {
            "handoff": "handoff",
            "approval_gate": "approval_gate",
            "save_and_respond": "save_and_respond",
        },
    )
    workflow.add_conditional_edges(
        "refund_agent",
        edge_specialist_post_decision,
        {
            "handoff": "handoff",
            "approval_gate": "approval_gate",
            "save_and_respond": "save_and_respond",
        },
    )
    workflow.add_conditional_edges(
        "technical_agent",
        edge_specialist_post_decision,
        {
            "handoff": "handoff",
            "approval_gate": "approval_gate",
            "save_and_respond": "save_and_respond",
        },
    )

    # Edges to save_and_respond
    workflow.add_edge("approval_gate", "save_and_respond")
    workflow.add_edge("handoff", "save_and_respond")

    # Edge to END
    workflow.add_edge("save_and_respond", END)

    return workflow


# Compiled singleton graph
_workflow = build_workflow()
app = _workflow.compile(checkpointer=get_checkpointer())


def get_graph() -> Any:
    """Return the compiled LangGraph application."""
    return app


# ---------------------------------------------------------------------------
# High-Level Execution Functions
# ---------------------------------------------------------------------------

def process_message(
    session_id: str,
    customer_id: int,
    message: str,
) -> dict[str, Any]:
    """Execute a turn through the LangGraph support system.

    Parameters
    ----------
    session_id:
        Conversation thread ID.
    customer_id:
        Authenticated customer ID.
    message:
        User's message content.

    Returns
    -------
    dict[str, Any]
        Dictionary with reply, intent, outcome, and status ('answered', 'awaiting_approval', 'handed_off').
    """
    initial_state: GraphState = {
        "session_id": session_id,
        "customer_id": customer_id,
        "messages": [{"role": "user", "content": message}],
        "llm_calls": 0,
    }

    config_dict = {"configurable": {"thread_id": session_id}}

    with tracing.trace("chat_turn", session_id=session_id, customer_id=customer_id) as t:
        try:
            result = app.invoke(initial_state, config=config_dict)
        except GraphInterrupt:
            snapshot = app.get_state(config_dict)
            result = snapshot.values if snapshot else {}
        except Exception as exc:
            logger.exception("Graph invocation error: %s", exc)
            return {
                "reply": "We are experiencing technical difficulties. A human agent will assist you shortly.",
                "intent": "complaint",
                "outcome": "handed_off",
                "status": "handed_off",
            }

    outcome = result.get("outcome") or "resolved"
    # Status mapping for API / downstream
    if outcome == "awaiting_approval":
        status = "awaiting_approval"
    elif outcome == "handed_off":
        status = "handed_off"
    else:
        status = "answered"

    return {
        "reply": result.get("reply", ""),
        "intent": result.get("intent"),
        "outcome": outcome,
        "status": status,
        "pending_refund_id": result.get("pending_refund_id"),
    }


def resume_approval(
    session_id: str,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    """Resume a thread paused at approval_gate with an admin decision.

    Parameters
    ----------
    session_id:
        Conversation thread ID.
    decision:
        'approve' or 'reject'.
    note:
        Optional administrative note.

    Returns
    -------
    dict[str, Any]
        Updated state and follow-up customer message.
    """
    config_dict = {"configurable": {"thread_id": session_id}}
    decision_payload = {"decision": decision, "note": note}

    result = app.invoke(Command(resume=decision_payload), config=config_dict)

    return {
        "reply": result.get("reply", ""),
        "outcome": result.get("outcome", "resolved"),
        "status": "answered",
    }

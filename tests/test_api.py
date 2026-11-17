"""Integration tests for FastAPI backend endpoints (Milestone 7).

Tests:
- Health check
- Demo login (valid and invalid email)
- Chat endpoint (authenticated, unauthenticated, rate limit, validation)
- Session messages retrieval (with incremental `after` filter)
- Admin auth protection (missing token -> 401, invalid token -> 403)
- Admin refunds listing
- Admin refund decision (approve/reject, duplicate -> 409, not found -> 404)
- Admin handoffs listing, detail, and resolve
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure src/ is on sys.path
_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

import pytest
from starlette.testclient import TestClient

from db.init_db import reset_db
from support import config, db, memory
from support.api import app, check_rate_limit, _rate_limits


@pytest.fixture(autouse=True)
def fresh_state():
    """Reset database and clear memory/rate limits before each test."""
    reset_db()
    _rate_limits.clear()
    yield


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

def test_health_check(client: TestClient):
    """Health check endpoint should return 200, status ok, and db true."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["db"] is True
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# Customer Login
# ---------------------------------------------------------------------------

def test_login_valid_customer(client: TestClient):
    """Logging in with a seeded customer email returns session_id and customer info."""
    resp = client.post("/api/login", json={"email": "aarav@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["customer"]["id"] == 1
    assert data["customer"]["name"] == "Aarav Sharma"
    assert data["customer"]["email"] == "aarav@example.com"

    # Verify session store binding
    store = memory.get_session_store()
    customer_id = store.get_meta(data["session_id"], "customer_id")
    assert customer_id == 1


def test_login_invalid_customer(client: TestClient):
    """Logging in with an unknown email returns 404."""
    resp = client.post("/api/login", json={"email": "unknown@example.com"})
    assert resp.status_code == 404
    data = resp.json()
    assert "not found" in data["detail"].lower()


def test_login_validation_error(client: TestClient):
    """Missing or invalid body returns 422."""
    resp = client.post("/api/login", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Chat Endpoint
# ---------------------------------------------------------------------------

def test_chat_unauthenticated_session(client: TestClient):
    """Chatting with an unrecognised session ID returns 401."""
    resp = client.post(
        "/api/chat",
        json={"session_id": "unregistered-session-id", "message": "hello"},
    )
    assert resp.status_code == 401
    assert "log in" in resp.json()["detail"].lower()


def test_chat_message_length_limit(client: TestClient):
    """Messages exceeding 1000 characters must return 422 validation error."""
    # First login to get a valid session
    login_resp = client.post("/api/login", json={"email": "aarav@example.com"})
    session_id = login_resp.json()["session_id"]

    long_message = "x" * 1001
    resp = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": long_message},
    )
    assert resp.status_code == 422


def test_chat_success(client: TestClient):
    """Valid chat message invokes graph and returns reply, intent, and status."""
    login_resp = client.post("/api/login", json={"email": "aarav@example.com"})
    session_id = login_resp.json()["session_id"]

    # Mock graph.process_message to verify endpoint orchestration cleanly
    mock_result = {
        "reply": "Your order #1001 is currently shipped and in transit.",
        "intent": "order",
        "outcome": "resolved",
        "status": "answered",
    }
    with patch("support.graph.process_message", return_value=mock_result) as mock_pm:
        resp = client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "Where is my order 1001?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == mock_result["reply"]
        assert data["intent"] == "order"
        assert data["status"] == "answered"
        mock_pm.assert_called_once_with(
            session_id=session_id,
            customer_id=1,
            message="Where is my order 1001?",
        )


def test_chat_rate_limiting(client: TestClient):
    """Exceeding 20 messages per minute on a session returns 429."""
    login_resp = client.post("/api/login", json={"email": "aarav@example.com"})
    session_id = login_resp.json()["session_id"]

    mock_result = {"reply": "Hi", "intent": "order", "status": "answered"}
    with patch("support.graph.process_message", return_value=mock_result):
        # Fire 20 allowed requests
        for i in range(20):
            res = client.post(
                "/api/chat",
                json={"session_id": session_id, "message": f"ping {i}"},
            )
            assert res.status_code == 200, f"Request {i} failed unexpectedly"

        # The 21st request must trigger 429 Too Many Requests
        rate_limited_res = client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "overflow"},
        )
        assert rate_limited_res.status_code == 429
        assert "rate limit" in rate_limited_res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Session Messages Retrieval
# ---------------------------------------------------------------------------

def test_get_session_messages_and_polling(client: TestClient):
    """Retrieve message history including incremental messages via ?after=id."""
    session_id = "test-session-poll-123"

    # Seed messages directly into chat_messages
    with db.get_conn() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO chat_messages (session_id, customer_id, role, content, intent)
                VALUES (%s, 1, 'user', 'Where is my order?', 'order'),
                       (%s, 1, 'assistant', 'Order 1001 is shipped.', 'order')
                """,
                (session_id, session_id),
            )

    # 1. Fetch all messages
    resp = client.get(f"/api/sessions/{session_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 2
    msg1 = data["messages"][0]
    msg2 = data["messages"][1]
    assert msg1["role"] == "user"
    assert msg2["role"] == "assistant"
    assert msg2["content"] == "Order 1001 is shipped."

    # 2. Add an asynchronous refund notification message
    with db.get_conn() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO chat_messages (session_id, customer_id, role, content, intent)
                VALUES (%s, 1, 'assistant', 'Your refund #2 has been approved.', 'refund')
                """,
                (session_id,),
            )

    # 3. Incremental polling with ?after={msg2['id']}
    after_id = msg2["id"]
    poll_resp = client.get(f"/api/sessions/{session_id}/messages?after={after_id}")
    assert poll_resp.status_code == 200
    poll_data = poll_resp.json()
    assert len(poll_data["messages"]) == 1
    assert poll_data["messages"][0]["content"] == "Your refund #2 has been approved."
    assert poll_data["messages"][0]["id"] > after_id


# ---------------------------------------------------------------------------
# Admin Security & Endpoints
# ---------------------------------------------------------------------------

def test_admin_auth_protection(client: TestClient):
    """Admin endpoints must enforce X-Admin-Token header (401 missing, 403 invalid)."""
    # 1. Missing header -> 401
    resp_missing = client.get("/api/admin/refunds")
    assert resp_missing.status_code == 401
    assert "missing" in resp_missing.json()["detail"].lower()

    # 2. Invalid header -> 403
    resp_invalid = client.get(
        "/api/admin/refunds",
        headers={"X-Admin-Token": "invalid-secret-key"},
    )
    assert resp_invalid.status_code == 403
    assert "invalid" in resp_invalid.json()["detail"].lower()


def test_admin_list_refunds(client: TestClient):
    """Listing refunds with valid admin token returns pending refunds joined with order/customer."""
    headers = {"X-Admin-Token": config.ADMIN_TOKEN}
    resp = client.get("/api/admin/refunds?status=pending", headers=headers)
    assert resp.status_code == 200
    refunds = resp.json()
    assert isinstance(refunds, list)
    # Seed data has refund #2 for order 1012 as 'pending'
    pending_ref = next((r for r in refunds if r["order_id"] == 1012), None)
    assert pending_ref is not None
    assert pending_ref["customer_name"] == "Aarav Sharma"
    assert pending_ref["customer_email"] == "aarav@example.com"
    assert pending_ref["status"] == "pending"
    assert "created_at" in pending_ref


def test_admin_refund_decision_flow_and_conflict(client: TestClient):
    """Approve a pending refund, verify state change, and check 409 on duplicate decision."""
    headers = {"X-Admin-Token": config.ADMIN_TOKEN}

    # In seed data, refund #2 (order 1012) is pending
    # 1. Approve refund
    decision_resp = client.post(
        "/api/admin/refunds/2/decision",
        json={"decision": "approve", "note": "Approved by senior manager"},
        headers=headers,
    )
    assert decision_resp.status_code == 200
    data = decision_resp.json()
    assert data["ok"] is True
    assert data["refund_id"] == 2
    assert data["status"] == "approved"

    # Verify DB state
    with db.get_conn() as conn:
        row = conn.execute("SELECT status, decided_by, decision_note FROM refunds WHERE id = 2").fetchone()
        assert row["status"] == "approved"
        assert row["decided_by"] == "admin"
        assert row["decision_note"] == "Approved by senior manager"

    # 2. Duplicate decision must return 409 Conflict
    dup_resp = client.post(
        "/api/admin/refunds/2/decision",
        json={"decision": "reject", "note": "Second attempt"},
        headers=headers,
    )
    assert dup_resp.status_code == 409
    assert "already" in dup_resp.json()["detail"].lower()

    # 3. Non-existent refund returns 404
    missing_resp = client.post(
        "/api/admin/refunds/99999/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert missing_resp.status_code == 404


def test_admin_handoffs_lifecycle(client: TestClient):
    """Test listing handoffs, retrieving detail, and marking resolved."""
    headers = {"X-Admin-Token": config.ADMIN_TOKEN}

    # 1. Insert a mock handoff
    with db.get_conn() as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO handoffs (session_id, customer_id, reason, summary, transcript, status)
                VALUES ('handoff-sess-1', 1, 'angry_customer', 'Customer was upset about delays.',
                        '[{"role": "user", "content": "I am angry"}]'::jsonb, 'open')
                RETURNING id
                """,
            ).fetchone()
            handoff_id = row["id"]

    # 2. List handoffs
    list_resp = client.get("/api/admin/handoffs?status=open", headers=headers)
    assert list_resp.status_code == 200
    handoffs = list_resp.json()
    assert any(h["id"] == handoff_id for h in handoffs)

    # 3. Retrieve handoff detail
    detail_resp = client.get(f"/api/admin/handoffs/{handoff_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == handoff_id
    assert detail["customer_name"] == "Aarav Sharma"
    assert detail["reason"] == "angry_customer"
    assert len(detail["transcript"]) == 1

    # 4. Resolve handoff
    resolve_resp = client.post(f"/api/admin/handoffs/{handoff_id}/resolve", headers=headers)
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "resolved"

    # Verify DB status
    with db.get_conn() as conn:
        res = conn.execute("SELECT status, resolved_at FROM handoffs WHERE id = %s", (handoff_id,)).fetchone()
        assert res["status"] == "resolved"
        assert res["resolved_at"] is not None

    # 5. Resolving non-existent handoff returns 404
    missing_resolve = client.post("/api/admin/handoffs/99999/resolve", headers=headers)
    assert missing_resolve.status_code == 404

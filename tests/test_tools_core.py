"""Integration tests for support.tools_core.

Each test resets the database to a known state via reset_db() so tests are
fully isolated and can run in any order.

Requires:
    - DATABASE_URL set in the environment (loaded from .env by config.py)
    - PostgreSQL accessible and schema seeded
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_db():
    """Reset the database before every test in this module."""
    # Import here so we don't trigger the import at collection time before
    # config has loaded (config loads .env on import).
    from db.init_db import reset_db

    reset_db()
    yield


# ---------------------------------------------------------------------------
# get_order_status
# ---------------------------------------------------------------------------


def test_get_order_status_found():
    """Order 1001 belongs to customer 1 and has status 'shipped'."""
    from support.tools_core import get_order_status

    result = get_order_status(customer_id=1, order_id=1001)
    assert result["ok"] is True
    assert result["found"] is True
    assert result["item"] == "Wireless Earbuds"
    assert result["status"] == "shipped"


def test_get_order_status_cross_customer_protection():
    """Customer 2 must NOT see customer 1's order 1001."""
    from support.tools_core import get_order_status

    result = get_order_status(customer_id=2, order_id=1001)
    assert result["ok"] is True
    assert result["found"] is False
    assert "not found" in result["message"].lower()


# ---------------------------------------------------------------------------
# list_customer_orders
# ---------------------------------------------------------------------------


def test_list_customer_orders_customer_1():
    """Customer 1 has 4 orders in the seed data."""
    from support.tools_core import list_customer_orders

    result = list_customer_orders(customer_id=1)
    assert result["ok"] is True
    # Orders for customer 1: 1001, 1002, 1007, 1012
    assert len(result["orders"]) == 4


# ---------------------------------------------------------------------------
# check_refund_eligibility
# ---------------------------------------------------------------------------


def test_check_refund_eligibility_eligible():
    """Order 1002 (customer 1, delivered 4 days ago, no existing refund) → ELIGIBLE."""
    from support.tools_core import check_refund_eligibility

    result = check_refund_eligibility(customer_id=1, order_id=1002)
    assert result["ok"] is True
    assert result["eligible"] is True
    assert result["reason_code"] == "ELIGIBLE"


def test_check_refund_eligibility_outside_window():
    """Order 1003 (customer 2, delivered 55 days ago) → OUTSIDE_WINDOW."""
    from support.tools_core import check_refund_eligibility

    result = check_refund_eligibility(customer_id=2, order_id=1003)
    assert result["ok"] is True
    assert result["eligible"] is False
    assert result["reason_code"] == "OUTSIDE_WINDOW"


# ---------------------------------------------------------------------------
# create_refund_request
# ---------------------------------------------------------------------------


def test_create_refund_request_success():
    """Creating a refund for eligible order 1002 (customer 1) succeeds."""
    from support.tools_core import create_refund_request

    result = create_refund_request(
        customer_id=1, order_id=1002, reason="Item arrived damaged"
    )
    assert result["ok"] is True
    assert "refund_id" in result
    assert result["status"] == "pending"


def test_create_refund_request_duplicate_pending():
    """Submitting a second refund for order 1002 → REFUND_PENDING."""
    from support.tools_core import create_refund_request

    # First request succeeds.
    first = create_refund_request(
        customer_id=1, order_id=1002, reason="Item arrived damaged"
    )
    assert first["ok"] is True

    # Second request hits the REFUND_PENDING guard.
    second = create_refund_request(
        customer_id=1, order_id=1002, reason="Trying again"
    )
    assert second["ok"] is False
    assert second["error_code"] == "REFUND_PENDING"


def test_create_refund_request_already_refunded():
    """Order 1007 already has an approved refund in the seed data → ALREADY_REFUNDED."""
    from support.tools_core import create_refund_request

    result = create_refund_request(
        customer_id=1, order_id=1007, reason="Leaking bottle"
    )
    assert result["ok"] is False
    assert result["error_code"] == "ALREADY_REFUNDED"


# ---------------------------------------------------------------------------
# finalize_refund
# ---------------------------------------------------------------------------


def test_finalize_refund_approve_and_idempotency():
    """Approving a pending refund succeeds; repeated decision returns ALREADY_DECIDED."""
    from support.tools_core import finalize_refund

    # Seed data has order 1012 with a pending refund (id 2).
    res1 = finalize_refund(refund_id=2, decision="approved", decided_by="admin@support.com", note="Approved by manager")
    assert res1["ok"] is True
    assert res1["status"] == "approved"

    # Repeated decision should fail idempotently with ALREADY_DECIDED
    res2 = finalize_refund(refund_id=2, decision="rejected", decided_by="admin@support.com", note="Attempt override")
    assert res2["ok"] is False
    assert res2["error_code"] == "ALREADY_DECIDED"


def test_finalize_refund_not_found():
    """Non-existent refund ID returns NOT_FOUND error."""
    from support.tools_core import finalize_refund

    result = finalize_refund(refund_id=999999, decision="approved", decided_by="admin", note="")
    assert result["ok"] is False
    assert result["error_code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# search_kb
# ---------------------------------------------------------------------------


def test_search_kb_refund_policy():
    """Searching for 'refund policy' should return at least one relevant section."""
    from support.tools_core import search_kb

    result = search_kb("refund policy")
    assert result["ok"] is True
    assert len(result["results"]) >= 1

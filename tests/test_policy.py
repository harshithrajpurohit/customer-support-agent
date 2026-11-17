"""Unit tests for support.policy — pure functions, no DB required.

Covers every reason_code and both boundary conditions for the refund window.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from support.policy import PolicyResult, check_refund_eligibility

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

TODAY = date(2024, 6, 15)
CUSTOMER_ID = 1

# A minimal "delivered" order that is comfortably within the refund window.
_BASE_ORDER = {
    "id": 9000,
    "customer_id": CUSTOMER_ID,
    "item": "Test Item",
    "amount": 999.00,
    "status": "delivered",
    "order_date": TODAY - timedelta(days=10),
    "delivered_date": TODAY - timedelta(days=5),
}


def _order(**overrides) -> dict:
    """Return a copy of _BASE_ORDER with overrides applied."""
    return {**_BASE_ORDER, **overrides}


# ---------------------------------------------------------------------------
# Rule 1 — NOT_FOUND
# ---------------------------------------------------------------------------


def test_not_found_nonexistent_order():
    """Passing None for the order returns NOT_FOUND."""
    result = check_refund_eligibility(
        order=None,
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "NOT_FOUND"
    assert result.message == "Order not found."


def test_not_found_wrong_customer():
    """An order belonging to a different customer returns NOT_FOUND with the same message."""
    order = _order(customer_id=99)  # 99 ≠ CUSTOMER_ID
    result = check_refund_eligibility(
        order=order,
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "NOT_FOUND"
    assert result.message == "Order not found."


# ---------------------------------------------------------------------------
# Rule 2 — ORDER_CANCELLED
# ---------------------------------------------------------------------------


def test_order_cancelled():
    result = check_refund_eligibility(
        order=_order(status="cancelled"),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "ORDER_CANCELLED"


# ---------------------------------------------------------------------------
# Rule 3 — NOT_DELIVERED
# ---------------------------------------------------------------------------


def test_not_delivered_processing():
    result = check_refund_eligibility(
        order=_order(status="processing", delivered_date=None),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "NOT_DELIVERED"


def test_not_delivered_shipped():
    result = check_refund_eligibility(
        order=_order(status="shipped", delivered_date=None),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "NOT_DELIVERED"


# ---------------------------------------------------------------------------
# Rule 4a — ALREADY_REFUNDED
# ---------------------------------------------------------------------------


def test_already_refunded():
    approved_refund = {"id": 1, "order_id": 9000, "status": "approved"}
    result = check_refund_eligibility(
        order=_order(),
        existing_refunds=[approved_refund],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "ALREADY_REFUNDED"


# ---------------------------------------------------------------------------
# Rule 4b — REFUND_PENDING
# ---------------------------------------------------------------------------


def test_refund_pending():
    pending_refund = {"id": 2, "order_id": 9000, "status": "pending"}
    result = check_refund_eligibility(
        order=_order(),
        existing_refunds=[pending_refund],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "REFUND_PENDING"


# ---------------------------------------------------------------------------
# Rule 5 — OUTSIDE_WINDOW
# ---------------------------------------------------------------------------


def test_outside_window_15_days():
    """15 days after delivery → OUTSIDE_WINDOW."""
    delivered = TODAY - timedelta(days=15)
    result = check_refund_eligibility(
        order=_order(delivered_date=delivered),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "OUTSIDE_WINDOW"


# ---------------------------------------------------------------------------
# Rule 6 — ELIGIBLE (clean)
# ---------------------------------------------------------------------------


def test_eligible_clean():
    """Order delivered 5 days ago with no prior refunds → ELIGIBLE."""
    result = check_refund_eligibility(
        order=_order(),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is True
    assert result.reason_code == "ELIGIBLE"
    assert result.flags == []


# ---------------------------------------------------------------------------
# Rule 6 — ELIGIBLE with HIGH_VALUE flag
# ---------------------------------------------------------------------------


def test_eligible_high_value():
    """An order >= HIGH_VALUE_THRESHOLD gets the HIGH_VALUE flag."""
    from support.config import HIGH_VALUE_THRESHOLD

    result = check_refund_eligibility(
        order=_order(amount=HIGH_VALUE_THRESHOLD),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is True
    assert result.reason_code == "ELIGIBLE"
    assert "HIGH_VALUE" in result.flags


# ---------------------------------------------------------------------------
# Boundary: exactly 14 days → ELIGIBLE
# ---------------------------------------------------------------------------


def test_boundary_exactly_14_days_eligible():
    """Exactly 14 days since delivery is still within the window (> not >=)."""
    delivered = TODAY - timedelta(days=14)
    result = check_refund_eligibility(
        order=_order(delivered_date=delivered),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is True
    assert result.reason_code == "ELIGIBLE"


# ---------------------------------------------------------------------------
# Boundary: 15 days → OUTSIDE_WINDOW (already tested above, explicit name)
# ---------------------------------------------------------------------------


def test_boundary_15_days_outside_window():
    """15 days since delivery is outside the window."""
    delivered = TODAY - timedelta(days=15)
    result = check_refund_eligibility(
        order=_order(delivered_date=delivered),
        existing_refunds=[],
        customer_id=CUSTOMER_ID,
        today=TODAY,
    )
    assert result.eligible is False
    assert result.reason_code == "OUTSIDE_WINDOW"

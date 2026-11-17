"""Refund eligibility policy engine.

All functions in this module are **pure** — they perform no database calls and
no LLM calls.  They accept plain dicts (as returned by the DB layer) and
return a :class:`PolicyResult` Pydantic model.

Policy rules (applied in order):

1. Order not found or belongs to a different customer → ``NOT_FOUND``
2. Order status is ``cancelled`` → ``ORDER_CANCELLED``
3. Order status is ``processing`` or ``shipped`` → ``NOT_DELIVERED``
4. An existing refund for this order is ``approved`` → ``ALREADY_REFUNDED``
5. An existing refund for this order is ``pending``  → ``REFUND_PENDING``
6. Delivery was more than :data:`REFUND_WINDOW_DAYS` days ago → ``OUTSIDE_WINDOW``
7. All checks pass → ``ELIGIBLE`` (with optional ``HIGH_VALUE`` flag)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from support.config import HIGH_VALUE_THRESHOLD, REFUND_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class PolicyResult(BaseModel):
    """Result returned by the policy engine.

    Attributes
    ----------
    eligible:
        ``True`` when the order is eligible for a refund.
    reason_code:
        A machine-readable string identifying the outcome (e.g. ``ELIGIBLE``,
        ``OUTSIDE_WINDOW``).
    message:
        A human-readable explanation suitable for display to the customer.
    flags:
        Optional list of advisory flags (e.g. ``["HIGH_VALUE"]``) that
        downstream processes may use to route the request.
    """

    eligible: bool
    reason_code: str
    message: str
    flags: list[str] = []


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


def check_refund_eligibility(
    order: dict[str, Any] | None,
    existing_refunds: list[dict[str, Any]],
    customer_id: int,
    today: date,
) -> PolicyResult:
    """Determine whether a refund request should be approved.

    Rules are applied in strict priority order; the first matching rule wins.

    Parameters
    ----------
    order:
        The order record as a dict (keys: ``id``, ``customer_id``, ``status``,
        ``amount``, ``delivered_date``, …).  Pass ``None`` when the order does
        not exist in the database.
    existing_refunds:
        All refund records for this order (may be an empty list).
    customer_id:
        The ID of the authenticated customer making the request.  Used to
        prevent one customer from accessing another's orders.
    today:
        The reference date for the refund-window calculation.  Injected so
        that tests can fix the date without monkey-patching.

    Returns
    -------
    PolicyResult
        Always returned; never raises.
    """

    # Rule 1 — Order must exist and belong to this customer.
    # We deliberately return the same message for both cases to avoid leaking
    # information about whether an order exists at all.
    if order is None or order["customer_id"] != customer_id:
        return PolicyResult(
            eligible=False,
            reason_code="NOT_FOUND",
            message="Order not found.",
        )

    # Rule 2 — Cancelled orders are never refundable.
    if order["status"] == "cancelled":
        return PolicyResult(
            eligible=False,
            reason_code="ORDER_CANCELLED",
            message="This order was cancelled and is not eligible for a refund.",
        )

    # Rule 3 — Must be delivered before we can refund.
    if order["status"] in ("processing", "shipped"):
        return PolicyResult(
            eligible=False,
            reason_code="NOT_DELIVERED",
            message="This order has not been delivered yet.",
        )

    # Rule 4 — Check for existing refunds on this order.
    for refund in existing_refunds:
        if refund.get("status") == "approved":
            return PolicyResult(
                eligible=False,
                reason_code="ALREADY_REFUNDED",
                message="A refund has already been approved for this order.",
            )
    for refund in existing_refunds:
        if refund.get("status") == "pending":
            return PolicyResult(
                eligible=False,
                reason_code="REFUND_PENDING",
                message="A refund request is already pending for this order.",
            )

    # Rule 5 — Refund window check.
    # Exactly REFUND_WINDOW_DAYS days after delivery is still within the window
    # (> not >=).
    delivered_date: date = order["delivered_date"]
    # delivered_date may already be a date object or a string — normalise.
    if isinstance(delivered_date, str):
        delivered_date = date.fromisoformat(delivered_date)

    days_since_delivery = (today - delivered_date).days
    if days_since_delivery > REFUND_WINDOW_DAYS:
        return PolicyResult(
            eligible=False,
            reason_code="OUTSIDE_WINDOW",
            message=(
                f"This order was delivered more than {REFUND_WINDOW_DAYS} days ago "
                "and is no longer eligible for a refund."
            ),
        )

    # Rule 6 — Eligible.  Add HIGH_VALUE flag if applicable.
    flags: list[str] = []
    amount = float(order.get("amount", 0))
    if amount >= HIGH_VALUE_THRESHOLD:
        flags.append("HIGH_VALUE")

    return PolicyResult(
        eligible=True,
        reason_code="ELIGIBLE",
        message="This order is eligible for a refund.",
        flags=flags,
    )

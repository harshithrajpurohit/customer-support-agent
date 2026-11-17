"""Core tool functions for the Multi-Agent Customer Support System.

Each function:
- Accepts ``customer_id: int`` explicitly (enforces row-level ownership).
- Uses :func:`support.db.get_conn` for all DB access.
- Returns a plain ``dict`` — never raises; errors become ``{"ok": False, ...}``.
- Uses parameterised queries only.

These functions are called by LLM tool-call handlers and by the MCP server.
The exception is :func:`finalize_refund`, which is **admin-only** and must
never be exposed to the LLM or MCP layer.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from support import db, policy

# Path to the knowledge-base FAQ file (resolved relative to this module's
# package root so it works regardless of the working directory).
_KB_PATH: Path = Path(__file__).resolve().parent.parent.parent / "kb" / "faq.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(d: Any) -> str | None:
    """Return *d* as an ISO-8601 date string, or ``None`` if *d* is falsy."""
    if d is None:
        return None
    if isinstance(d, str):
        return d
    return d.isoformat()


def _estimated_state(status: str, delivered_date: Any) -> str:
    """Return a friendly human-readable delivery state description."""
    if status == "delivered" and delivered_date:
        d = delivered_date if isinstance(delivered_date, date) else date.fromisoformat(str(delivered_date))
        return f"Delivered on {d.strftime('%d %b %Y')}"
    if status == "shipped":
        return "In transit – estimated delivery in 1–3 days"
    if status == "processing":
        return "Order is being processed"
    if status == "cancelled":
        return "Order was cancelled"
    return status.capitalize()


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def get_order_status(customer_id: int, order_id: int) -> dict:
    """Fetch the status and details of a specific order.

    Returns the order's item, status, dates, amount, and a friendly
    ``estimated_state`` string.  If the order does not exist or belongs to a
    different customer, returns a *not-found* dict (same response for both
    cases to avoid information leakage).

    Parameters
    ----------
    customer_id:
        ID of the authenticated customer; used to enforce row-level ownership.
    order_id:
        The order to look up.

    Returns
    -------
    dict
        ``{"ok": True, "found": True, ...}`` on success,
        ``{"ok": True, "found": False, "message": "Order not found."}`` when
        the order is absent or belongs to another customer, or
        ``{"ok": False, "error_code": "DB_ERROR", "message": "..."}`` on
        unexpected errors.
    """
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, customer_id, item, status, order_date, "
                "       delivered_date, amount "
                "FROM orders WHERE id = %s",
                (order_id,),
            ).fetchone()
    except Exception as exc:
        return {"ok": False, "error_code": "DB_ERROR", "message": str(exc)}

    if row is None or row["customer_id"] != customer_id:
        return {"ok": True, "found": False, "message": "Order not found."}

    return {
        "ok": True,
        "found": True,
        "order_id": row["id"],
        "item": row["item"],
        "status": row["status"],
        "order_date": _iso(row["order_date"]),
        "delivered_date": _iso(row["delivered_date"]),
        "amount": float(row["amount"]) if row["amount"] is not None else None,
        "estimated_state": _estimated_state(row["status"], row["delivered_date"]),
    }


def list_customer_orders(customer_id: int) -> dict:
    """Return a summary list of recent orders for a customer.

    Parameters
    ----------
    customer_id:
        ID of the authenticated customer.

    Returns
    -------
    dict
        ``{"ok": True, "orders": [...]}`` where each element has keys
        ``id``, ``item``, ``status``, or
        ``{"ok": False, "error_code": "DB_ERROR", "message": "..."}`` on
        unexpected errors.
    """
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, item, status "
                "FROM orders "
                "WHERE customer_id = %s "
                "ORDER BY order_date DESC",
                (customer_id,),
            ).fetchall()
    except Exception as exc:
        return {"ok": False, "error_code": "DB_ERROR", "message": str(exc)}

    return {
        "ok": True,
        "orders": [
            {"id": r["id"], "item": r["item"], "status": r["status"]}
            for r in rows
        ],
    }


def check_refund_eligibility(customer_id: int, order_id: int) -> dict:
    """Check whether an order is eligible for a refund.

    Fetches the order and any existing refunds from the DB, then delegates to
    the pure-function policy engine.

    Parameters
    ----------
    customer_id:
        ID of the authenticated customer.
    order_id:
        The order to evaluate.

    Returns
    -------
    dict
        ``{"ok": True, "eligible": bool, "reason_code": str, "message": str,
        "flags": [...]}`` or
        ``{"ok": False, "error_code": "DB_ERROR", "message": "..."}`` on
        unexpected errors.
    """
    try:
        with db.get_conn() as conn:
            order = conn.execute(
                "SELECT id, customer_id, item, status, amount, "
                "       order_date, delivered_date "
                "FROM orders WHERE id = %s",
                (order_id,),
            ).fetchone()

            refunds = conn.execute(
                "SELECT id, order_id, status FROM refunds WHERE order_id = %s",
                (order_id,),
            ).fetchall()
    except Exception as exc:
        return {"ok": False, "error_code": "DB_ERROR", "message": str(exc)}

    result = policy.check_refund_eligibility(
        order=dict(order) if order else None,
        existing_refunds=[dict(r) for r in refunds],
        customer_id=customer_id,
        today=date.today(),
    )

    return {
        "ok": True,
        "eligible": result.eligible,
        "reason_code": result.reason_code,
        "message": result.message,
        "flags": result.flags,
    }


def create_refund_request(customer_id: int, order_id: int, reason: str) -> dict:
    """Create a pending refund request after re-validating policy.

    The policy check is re-run inside the same DB transaction to avoid
    race conditions.  If the order is eligible, a ``pending`` refund record is
    inserted.  This function does **not** approve or finalise the refund.

    Parameters
    ----------
    customer_id:
        ID of the authenticated customer.
    order_id:
        The order for which a refund is requested.
    reason:
        Customer-supplied reason for the refund.

    Returns
    -------
    dict
        ``{"ok": True, "refund_id": int, "status": "pending", "message": str}``
        on success, or ``{"ok": False, "error_code": str, "message": str}`` if
        the order is not eligible or an error occurs.
    """
    try:
        with db.get_conn() as conn:
            with conn.transaction():
                order = conn.execute(
                    "SELECT id, customer_id, item, status, amount, "
                    "       order_date, delivered_date "
                    "FROM orders WHERE id = %s FOR UPDATE",
                    (order_id,),
                ).fetchone()

                refunds = conn.execute(
                    "SELECT id, order_id, status FROM refunds WHERE order_id = %s",
                    (order_id,),
                ).fetchall()

                result = policy.check_refund_eligibility(
                    order=dict(order) if order else None,
                    existing_refunds=[dict(r) for r in refunds],
                    customer_id=customer_id,
                    today=date.today(),
                )

                if not result.eligible:
                    return {
                        "ok": False,
                        "error_code": result.reason_code,
                        "message": result.message,
                    }

                amount = float(order["amount"]) if order["amount"] is not None else 0.0
                row = conn.execute(
                    "INSERT INTO refunds (order_id, amount, reason, status, flags) "
                    "VALUES (%s, %s, %s, 'pending', %s) "
                    "RETURNING id",
                    (order_id, amount, reason, result.flags),
                ).fetchone()

    except Exception as exc:
        return {"ok": False, "error_code": "DB_ERROR", "message": str(exc)}

    return {
        "ok": True,
        "refund_id": row["id"],
        "status": "pending",
        "message": "Refund request submitted for review.",
    }


def finalize_refund(
    refund_id: int,
    decision: str,
    decided_by: str,
    note: str,
) -> dict:
    """Approve or reject a pending refund (admin-only).

    **This function must never be exposed to the LLM or MCP layer.**
    It is called only by the admin API after verifying ``ADMIN_TOKEN``.

    The operation is idempotent: calling it again on an already-decided refund
    returns a ``409``-style error dict instead of raising.

    Parameters
    ----------
    refund_id:
        The refund record to update.
    decision:
        Either ``"approved"`` or ``"rejected"``.
    decided_by:
        Identifier of the admin who made the decision (e.g. email or username).
    note:
        Optional free-text explanation stored alongside the decision.

    Returns
    -------
    dict
        ``{"ok": True, "refund_id": int, "status": str}`` on success,
        ``{"ok": False, "error_code": "ALREADY_DECIDED", ...}`` if the refund
        was already finalised, or
        ``{"ok": False, "error_code": "NOT_FOUND", ...}`` if the ID is unknown.
    """
    if decision not in ("approved", "rejected"):
        return {
            "ok": False,
            "error_code": "INVALID_DECISION",
            "message": "decision must be 'approved' or 'rejected'.",
        }

    try:
        with db.get_conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT id, status FROM refunds WHERE id = %s FOR UPDATE",
                    (refund_id,),
                ).fetchone()

                if row is None:
                    return {
                        "ok": False,
                        "error_code": "NOT_FOUND",
                        "message": f"Refund {refund_id} not found.",
                    }

                if row["status"] != "pending":
                    return {
                        "ok": False,
                        "error_code": "ALREADY_DECIDED",
                        "message": (
                            f"Refund {refund_id} has already been {row['status']}. "
                            "No changes were made."
                        ),
                    }

                conn.execute(
                    "UPDATE refunds "
                    "SET status = %s, decided_by = %s, decided_at = NOW(), "
                    "    decision_note = %s "
                    "WHERE id = %s",
                    (decision, decided_by, note, refund_id),
                )

    except Exception as exc:
        return {"ok": False, "error_code": "DB_ERROR", "message": str(exc)}

    return {"ok": True, "refund_id": refund_id, "status": decision}


def search_kb(query: str) -> dict:
    """Search the knowledge-base FAQ for sections relevant to *query*.

    Splits ``kb/faq.md`` by ``##`` headings, scores each section by counting
    how many (unique, case-insensitive) query words appear in it, and returns
    the top two scoring sections.

    Parameters
    ----------
    query:
        A free-text search string.

    Returns
    -------
    dict
        ``{"ok": True, "results": [{"heading": str, "content": str}, ...]}``
        with up to 2 results, or
        ``{"ok": True, "results": [], "message": "No relevant information found."}``
        when nothing matches.
    """
    try:
        text = _KB_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {
            "ok": False,
            "error_code": "KB_NOT_FOUND",
            "message": f"Knowledge base not found at {_KB_PATH}.",
        }

    # Split on ## headings (level-2); keep intro section if any.
    raw_sections = re.split(r"(?m)^##\s+", text)
    sections: list[dict[str, str]] = []
    for i, chunk in enumerate(raw_sections):
        chunk = chunk.strip()
        if not chunk:
            continue
        if i == 0:
            # Text before the first ## heading — treat as a preamble.
            sections.append({"heading": "Introduction", "content": chunk})
        else:
            newline = chunk.find("\n")
            if newline == -1:
                sections.append({"heading": chunk, "content": ""})
            else:
                sections.append(
                    {
                        "heading": chunk[:newline].strip(),
                        "content": chunk[newline:].strip(),
                    }
                )

    # Score each section.
    query_words = set(re.findall(r"\w+", query.lower()))
    scored: list[tuple[int, dict[str, str]]] = []
    for sec in sections:
        combined = (sec["heading"] + " " + sec["content"]).lower()
        section_words = set(re.findall(r"\w+", combined))
        score = len(query_words & section_words)
        if score > 0:
            scored.append((score, sec))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = [s for _, s in scored[:2]]

    if not top:
        return {
            "ok": True,
            "results": [],
            "message": "No relevant information found.",
        }

    return {"ok": True, "results": top}

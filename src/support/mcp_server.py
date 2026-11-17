"""MCP server exposing support tools via the Model Context Protocol.

This server runs over stdio and exposes tools for order tracking, refund
eligibility checks, refund request creation, and knowledge-base search.
Admin-only actions like `finalize_refund` are strictly excluded from this server.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure package root is in sys.path when running as a standalone script
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Import FastMCP from MCP SDK (with fallback for MCP 2.x where FastMCP was renamed to MCPServer)
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    import types
    from mcp.server.mcpserver import MCPServer as FastMCP

    # Provide module shim so any import of mcp.server.fastmcp succeeds
    _mod = types.ModuleType("mcp.server.fastmcp")
    _mod.FastMCP = FastMCP
    sys.modules["mcp.server.fastmcp"] = _mod

from support import tools_core

# Initialize FastMCP server
mcp = FastMCP("SupportTools")


@mcp.tool()
def get_order_status(customer_id: int, order_id: int) -> dict:
    """Fetch status and delivery details for a customer's specific order.

    Returns order details including item, status, amount, and estimated state.
    Returns a generic not-found message if the order is missing or belongs to another customer.
    """
    return tools_core.get_order_status(customer_id=customer_id, order_id=order_id)


@mcp.tool()
def list_customer_orders(customer_id: int) -> dict:
    """List recent orders for a customer with order IDs, item names, and current statuses."""
    return tools_core.list_customer_orders(customer_id=customer_id)


@mcp.tool()
def check_refund_eligibility(customer_id: int, order_id: int) -> dict:
    """Check whether a customer's delivered order is eligible for a refund.

    Evaluates return policy rules (such as 14-day window and existing refund status)
    and returns eligibility status, reason code, message, and policy flags.
    """
    return tools_core.check_refund_eligibility(customer_id=customer_id, order_id=order_id)


@mcp.tool()
def create_refund_request(customer_id: int, order_id: int, reason: str) -> dict:
    """Create a pending refund request for an eligible order with a customer-supplied reason.

    Re-checks policy inside a transaction and records a pending refund.
    Note: Refunds require human administrative approval before money moves.
    """
    return tools_core.create_refund_request(customer_id=customer_id, order_id=order_id, reason=reason)


@mcp.tool()
def search_kb(query: str) -> dict:
    """Search the support knowledge base FAQ for answers to policy and technical questions.

    Returns the top matching FAQ sections or an empty result if no match is found.
    """
    return tools_core.search_kb(query=query)


if __name__ == "__main__":
    mcp.run(transport="stdio")

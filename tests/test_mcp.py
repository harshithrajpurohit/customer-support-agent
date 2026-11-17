"""Unit and integration tests for MCP server and MCP client (Milestone 5)."""

from __future__ import annotations

import pytest

from support import mcp_client
from support.mcp_client import AGENT_ALLOWED_TOOLS, MCPClient


@pytest.fixture(scope="module")
def client():
    """Start MCPClient once for this test module and close at teardown."""
    c = MCPClient()
    c.start()
    yield c
    c.close()


def test_registered_tools(client: MCPClient) -> None:
    """Verify only the 5 specified tools are registered and finalize_refund is absent."""
    tools = client.get_tools()
    tool_names = {t["function"]["name"] for t in tools}
    expected = {
        "get_order_status",
        "list_customer_orders",
        "check_refund_eligibility",
        "create_refund_request",
        "search_kb",
    }
    assert tool_names == expected
    assert "finalize_refund" not in tool_names


def test_customer_id_stripped_from_schemas(client: MCPClient) -> None:
    """CRITICAL SECURITY TEST: customer_id must not appear in properties or required."""
    tools = client.get_tools()
    for tool in tools:
        name = tool["function"]["name"]
        params = tool["function"]["parameters"]
        props = params.get("properties", {})
        req = params.get("required", [])

        assert "customer_id" not in props, f"customer_id found in properties of {name}"
        assert "customer_id" not in req, f"customer_id found in required of {name}"


def test_per_agent_allow_lists(client: MCPClient) -> None:
    """Verify per-agent role filtering matches the prompt specification."""
    orders_tools = {t["function"]["name"] for t in client.get_tools(role="orders")}
    assert orders_tools == {"list_customer_orders", "get_order_status"}

    refund_tools = {t["function"]["name"] for t in client.get_tools(role="refund")}
    assert refund_tools == {
        "list_customer_orders",
        "get_order_status",
        "check_refund_eligibility",
        "create_refund_request",
    }

    tech_tools = {t["function"]["name"] for t in client.get_tools(role="technical")}
    assert tech_tools == {"search_kb"}


def test_get_order_status_success(client: MCPClient) -> None:
    """Test calling get_order_status for order 1001 with customer 1."""
    res = client.call_tool(
        name="get_order_status",
        args={"order_id": 1001},
        customer_id=1,
        role="orders",
    )
    assert res.get("ok") is True
    assert res.get("found") is True
    assert res.get("order_id") == 1001
    assert res.get("item") == "Wireless Earbuds"
    assert res.get("status") == "shipped"


def test_security_customer_id_injection_overwrites_spoof(client: MCPClient) -> None:
    """Test that customer_id provided in args by the caller/LLM is ignored."""
    res = client.call_tool(
        name="get_order_status",
        args={"order_id": 1001, "customer_id": 999},  # Spoofed customer_id
        customer_id=1,  # Verified customer_id
        role="orders",
    )
    assert res.get("ok") is True
    assert res.get("found") is True
    assert res.get("order_id") == 1001


def test_role_enforcement_blocks_forbidden_tools(client: MCPClient) -> None:
    """Test that calling a tool not on the agent's allow-list returns FORBIDDEN_TOOL."""
    res = client.call_tool(
        name="get_order_status",
        args={"order_id": 1001},
        customer_id=1,
        role="technical",
    )
    assert res.get("ok") is False
    assert res.get("error_code") == "FORBIDDEN_TOOL"


def test_search_kb_tool(client: MCPClient) -> None:
    """Test searching the knowledge base through technical role."""
    res = client.call_tool(
        name="search_kb",
        args={"query": "refund policy"},
        customer_id=1,
        role="technical",
    )
    assert res.get("ok") is True
    assert len(res.get("results", [])) > 0

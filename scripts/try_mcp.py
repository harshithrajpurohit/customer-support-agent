"""Verification script for Milestone 5: MCP Server and Client.

Tests:
1. MCP server startup and tool discovery.
2. Security verification: `customer_id` is stripped from all OpenAI/Groq schemas.
3. Allow-list verification for `orders`, `refund`, and `technical` roles.
4. Calling `get_order_status` for order 1001 with customer 1.
5. Security rule verification: LLM cannot spoof customer_id; verified session ID is injected.
6. Role permission enforcement: forbidden tools are rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from support import mcp_client


def main() -> None:
    print("=" * 60)
    print("Milestone 5 Verification: MCP Server and Client")
    print("=" * 60)

    # 1. Start MCP Client
    print("\n1. Starting MCP client and connecting via stdio...")
    client = mcp_client.get_client()
    client.start()
    print("   [PASS] MCP client started successfully.")

    try:
        # 2. Tool Discovery and customer_id Stripping Check
        print("\n2. Discovering tools and verifying customer_id stripping...")
        all_tools = client.get_tools()
        tool_names = [t["function"]["name"] for t in all_tools]
        print(f"   Registered tools ({len(tool_names)}): {tool_names}")

        expected_tools = {
            "get_order_status",
            "list_customer_orders",
            "check_refund_eligibility",
            "create_refund_request",
            "search_kb",
        }
        assert set(tool_names) == expected_tools, (
            f"Expected {expected_tools}, got {set(tool_names)}"
        )
        assert "finalize_refund" not in tool_names, (
            "CRITICAL SECURITY FAIL: finalize_refund must NOT be registered!"
        )

        for tool in all_tools:
            name = tool["function"]["name"]
            params = tool["function"]["parameters"]
            props = params.get("properties", {})
            req = params.get("required", [])

            assert "customer_id" not in props, (
                f"SECURITY FAIL: customer_id found in properties of {name}"
            )
            assert "customer_id" not in req, (
                f"SECURITY FAIL: customer_id found in required list of {name}"
            )
            print(f"   - {name}: properties={list(props.keys())}, required={req}")

        print("   [PASS] customer_id is completely stripped from all tool schemas exposed to LLM.")

        # 3. Verify Per-Agent Role Filtering
        print("\n3. Verifying per-agent tool allow-lists...")
        orders_tools = [t["function"]["name"] for t in client.get_tools(role="orders")]
        refund_tools = [t["function"]["name"] for t in client.get_tools(role="refund")]
        tech_tools = [t["function"]["name"] for t in client.get_tools(role="technical")]

        print(f"   Orders agent tools:    {orders_tools}")
        print(f"   Refund agent tools:    {refund_tools}")
        print(f"   Technical agent tools: {tech_tools}")

        assert set(orders_tools) == {"list_customer_orders", "get_order_status"}
        assert set(refund_tools) == {
            "list_customer_orders",
            "get_order_status",
            "check_refund_eligibility",
            "create_refund_request",
        }
        assert set(tech_tools) == {"search_kb"}
        print("   [PASS] Per-agent tool allow-lists match specification.")

        # 4. Call get_order_status for order 1001 with customer 1
        print("\n4. Calling get_order_status(customer_id=1, order_id=1001)...")
        res = client.call_tool(
            name="get_order_status",
            args={"order_id": 1001},
            customer_id=1,
            role="orders",
        )
        print("   Result:", res)
        assert res.get("ok") is True, f"Tool call failed: {res}"
        assert res.get("found") is True, f"Order not found: {res}"
        assert res.get("order_id") == 1001, f"Wrong order_id: {res.get('order_id')}"
        assert res.get("item") == "Wireless Earbuds", f"Unexpected item: {res.get('item')}"
        assert res.get("status") == "shipped", f"Unexpected status: {res.get('status')}"
        print(f"   [PASS] get_order_status returned status '{res.get('status')}' for order 1001.")

        # 5. Security Rule: Spoofed customer_id in args must be ignored/overwritten
        print("\n5. Testing security rule: LLM passing spoofed customer_id...")
        # LLM attempts to pass customer_id=2 in args, but session customer_id is 1
        res_spoof = client.call_tool(
            name="get_order_status",
            args={"order_id": 1001, "customer_id": 2},
            customer_id=1,
            role="orders",
        )
        assert res_spoof.get("ok") is True
        assert res_spoof.get("found") is True
        assert res_spoof.get("order_id") == 1001
        print("   [PASS] Spoofed customer_id in args was overridden by session customer_id.")

        # Cross-customer access check: customer 2 trying to look up customer 1's order 1001
        res_cross = client.call_tool(
            name="get_order_status",
            args={"order_id": 1001},
            customer_id=2,
            role="orders",
        )
        assert res_cross.get("ok") is True
        assert res_cross.get("found") is False
        print("   [PASS] Cross-customer access returned generic not-found as expected.")

        # 6. Role Enforcement: Forbidden tool must be rejected
        print("\n6. Testing role enforcement: technical agent calling get_order_status...")
        res_forbidden = client.call_tool(
            name="get_order_status",
            args={"order_id": 1001},
            customer_id=1,
            role="technical",
        )
        assert res_forbidden.get("ok") is False
        assert res_forbidden.get("error_code") == "FORBIDDEN_TOOL"
        print("   [PASS] Forbidden tool call was blocked with FORBIDDEN_TOOL.")

        # 7. Test Technical Tool: search_kb
        print("\n7. Testing search_kb via technical role...")
        res_kb = client.call_tool(
            name="search_kb",
            args={"query": "refund policy"},
            customer_id=1,
            role="technical",
        )
        assert res_kb.get("ok") is True
        assert len(res_kb.get("results", [])) > 0
        print(f"   [PASS] search_kb returned {len(res_kb['results'])} matching sections.")

        print("\n" + "=" * 60)
        print("ALL MILESTONE 5 VERIFICATION CHECKS PASSED!")
        print("=" * 60)

    finally:
        print("\nClosing MCP client...")
        client.close()
        print("MCP client closed.")


if __name__ == "__main__":
    main()

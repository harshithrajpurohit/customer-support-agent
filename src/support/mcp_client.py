"""MCP client for the Multi-Agent Customer Support System.

Connects to `src/support/mcp_server.py` via stdio transport using the official
MCP SDK client (`mcp.client.stdio.stdio_client`, `ClientSession`).

Key capabilities:
- Discovers registered MCP tools and translates them to OpenAI/Groq function calling format.
- CRITICAL SECURITY RULE: `customer_id` is completely stripped from schemas exposed
  to the LLM, and injected securely from the session on every tool invocation.
- Enforces per-agent tool allow-lists (orders, refund, technical).
- Integrates with Langfuse tracing (`tracing.span`).
- Supports both synchronous and asynchronous execution cleanly across all runtimes.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from support import tracing

logger = logging.getLogger(__name__)

# Default path to mcp_server.py
_DEFAULT_SERVER_PATH: Path = Path(__file__).resolve().parent / "mcp_server.py"

# Per-agent tool allow-lists as defined in requirements
AGENT_ALLOWED_TOOLS: dict[str, set[str]] = {
    "orders": {"list_customer_orders", "get_order_status"},
    "orders_agent": {"list_customer_orders", "get_order_status"},
    "refund": {
        "list_customer_orders",
        "get_order_status",
        "check_refund_eligibility",
        "create_refund_request",
    },
    "refunds": {
        "list_customer_orders",
        "get_order_status",
        "check_refund_eligibility",
        "create_refund_request",
    },
    "refund_agent": {
        "list_customer_orders",
        "get_order_status",
        "check_refund_eligibility",
        "create_refund_request",
    },
    "technical": {"search_kb"},
    "technical_agent": {"search_kb"},
}


class ToolResult(dict):
    """A dictionary subclass that is also awaitable.

    Allows callers to either use the tool result directly as a dict in synchronous code
    or await it seamlessly in async code without needing type checks or conversions.
    """

    def __await__(self):
        async def _wrapper() -> ToolResult:
            return self

        return _wrapper().__await__()


def _convert_tool_to_openai(tool: Any) -> dict[str, Any]:
    """Convert an MCP Tool definition to OpenAI function-calling format.

    CRITICAL SECURITY RULE:
    `customer_id` is stripped from both `properties` and `required` lists
    so that the LLM is never aware of or asked to supply customer IDs.
    """
    raw_schema = (
        copy.deepcopy(tool.input_schema)
        if hasattr(tool, "input_schema") and tool.input_schema
        else {}
    )

    properties = dict(raw_schema.get("properties", {}))
    # Strip customer_id from parameters properties
    properties.pop("customer_id", None)

    # Strip customer_id from required list
    required = [r for r in raw_schema.get("required", []) if r != "customer_id"]

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


def _parse_tool_result(raw_result: Any) -> Any:
    """Parse an MCP CallToolResult into a JSON-serializable Python structure."""
    # 1. Check structured_content
    sc = getattr(raw_result, "structured_content", None)
    if sc is not None:
        if isinstance(sc, dict) and "result" in sc and len(sc) == 1:
            return sc["result"]
        return sc

    # 2. Check content blocks
    content = getattr(raw_result, "content", None)
    if content and len(content) > 0:
        first = content[0]
        text = getattr(first, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except Exception:
                if getattr(raw_result, "is_error", False):
                    return {"ok": False, "error_code": "TOOL_ERROR", "message": text}
                return {"ok": True, "text": text}
        return {"ok": True, "data": str(first)}

    if getattr(raw_result, "is_error", False):
        return {
            "ok": False,
            "error_code": "TOOL_ERROR",
            "message": "Tool execution returned an error.",
        }

    return {"ok": True}


class MCPClient:
    """Client for managing the MCP server subprocess and invoking tools.

    Runs an event loop in a dedicated background daemon thread so that stdio
    sessions and anyio task groups remain in the same task lifecycle while
    offering synchronous, awaitable, and thread-safe APIs to callers.
    """

    def __init__(self, server_path: Path | str | None = None) -> None:
        self._server_path: Path = (
            Path(server_path).resolve() if server_path else _DEFAULT_SERVER_PATH
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queue: asyncio.Queue | None = None
        self._ready_event = threading.Event()
        self._session: ClientSession | None = None
        self._raw_tools_by_name: dict[str, Any] = {}
        self._openai_tools: dict[str, dict[str, Any]] = {}
        self._is_started: bool = False
        self._startup_error: Exception | None = None
        self._lock = threading.Lock()

    def _build_env(self) -> dict[str, str]:
        """Build environment variables ensuring package root is on PYTHONPATH."""
        env = dict(os.environ)
        src_dir = str(Path(__file__).resolve().parent.parent)
        existing = env.get("PYTHONPATH", "")
        if existing:
            env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}"
        else:
            env["PYTHONPATH"] = src_dir
        return env

    def _worker_thread(self) -> None:
        """Entry point for the background event loop thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker())
        finally:
            self._loop.close()

    async def _worker(self) -> None:
        """Worker task managing the stdio client, session, and dispatch queue."""
        self._queue = asyncio.Queue()
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(self._server_path)],
            env=self._build_env(),
        )
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._session = session
                    await self._discover_tools(session)
                    self._ready_event.set()

                    while True:
                        item = await self._queue.get()
                        if item is None:
                            # Shutdown signal
                            break
                        func, args, fut = item
                        try:
                            result = await func(*args)
                            if not fut.done():
                                self._loop.call_soon_threadsafe(fut.set_result, result)
                        except Exception as exc:
                            if not fut.done():
                                self._loop.call_soon_threadsafe(
                                    fut.set_exception, exc
                                )
        except Exception as exc:
            logger.exception("MCP server worker failed: %s", exc)
            self._startup_error = exc
            self._ready_event.set()

    async def _discover_tools(self, session: ClientSession) -> None:
        """Fetch and cache tool definitions from the server."""
        tool_list = await session.list_tools()
        self._raw_tools_by_name = {t.name: t for t in tool_list.tools}
        self._openai_tools = {
            t.name: _convert_tool_to_openai(t) for t in tool_list.tools
        }

    def start(self, timeout: float = 15.0) -> None:
        """Start the MCP client and background server subprocess."""
        with self._lock:
            if self._is_started:
                return
            self._ready_event.clear()
            self._startup_error = None
            self._thread = threading.Thread(target=self._worker_thread, daemon=True)
            self._thread.start()

            if not self._ready_event.wait(timeout=timeout):
                raise TimeoutError("MCP client timed out waiting for server startup.")
            if self._startup_error is not None:
                raise RuntimeError(
                    f"Failed to start MCP server: {self._startup_error}"
                ) from self._startup_error

            self._is_started = True

    def close(self, timeout: float = 10.0) -> None:
        """Shut down the MCP server subprocess and client session cleanly."""
        with self._lock:
            if not self._is_started:
                return
            if self._loop and self._queue:
                asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
            if self._thread:
                self._thread.join(timeout=timeout)
            self._session = None
            self._is_started = False

    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> MCPClient:
        self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()

    def get_tools(self, role: str | None = None) -> list[dict[str, Any]]:
        """Return tool definitions in Groq/OpenAI format, filtered by agent role.

        CRITICAL: All returned schemas have `customer_id` stripped.

        Parameters
        ----------
        role:
            Agent role name (e.g. 'orders', 'refund', 'technical'). If None,
            all registered tools are returned.
        """
        if not self._is_started:
            self.start()

        if role is None:
            return list(self._openai_tools.values())

        role_key = role.strip().lower()
        allowed = AGENT_ALLOWED_TOOLS.get(role_key)
        if allowed is None:
            raise ValueError(
                f"Unknown agent role '{role}'. Valid roles are: 'orders', 'refund', 'technical'."
            )

        return [
            tool
            for name, tool in self._openai_tools.items()
            if name in allowed
        ]

    def _takes_customer_id(self, tool_name: str) -> bool:
        """Check whether the underlying MCP tool expects `customer_id`."""
        raw = self._raw_tools_by_name.get(tool_name)
        if raw and hasattr(raw, "input_schema") and raw.input_schema:
            return "customer_id" in raw.input_schema.get("properties", {})
        return False

    def _execute_mcp_call(
        self, name: str, args: dict[str, Any], timeout: float = 30.0
    ) -> Any:
        """Dispatch a tool call to the background worker loop."""
        if not self._is_started:
            self.start()

        fut: concurrent.futures.Future = concurrent.futures.Future()

        async def _call() -> Any:
            assert self._session is not None
            return await self._session.call_tool(name=name, arguments=args)

        assert self._loop is not None
        assert self._queue is not None
        asyncio.run_coroutine_threadsafe(self._queue.put((_call, (), fut)), self._loop)
        return fut.result(timeout=timeout)

    def call_tool(
        self,
        name: str,
        args: dict[str, Any] | None,
        customer_id: int,
        role: str | None = None,
        parent_span: Any = None,
    ) -> ToolResult:
        """Call an MCP tool with verified customer_id injected.

        Enforces security policy:
        1. Checks agent allow-list for `role` if specified.
        2. Injects verified `customer_id` into parameters for any tool expecting it.
        3. Overwrites/ignores any customer_id supplied in `args`.
        4. Traces execution with `tracing.span()`.
        5. Returns a JSON-serializable ToolResult (dict subclass, also awaitable).
        """
        if not self._is_started:
            self.start()

        # Enforce allow-list
        if role is not None:
            role_key = role.strip().lower()
            allowed = AGENT_ALLOWED_TOOLS.get(role_key)
            if allowed is not None and name not in allowed:
                return ToolResult(
                    {
                        "ok": False,
                        "error_code": "FORBIDDEN_TOOL",
                        "message": f"Tool '{name}' is not permitted for agent role '{role}'.",
                    }
                )

        # Check tool exists
        if name not in self._raw_tools_by_name:
            return ToolResult(
                {
                    "ok": False,
                    "error_code": "UNKNOWN_TOOL",
                    "message": f"Tool '{name}' is not registered on the MCP server.",
                }
            )

        # Prepare safe arguments
        safe_args = dict(args) if args else {}
        if self._takes_customer_id(name):
            safe_args["customer_id"] = int(customer_id)
        else:
            safe_args.pop("customer_id", None)

        # Traced execution
        try:
            with tracing.span(
                parent_span,
                f"tool:{name}",
                input={"name": name, "args": safe_args, "customer_id": customer_id},
            ):
                raw_result = self._execute_mcp_call(name, safe_args)
                parsed = _parse_tool_result(raw_result)
                if isinstance(parsed, dict):
                    return ToolResult(parsed)
                return ToolResult({"ok": True, "result": parsed})
        except Exception as exc:
            logger.exception("Error executing MCP tool '%s': %s", name, exc)
            return ToolResult(
                {"ok": False, "error_code": "MCP_ERROR", "message": str(exc)}
            )

    async def call_tool_async(
        self,
        name: str,
        args: dict[str, Any] | None,
        customer_id: int,
        role: str | None = None,
        parent_span: Any = None,
    ) -> ToolResult:
        """Asynchronously call an MCP tool without blocking the caller's event loop."""
        return await asyncio.to_thread(
            self.call_tool,
            name=name,
            args=args,
            customer_id=customer_id,
            role=role,
            parent_span=parent_span,
        )


# ---------------------------------------------------------------------------
# Module-level singleton and convenience functions
# ---------------------------------------------------------------------------

_default_client: MCPClient | None = None
_client_lock = threading.Lock()


def get_client() -> MCPClient:
    """Return the process-wide shared MCPClient instance."""
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = MCPClient()
        return _default_client


def start() -> None:
    """Start the shared MCPClient instance."""
    get_client().start()


def close() -> None:
    """Close the shared MCPClient instance."""
    global _default_client
    with _client_lock:
        if _default_client is not None:
            _default_client.close()
            _default_client = None


def get_tools(role: str | None = None) -> list[dict[str, Any]]:
    """Get tool definitions filtered by agent role with customer_id stripped."""
    return get_client().get_tools(role=role)


def call_tool(
    name: str,
    args: dict[str, Any] | None,
    customer_id: int,
    role: str | None = None,
    parent_span: Any = None,
) -> ToolResult:
    """Call an MCP tool with verified customer_id injected."""
    return get_client().call_tool(
        name=name,
        args=args,
        customer_id=customer_id,
        role=role,
        parent_span=parent_span,
    )


async def call_tool_async(
    name: str,
    args: dict[str, Any] | None,
    customer_id: int,
    role: str | None = None,
    parent_span: Any = None,
) -> ToolResult:
    """Asynchronously call an MCP tool with verified customer_id injected."""
    return await get_client().call_tool_async(
        name=name,
        args=args,
        customer_id=customer_id,
        role=role,
        parent_span=parent_span,
    )

# Architecture & Implementation Decisions

## Decision 1: MCP 1.x / 2.x FastMCP Compatibility Shim
- **Decision:** Support both MCP 1.x (`mcp.server.fastmcp.FastMCP`) and MCP 2.x (`mcp.server.mcpserver.MCPServer`) using a fallback import and dynamic module injection into `sys.modules["mcp.server.fastmcp"]`.
- **Alternatives Considered:** Pinning `mcp<2` in `pyproject.toml`.
- **Reason:** The project's `pyproject.toml` specifies `mcp>=2.3.0`, where FastMCP was refactored into `MCPServer`. The specification in `prompt.md` requires `from mcp.server.fastmcp import FastMCP`. The compatibility shim allows both modern `mcp>=2.3.0` and legacy `mcp<2` code to function identically with zero dependency conflicts.

## Decision 2: Background Daemon Worker for MCPClient
- **Decision:** Implement `MCPClient` with a dedicated background thread running an isolated `asyncio` event loop. All `stdio_client` and `ClientSession` context managers are entered and managed within a single long-running async worker task. Calls are dispatched via an `asyncio.Queue` and returned via concurrent futures.
- **Alternatives Considered:**
  1. Spawning a new stdio client per request (disallowed by prompt: "Keep the MCP session alive for the process lifetime").
  2. Running `AsyncExitStack` across different coroutines (fails with AnyIO's `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`).
- **Reason:** AnyIO task groups require entering and exiting within the exact same asyncio task. A worker task ensures clean lifetime management for the stdio subprocess, handles process teardown cleanly without orphaned processes, and allows seamless synchronous (`ToolResult` dict) and asynchronous (`await call_tool` / `call_tool_async`) usage across FastAPI lifespan, LangGraph nodes, and CLI scripts.

## Decision 3: Dual Sync/Awaitable ToolResult
- **Decision:** Return a `ToolResult` dictionary subclass that implements `__await__`.
- **Alternatives Considered:** Separate incompatible sync and async functions that raise errors if awaited or not awaited.
- **Reason:** Simplifies integration across heterogeneous agents. Synchronous agents can treat the return value directly as a `dict`, while asynchronous agents can `await` it without breaking.

## Decision 4: Customer ID Stripping and Injection
- **Decision:** Strip `customer_id` from `parameters.properties` and `parameters.required` on all tool schemas exposed to the LLM. In `call_tool()`, overwrite any argument named `customer_id` with the authenticated session `customer_id`.
- **Alternatives Considered:** Letting the LLM generate `customer_id` and validating it afterwards.
- **Reason:** Defense-in-depth against prompt injection and cross-tenant data leakage. The model cannot choose or see other customers' IDs.

## Decision 5: LangGraph Checkpointer Strategy (PostgresSaver with MemorySaver Fallback)
- **Decision:** Use `PostgresSaver.from_conn_string(DATABASE_URL)` as the primary checkpointer with an automatic fallback to `MemorySaver()`. Checkpoint tables are established via `setup()`.
- **Alternatives Considered:** Using only `MemorySaver` or exclusively `PostgresSaver`.
- **Reason:** `PostgresSaver` ensures persistence across server restarts and allows human-in-the-loop threads to be resumed asynchronously by administrative endpoints. The graceful fallback to `MemorySaver` ensures unit test suites and offline verification scripts run seamlessly without requiring an active PostgreSQL instance.

## Decision 6: Human-in-the-Loop Approval Interrupt Pattern
- **Decision:** Use LangGraph's native `interrupt` function inside `approval_gate` to pause execution when a refund request is created. The awaiting-approval response is persisted to chat history, and the graph remains paused at the gate checkpoint until resumed with `Command(resume=decision_payload)`.
- **Alternatives Considered:** Polling loop, external worker queue, or immediate resolution.
- **Reason:** Guarantees money safety: no refund can ever be finalized without human review. The graph thread pauses deterministically and resumes with complete context upon admin approval or rejection.

## Decision 7: Deterministic Escalation and Failed Attempt Tracking
- **Decision:** Track consecutive unresolvable queries in `failed_attempts` via `SessionStore` and `GraphState`. If `sentiment == "angry"`, `wants_human is True`, `intent == "complaint"`, or `failed_attempts >= MAX_FAILED_ATTEMPTS (2)`, route deterministically to `handoff`.
- **Alternatives Considered:** Relying on the LLM to decide when to escalate.
- **Reason:** Deterministic state machine transitions guarantee that frustrated or angry customers and unresolvable issues are escalated reliably within the same turn without looping or hallucinating workarounds.

## Decision 8: FastAPI Backend Lifecycle, Rate Limiting, and Admin Security
- **Decision:** 
  1. Manage background resources (DB pool, MCP client process, Langfuse tracing flush) cleanly in FastAPI's `lifespan` context manager.
  2. Protect endpoints against abuse with a sliding-window rate limiter (20 messages/minute per session ID) and input length validation (1000 characters).
  3. Enforce constant-time token comparison for admin routes (`X-Admin-Token` matching `config.ADMIN_TOKEN`), returning 401 for missing tokens and 403 for invalid tokens.
  4. Ensure idempotent refund decisions with 409 Conflict returns on duplicates, while resuming LangGraph threads via `graph.resume_approval` and synchronizing customer notification messages to `chat_messages` for 3-second widget polling.
- **Alternatives Considered:** Basic auth, OAuth2/JWT (disallowed by prompt as out-of-scope for student edition), per-request MCP connections.
- **Reason:** Follows production security best practices while keeping the implementation focused, deterministic, and fully compatible with the React frontend and test runners.

## Decision 9: Evaluation Suite Design, Prompt Clarifications, and Adversarial Guardrails
- **Decision:**
  1. **Router Intent Clarification:** Explicitly distinguished between queries concerning specific customer orders (`order` / `refund`) and general FAQ inquiries (`technical`). General questions about policy, delivery timelines, or return windows route to `technical` (calling `search_kb`), while specific inquiries about order tracking or returning an item route to `orders_agent` / `refund_agent`.
  2. **Technical Agent Mandatory Tool Invocation:** Required the technical agent to always invoke `search_kb` for every query, preventing the LLM from answering without knowledge base ground truth or failing to search for unusual/impossible queries.
  3. **Adversarial Security Posture:** Verified that prompt injection attempts and system prompt extraction requests are either securely neutralized (order not found / ineligible without creating any refund) or safely escalated to human review via handoff. In scenario 40, Groq's guardrails refuse prompt extraction, which safely triggers the router fallback and escalates to human handoff with zero prompt leakage.
  4. **GraphInterrupt Handling at Approval Gate:** Caught `GraphInterrupt` in `graph.process_message()` to properly retrieve the checkpointed state snapshot (`snapshot.values`) when paused at `approval_gate`, allowing `awaiting_approval` status to be returned seamlessly to API clients and eval runners.

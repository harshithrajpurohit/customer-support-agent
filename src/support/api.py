"""FastAPI backend application for the Multi-Agent Customer Support System.

Provides customer-facing chat and session endpoints, along with secured admin
endpoints for managing refund approvals and handoff escalations.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from support import config, db, graph, mcp_client, memory, tools_core, tracing

logger = logging.getLogger("support.api")


# ---------------------------------------------------------------------------
# Rate Limiting State (Per Session)
# ---------------------------------------------------------------------------

_rate_limits: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()


def check_rate_limit(session_id: str, max_requests: int = 20, window_seconds: float = 60.0) -> bool:
    """Enforce per-session sliding window rate limit.

    Parameters
    ----------
    session_id:
        Conversation thread ID.
    max_requests:
        Maximum permitted requests in the time window (default: 20).
    window_seconds:
        Sliding time window duration in seconds (default: 60.0).

    Returns
    -------
    bool
        True if the request is permitted, False if the rate limit was reached.
    """
    now = time.time()
    with _rate_limit_lock:
        history = _rate_limits.setdefault(session_id, [])
        cutoff = now - window_seconds
        valid = [t for t in history if t > cutoff]
        if len(valid) >= max_requests:
            _rate_limits[session_id] = valid
            return False
        valid.append(now)
        _rate_limits[session_id] = valid
        return True


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """Payload for customer demo login."""

    email: str = Field(..., min_length=3, max_length=255, description="Customer email address")


class CustomerInfo(BaseModel):
    """Customer profile info."""

    id: int
    name: str
    email: str | None = None


class LoginResponse(BaseModel):
    """Response returned upon successful demo login."""

    session_id: str
    customer: CustomerInfo


class ChatRequest(BaseModel):
    """Payload for a customer chat message."""

    session_id: str = Field(..., min_length=1, max_length=128, description="Active session ID")
    message: str = Field(..., min_length=1, max_length=1000, description="User message content")


class ChatResponse(BaseModel):
    """Response payload for customer chat message."""

    reply: str
    intent: str | None = None
    status: Literal["answered", "awaiting_approval", "handed_off"]


class ChatMessageItem(BaseModel):
    """Individual chat message record."""

    id: int
    role: str
    content: str
    intent: str | None = None
    created_at: str


class MessagesResponse(BaseModel):
    """List of chat messages for a session."""

    messages: list[ChatMessageItem]


class AdminRefundItem(BaseModel):
    """Refund item view for admin dashboard."""

    id: int
    order_id: int
    customer_name: str
    customer_email: str
    item: str | None = None
    amount: float
    reason: str | None = None
    flags: list[str] = Field(default_factory=list)
    status: str
    created_at: str


class RefundDecisionRequest(BaseModel):
    """Payload for approving or rejecting a pending refund."""

    decision: Literal["approve", "reject", "approved", "rejected"]
    note: str = Field(default="", max_length=500)


class AdminDecisionResponse(BaseModel):
    """Result of an admin refund decision."""

    ok: bool
    refund_id: int
    status: str


class AdminHandoffItem(BaseModel):
    """Summary of an escalation handoff item."""

    id: int
    session_id: str
    customer_name: str
    reason: str
    summary: str
    status: str
    created_at: str


class AdminHandoffDetail(BaseModel):
    """Full handoff details including conversation transcript."""

    id: int
    session_id: str
    customer_id: int
    customer_name: str
    customer_email: str
    reason: str
    summary: str
    transcript: list[dict[str, Any]] | dict[str, Any]
    status: str
    created_at: str
    resolved_at: str | None = None


class AdminResolveResponse(BaseModel):
    """Result of marking a handoff resolved."""

    ok: bool
    handoff_id: int
    status: str


class HealthResponse(BaseModel):
    """System health check status."""

    status: str
    db: bool
    timestamp: str


# ---------------------------------------------------------------------------
# Admin Security Dependency
# ---------------------------------------------------------------------------

async def verify_admin_token(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token")
) -> str:
    """Validate admin token from X-Admin-Token request header.

    Raises
    ------
    HTTPException
        401 if header is missing, 403 if token does not match config.ADMIN_TOKEN.
    """
    if not x_admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin token",
        )
    if not secrets.compare_digest(x_admin_token, config.ADMIN_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token",
        )
    return x_admin_token


# ---------------------------------------------------------------------------
# Lifespan Context Manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    logger.info("Initializing database connection pool...")
    try:
        db.get_pool()
    except Exception as exc:
        logger.warning("DB pool initialization warning: %s", exc)

    logger.info("Starting MCP client...")
    try:
        mcp_client.start()
    except Exception as exc:
        logger.warning("MCP client startup warning: %s", exc)

    yield

    logger.info("Shutting down application...")
    try:
        tracing.flush()
    except Exception as exc:
        logger.warning("Error flushing Langfuse: %s", exc)

    try:
        mcp_client.close()
    except Exception as exc:
        logger.warning("Error closing MCP client: %s", exc)

    try:
        db.close_pool()
    except Exception as exc:
        logger.warning("Error closing DB pool: %s", exc)


# ---------------------------------------------------------------------------
# FastAPI Application Setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Multi-Agent Customer Support System",
    description="Backend API powering chat customer support and admin operations.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Safe logging middleware (no PII or secrets logged)
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    logger.info(
        "%s %s -> %d (%.3fs)",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response


# Consistent error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"ok": False, "detail": "Validation error", "errors": exc.errors()},
    )


# ---------------------------------------------------------------------------
# Health Check Endpoint
# ---------------------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
@app.get("/health", response_model=HealthResponse, include_in_schema=False)
def get_health() -> HealthResponse:
    """Return health status and database connectivity."""
    db_ok = False
    try:
        with db.get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception as exc:
        logger.warning("Health check DB probe failed: %s", exc)
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db=db_ok,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Customer Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/login", response_model=LoginResponse, tags=["Customer"])
def login(req: LoginRequest) -> LoginResponse:
    """Find customer by email and establish a session.

    Note: This is a demo-only identity endpoint. Real auth providers are out of scope.
    """
    clean_email = req.email.strip().lower()
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, name, email FROM customers WHERE LOWER(email) = %s",
                (clean_email,),
            ).fetchone()
    except Exception as exc:
        logger.exception("Database error during login: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal database error",
        ) from exc

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    session_id = uuid.uuid4().hex
    store = memory.get_session_store()
    store.set_meta(session_id, "customer_id", row["id"])
    store.set_meta(session_id, "customer_name", row["name"])
    store.set_meta(session_id, "customer_email", row["email"])

    return LoginResponse(
        session_id=session_id,
        customer=CustomerInfo(
            id=row["id"],
            name=row["name"],
            email=row["email"],
        ),
    )


@app.post("/api/chat", response_model=ChatResponse, tags=["Customer"])
def chat(req: ChatRequest) -> ChatResponse:
    """Process a customer message through the support LangGraph."""
    # Enforce rate limit
    if not check_rate_limit(req.session_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Maximum 20 messages per minute.",
        )

    store = memory.get_session_store()
    customer_id = store.get_meta(req.session_id, "customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in first.",
        )

    try:
        result = graph.process_message(
            session_id=req.session_id,
            customer_id=int(customer_id),
            message=req.message,
        )
    except Exception as exc:
        logger.exception("Chat graph execution error: %s", exc)
        return ChatResponse(
            reply="We encountered an issue processing your request. A human agent will assist you shortly.",
            intent="complaint",
            status="handed_off",
        )

    # Map outcome to documented API status values: 'answered', 'awaiting_approval', 'handed_off'
    raw_status = result.get("status") or result.get("outcome") or "answered"
    if raw_status == "awaiting_approval":
        final_status: Literal["answered", "awaiting_approval", "handed_off"] = "awaiting_approval"
    elif raw_status == "handed_off":
        final_status = "handed_off"
    else:
        final_status = "answered"

    return ChatResponse(
        reply=result.get("reply", ""),
        intent=result.get("intent"),
        status=final_status,
    )


@app.get(
    "/api/sessions/{session_id}/messages",
    response_model=MessagesResponse,
    tags=["Customer"],
)
def get_session_messages(
    session_id: str,
    after: int | None = Query(None, ge=0, description="Retrieve messages strictly after this message ID"),
) -> MessagesResponse:
    """Retrieve chat history for a session, supporting incremental polling."""
    try:
        with db.get_conn() as conn:
            if after is not None:
                rows = conn.execute(
                    """
                    SELECT id, role, content, intent, created_at
                    FROM chat_messages
                    WHERE session_id = %s AND id > %s
                    ORDER BY id ASC
                    """,
                    (session_id, after),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, role, content, intent, created_at
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY id ASC
                    """,
                    (session_id,),
                ).fetchall()
    except Exception as exc:
        logger.exception("Error fetching session messages: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving messages",
        ) from exc

    items = [
        ChatMessageItem(
            id=row["id"],
            role=row["role"],
            content=row["content"],
            intent=row.get("intent"),
            created_at=row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"]),
        )
        for row in rows
    ]

    return MessagesResponse(messages=items)


# ---------------------------------------------------------------------------
# Admin Endpoints (Secured by X-Admin-Token)
# ---------------------------------------------------------------------------

@app.get(
    "/api/admin/refunds",
    response_model=list[AdminRefundItem],
    dependencies=[Depends(verify_admin_token)],
    tags=["Admin"],
)
def list_admin_refunds(
    status_filter: str | None = Query("pending", alias="status", description="Filter by status (e.g. 'pending', 'all')"),
) -> list[AdminRefundItem]:
    """List refunds joined with order and customer details."""
    try:
        with db.get_conn() as conn:
            if status_filter and status_filter.lower() != "all":
                rows = conn.execute(
                    """
                    SELECT 
                        r.id,
                        r.order_id,
                        c.name AS customer_name,
                        c.email AS customer_email,
                        o.item,
                        r.amount,
                        r.reason,
                        r.flags,
                        r.status,
                        r.created_at
                    FROM refunds r
                    JOIN orders o ON r.order_id = o.id
                    JOIN customers c ON o.customer_id = c.id
                    WHERE r.status = %s
                    ORDER BY r.created_at DESC
                    """,
                    (status_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT 
                        r.id,
                        r.order_id,
                        c.name AS customer_name,
                        c.email AS customer_email,
                        o.item,
                        r.amount,
                        r.reason,
                        r.flags,
                        r.status,
                        r.created_at
                    FROM refunds r
                    JOIN orders o ON r.order_id = o.id
                    JOIN customers c ON o.customer_id = c.id
                    ORDER BY r.created_at DESC
                    """
                ).fetchall()
    except Exception as exc:
        logger.exception("Error listing refunds: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving refunds",
        ) from exc

    return [
        AdminRefundItem(
            id=row["id"],
            order_id=row["order_id"],
            customer_name=row["customer_name"],
            customer_email=row["customer_email"],
            item=row["item"],
            amount=float(row["amount"]) if row["amount"] is not None else 0.0,
            reason=row["reason"],
            flags=row["flags"] or [],
            status=row["status"],
            created_at=row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"]),
        )
        for row in rows
    ]


@app.post(
    "/api/admin/refunds/{id}/decision",
    response_model=AdminDecisionResponse,
    dependencies=[Depends(verify_admin_token)],
    tags=["Admin"],
)
def decide_refund(
    id: int,
    req: RefundDecisionRequest,
) -> AdminDecisionResponse:
    """Approve or reject a pending refund and resume LangGraph workflow."""
    norm_decision = "approved" if req.decision in ("approve", "approved") else "rejected"

    res = tools_core.finalize_refund(
        refund_id=id,
        decision=norm_decision,
        decided_by="admin",
        note=req.note,
    )

    if not res.get("ok"):
        err_code = res.get("error_code")
        if err_code == "ALREADY_DECIDED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=res.get("message", f"Refund {id} has already been decided."),
            )
        elif err_code == "NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=res.get("message", f"Refund {id} not found."),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=res.get("message", "Error finalizing refund."),
            )

    # Resume graph thread using graph.resume_approval
    store = memory.get_session_store()
    session_id = store.get_meta(f"refund_session:{id}", "session_id")
    cust_id = store.get_meta(f"refund_customer:{id}", "customer_id")

    if not session_id:
        # Fall back to finding session from chat_messages
        try:
            with db.get_conn() as conn:
                msg_row = conn.execute(
                    """
                    SELECT session_id, customer_id
                    FROM chat_messages
                    WHERE content LIKE %s
                    ORDER BY id DESC LIMIT 1
                    """,
                    (f"%#{id}%",),
                ).fetchone()
                if msg_row:
                    session_id = msg_row["session_id"]
                    cust_id = msg_row["customer_id"]
        except Exception as exc:
            logger.warning("Failed to lookup session for refund %s: %s", id, exc)

    if session_id:
        resumed = False
        try:
            graph.resume_approval(session_id=session_id, decision=norm_decision, note=req.note)
            resumed = True
        except Exception as exc:
            logger.warning("Graph resume_approval failed for session %s: %s", session_id, exc)

        # If resume failed or thread was not in an active interrupt, push notification directly
        if not resumed:
            notification = (
                f"Good news! Your refund request #{id} has been approved."
                if norm_decision == "approved"
                else f"Your refund request #{id} has been reviewed and rejected."
            )
            if req.note and norm_decision != "approved":
                notification += f" Reason: {req.note}"

            store.append_message(session_id, "assistant", notification)
            try:
                with db.get_conn() as conn:
                    with conn.transaction():
                        conn.execute(
                            """
                            INSERT INTO chat_messages (session_id, customer_id, role, content, intent)
                            VALUES (%s, %s, 'assistant', %s, 'refund')
                            """,
                            (session_id, cust_id, notification),
                        )
            except Exception as exc:
                logger.error("Failed to insert customer notification: %s", exc)

    return AdminDecisionResponse(
        ok=True,
        refund_id=id,
        status=norm_decision,
    )


@app.get(
    "/api/admin/handoffs",
    response_model=list[AdminHandoffItem],
    dependencies=[Depends(verify_admin_token)],
    tags=["Admin"],
)
def list_admin_handoffs(
    status_filter: str | None = Query("open", alias="status", description="Filter by status (e.g. 'open', 'resolved', 'all')"),
) -> list[AdminHandoffItem]:
    """List escalation handoff items joined with customer names."""
    try:
        with db.get_conn() as conn:
            if status_filter and status_filter.lower() != "all":
                rows = conn.execute(
                    """
                    SELECT 
                        h.id,
                        h.session_id,
                        c.name AS customer_name,
                        h.reason,
                        h.summary,
                        h.status,
                        h.created_at
                    FROM handoffs h
                    JOIN customers c ON h.customer_id = c.id
                    WHERE h.status = %s
                    ORDER BY h.created_at DESC
                    """,
                    (status_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT 
                        h.id,
                        h.session_id,
                        c.name AS customer_name,
                        h.reason,
                        h.summary,
                        h.status,
                        h.created_at
                    FROM handoffs h
                    JOIN customers c ON h.customer_id = c.id
                    ORDER BY h.created_at DESC
                    """
                ).fetchall()
    except Exception as exc:
        logger.exception("Error listing handoffs: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving handoffs",
        ) from exc

    return [
        AdminHandoffItem(
            id=row["id"],
            session_id=row["session_id"],
            customer_name=row["customer_name"],
            reason=row["reason"],
            summary=row["summary"],
            status=row["status"],
            created_at=row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"]),
        )
        for row in rows
    ]


@app.get(
    "/api/admin/handoffs/{id}",
    response_model=AdminHandoffDetail,
    dependencies=[Depends(verify_admin_token)],
    tags=["Admin"],
)
def get_admin_handoff(id: int) -> AdminHandoffDetail:
    """Retrieve full details and transcript for a single handoff item."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """
                SELECT 
                    h.id,
                    h.session_id,
                    h.customer_id,
                    c.name AS customer_name,
                    c.email AS customer_email,
                    h.reason,
                    h.summary,
                    h.transcript,
                    h.status,
                    h.created_at,
                    h.resolved_at
                FROM handoffs h
                JOIN customers c ON h.customer_id = c.id
                WHERE h.id = %s
                """,
                (id,),
            ).fetchone()
    except Exception as exc:
        logger.exception("Error retrieving handoff %s: %s", id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving handoff",
        ) from exc

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Handoff {id} not found",
        )

    return AdminHandoffDetail(
        id=row["id"],
        session_id=row["session_id"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        customer_email=row["customer_email"],
        reason=row["reason"],
        summary=row["summary"],
        transcript=row["transcript"],
        status=row["status"],
        created_at=row["created_at"].isoformat()
        if hasattr(row["created_at"], "isoformat")
        else str(row["created_at"]),
        resolved_at=row["resolved_at"].isoformat()
        if row.get("resolved_at") and hasattr(row["resolved_at"], "isoformat")
        else (str(row["resolved_at"]) if row.get("resolved_at") else None),
    )


@app.post(
    "/api/admin/handoffs/{id}/resolve",
    response_model=AdminResolveResponse,
    dependencies=[Depends(verify_admin_token)],
    tags=["Admin"],
)
def resolve_admin_handoff(id: int) -> AdminResolveResponse:
    """Mark an escalation handoff as resolved."""
    try:
        with db.get_conn() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE handoffs
                    SET status = 'resolved', resolved_at = NOW()
                    WHERE id = %s
                    RETURNING id, status
                    """,
                    (id,),
                ).fetchone()
    except Exception as exc:
        logger.exception("Error resolving handoff %s: %s", id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error resolving handoff",
        ) from exc

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Handoff {id} not found",
        )

    return AdminResolveResponse(
        ok=True,
        handoff_id=id,
        status="resolved",
    )

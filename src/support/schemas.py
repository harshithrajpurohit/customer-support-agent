"""Pydantic schemas and graph state definitions for the Multi-Agent Support System."""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from pydantic import BaseModel, Field


class RouterOutput(BaseModel):
    """Structured classification output produced by the router agent."""

    intent: Literal["order", "refund", "technical", "complaint"] = Field(
        description="The primary intent of the user message."
    )
    sentiment: Literal["calm", "frustrated", "angry"] = Field(
        description="Detected user sentiment."
    )
    wants_human: bool = Field(
        default=False,
        description="Whether the user explicitly requested a human agent or representative.",
    )
    order_id: int | None = Field(
        default=None,
        description="Specific order ID mentioned or referenced in the message, if any.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for the classification between 0.0 and 1.0.",
    )


class SpecialistOutput(BaseModel):
    """Output returned by specialist agents (orders, refunds, technical)."""

    reply: str = Field(
        description="The response message to be sent to the customer."
    )
    resolved: bool = Field(
        default=True,
        description="True if the request was answered/handled according to policy; False if agent could not help.",
    )
    refund_id: int | None = Field(
        default=None,
        description="Created refund request ID if a refund was created; otherwise None.",
    )


class GraphState(TypedDict, total=False):
    """State dictionary passed between nodes in the LangGraph state machine."""

    session_id: str
    customer_id: int
    messages: list[dict[str, Any]]
    intent: str | None
    sentiment: str | None
    wants_human: bool | None
    failed_attempts: int
    pending_refund_id: int | None
    handoff_reason: str | None
    reply: str | None
    outcome: str | None  # "resolved", "awaiting_approval", "handed_off"
    awaiting_approval: bool | None
    resolved: bool | None
    llm_calls: int
    _already_handed_off: bool | None
    _reply_saved: bool | None

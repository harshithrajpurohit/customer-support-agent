"""Configuration module for the Multi-Agent Customer Support System.

Loads environment variables from .env (parent dir first, then local),
exposes typed constants and validates required secrets at import time.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# .env loading strategy
# ---------------------------------------------------------------------------
# 1. Try the canonical parent-of-parent .env  (c:\Users\lenovo\harshith-ai-engineer\.env)
# 2. Fall back to any local .env discovered by python-dotenv
# Neither call uses override=True so env vars already set in the shell win.

_parent_env: Path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=_parent_env, override=False)
load_dotenv(override=False)  # picks up a local .env if one exists

# ---------------------------------------------------------------------------
# Static constants
# ---------------------------------------------------------------------------

MODEL: str = "openai/gpt-oss-120b"
"""Name of the LLM model used for all completions."""

REFUND_WINDOW_DAYS: int = 14
"""Maximum number of days after purchase within which a refund is allowed."""

HIGH_VALUE_THRESHOLD: float = 20_000.0
"""Order value (in currency units) above which an order is considered high-value."""

MAX_FAILED_ATTEMPTS: int = 2
"""Number of consecutive failed verifications before a session is locked."""

SESSION_TTL_SECONDS: int = 3_600
"""Time-to-live for session data (1 hour)."""

MAX_TOOL_ITERATIONS: int = 5
"""Maximum number of tool-call rounds per agent turn."""

MAX_LLM_CALLS_PER_TURN: int = 12
"""Hard cap on LLM calls within a single user turn to prevent run-away loops."""

# ---------------------------------------------------------------------------
# Secrets and optional integrations (read from environment)
# ---------------------------------------------------------------------------

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
"""Groq API key — **required**."""

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
"""PostgreSQL connection string — **required**."""

REDIS_URL: str | None = os.environ.get("REDIS_URL") or None
"""Redis connection URL — optional.  Falls back to in-memory session store."""

LANGFUSE_PUBLIC_KEY: str | None = os.environ.get("LANGFUSE_PUBLIC_KEY") or None
"""Langfuse public key — optional.  Tracing is disabled when absent."""

LANGFUSE_SECRET_KEY: str | None = os.environ.get("LANGFUSE_SECRET_KEY") or None
"""Langfuse secret key — optional.  Tracing is disabled when absent."""

LANGFUSE_HOST: str | None = os.environ.get("LANGFUSE_HOST") or None
"""Langfuse host URL — optional (uses Langfuse Cloud if unset)."""

# ADMIN_TOKEN: use the env var if present; otherwise generate one (printed once).
_admin_token_from_env: str | None = os.environ.get("ADMIN_TOKEN") or None
if _admin_token_from_env:
    ADMIN_TOKEN: str = _admin_token_from_env
else:
    ADMIN_TOKEN = secrets.token_hex(32)
    # This is the ONE secret we are allowed to print during startup so the
    # operator can persist it in .env.  We do NOT log any other secrets.
    print(  # noqa: T201
        f"ADMIN_TOKEN not set; generated: {ADMIN_TOKEN} — add to .env",
        file=sys.stderr,
    )

# ---------------------------------------------------------------------------
# Fail-fast validation
# ---------------------------------------------------------------------------

def _require(name: str, value: str) -> None:
    """Raise RuntimeError with a clear message if *value* is falsy."""
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Check your .env file and try again."
        )


_require("GROQ_API_KEY", GROQ_API_KEY)
_require("DATABASE_URL", DATABASE_URL)

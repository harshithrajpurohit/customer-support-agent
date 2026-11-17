"""Session-memory module for the Multi-Agent Customer Support System.

Provides a ``SessionStore`` protocol with two concrete implementations:
- ``RedisSessionStore`` — backed by Redis (preferred when REDIS_URL is set)
- ``InMemorySessionStore`` — in-process dict fallback (no persistence)

Use ``get_session_store()`` to obtain the right implementation at runtime.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from support import config

logger = logging.getLogger(__name__)

# Maximum messages retained per session (older ones are dropped)
_MAX_MESSAGES: int = 20

# ---------------------------------------------------------------------------
# Protocol (structural interface)
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionStore(Protocol):
    """Interface that all session-storage backends must satisfy."""

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """Return up to the last *_MAX_MESSAGES* messages for *session_id*.

        Args:
            session_id: Unique identifier for the conversation session.

        Returns:
            List of message dicts (``{"role": …, "content": …}``), oldest first.
        """
        ...

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """Append a single message to the session history.

        Args:
            session_id: Unique identifier for the conversation session.
            role: Message role (``"user"``, ``"assistant"``, ``"tool"``, etc.).
            content: Text content of the message.
        """
        ...

    def incr_failed(self, session_id: str) -> int:
        """Increment the consecutive-failed-verification counter and return the new value.

        Args:
            session_id: Unique identifier for the conversation session.

        Returns:
            The updated counter value (1-based).
        """
        ...

    def get_failed(self, session_id: str) -> int:
        """Return the current consecutive-failed-verification counter value.

        Args:
            session_id: Unique identifier for the conversation session.

        Returns:
            Current counter value (default 0).
        """
        ...

    def reset_failed(self, session_id: str) -> None:
        """Reset the failed-verification counter to zero.

        Args:
            session_id: Unique identifier for the conversation session.
        """
        ...

    def set_meta(self, session_id: str, key: str, value: Any) -> None:
        """Store an arbitrary metadata value for a session.

        Args:
            session_id: Unique identifier for the conversation session.
            key: Metadata key (e.g. ``"customer_id"``).
            value: JSON-serialisable value.
        """
        ...

    def get_meta(self, session_id: str, key: str, default: Any = None) -> Any:
        """Retrieve a metadata value previously stored with :meth:`set_meta`.

        Args:
            session_id: Unique identifier for the conversation session.
            key: Metadata key to look up.
            default: Value returned when the key is absent (default ``None``).

        Returns:
            The stored value, or *default* if not found.
        """
        ...


# ---------------------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------------------


class RedisSessionStore:
    """Redis-backed session store with automatic TTL management.

    Keys used per session:
    - ``session:{id}:msgs``   — Redis list of JSON-encoded message dicts
    - ``session:{id}:meta``   — Redis hash of metadata key-value pairs
    - ``session:{id}:failed`` — Redis string holding the failed-auth counter

    Args:
        redis_url: Redis connection URL (e.g. ``"redis://localhost:6379/0"``).
    """

    def __init__(self, redis_url: str) -> None:
        import redis as redis_pkg  # noqa: PLC0415

        self._r = redis_pkg.from_url(redis_url, decode_responses=True)
        self._ttl: int = config.SESSION_TTL_SECONDS

    # Key helpers ----------------------------------------------------------

    @staticmethod
    def _msgs_key(session_id: str) -> str:
        return f"session:{session_id}:msgs"

    @staticmethod
    def _meta_key(session_id: str) -> str:
        return f"session:{session_id}:meta"

    @staticmethod
    def _failed_key(session_id: str) -> str:
        return f"session:{session_id}:failed"

    def _refresh_ttl(self, session_id: str) -> None:
        """Refresh TTL on all keys belonging to *session_id*."""
        pipe = self._r.pipeline()
        pipe.expire(self._msgs_key(session_id), self._ttl)
        pipe.expire(self._meta_key(session_id), self._ttl)
        pipe.expire(self._failed_key(session_id), self._ttl)
        pipe.execute()

    # SessionStore interface -----------------------------------------------

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """Return up to the last *_MAX_MESSAGES* messages (oldest first)."""
        raw_items: list[str] = self._r.lrange(self._msgs_key(session_id), 0, -1)
        messages: list[dict[str, Any]] = []
        for raw in raw_items:
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning("Could not deserialise message from Redis; skipping.")
        return messages

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """Append a message and trim the list to *_MAX_MESSAGES* entries."""
        key = self._msgs_key(session_id)
        encoded = json.dumps({"role": role, "content": content})
        pipe = self._r.pipeline()
        pipe.rpush(key, encoded)
        # Keep only the last N messages
        pipe.ltrim(key, -_MAX_MESSAGES, -1)
        pipe.execute()
        self._refresh_ttl(session_id)

    def incr_failed(self, session_id: str) -> int:
        """Increment the failed counter and return the new value."""
        new_val = int(self._r.incr(self._failed_key(session_id)))
        self._refresh_ttl(session_id)
        self.set_meta(session_id, "failed_attempts", new_val)
        return new_val

    def get_failed(self, session_id: str) -> int:
        """Return the current failed counter value."""
        val = self._r.get(self._failed_key(session_id))
        return int(val) if val is not None else 0

    def reset_failed(self, session_id: str) -> None:
        """Reset the failed counter to 0."""
        self._r.set(self._failed_key(session_id), 0)
        self._refresh_ttl(session_id)
        self.set_meta(session_id, "failed_attempts", 0)

    def set_meta(self, session_id: str, key: str, value: Any) -> None:
        """Store a JSON-serialised metadata value in the session hash."""
        self._r.hset(self._meta_key(session_id), key, json.dumps(value))
        self._refresh_ttl(session_id)

    def get_meta(self, session_id: str, key: str, default: Any = None) -> Any:
        """Retrieve and deserialise a metadata value from the session hash."""
        raw = self._r.hget(self._meta_key(session_id), key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default


# ---------------------------------------------------------------------------
# In-memory fallback implementation
# ---------------------------------------------------------------------------


class InMemorySessionStore:
    """In-process dict-based session store (no persistence, no TTL enforcement).

    Used as a fallback when Redis is unavailable.  All data is lost when the
    process restarts.  Mirrors the full :class:`SessionStore` interface.
    """

    def __init__(self) -> None:
        # {session_id: list[dict]}
        self._messages: dict[str, list[dict[str, Any]]] = {}
        # {session_id: dict[str, Any]}
        self._meta: dict[str, dict[str, Any]] = {}
        # {session_id: int}
        self._failed: dict[str, int] = {}

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """Return up to the last *_MAX_MESSAGES* messages (oldest first)."""
        return list(self._messages.get(session_id, []))[-_MAX_MESSAGES:]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """Append a message and trim the list to *_MAX_MESSAGES* entries."""
        bucket = self._messages.setdefault(session_id, [])
        bucket.append({"role": role, "content": content})
        # Keep only the most recent N messages in-place
        if len(bucket) > _MAX_MESSAGES:
            del bucket[: len(bucket) - _MAX_MESSAGES]

    def incr_failed(self, session_id: str) -> int:
        """Increment and return the failed counter."""
        self._failed[session_id] = self._failed.get(session_id, 0) + 1
        self.set_meta(session_id, "failed_attempts", self._failed[session_id])
        return self._failed[session_id]

    def get_failed(self, session_id: str) -> int:
        """Return the current failed counter value."""
        return int(self._failed.get(session_id, 0))

    def reset_failed(self, session_id: str) -> None:
        """Reset the failed counter to 0."""
        self._failed[session_id] = 0
        self.set_meta(session_id, "failed_attempts", 0)

    def set_meta(self, session_id: str, key: str, value: Any) -> None:
        """Store a metadata value (any JSON-serialisable type)."""
        self._meta.setdefault(session_id, {})[key] = value

    def get_meta(self, session_id: str, key: str, default: Any = None) -> Any:
        """Retrieve a metadata value, returning *default* if absent."""
        return self._meta.get(session_id, {}).get(key, default)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_default_session_store: SessionStore | None = None
import threading
_store_lock: threading.Lock = threading.Lock()


def get_session_store() -> SessionStore:
    """Return the appropriate :class:`SessionStore` based on configuration.

    Returns:
        A :class:`RedisSessionStore` when ``config.REDIS_URL`` is set, otherwise
        an :class:`InMemorySessionStore` with a logged warning.
    """
    global _default_session_store
    if _default_session_store is not None:
        return _default_session_store

    with _store_lock:
        if _default_session_store is not None:
            return _default_session_store

        if config.REDIS_URL:
            try:
                store = RedisSessionStore(config.REDIS_URL)
                # Quick connectivity check
                store._r.ping()
                logger.info("Using RedisSessionStore at %s", config.REDIS_URL)
                _default_session_store = store
                return _default_session_store
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Redis connection failed (%s); falling back to InMemorySessionStore.", exc
                )

        logger.warning(
            "REDIS_URL not set or Redis unavailable; using InMemorySessionStore. "
            "Session data will be lost on restart."
        )
        _default_session_store = InMemorySessionStore()
        return _default_session_store

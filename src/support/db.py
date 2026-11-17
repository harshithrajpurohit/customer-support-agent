"""Database connection pool for the Multi-Agent Customer Support System.

Provides a lazily-initialised singleton ``psycopg_pool.ConnectionPool`` and
a context-manager helper for safe, scoped connection use.

Usage::

    from support.db import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT 1").fetchone()
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Generator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from support import config

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------

_pool: ConnectionPool | None = None
_pool_lock: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_pool() -> ConnectionPool:
    """Return the shared connection pool, creating it on first call.

    The pool is initialised from :data:`support.config.DATABASE_URL`.  It is
    safe to call this function from multiple threads; creation is protected by
    a lock so the pool is instantiated exactly once.

    Returns
    -------
    ConnectionPool
        The singleton connection pool configured with a ``dict_row`` row
        factory so every query returns dicts instead of tuples.
    """
    global _pool

    if _pool is not None:
        return _pool

    with _pool_lock:
        # Double-checked locking: another thread may have initialised while we
        # were waiting for the lock.
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=config.DATABASE_URL,
                kwargs={"row_factory": dict_row},
                min_size=1,
                max_size=10,
                open=True,
            )

    return _pool


@contextmanager
def get_conn() -> Generator[psycopg.Connection, None, None]:
    """Context manager that yields a connection from the pool.

    The connection is returned to the pool automatically when the ``with``
    block exits, whether normally or via an exception.  The caller is
    responsible for committing or rolling back transactions explicitly.

    Yields
    ------
    psycopg.Connection
        A live database connection with a ``dict_row`` row factory.

    Example
    -------
    ::

        with get_conn() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO refunds (order_id, amount, reason) "
                    "VALUES (%s, %s, %s)",
                    (order_id, amount, reason),
                )
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def close_pool() -> None:
    """Close the connection pool and release all connections.

    Safe to call even if the pool has never been opened.  After this call,
    any subsequent :func:`get_conn` or :func:`get_pool` call will create a
    fresh pool.
    """
    global _pool

    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None

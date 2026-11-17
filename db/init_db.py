"""
db/init_db.py
=============
Initialises the support_db database:
  - Loads DATABASE_URL from the nearest .env (local first, then parent)
  - Drops and recreates all tables via schema.sql
  - Seeds deterministic test data via seed.sql
  - Prints row counts as a sanity check
  - Exports reset_db() so test/eval runners can restore clean state

Usage:
    uv run db/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── locate .env ────────────────────────────────────────────────────────────────
def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("python-dotenv not installed – skipping .env load", file=sys.stderr)
        return

    here = Path(__file__).resolve().parent          # db/
    project_root = here.parent                      # support-agent/
    parent_dir   = project_root.parent              # harshith-ai-engineer/

    # Priority: local .env > project-root .env > parent .env
    for candidate in [
        project_root / ".env",
        parent_dir   / ".env",
    ]:
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            break


_load_env()

import os
import psycopg


# ── helpers ────────────────────────────────────────────────────────────────────
DB_DIR = Path(__file__).resolve().parent


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print(
            "\n[ERROR] DATABASE_URL is not set.\n"
            "Make sure c:\\Users\\lenovo\\harshith-ai-engineer\\.env contains:\n"
            "  DATABASE_URL=postgresql://support_user:support123@localhost:5432/support_db\n",
            file=sys.stderr,
        )
        sys.exit(1)
    return url


def _run_sql_file(conn: psycopg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    conn.execute(sql)


def _row_counts(conn: psycopg.Connection) -> dict[str, int]:
    tables = ["customers", "orders", "refunds", "chat_messages", "handoffs"]
    counts: dict[str, int] = {}
    for t in tables:
        row = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
        counts[t] = row[0] if row else 0
    return counts


# ── public API ─────────────────────────────────────────────────────────────────
def reset_db(url: str | None = None) -> dict[str, int]:
    """Drop, recreate, and re-seed the database.  Returns row counts."""
    db_url = url or _get_url()
    schema_path = DB_DIR / "schema.sql"
    seed_path   = DB_DIR / "seed.sql"

    # psycopg v3: use autocommit=False for explicit transaction control
    with psycopg.connect(db_url, autocommit=False) as conn:
        try:
            _run_sql_file(conn, schema_path)
            _run_sql_file(conn, seed_path)
            counts = _row_counts(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return counts


# ── main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    url = _get_url()
    print("Connecting to database …")

    try:
        counts = reset_db(url)
    except psycopg.OperationalError as exc:
        print(
            f"\n[ERROR] Cannot connect to PostgreSQL:\n  {exc}\n\n"
            "PowerShell commands to fix:\n"
            '  # 1. Ensure PostgreSQL service is running:\n'
            '  Start-Service postgresql-x64-18\n\n'
            '  # 2. Create the user and database (run as postgres superuser):\n'
            '  $env:PGPASSWORD = "your_postgres_password"\n'
            '  & "C:\\Program Files\\PostgreSQL\\18\\bin\\psql.exe" -U postgres -h 127.0.0.1 -c '
            '"CREATE USER support_user WITH PASSWORD \'support123\';"\n'
            '  & "C:\\Program Files\\PostgreSQL\\18\\bin\\psql.exe" -U postgres -h 127.0.0.1 -c '
            '"CREATE DATABASE support_db OWNER support_user;"\n'
            '  & "C:\\Program Files\\PostgreSQL\\18\\bin\\psql.exe" -U postgres -h 127.0.0.1 -c '
            '"GRANT ALL PRIVILEGES ON DATABASE support_db TO support_user;"',
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n✅ Database initialised successfully!")
    print(f"   customers    : {counts['customers']}")
    print(f"   orders       : {counts['orders']}")
    print(f"   refunds      : {counts['refunds']}")
    print(f"   chat_messages: {counts['chat_messages']}")
    print(f"   handoffs     : {counts['handoffs']}")

    # Fail loudly if seed data is wrong
    expected = {"customers": 4, "orders": 12, "refunds": 2, "chat_messages": 0, "handoffs": 0}
    failures = [f"{t}: expected {exp}, got {counts[t]}" for t, exp in expected.items() if counts[t] != exp]
    if failures:
        print("\n[WARN] Unexpected row counts:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)

    print("\n🎉 All row counts verified.")


if __name__ == "__main__":
    main()

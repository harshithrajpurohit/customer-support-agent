-- ============================================================
-- schema.sql  –  Multi-Agent Customer Support System
-- Drop in reverse-dependency order, then recreate.
-- Safe to run multiple times (DROP IF EXISTS).
-- ============================================================

-- ── Drop tables in reverse dependency order ─────────────────
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS handoffs      CASCADE;
DROP TABLE IF EXISTS refunds       CASCADE;
DROP TABLE IF EXISTS orders        CASCADE;
DROP TABLE IF EXISTS customers     CASCADE;

-- ── customers ───────────────────────────────────────────────
CREATE TABLE customers (
    id    SERIAL       PRIMARY KEY,
    name  TEXT         NOT NULL,
    email TEXT         NOT NULL UNIQUE
);

-- ── orders ──────────────────────────────────────────────────
CREATE TABLE orders (
    id             INTEGER      PRIMARY KEY,
    customer_id    INTEGER      NOT NULL REFERENCES customers(id),
    item           TEXT,
    amount         NUMERIC(10,2),
    status         TEXT         NOT NULL
                                CHECK (status IN ('processing','shipped','delivered','cancelled')),
    order_date     DATE,
    delivered_date DATE
);

-- ── refunds ─────────────────────────────────────────────────
CREATE TABLE refunds (
    id            SERIAL       PRIMARY KEY,
    order_id      INTEGER      NOT NULL REFERENCES orders(id),
    amount        NUMERIC(10,2),
    reason        TEXT,
    status        TEXT         NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','approved','rejected')),
    flags         TEXT[]       NOT NULL DEFAULT '{}',
    decided_by    TEXT,
    decided_at    TIMESTAMP,
    decision_note TEXT,
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Only one active (pending or approved) refund per order
CREATE UNIQUE INDEX uix_refund_active ON refunds(order_id)
    WHERE status IN ('pending','approved');

-- ── chat_messages ────────────────────────────────────────────
CREATE TABLE chat_messages (
    id          BIGSERIAL    PRIMARY KEY,
    session_id  TEXT         NOT NULL,
    customer_id INTEGER,
    role        TEXT         NOT NULL,
    content     TEXT         NOT NULL,
    intent      TEXT,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── handoffs ─────────────────────────────────────────────────
CREATE TABLE handoffs (
    id          SERIAL       PRIMARY KEY,
    session_id  TEXT         NOT NULL,
    customer_id INTEGER      NOT NULL,
    reason      TEXT         NOT NULL,
    summary     TEXT         NOT NULL,
    transcript  JSONB        NOT NULL,
    status      TEXT         NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open','resolved')),
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

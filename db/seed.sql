-- ============================================================
-- seed.sql  –  Deterministic test data
-- ============================================================

-- ── Customers ────────────────────────────────────────────────
INSERT INTO customers (id, name, email) VALUES
    (1, 'Aarav Sharma', 'aarav@example.com'),
    (2, 'Priya Verma',  'priya@example.com'),
    (3, 'Rohan Gupta',  'rohan@example.com'),
    (4, 'Neha Iyer',    'neha@example.com');

-- Keep serial in sync
SELECT setval('customers_id_seq', (SELECT MAX(id) FROM customers));

-- ── Orders ───────────────────────────────────────────────────
INSERT INTO orders (id, customer_id, item, amount, status, order_date, delivered_date) VALUES
    (1001, 1, 'Wireless Earbuds',  2499.00, 'shipped',    CURRENT_DATE - 3,  NULL),
    (1002, 1, 'Running Shoes',     3999.00, 'delivered',  CURRENT_DATE - 8,  CURRENT_DATE - 4),
    (1003, 2, 'Bluetooth Speaker', 4999.00, 'delivered',  CURRENT_DATE - 60, CURRENT_DATE - 55),
    (1004, 2, 'Phone Case',         499.00, 'cancelled',  CURRENT_DATE - 10, NULL),
    (1005, 3, 'Gaming Laptop',    89999.00, 'delivered',  CURRENT_DATE - 6,  CURRENT_DATE - 2),
    (1006, 3, 'Yoga Mat',           899.00, 'processing', CURRENT_DATE - 1,  NULL),
    (1007, 1, 'Water Bottle',       699.00, 'delivered',  CURRENT_DATE - 9,  CURRENT_DATE - 5),
    (1008, 4, 'Desk Lamp',         1299.00, 'delivered',  CURRENT_DATE - 12, CURRENT_DATE - 10),
    (1009, 4, 'Backpack',          1599.00, 'delivered',  CURRENT_DATE - 16, CURRENT_DATE - 14),
    (1010, 4, 'Hoodie',            1999.00, 'delivered',  CURRENT_DATE - 19, CURRENT_DATE - 15),
    (1011, 2, 'Smart Watch',       3499.00, 'shipped',    CURRENT_DATE - 5,  NULL),
    (1012, 1, 'Sneaker Cleaner',   2199.00, 'delivered',  CURRENT_DATE - 6,  CURRENT_DATE - 3);

-- ── Refunds ──────────────────────────────────────────────────
-- order 1007: approved refund (leaking bottle) – pull amount from orders
INSERT INTO refunds (order_id, amount, reason, status) VALUES
    (1007, (SELECT amount FROM orders WHERE id = 1007), 'leaking bottle', 'approved');

-- order 1012: pending refund (wrong size)
INSERT INTO refunds (order_id, amount, reason, status) VALUES
    (1012, (SELECT amount FROM orders WHERE id = 1012), 'wrong size', 'pending');

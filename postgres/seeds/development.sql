-- Development seed data (idempotent, small).

INSERT INTO products (name, price, category)
SELECT
    'Product ' || g,
    round((random() * 990 + 10)::numeric, 2),
    (ARRAY['electronics', 'fashion', 'groceries', 'toys', 'books'])[1 + (g % 5)]
FROM generate_series(1, 200) AS g
WHERE NOT EXISTS (SELECT 1 FROM products);

INSERT INTO orders (customer_id, total)
SELECT
    1 + (g % 50),
    0
FROM generate_series(1, 500) AS g
WHERE NOT EXISTS (SELECT 1 FROM orders);

INSERT INTO order_items (order_id, product_id, quantity, price)
SELECT
    o.id,
    p.id,
    1 + floor(random() * 5)::int,
    p.price
FROM orders o
CROSS JOIN LATERAL (
    SELECT id, price FROM products ORDER BY random() LIMIT 3
) p
WHERE NOT EXISTS (SELECT 1 FROM order_items);

UPDATE orders o
SET total = s.item_total
FROM (
    SELECT order_id, SUM(quantity * price) AS item_total
    FROM order_items
    GROUP BY order_id
) s
WHERE s.order_id = o.id;

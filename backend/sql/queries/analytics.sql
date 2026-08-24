-- name: GetOrderComplex :one
SELECT
    o.id AS order_id,
    o.customer_id,
    o.total,
    COUNT(oi.id) AS item_count,
    SUM(oi.quantity) AS total_quantity,
    MAX(oi.price)::float8 AS max_item_price,
    MIN(oi.price)::float8 AS min_item_price,
    AVG(oi.quantity * oi.price)::float8 AS avg_line_value
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN products p ON p.id = oi.product_id
WHERE o.id = $1
GROUP BY o.id, o.customer_id, o.total;


-- name: GetOrderItemsWithProducts :many
SELECT
    oi.id,
    oi.quantity,
    oi.price,
    p.id AS product_id,
    p.name AS product_name,
    p.category
FROM order_items oi
JOIN products p ON p.id = oi.product_id
WHERE oi.order_id = $1
ORDER BY oi.id;


-- name: CategoryAggregation :many
SELECT
    p.category,
    COUNT(DISTINCT p.id) AS product_count,
    COUNT(oi.id) AS sold_items,
    COALESCE(SUM(oi.quantity), 0)::bigint AS total_quantity,
    COALESCE(AVG(p.price), 0)::numeric AS avg_price,
    COALESCE(SUM(oi.quantity * oi.price), 0)::numeric AS revenue
FROM products p
LEFT JOIN order_items oi ON oi.product_id = p.id
GROUP BY p.category
ORDER BY revenue DESC;

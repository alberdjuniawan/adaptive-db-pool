-- name: GetOrderSummary :one
SELECT
    o.id,
    o.customer_id,
    COALESCE(SUM(oi.quantity * oi.price), 0)::numeric AS total
FROM orders o
LEFT JOIN order_items oi
    ON oi.order_id = o.id
WHERE o.id = $1
GROUP BY o.id, o.customer_id;


-- name: ListOrderSummaries :many
SELECT
    o.id,
    o.customer_id,
    COALESCE(SUM(oi.quantity * oi.price), 0)::numeric AS total
FROM orders o
LEFT JOIN order_items oi
    ON oi.order_id = o.id
GROUP BY o.id, o.customer_id
ORDER BY o.id
LIMIT $1
OFFSET $2;
-- name: GetProduct :one
SELECT
    id,
    name,
    price,
    category,
    created_at
FROM products
WHERE id = $1
LIMIT 1;


-- name: ListProducts :many
SELECT
    id,
    name,
    price,
    category,
    created_at
FROM products
ORDER BY id
LIMIT $1
OFFSET $2;
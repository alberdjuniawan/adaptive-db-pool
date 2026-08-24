package workload

import (
	"context"
	"errors"

	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/workload"
)

// ErrNotFound indicates the requested entity does not exist.
var ErrNotFound = errors.New("workload: entity not found")

// Repository is defined next to its consumer . The
// infrastructure package implements it over sqlc/pgxpool.
type Repository interface {
	GetProduct(ctx context.Context, id int64) (workload.Product, error)
	ListProducts(ctx context.Context, limit, offset int32) ([]workload.Product, error)
	GetOrderSummary(ctx context.Context, id int64) (workload.OrderSummary, error)
	GetOrderComplex(ctx context.Context, id int64) (workload.OrderComplex, error)
	CategoryAggregation(ctx context.Context) ([]workload.CategoryRevenue, error)
}

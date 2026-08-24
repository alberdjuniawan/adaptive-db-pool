package repository

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	appworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/workload"
	domainworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/workload"
	sqlc "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/infrastructure/database/sqlc"
)

// WorkloadRepository implements application/workload.Repository with
// sqlc-generated queries. Technology errors are translated to
// application-level sentinels at this boundary .
type WorkloadRepository struct {
	queries *sqlc.Queries
	logger  *slog.Logger
}

// Compile-time interface conformance check.
var _ appworkload.Repository = (*WorkloadRepository)(nil)

// NewWorkloadRepository constructs the PostgreSQL adapter.
func NewWorkloadRepository(pool *pgxpool.Pool, logger *slog.Logger) *WorkloadRepository {
	if logger == nil {
		logger = slog.Default()
	}
	return &WorkloadRepository{
		queries: sqlc.New(pool),
		logger:  logger,
	}
}

func (r *WorkloadRepository) GetProduct(ctx context.Context, id int64) (domainworkload.Product, error) {
	row, err := r.queries.GetProduct(ctx, id)
	if err != nil {
		return domainworkload.Product{}, translate(err)
	}

	price, err := numericToFloat(row.Price)
	if err != nil {
		return domainworkload.Product{}, err
	}

	return domainworkload.Product{
		ID:        row.ID,
		Name:      row.Name,
		Price:     price,
		Category:  row.Category,
		CreatedAt: timestampToString(row.CreatedAt),
	}, nil
}

func (r *WorkloadRepository) ListProducts(ctx context.Context, limit, offset int32) ([]domainworkload.Product, error) {
	rows, err := r.queries.ListProducts(ctx, sqlc.ListProductsParams{
		Limit:  limit,
		Offset: offset,
	})
	if err != nil {
		return nil, translate(err)
	}

	items := make([]domainworkload.Product, 0, len(rows))
	for _, row := range rows {
		price, err := numericToFloat(row.Price)
		if err != nil {
			return nil, err
		}
		items = append(items, domainworkload.Product{
			ID:        row.ID,
			Name:      row.Name,
			Price:     price,
			Category:  row.Category,
			CreatedAt: timestampToString(row.CreatedAt),
		})
	}
	return items, nil
}

func (r *WorkloadRepository) GetOrderSummary(ctx context.Context, id int64) (domainworkload.OrderSummary, error) {
	row, err := r.queries.GetOrderSummary(ctx, id)
	if err != nil {
		return domainworkload.OrderSummary{}, translate(err)
	}

	total, err := numericToFloat(row.Total)
	if err != nil {
		return domainworkload.OrderSummary{}, err
	}

	return domainworkload.OrderSummary{
		ID:         row.ID,
		CustomerID: row.CustomerID,
		Total:      total,
	}, nil
}

func (r *WorkloadRepository) GetOrderComplex(ctx context.Context, id int64) (domainworkload.OrderComplex, error) {
	row, err := r.queries.GetOrderComplex(ctx, id)
	if err != nil {
		return domainworkload.OrderComplex{}, translate(err)
	}

	total, err := numericToFloat(row.Total)
	if err != nil {
		return domainworkload.OrderComplex{}, err
	}

	items, err := r.queries.GetOrderItemsWithProducts(ctx, id)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return domainworkload.OrderComplex{}, translate(err)
	}

	details := make([]domainworkload.OrderItemDetail, 0, len(items))
	for _, item := range items {
		price, err := numericToFloat(item.Price)
		if err != nil {
			return domainworkload.OrderComplex{}, err
		}
		details = append(details, domainworkload.OrderItemDetail{
			ID:          item.ID,
			Quantity:    item.Quantity,
			Price:       price,
			ProductID:   item.ProductID,
			ProductName: item.ProductName,
			Category:    item.Category,
		})
	}

	return domainworkload.OrderComplex{
		OrderID:       row.OrderID,
		CustomerID:    row.CustomerID,
		Total:         total,
		ItemCount:     row.ItemCount,
		TotalQuantity: row.TotalQuantity,
		MaxItemPrice:  row.MaxItemPrice,
		MinItemPrice:  row.MinItemPrice,
		AvgLineValue:  row.AvgLineValue,
		Items:         details,
	}, nil
}

func (r *WorkloadRepository) CategoryAggregation(ctx context.Context) ([]domainworkload.CategoryRevenue, error) {
	rows, err := r.queries.CategoryAggregation(ctx)
	if err != nil {
		return nil, translate(err)
	}

	out := make([]domainworkload.CategoryRevenue, 0, len(rows))
	for _, row := range rows {
		avgPrice, err := numericToFloat(row.AvgPrice)
		if err != nil {
			return nil, err
		}
		revenue, err := numericToFloat(row.Revenue)
		if err != nil {
			return nil, err
		}
		out = append(out, domainworkload.CategoryRevenue{
			Category:      row.Category,
			ProductCount:  row.ProductCount,
			SoldItems:     row.SoldItems,
			TotalQuantity: row.TotalQuantity,
			AvgPrice:      avgPrice,
			Revenue:       revenue,
		})
	}
	return out, nil
}

// translate converts driver errors into application sentinels so the
// application layer never depends on pgx.
func translate(err error) error {
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		return appworkload.ErrNotFound
	default:
		r := err
		return r
	}
}

func numericToFloat(n pgtype.Numeric) (float64, error) {
	v, err := n.Float64Value()
	if err != nil {
		return 0, err
	}
	if !v.Valid {
		return 0, nil
	}
	return v.Float64, nil
}

func timestampToString(t pgtype.Timestamptz) string {
	if !t.Valid {
		return ""
	}
	return t.Time.UTC().Format(time.RFC3339)
}

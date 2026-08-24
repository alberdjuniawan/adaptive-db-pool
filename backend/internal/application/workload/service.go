package workload

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/admission"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/metrics"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/workload"
)

// Service executes workload use cases under admission control. Every DB
// access is wrapped by Acquire/Release so concurrency reaching PostgreSQL
// equals the configured admission limit .
type Service struct {
	repo      Repository
	strategy  admission.AdmissionStrategy
	telemetry metrics.AdmissionTelemetry
	timeout   time.Duration
}

// NewService builds the application service.
func NewService(repo Repository, strategy admission.AdmissionStrategy, telemetry metrics.AdmissionTelemetry, timeout time.Duration) *Service {
	if telemetry == nil {
		telemetry = metrics.NoopTelemetry{}
	}
	return &Service{
		repo:      repo,
		strategy:  strategy,
		telemetry: telemetry,
		timeout:   timeout,
	}
}

// GetProduct runs the simple workload class.
func (s *Service) GetProduct(ctx context.Context, id int64) (workload.Product, error) {
	return execute(ctx, s.strategy, s.telemetry, s.timeout, func(rc context.Context) (workload.Product, error) {
		return s.repo.GetProduct(rc, id)
	})
}

// ListProducts runs a paged catalog listing.
func (s *Service) ListProducts(ctx context.Context, limit, offset int32) ([]workload.Product, error) {
	return execute(ctx, s.strategy, s.telemetry, s.timeout, func(rc context.Context) ([]workload.Product, error) {
		return s.repo.ListProducts(rc, limit, offset)
	})
}

// GetOrderSummary runs the medium workload class.
func (s *Service) GetOrderSummary(ctx context.Context, id int64) (workload.OrderSummary, error) {
	return execute(ctx, s.strategy, s.telemetry, s.timeout, func(rc context.Context) (workload.OrderSummary, error) {
		return s.repo.GetOrderSummary(rc, id)
	})
}

// GetOrderComplex runs the complex workload class.
func (s *Service) GetOrderComplex(ctx context.Context, id int64) (workload.OrderComplex, error) {
	return execute(ctx, s.strategy, s.telemetry, s.timeout, func(rc context.Context) (workload.OrderComplex, error) {
		return s.repo.GetOrderComplex(rc, id)
	})
}

// CategoryAggregation runs the analytical workload class.
func (s *Service) CategoryAggregation(ctx context.Context) ([]workload.CategoryRevenue, error) {
	return execute(ctx, s.strategy, s.telemetry, s.timeout, s.repo.CategoryAggregation)
}

// AdmissionError reports failure to enter admission within the deadline.
type AdmissionError struct {
	Cause error
}

func (e *AdmissionError) Error() string {
	return fmt.Sprintf("admission rejected: %v", e.Cause)
}

func (e *AdmissionError) Unwrap() error { return e.Cause }

// execute wraps any repository call in acquire/execute/release and
// guarantees release on every path . Context cancellation
// is respected; waiters abandoned after grant release their slot.
// Admission wait duration is observed for every acquire attempt so the
// admission_wait_seconds histogram reflects the true wait distribution.
func execute[T any](ctx context.Context, strategy admission.AdmissionStrategy, telemetry metrics.AdmissionTelemetry, timeout time.Duration, fn func(context.Context) (T, error)) (T, error) {
	var zero T

	acquireCtx := ctx
	cancel := func() {}
	if timeout > 0 {
		acquireCtx, cancel = context.WithTimeout(ctx, timeout)
	}
	defer cancel()

	waitStart := time.Now()
	err := strategy.Acquire(acquireCtx)
	telemetry.ObserveAdmissionWait(time.Since(waitStart))
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) || ctx.Err() != nil {
			return zero, &AdmissionError{Cause: err}
		}
		return zero, fmt.Errorf("admission acquire: %w", err)
	}

	defer strategy.Release()

	result, err := fn(acquireCtx)
	if err != nil {
		return zero, err
	}
	return result, nil
}

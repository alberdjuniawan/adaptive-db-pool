package admission

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/admission"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/metrics"
)

// Service owns the active AdmissionStrategy, its telemetry loop, and the
// external control endpoint used by the closed-loop controller service.
// It is assembled once in main and injected into handlers .
type Service struct {
	strategy  admission.AdmissionStrategy
	label     admission.StrategyLabel
	telemetry metrics.AdmissionTelemetry
	logger    *slog.Logger

	mu      sync.Mutex
	changes int64

	stopOnce sync.Once
	done     chan struct{}
}

// NewService wires a strategy with telemetry. strategy may be any of the
// interchangeable implementations; static/heuristic/adaptive all expose
// Acquire/Release/Limit identically .
func NewService(strategy admission.AdmissionStrategy, label admission.StrategyLabel, telemetry metrics.AdmissionTelemetry, logger *slog.Logger) *Service {
	if telemetry == nil {
		telemetry = metrics.NoopTelemetry{}
	}
	if logger == nil {
		logger = slog.Default()
	}
	s := &Service{
		strategy:  strategy,
		label:     label,
		telemetry: telemetry,
		logger:    logger,
		done:      make(chan struct{}),
	}

	telemetry.SetAdmissionLimit(strategy.Limit())
	return s
}

// Strategy exposes the underlying strategy for controller endpoints.
func (s *Service) Strategy() admission.AdmissionStrategy {
	return s.strategy
}

// Label returns the strategy identifier for telemetry labels.
func (s *Service) Label() admission.StrategyLabel {
	return s.label
}

// ApplyLimit is the control-plane entry point: it forwards a candidate
// limit to adaptive strategies that implement applier. Telemetry and
// logging happen in the strategy decision sink wired at assembly time.
// Static strategies reject changes with ErrNotAdaptive.
func (s *Service) ApplyLimit(candidate int, reason string) (int, error) {
	applier, ok := s.strategy.(interface {
		Apply(candidate int, reason string) int
	})
	if !ok {
		return s.strategy.Limit(), ErrNotAdaptive
	}

	applied := applier.Apply(candidate, reason)
	return applied, nil
}

// Start launches background loops: periodic EnsureFresh fail-safe for
// adaptive strategies and limit-gauge synchronization.
func (s *Service) Start(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = time.Second
	}
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-s.done:
				return
			case <-ticker.C:
				if fresher, ok := s.strategy.(interface{ EnsureFresh() }); ok {
					fresher.EnsureFresh()
				}
				s.telemetry.SetAdmissionLimit(s.strategy.Limit())
			}
		}
	}()
}

// Stop terminates background loops.
func (s *Service) Stop() {
	s.stopOnce.Do(func() { close(s.done) })
}

// RecordDecision is called by strategy decision sinks so every change is
// logged and counted .
func (s *Service) RecordDecision(reason string, oldLimit, newLimit int) {
	s.mu.Lock()
	s.changes++
	s.mu.Unlock()

	s.telemetry.IncControllerDecisions(string(s.label))
	s.telemetry.AddControllerLimitChanges(1)
	s.telemetry.SetAdmissionLimit(newLimit)

	s.logger.Info("admission limit changed",
		"strategy", string(s.label),
		"reason", reason,
		"current_limit", oldLimit,
		"new_limit", newLimit,
	)
}

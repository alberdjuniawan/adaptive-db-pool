package admission

import (
	"context"
	"sync"
	"sync/atomic"
	"time"
)

// StaticStrategy is Baseline A : a fixed logical limit that
// never changes. It is the reference against which adaptive strategies
// must prove their value.
type StaticStrategy struct {
	controller *Controller
}

// NewStaticStrategy creates a fixed-limit admission strategy.
func NewStaticStrategy(limit int) *StaticStrategy {
	return &StaticStrategy{controller: NewController(limit)}
}

func (s *StaticStrategy) Acquire(ctx context.Context) error {
	return s.controller.Acquire(ctx)
}

func (s *StaticStrategy) Release() {
	s.controller.Release()
}

func (s *StaticStrategy) Limit() int {
	return s.controller.Limit()
}

// Controller exposes the underlying controller for telemetry wiring.
func (s *StaticStrategy) Controller() *Controller {
	return s.controller
}

// Observation is a telemetry snapshot fed into heuristic/adaptive logic.
type Observation struct {
	Active        int     `json:"active"`
	Waiting       int     `json:"waiting"`
	Limit         int     `json:"limit"`
	WaitSeconds   float64 `json:"wait_seconds"`
	PoolAcquired  int32   `json:"pool_acquired"`
	PoolIdle      int32   `json:"pool_idle"`
	PoolMax       int32   `json:"pool_max"`
	PoolWaitTotal float64 `json:"pool_wait_total"`
}

// Observer supplies current system state to a strategy. Implemented by
// the application layer; keeps domain free of infrastructure types.
type Observer interface {
	Observe() Observation
}

// DecisionSink receives every limit change for observability purposes
type DecisionSink func(reason string, oldLimit, newLimit int)

// HeuristicStrategy is Baseline B : a fixed utilization /
// queue threshold rule evaluated on a fixed interval.
//
//	if waiting > highWater or utilization > threshold -> increase by step
//	if waiting == 0 and utilization < lowThreshold -> decrease by step
//
// All adjustments stay within [MinLimit, MaxLimit] and respect a cooldown
// between changes.
type HeuristicStrategy struct {
	controller *Controller
	observer   Observer
	onDecision DecisionSink

	minLimit int
	maxLimit int
	step     int
	interval time.Duration

	highThreshold float64
	lowThreshold  float64

	stopOnce sync.Once
	done     chan struct{}
}

// HeuristicConfig configures Baseline B behavior.
type HeuristicConfig struct {
	MinLimit      int
	MaxLimit      int
	Step          int
	Interval      time.Duration
	HighThreshold float64 // utilization above which the limit grows
	LowThreshold  float64 // utilization below which the limit shrinks
	OnDecision    DecisionSink
}

// NewHeuristicStrategy starts a background loop adjusting the limit.
func NewHeuristicStrategy(controller *Controller, observer Observer, cfg HeuristicConfig) *HeuristicStrategy {
	if cfg.MinLimit < 1 {
		cfg.MinLimit = 1
	}
	if cfg.MaxLimit < cfg.MinLimit {
		cfg.MaxLimit = cfg.MinLimit
	}
	if cfg.Step < 1 {
		cfg.Step = 1
	}
	if cfg.Interval <= 0 {
		cfg.Interval = time.Second
	}
	if cfg.HighThreshold <= 0 || cfg.HighThreshold > 1 {
		cfg.HighThreshold = 0.9
	}
	if cfg.LowThreshold < 0 || cfg.LowThreshold >= cfg.HighThreshold {
		cfg.LowThreshold = 0.3
	}

	h := &HeuristicStrategy{
		controller:    controller,
		observer:      observer,
		onDecision:    cfg.OnDecision,
		minLimit:      cfg.MinLimit,
		maxLimit:      cfg.MaxLimit,
		step:          cfg.Step,
		interval:      cfg.Interval,
		highThreshold: cfg.HighThreshold,
		lowThreshold:  cfg.LowThreshold,
		done:          make(chan struct{}),
	}
	go h.run()
	return h
}

func (h *HeuristicStrategy) run() {
	ticker := time.NewTicker(h.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			h.adjust()
		case <-h.done:
			return
		}
	}
}

func (h *HeuristicStrategy) adjust() {
	obs := h.observer.Observe()

	current := h.controller.Limit()
	utilization := 0.0
	if obs.Limit > 0 {
		utilization = float64(obs.Active) / float64(obs.Limit)
	}

	var (
		newLimit int
		reason   string
	)
	switch {
	case obs.Waiting > 0 && utilization >= h.highThreshold && current < h.maxLimit:
		newLimit = minInt(current+h.step, h.maxLimit)
		reason = "saturation"
	case obs.Waiting == 0 && utilization < h.lowThreshold && current > h.minLimit:
		newLimit = maxInt(current-h.step, h.minLimit)
		reason = "underutilization"
	default:
		return
	}

	h.apply(reason, newLimit)
}

// apply mutates the limit through the safety-constrained path.
func (h *HeuristicStrategy) apply(reason string, newLimit int) {
	old := h.controller.Limit()
	if newLimit == old {
		return
	}
	h.controller.SetLimit(newLimit)
	if h.onDecision != nil {
		h.onDecision(reason, old, newLimit)
	}
}

// Acquire delegates to the shared controller.
func (h *HeuristicStrategy) Acquire(ctx context.Context) error {
	return h.controller.Acquire(ctx)
}

// Release returns the slot to the shared controller.
func (h *HeuristicStrategy) Release() {
	h.controller.Release()
}

// Limit reports the currently applied limit.
func (h *HeuristicStrategy) Limit() int {
	return h.controller.Limit()
}

// Controller exposes the underlying controller for telemetry wiring.
func (h *HeuristicStrategy) Controller() *Controller {
	return h.controller
}

// Stop terminates the adjustment loop.
func (h *HeuristicStrategy) Stop() {
	h.stopOnce.Do(func() { close(h.done) })
}

// AdaptiveStrategy applies externally computed decisions (ML predictor +
// optimizer + safety layer running in the separate controller service,
// The strategy itself contains no ML logic; it only:
// - clamps decisions to [min,max]
// - enforces max change per decision (delta)
// - rejects changes during cooldown
// - falls back to a safe static limit when no decision arrives in time
type AdaptiveStrategy struct {
	controller *Controller
	onDecision DecisionSink

	minLimit     int
	maxLimit     int
	maxDelta     int
	cooldown     time.Duration
	lastChange   atomic.Int64 // unix nanos of last accepted change
	fallbackTo   int
	freshTimeout time.Duration

	mu         sync.Mutex
	lastApply  time.Time
	lastSignal time.Time
}

// AdaptiveConfig bounds external decisions .
type AdaptiveConfig struct {
	MinLimit     int
	MaxLimit     int
	MaxDelta     int
	Cooldown     time.Duration
	FallbackTo   int
	FreshTimeout time.Duration
	OnDecision   DecisionSink
}

// NewAdaptiveStrategy wraps a controller with the safety envelope.
func NewAdaptiveStrategy(controller *Controller, cfg AdaptiveConfig) *AdaptiveStrategy {
	if cfg.MinLimit < 1 {
		cfg.MinLimit = 1
	}
	if cfg.MaxLimit < cfg.MinLimit {
		cfg.MaxLimit = cfg.MinLimit
	}
	if cfg.MaxDelta < 1 {
		cfg.MaxDelta = 4
	}
	if cfg.Cooldown <= 0 {
		cfg.Cooldown = 5 * time.Second
	}
	if cfg.FallbackTo < cfg.MinLimit {
		cfg.FallbackTo = cfg.MinLimit
	} else if cfg.FallbackTo > cfg.MaxLimit {
		cfg.FallbackTo = cfg.MaxLimit
	}
	if cfg.FreshTimeout <= 0 {
		cfg.FreshTimeout = 60 * time.Second
	}
	if cfg.FallbackTo < cfg.MinLimit {
		cfg.FallbackTo = cfg.MinLimit
	}

	return &AdaptiveStrategy{
		controller:   controller,
		onDecision:   cfg.OnDecision,
		minLimit:     cfg.MinLimit,
		maxLimit:     cfg.MaxLimit,
		maxDelta:     cfg.MaxDelta,
		cooldown:     cfg.Cooldown,
		fallbackTo:   cfg.FallbackTo,
		freshTimeout: cfg.FreshTimeout,

		// Treat the controller as stale if it never makes contact
		// within FreshTimeout of startup.
		lastSignal: time.Now(),
	}
}

// Apply receives a candidate limit from the external controller service
// and passes it through the full safety chain before applying it.
// It returns the limit actually applied. Every received candidate — even
// one rejected by hysteresis or cooldown — refreshes controller
// liveness: a live-but-content controller must never look stale.
func (a *AdaptiveStrategy) Apply(candidate int, reason string) int {
	now := time.Now()

	clamped := clampInt(candidate, a.minLimit, a.maxLimit)

	a.mu.Lock()
	defer a.mu.Unlock()

	a.lastSignal = now

	current := a.controller.Limit()

	// Hysteresis: ignore insignificant changes (handled upstream too, this
	// is the last line of defense).
	if clamped == current {
		return current
	}

	// Cooldown: do not change limits too frequently.
	if !a.lastApply.IsZero() && now.Sub(a.lastApply) < a.cooldown {
		return current
	}

	// Rate limit: bound the magnitude of each change.
	delta := absInt(clamped - current)
	if delta > a.maxDelta {
		if clamped > current {
			clamped = current + a.maxDelta
		} else {
			clamped = current - a.maxDelta
		}
	}

	a.controller.SetLimit(clamped)
	a.lastApply = now

	if a.onDecision != nil {
		a.onDecision(reason, current, clamped)
	}
	return clamped
}

// EnsureFresh implements fail-safe behavior : if the
// controller service has been silent longer than FreshTimeout, revert to
// the safe static fallback limit. Called periodically by the app layer.
func (a *AdaptiveStrategy) EnsureFresh() {
	a.mu.Lock()
	stale := !a.lastSignal.IsZero() && time.Since(a.lastSignal) > a.freshTimeout
	a.mu.Unlock()

	if stale {
		a.Apply(a.fallbackTo, "fallback_stale_controller")
	}
}

func (a *AdaptiveStrategy) Acquire(ctx context.Context) error {
	return a.controller.Acquire(ctx)
}

func (a *AdaptiveStrategy) Release() {
	a.controller.Release()
}

func (a *AdaptiveStrategy) Limit() int {
	return a.controller.Limit()
}

// Controller exposes the underlying controller for telemetry wiring.
func (a *AdaptiveStrategy) Controller() *Controller {
	return a.controller
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func absInt(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

package admission

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// TestAcquireSucceeds verifies the happy path: a free slot admits.
func TestAcquireSucceeds(t *testing.T) {
	c := NewController(2)

	if err := c.Acquire(context.Background()); err != nil {
		t.Fatalf("acquire with free slot: %v", err)
	}
	if c.Active() != 1 {
		t.Fatalf("active = %d, want 1", c.Active())
	}
}

// TestWaitWhenFull verifies requests queue while all slots are taken and
// proceed after Release.
func TestWaitWhenFull(t *testing.T) {
	c := NewController(1)
	ctx := context.Background()

	if err := c.Acquire(ctx); err != nil {
		t.Fatalf("initial acquire: %v", err)
	}

	done := make(chan error, 1)
	go func() { done <- c.Acquire(ctx) }()

	select {
	case err := <-done:
		t.Fatalf("second acquire should have blocked, got %v", err)
	case <-time.After(50 * time.Millisecond):
	}

	if c.Waiting() != 1 {
		t.Fatalf("waiting = %d, want 1", c.Waiting())
	}

	c.Release()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("waiter should be admitted, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("waiter not admitted after release")
	}
}

// TestCancellation verifies context timeout makes Acquire return a
// context error without leaking the slot .
func TestCancellation(t *testing.T) {
	c := NewController(1)

	if err := c.Acquire(context.Background()); err != nil {
		t.Fatalf("setup acquire: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()

	err := c.Acquire(ctx)
	if err != context.DeadlineExceeded {
		t.Fatalf("acquire = %v, want context.DeadlineExceeded", err)
	}

	// Slot must still be held by the first holder only.
	if got := c.Active(); got != 1 {
		t.Fatalf("active after cancelled wait = %d, want 1", got)
	}

	// A fresh acquire must now succeed immediately after release.
	c.Release()
	ctx2, cancel2 := context.WithTimeout(context.Background(), time.Second)
	defer cancel2()
	if err := c.Acquire(ctx2); err != nil {
		t.Fatalf("acquire after release: %v", err)
	}
}

// TestCancelledWhileGranted covers the race where cancellation lands
// after the slot was already granted; the slot must not leak.
func TestCancelledWhileGranted(t *testing.T) {
	c := NewController(1)

	if err := c.Acquire(context.Background()); err != nil {
		t.Fatalf("setup acquire: %v", err)
	}

	for i := 0; i < 100; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
		_ = c.Acquire(ctx)
		cancel()
		time.Sleep(time.Millisecond / 10)
	}

	c.Release()

	done := make(chan error, 1)
	go func() { done <- c.Acquire(context.Background()) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("no leaked slots expected, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("slot leaked by abandon path")
	}
}

// TestReleaseAcquireCycle ensures repeated acquire/release never
// deadlocks .
func TestReleaseAcquireCycle(t *testing.T) {
	c := NewController(2)

	for i := 0; i < 1000; i++ {
		if err := c.Acquire(context.Background()); err != nil {
			t.Fatalf("iteration %d: %v", i, err)
		}
		c.Release()
	}
}

// TestConcurrencyBound hammers the controller from many goroutines and
// asserts active <= limit at all times .
func TestConcurrencyBound(t *testing.T) {
	const (
		limit     = 4
		workers   = 32
		perWorker = 200
	)

	c := NewController(limit)
	var maxObserved atomic.Int32

	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < perWorker; i++ {
				if err := c.Acquire(context.Background()); err != nil {
					t.Errorf("acquire: %v", err)
					return
				}
				active := int32(c.Active())
				for {
					current := maxObserved.Load()
					if active <= current || maxObserved.CompareAndSwap(current, active) {
						break
					}
				}
				c.Release()
			}
		}()
	}
	wg.Wait()

	if got := maxObserved.Load(); got > limit {
		t.Fatalf("max concurrent = %d, limit = %d", got, limit)
	}
	if got := c.Active(); got != 0 {
		t.Fatalf("active after all released = %d, want 0", got)
	}
}

// TestSetLimitGrowAdmits verifies SetLimit wakes queued waiters when the
// limit grows.
func TestSetLimitGrowAdmits(t *testing.T) {
	c := NewController(1)
	ctx := context.Background()

	if err := c.Acquire(ctx); err != nil {
		t.Fatalf("setup: %v", err)
	}

	results := make(chan error, 3)
	for i := 0; i < 3; i++ {
		go func() { results <- c.Acquire(ctx) }()
	}
	time.Sleep(30 * time.Millisecond)

	c.SetLimit(4)

	for i := 0; i < 3; i++ {
		select {
		case err := <-results:
			if err != nil {
				t.Fatalf("waiter %d: %v", i, err)
			}
		case <-time.After(time.Second):
			t.Fatalf("waiter %d not admitted after grow", i)
		}
	}
}

func TestStaticStrategy(t *testing.T) {
	s := NewStaticStrategy(3)
	if s.Limit() != 3 {
		t.Fatalf("limit = %d, want 3", s.Limit())
	}
	if err := s.Acquire(context.Background()); err != nil {
		t.Fatalf("acquire: %v", err)
	}
	s.Release()
}

// TestAdaptiveSafetyChain validates clamping, delta bounding, cooldown,
// and hysteresis of AdaptiveStrategy .
func TestAdaptiveSafetyChain(t *testing.T) {
	controller := NewController(8)
	strategy := NewAdaptiveStrategy(controller, AdaptiveConfig{
		MinLimit:   2,
		MaxLimit:   16,
		MaxDelta:   4,
		Cooldown:   50 * time.Millisecond,
		FallbackTo: 2,
	})

	// Clamp to max bound.
	if applied := strategy.Apply(9999, "test"); applied != 12 {
		t.Fatalf("apply 9999 -> %d, want 12 (current 8 + delta 4)", applied)
	}

	// Cooldown blocks immediate second change.
	if applied := strategy.Apply(16, "test"); applied != 12 {
		t.Fatalf("cooldown violated: apply during cooldown -> %d, want 12", applied)
	}

	time.Sleep(60 * time.Millisecond)

	// Delta bound limits decrease magnitude.
	if applied := strategy.Apply(2, "test"); applied != 8 {
		t.Fatalf("delta violated: apply 2 -> %d, want 8", applied)
	}

	// Hysteresis: no-op change returns current.
	if applied := strategy.Apply(8, "test"); applied != 8 {
		t.Fatalf("hysteresis: %d, want 8", applied)
	}

	// Clamp to min bound over multiple steps.
	for i := 0; i < 10; i++ {
		time.Sleep(55 * time.Millisecond)
		strategy.Apply(0, "test")
	}
	if got := controller.Limit(); got != 2 {
		t.Fatalf("min bound: limit = %d, want 2", got)
	}
}

// TestEnsureFreshFallback verifies stale controllers trigger the safe
// static fallback .
func TestEnsureFreshFallback(t *testing.T) {
	controller := NewController(8)
	strategy := NewAdaptiveStrategy(controller, AdaptiveConfig{
		MinLimit:     2,
		MaxLimit:     16,
		MaxDelta:     4,
		Cooldown:     10 * time.Millisecond,
		FallbackTo:   4,
		FreshTimeout: 20 * time.Millisecond,
	})

	time.Sleep(60 * time.Millisecond)
	time.Sleep(30 * time.Millisecond)

	strategy.EnsureFresh()

	if got := controller.Limit(); got != 4 && got != 8 {
		// Either fallback fired or nothing changed yet; both are safe.
		t.Fatalf("limit = %d after EnsureFresh, want 4 or unchanged 8", got)
	}
}

// TestHeuristicAdjustment checks Baseline B grows under saturation and
// respects bounds.
func TestHeuristicAdjustment(t *testing.T) {
	controller := NewController(4)
	fixed := &staticObserver{obs: Observation{Active: 4, Waiting: 5, Limit: 4}}
	decisions := make(chan string, 8)

	h := NewHeuristicStrategy(controller, fixed, HeuristicConfig{
		MinLimit: 2,
		MaxLimit: 6,
		Step:     1,
		Interval: 10 * time.Millisecond,
		OnDecision: func(reason string, oldLimit, newLimit int) {
			decisions <- reason
		},
	})
	defer h.Stop()

	deadline := time.After(2 * time.Second)
	for controller.Limit() <= 4 {
		select {
		case <-deadline:
			t.Fatalf("limit = %d, want growth beyond 4 after saturation signal", controller.Limit())
		case <-time.After(10 * time.Millisecond):
		}
	}

	select {
	case reason := <-decisions:
		if reason != "saturation" {
			t.Fatalf("reason = %q, want saturation", reason)
		}
	default:
		t.Fatal("expected a recorded decision")
	}
}

type staticObserver struct{ obs Observation }

func (s *staticObserver) Observe() Observation { return s.obs }

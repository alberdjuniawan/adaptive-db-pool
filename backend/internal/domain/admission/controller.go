package admission

import (
	"context"
	"errors"
	"sync"
	"time"
)

// ErrRejected is returned when a request cannot be admitted within its
// context deadline. Callers must translate it to an HTTP 503/504 at the
// delivery boundary.
var ErrRejected = errors.New("admission: request rejected")

// StrategyLabel identifies the active strategy in telemetry and logs.
type StrategyLabel string

const (
	StrategyLabelStatic    StrategyLabel = "static"
	StrategyLabelHeuristic StrategyLabel = "heuristic"
	StrategyLabelAdaptive  StrategyLabel = "adaptive"
)

// AdmissionStrategy is the Strategy Pattern seam that makes static,
// heuristic, and adaptive controllers interchangeable .
type AdmissionStrategy interface {
	Acquire(ctx context.Context) error
	Release()
	Limit() int
}

// Controller is a semaphore-like logical admission controller with a
// dynamically adjustable limit. It is safe for concurrent use.
// Safety invariants :
// - active never exceeds limit
// - context cancellation is respected
// - slots are never leaked: every Acquire path either grants a slot the
// caller releases, or returns without granting one
type Controller struct {
	mu      sync.Mutex
	limit   int
	active  int
	waiting int
	queue   []chan struct{}

	waitSecondsTotal float64
}

// NewController creates a controller with the given initial limit.
func NewController(limit int) *Controller {
	if limit < 1 {
		limit = 1
	}
	return &Controller{limit: limit}
}

// Acquire blocks until a slot is available or ctx is done.
func (c *Controller) Acquire(ctx context.Context) error {
	started := time.Now()

	c.mu.Lock()
	if c.active < c.limit {
		c.active++
		c.mu.Unlock()
		c.recordWait(time.Since(started))
		return nil
	}

	granted := make(chan struct{}, 1)
	c.queue = append(c.queue, granted)
	c.waiting++
	c.mu.Unlock()

	select {
	case <-granted:
		c.recordWait(time.Since(started))
		return nil
	case <-ctx.Done():
		return c.abandon(ctx, granted)
	}
}

func (c *Controller) recordWait(d time.Duration) {
	c.mu.Lock()
	c.waitSecondsTotal += d.Seconds()
	c.mu.Unlock()
}

// abandon removes a waiter after cancellation. If the slot was already
// granted concurrently, the caller holds a slot nobody will release, so
// this method releases it on the caller's behalf to prevent leaks.
// The context error is propagated unchanged: cancellation yields
// context.Canceled, deadline expiry yields context.DeadlineExceeded.
func (c *Controller) abandon(ctx context.Context, ch chan struct{}) error {
	c.mu.Lock()
	for i, w := range c.queue {
		if w == ch {
			c.queue = append(c.queue[:i], c.queue[i+1:]...)
			c.waiting--
			c.mu.Unlock()
			return ctx.Err()
		}
	}

	c.active--
	if c.active < 0 {
		c.active = 0
	}
	c.waiting--
	if c.waiting < 0 {
		c.waiting = 0
	}
	c.notifyLocked()
	c.mu.Unlock()

	return ctx.Err()
}

// Release returns one acquired slot and wakes the next eligible waiter.
// Must be called exactly once per successful Acquire, ideally via defer.
func (c *Controller) Release() {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.active > 0 {
		c.active--
	}
	c.notifyLocked()
}

// notifyLocked wakes waiters while capacity is available.
// Caller must hold c.mu.
func (c *Controller) notifyLocked() {
	for len(c.queue) > 0 && c.active < c.limit {
		next := c.queue[0]
		c.queue = c.queue[1:]
		c.active++
		c.waiting--
		next <- struct{}{}
	}
}

// Limit returns the current configured admission limit.
func (c *Controller) Limit() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.limit
}

// SetLimit atomically changes the limit and admits waiters if capacity
// grew. Used by adaptive/heuristic strategies; the safety layer owning
// those strategies is responsible for bounds validation before calling.
func (c *Controller) SetLimit(n int) {
	if n < 1 {
		n = 1
	}
	c.mu.Lock()
	c.limit = n
	c.notifyLocked()
	c.mu.Unlock()
}

// Active returns the number of currently admitted requests.
func (c *Controller) Active() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.active
}

// Waiting returns the number of requests queued for admission.
func (c *Controller) Waiting() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.waiting
}

// WaitSecondsTotal returns cumulative admission wait time in seconds.
func (c *Controller) WaitSecondsTotal() float64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.waitSecondsTotal
}

// Stats is a point-in-time snapshot of controller state for telemetry.
type Stats struct {
	Limit         int   `json:"limit"`
	Active        int   `json:"active"`
	Waiting       int   `json:"waiting"`
	AdmittedTotal int64 `json:"-"`
}

// Snapshot captures controller state consistently.
func (c *Controller) Snapshot() Stats {
	c.mu.Lock()
	defer c.mu.Unlock()
	return Stats{
		Limit:   c.limit,
		Active:  c.active,
		Waiting: c.waiting,
	}
}

package metrics

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/admission"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/metrics"
)

const namespace = "adaptive_db_pool"

// Registry owns every Prometheus collector exposed by the backend.
// Metric names follow the metric naming contract (docs/architecture.md). Labels stay low-cardinality.
type Registry struct {
	reg      prometheus.Registerer
	gatherer prometheus.Gatherer

	requestsTotal   *prometheus.CounterVec
	requestDuration *prometheus.HistogramVec
	requestErrors   *prometheus.CounterVec

	admissionActive  prometheus.Gauge
	admissionWaiting prometheus.Gauge
	admissionLimit   prometheus.Gauge
	admissionWait    prometheus.Histogram

	poolMax      prometheus.Gauge
	poolAcquired prometheus.Gauge
	poolIdle     prometheus.Gauge
	poolWait     prometheus.Gauge

	controllerDecisions    *prometheus.CounterVec
	controllerLimitChanges prometheus.Counter
}

// NewRegistry registers all collectors on the given registerer.
func NewRegistry(reg prometheus.Registerer) *Registry {
	factory := promauto.With(reg)

	gatherer, _ := reg.(prometheus.Gatherer)
	if gatherer == nil {
		gatherer = prometheus.DefaultGatherer
	}

	r := &Registry{
		reg:      reg,
		gatherer: gatherer,
		requestsTotal: factory.NewCounterVec(prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "requests_total",
			Help:      "Total HTTP requests processed.",
		}, []string{"method", "route", "status"}),

		requestDuration: factory.NewHistogramVec(prometheus.HistogramOpts{
			Namespace: namespace,
			Name:      "request_duration_seconds",
			Help:      "HTTP request latency in seconds.",
			Buckets:   []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10},
		}, []string{"method", "route", "query_class"}),

		requestErrors: factory.NewCounterVec(prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "request_errors_total",
			Help:      "Total HTTP error responses.",
		}, []string{"method", "route", "status"}),

		admissionActive: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "admission_active",
			Help:      "Currently admitted in-flight requests.",
		}),
		admissionWaiting: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "admission_waiting",
			Help:      "Requests queued at the admission controller.",
		}),
		admissionLimit: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "admission_limit",
			Help:      "Current configured admission limit.",
		}),
		admissionWait: factory.NewHistogram(prometheus.HistogramOpts{
			Namespace: namespace,
			Name:      "admission_wait_seconds",
			Help:      "Time spent waiting for admission.",
			Buckets:   prometheus.DefBuckets,
		}),

		poolMax: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "db_pool_max_connections",
			Help:      "Physical pool MaxConns (static during baseline research).",
		}),
		poolAcquired: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "db_pool_acquired_connections",
			Help:      "Connections currently acquired from pgxpool.",
		}),
		poolIdle: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "db_pool_idle_connections",
			Help:      "Idle connections held by pgxpool.",
		}),
		poolWait: factory.NewGauge(prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "db_pool_wait_seconds",
			Help:      "Cumulative connection acquisition wait time.",
		}),

		controllerDecisions: factory.NewCounterVec(prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "controller_decisions_total",
			Help:      "Admission limit decisions taken by the controller.",
		}, []string{"controller_strategy", "reason"}),
		controllerLimitChanges: factory.NewCounter(prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "controller_limit_changes_total",
			Help:      "Total number of admission limit changes applied.",
		}),
	}

	return r
}

// Gatherer exposes the registry for the /metrics HTTP handler.
func (r *Registry) Gatherer() prometheus.Gatherer {
	return r.gatherer
}

// ObserveRequest records one completed HTTP request.
func (r *Registry) ObserveRequest(method, route string, status int, duration time.Duration, queryClass string) {
	statusStr := statusString(status)
	r.requestsTotal.WithLabelValues(method, route, statusStr).Inc()
	r.requestDuration.WithLabelValues(method, route, queryClass).Observe(duration.Seconds())
	if status >= 400 {
		r.requestErrors.WithLabelValues(method, route, statusStr).Inc()
	}
}

// UpdateAdmission publishes admission controller state to gauges.
func (r *Registry) UpdateAdmission(stats admission.Stats) {
	r.admissionActive.Set(float64(stats.Active))
	r.admissionWaiting.Set(float64(stats.Waiting))
	r.admissionLimit.Set(float64(stats.Limit))
}

// RecordAdmissionWait observes time spent waiting for admission.
func (r *Registry) RecordAdmissionWait(d time.Duration) {
	r.admissionWait.Observe(d.Seconds())
}

// UpdatePool publishes pgxpool state to gauges.
func (r *Registry) UpdatePool(stats *pgxpool.Stat) {
	r.poolMax.Set(float64(stats.MaxConns()))
	r.poolAcquired.Set(float64(stats.AcquiredConns()))
	r.poolIdle.Set(float64(stats.IdleConns()))
}

// SetPoolWait sets cumulative pool acquisition wait seconds.
func (r *Registry) SetPoolWait(seconds float64) {
	r.poolWait.Set(seconds)
}

// IncControllerDecision records one controller decision with reason.
func (r *Registry) IncControllerDecision(strategy admission.StrategyLabel, reason string) {
	r.controllerDecisions.WithLabelValues(string(strategy), reason).Inc()
}

// AddLimitChange counts one applied limit change.
func (r *Registry) AddLimitChange() {
	r.controllerLimitChanges.Inc()
}

// AdmissionTelemetry adapts the registry to the domain contract.
func (r *Registry) AdmissionTelemetry() metrics.AdmissionTelemetry {
	return &admissionTelemetryAdapter{registry: r}
}

type admissionTelemetryAdapter struct {
	registry *Registry
}

func (a *admissionTelemetryAdapter) SetAdmissionLimit(limit int) {
	a.registry.admissionLimit.Set(float64(limit))
}

func (a *admissionTelemetryAdapter) ObserveAdmissionWait(d time.Duration) {
	a.registry.RecordAdmissionWait(d)
}

func (a *admissionTelemetryAdapter) IncControllerDecisions(strategy string) {
	a.registry.controllerDecisions.WithLabelValues(strategy, "external").Inc()
}

func (a *admissionTelemetryAdapter) AddControllerLimitChanges(delta float64) {
	a.registry.controllerLimitChanges.Add(delta)
}

// StartPoolCollector samples pgxpool stats until ctx is done.
func (r *Registry) StartPoolCollector(ctx context.Context, pool *pgxpool.Pool, interval time.Duration) {
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				stats := pool.Stat()
				r.UpdatePool(stats)
				r.SetPoolWait(stats.AcquireDuration().Seconds())
			}
		}
	}()
}

// StartAdmissionCollector samples controller state until ctx is done.
func (r *Registry) StartAdmissionCollector(ctx context.Context, ctrl *admission.Controller, interval time.Duration) {
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				r.UpdateAdmission(ctrl.Snapshot())
			}
		}
	}()
}

func statusString(status int) string {
	switch {
	case status >= 500:
		return "5xx"
	case status >= 400:
		return "4xx"
	case status >= 300:
		return "3xx"
	default:
		return "2xx"
	}
}

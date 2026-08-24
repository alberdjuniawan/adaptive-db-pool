package metrics

import "time"

// AdmissionTelemetry is the domain-level telemetry contract. The
// infrastructure layer implements it with Prometheus; tests use no-ops.
// Domain packages depend on this interface, never on client_golang.
type AdmissionTelemetry interface {
	SetAdmissionLimit(limit int)
	ObserveAdmissionWait(d time.Duration)
	IncControllerDecisions(strategy string)
	AddControllerLimitChanges(delta float64)
}

// NoopTelemetry is a no-op implementation for tests and disabled setups.
type NoopTelemetry struct{}

func (NoopTelemetry) SetAdmissionLimit(int)              {}
func (NoopTelemetry) ObserveAdmissionWait(time.Duration) {}
func (NoopTelemetry) IncControllerDecisions(string)      {}
func (NoopTelemetry) AddControllerLimitChanges(float64)  {}

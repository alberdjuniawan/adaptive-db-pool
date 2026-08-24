package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"

	appadmission "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/admission"
	appworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/workload"
	deliveryhttp "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/delivery/http"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/admission"
	domainmetrics "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/metrics"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/infrastructure/config"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/infrastructure/database"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/infrastructure/database/repository"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/infrastructure/metrics"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))

	cfg, err := config.Load()
	if err != nil {
		logger.Error("configuration error", "error", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Config -> DB Pool. The physical pool stays static for the whole
	// baseline research phase .
	pool, err := database.NewPool(ctx, cfg)
	if err != nil {
		logger.Error("database connection failed", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	// Metrics registry + domain telemetry adapter.
	var (
		reg       *metrics.Registry
		telemetry domainmetrics.AdmissionTelemetry = domainmetrics.NoopTelemetry{}
	)
	if cfg.PrometheusEnabled {
		reg = metrics.NewRegistry(prometheus.DefaultRegisterer)
		telemetry = reg.AdmissionTelemetry()
	}

	// Admission controller assembly. Strategy selection is configuration,
	// not code; static/heuristic/adaptive are interchangeable .
	strategyLabel := cfg.ToDomainStrategy()

	var strategy admission.AdmissionStrategy
	switch cfg.AdmissionStrategy {
	case config.StrategyHeuristic:
		controller := admission.NewController(cfg.AdmissionLimit)
		observer := &poolObserver{pool: pool, controller: controller}
		strategy = admission.NewHeuristicStrategy(controller, observer, admission.HeuristicConfig{
			MinLimit:   cfg.AdmissionMin,
			MaxLimit:   cfg.AdmissionMax,
			Step:       2,
			Interval:   cfg.HeuristicInterval,
			OnDecision: decisionSink(logger, telemetry, strategyLabel),
		})
	case config.StrategyAdaptive:
		controller := admission.NewController(cfg.AdmissionLimit)
		strategy = admission.NewAdaptiveStrategy(controller, admission.AdaptiveConfig{
			MinLimit:     cfg.AdmissionMin,
			MaxLimit:     cfg.AdmissionMax,
			MaxDelta:     cfg.AdmissionMaxDelta,
			Cooldown:     cfg.AdmissionCooldown,
			FallbackTo:   cfg.AdmissionMin,
			FreshTimeout: cfg.ControllerFreshTime,
			OnDecision:   decisionSink(logger, telemetry, strategyLabel),
		})
	default:
		strategy = admission.NewStaticStrategy(cfg.AdmissionLimit)
	}

	// Telemetry must sample the controller instance owned by the
	// active strategy, never an orphan.
	var controller *admission.Controller
	if owner, ok := strategy.(interface{ Controller() *admission.Controller }); ok {
		controller = owner.Controller()
	}

	admissionService := appadmission.NewService(strategy, strategyLabel, telemetry, logger)

	// sqlc Queries -> Repository -> Application Service.
	workloadRepo := repository.NewWorkloadRepository(pool, logger)
	workloadService := appworkload.NewService(workloadRepo, strategy, telemetry, cfg.RequestTimeout)

	// Background gauge collectors.
	if reg != nil && controller != nil {
		reg.StartPoolCollector(ctx, pool, 5*time.Second)
		reg.StartAdmissionCollector(ctx, controller, time.Second)
	}
	admissionService.Start(ctx, 5*time.Second)

	// Handler -> Router -> Serve.
	app := deliveryhttp.New(deliveryhttp.Deps{
		Logger:    logger,
		Workload:  workloadService,
		Admission: admissionService,
		Metrics:   reg,
	})

	logger.Info("starting backend",
		"port", cfg.Port,
		"strategy", string(strategyLabel),
		"admission_limit", strategy.Limit(),
		"db_max_conns", cfg.DBMaxConns,
		"db_min_conns", cfg.DBMinConns,
		"prometheus_enabled", cfg.PrometheusEnabled,
	)

	go func() {
		if err := app.Listen(":" + strconv.Itoa(cfg.Port)); err != nil {
			logger.Error("http server stopped", "error", err)
		}
	}()

	<-ctx.Done()
	logger.Info("shutting down")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := app.ShutdownWithContext(shutdownCtx); err != nil {
		logger.Error("graceful shutdown failed", "error", err)
	}
	admissionService.Stop()

	if heuristic, ok := strategy.(*admission.HeuristicStrategy); ok {
		heuristic.Stop()
	}
}

// decisionSink returns the callback strategies invoke on every accepted
// limit change so decisions are logged and counted .
func decisionSink(logger *slog.Logger, telemetry domainmetrics.AdmissionTelemetry, label admission.StrategyLabel) func(string, int, int) {
	return func(reason string, oldLimit, newLimit int) {
		telemetry.IncControllerDecisions(string(label))
		telemetry.AddControllerLimitChanges(1)
		telemetry.SetAdmissionLimit(newLimit)
		logger.Info("controller decision applied",
			"controller_strategy", string(label),
			"reason", reason,
			"current_limit", oldLimit,
			"new_limit", newLimit,
			"timestamp", time.Now().UTC().Format(time.RFC3339),
		)
	}
}

// poolObserver adapts pgxpool stats to the domain Observer interface so
// heuristic strategies can read utilization without importing pgx.
type poolObserver struct {
	pool       *pgxpool.Pool
	controller *admission.Controller
}

func (p *poolObserver) Observe() admission.Observation {
	stats := p.pool.Stat()
	snap := p.controller.Snapshot()
	waitTotal := p.controller.WaitSecondsTotal()

	return admission.Observation{
		Active:        snap.Active,
		Waiting:       snap.Waiting,
		Limit:         snap.Limit,
		WaitSeconds:   waitTotal,
		PoolAcquired:  stats.AcquiredConns(),
		PoolIdle:      stats.IdleConns(),
		PoolMax:       stats.MaxConns(),
		PoolWaitTotal: stats.AcquireDuration().Seconds(),
	}
}

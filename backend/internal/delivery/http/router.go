package http

import (
	"log/slog"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/adaptor"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	appadmission "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/admission"
	appworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/workload"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/delivery/http/handler"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/delivery/http/middleware"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/infrastructure/metrics"
)

// Deps carries every collaborator the HTTP layer needs, assembled once
// in main via manual dependency injection .
type Deps struct {
	Logger    *slog.Logger
	Workload  *appworkload.Service
	Admission *appadmission.Service
	Metrics   *metrics.Registry
}

// New builds the Fiber app with the full middleware chain and all
// routes. Route paths double as the low-cardinality `route` label.
func New(deps Deps) *fiber.App {
	app := fiber.New(fiber.Config{
		AppName:               "adaptive-db-pool",
		DisableStartupMessage: true,
		// Metrics middleware reads request values after the handler
		// chain completes; views over recycled fasthttp buffers corrupt
		// Prometheus labels.
		Immutable: true,
		ErrorHandler: func(c *fiber.Ctx, err error) error {
			return handler.RespondError(c, err)
		},
	})

	logger := deps.Logger

	app.Use(middleware.RequestID())
	app.Use(middleware.Recovery(logger))
	app.Use(middleware.Logging(logger))
	if deps.Metrics != nil {
		app.Use(requestMetrics(deps.Metrics))
	}

	workloadHandler := handler.NewWorkloadHandler(deps.Workload, deps.Admission)

	app.Get("/health", workloadHandler.GetHealth)

	if deps.Metrics != nil {
		app.Get("/metrics", adaptor.HTTPHandler(promhttp.HandlerFor(deps.Metrics.Gatherer(), promhttp.HandlerOpts{})))
	}

	api := app.Group("/api")
	workloadHandler.Register(api)
	handler.NewAdminHandler(deps.Admission).Register(api.Group("/admin"))

	return app
}

// requestMetrics records per-route Prometheus observations after each
// request completes. Labels stay low-cardinality .
func requestMetrics(reg *metrics.Registry) fiber.Handler {
	return func(c *fiber.Ctx) error {
		start := time.Now()
		err := c.Next()

		// Copy values out immediately: Fiber accessors may read
		// recycled fasthttp buffers once the handler chain has
		// completed. Uncopied method labels were observed to corrupt
		// into garbage ("GETD"/"GETT"), which eventually made /metrics
		// fail consistency checks with duplicate-sample errors.
		method := string(c.Method())
		route := c.Route().Path
		status := c.Response().StatusCode()
		queryClass, _ := c.Locals("query_class").(string)

		reg.ObserveRequest(method, route, status, time.Since(start), queryClass)
		return err
	}
}

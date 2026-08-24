package handler

import (
	"errors"
	"strconv"

	"github.com/gofiber/fiber/v2"

	appadmission "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/admission"
	appworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/workload"
	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/admission"
	domainworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/workload"
)

// WorkloadHandler exposes workload use cases over HTTP. It contains no
// business logic: parse, delegate, map errors .
type WorkloadHandler struct {
	service          *appworkload.Service
	admissionService *appadmission.Service
	maxPageSize      int32
}

// NewWorkloadHandler constructs the handler.
func NewWorkloadHandler(service *appworkload.Service, admissionService *appadmission.Service) *WorkloadHandler {
	return &WorkloadHandler{
		service:          service,
		admissionService: admissionService,
		maxPageSize:      100,
	}
}

// Register mounts all workload routes on the given router group.
func (h *WorkloadHandler) Register(router fiber.Router) {
	router.Get("/workload/simple/:id", h.withClass(domainworkload.ClassSimple, h.getSimple))
	router.Get("/workload/medium/:id", h.withClass(domainworkload.ClassMedium, h.getMedium))
	router.Get("/workload/complex/:id", h.withClass(domainworkload.ClassComplex, h.getComplex))
	router.Get("/workload/aggregation", h.withClass(domainworkload.ClassAggregation, h.getAggregation))
	router.Get("/workload/products", h.withClass(domainworkload.ClassSimple, h.listProducts))
}

// GetHealth reports liveness and current admission state.
func (h *WorkloadHandler) GetHealth(c *fiber.Ctx) error {
	strategy := h.admissionService.Strategy()
	return c.JSON(fiber.Map{
		"status":  "ok",
		"limit":   strategy.Limit(),
		"waiting": waitingCount(strategy),
	})
}

func (h *WorkloadHandler) getSimple(c *fiber.Ctx) error {
	id, err := parseID(c.Params("id"))
	if err != nil {
		return RespondError(c, errInvalidInput)
	}

	product, err := h.service.GetProduct(c.UserContext(), id)
	if err != nil {
		return RespondError(c, err)
	}
	return c.JSON(product)
}

func (h *WorkloadHandler) listProducts(c *fiber.Ctx) error {
	limit, offset := int32(20), int32(0)
	if v := c.Query("limit"); v != "" {
		parsed, err := strconv.ParseInt(v, 10, 32)
		if err != nil || parsed < 1 || parsed > int64(h.maxPageSize) {
			return RespondError(c, errInvalidInput)
		}
		limit = int32(parsed)
	}
	if v := c.Query("offset"); v != "" {
		parsed, err := strconv.ParseInt(v, 10, 32)
		if err != nil || parsed < 0 {
			return RespondError(c, errInvalidInput)
		}
		offset = int32(parsed)
	}

	items, err := h.service.ListProducts(c.UserContext(), limit, offset)
	if err != nil {
		return RespondError(c, err)
	}
	return c.JSON(fiber.Map{"items": items, "count": len(items)})
}

func (h *WorkloadHandler) getMedium(c *fiber.Ctx) error {
	id, err := parseID(c.Params("id"))
	if err != nil {
		return RespondError(c, errInvalidInput)
	}

	summary, err := h.service.GetOrderSummary(c.UserContext(), id)
	if err != nil {
		return RespondError(c, err)
	}
	return c.JSON(summary)
}

func (h *WorkloadHandler) getComplex(c *fiber.Ctx) error {
	id, err := parseID(c.Params("id"))
	if err != nil {
		return RespondError(c, errInvalidInput)
	}

	result, err := h.service.GetOrderComplex(c.UserContext(), id)
	if err != nil {
		return RespondError(c, err)
	}
	return c.JSON(result)
}

func (h *WorkloadHandler) getAggregation(c *fiber.Ctx) error {
	rows, err := h.service.CategoryAggregation(c.UserContext())
	if err != nil {
		return RespondError(c, err)
	}
	return c.JSON(fiber.Map{"items": rows, "count": len(rows)})
}

// withClass tags the handler context with the workload class so the
// logging/metrics middleware can label observations by query_class.
func (h *WorkloadHandler) withClass(class domainworkload.Class, next fiber.Handler) fiber.Handler {
	return func(c *fiber.Ctx) error {
		c.Locals("query_class", string(class))
		return next(c)
	}
}

var errInvalidInput = errors.New("invalid input")

func parseID(raw string) (int64, error) {
	id, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || id < 1 {
		return 0, errInvalidInput
	}
	return id, nil
}

func waitingCount(strategy admission.AdmissionStrategy) int {
	type waiter interface{ Waiting() int }
	if w, ok := strategy.(waiter); ok {
		return w.Waiting()
	}
	return -1
}

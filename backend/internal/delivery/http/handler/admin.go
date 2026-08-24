package handler

import (
	"errors"

	"github.com/gofiber/fiber/v2"

	appadmission "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/admission"
)

// AdminHandler exposes the control plane used by the external adaptive
// controller service. It applies candidate admission limits that have
// already passed the remote safety layer; the backend enforces its own
// bounds again inside AdaptiveStrategy.Apply as defense in depth
type AdminHandler struct {
	admissionService *appadmission.Service
}

// NewAdminHandler constructs the control-plane handler.
func NewAdminHandler(admissionService *appadmission.Service) *AdminHandler {
	return &AdminHandler{admissionService: admissionService}
}

// Register mounts control-plane routes under /api/admin.
func (h *AdminHandler) Register(router fiber.Router) {
	router.Get("/admission", h.getAdmissionState)
	router.Post("/admission/limit", h.applyLimit)
}

type applyLimitRequest struct {
	Limit  int    `json:"limit"`
	Reason string `json:"reason"`
}

func (h *AdminHandler) getAdmissionState(c *fiber.Ctx) error {
	strategy := h.admissionService.Strategy()

	state := fiber.Map{
		"strategy": string(h.admissionService.Label()),
		"limit":    strategy.Limit(),
	}
	if snap, ok := strategySnapshot(strategy); ok {
		state["active"] = snap.active
		state["waiting"] = snap.waiting
	}

	return c.JSON(state)
}

func (h *AdminHandler) applyLimit(c *fiber.Ctx) error {
	var req applyLimitRequest
	if err := c.BodyParser(&req); err != nil {
		return errInvalidInput
	}
	if req.Limit < 1 {
		return errInvalidInput
	}
	if req.Reason == "" {
		req.Reason = "unspecified"
	}

	applied, err := h.admissionService.ApplyLimit(req.Limit, req.Reason)
	if err != nil {
		if errors.Is(err, appadmission.ErrNotAdaptive) {
			return c.Status(fiber.StatusConflict).JSON(fiber.Map{
				"error": "active strategy does not accept limit changes",
			})
		}
		return err
	}

	return c.JSON(fiber.Map{
		"applied_limit": applied,
		"reason":        req.Reason,
	})
}

type snapshotView struct {
	active  int
	waiting int
}

func strategySnapshot(s interface{}) (snapshotView, bool) {
	type waiter interface {
		Active() int
		Waiting() int
	}
	if w, ok := s.(waiter); ok {
		return snapshotView{active: w.Active(), waiting: w.Waiting()}, true
	}
	return snapshotView{}, false
}

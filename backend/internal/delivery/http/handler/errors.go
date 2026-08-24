package handler

import (
	"context"
	"errors"

	"github.com/gofiber/fiber/v2"

	appworkload "github.com/alberdjuniawan/adaptive-db-pool/backend/internal/application/workload"
)

// RespondError is the centralized HTTP error mapping :
//
//	invalid input -> 400
//	not found -> 404
//	context deadline / cancellation -> 504
//	admission rejected -> 503
//	unexpected db error -> 500
//
// Context errors are checked first because AdmissionError wraps them;
// a timed-out admission wait must surface as 504 per the contract.
// Raw PostgreSQL and internal mechanism errors are never exposed.
func RespondError(c *fiber.Ctx, err error) error {
	var admissionErr *appworkload.AdmissionError

	switch {
	case errors.Is(err, errInvalidInput):
		return c.Status(fiber.StatusBadRequest).JSON(fiber.Map{"error": "invalid request parameters"})
	case errors.Is(err, appworkload.ErrNotFound):
		return c.Status(fiber.StatusNotFound).JSON(fiber.Map{"error": "resource not found"})
	case errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled):
		return c.Status(fiber.StatusGatewayTimeout).JSON(fiber.Map{"error": "upstream timeout"})
	case errors.As(err, &admissionErr):
		return c.Status(fiber.StatusServiceUnavailable).JSON(fiber.Map{
			"error": "request rejected by admission controller",
		})
	default:
		return c.Status(fiber.StatusInternalServerError).JSON(fiber.Map{"error": "internal server error"})
	}
}

package middleware

import (
	"log/slog"
	"time"

	"github.com/gofiber/fiber/v2"
)

// Logging emits one structured log line per request .
// Sensitive values are never logged.
func Logging(logger *slog.Logger) fiber.Handler {
	return func(c *fiber.Ctx) error {
		start := time.Now()

		err := c.Next()

		route := c.Route().Path
		fields := []any{
			"request_id", localRequestID(c),
			"method", c.Method(),
			"route", route,
			"path", c.Path(),
			"status", c.Response().StatusCode(),
			"duration_ms", float64(time.Since(start).Microseconds()) / 1000.0,
			"bytes_out", len(c.Response().Body()),
		}

		if err != nil {
			fields = append(fields, "error", err.Error())
			logger.Error("http request failed", fields...)
			return err
		}
		logger.Info("http request", fields...)
		return nil
	}
}

func localRequestID(c *fiber.Ctx) string {
	if v, ok := c.Locals(string(RequestIDKey)).(string); ok {
		return v
	}
	return ""
}

package middleware

import (
	"context"
	"crypto/rand"
	"encoding/hex"

	"github.com/gofiber/fiber/v2"
)

type contextKey string

const RequestIDKey contextKey = "request_id"

const requestIDHeader = "X-Request-ID"

// RequestID assigns a unique identifier to every request, honoring an
// inbound header when present.
func RequestID() fiber.Handler {
	return func(c *fiber.Ctx) error {
		id := c.Get(requestIDHeader)
		if id == "" {
			generated, err := randomID()
			if err != nil {
				return c.Next()
			}
			id = generated
		}

		c.Set(requestIDHeader, id)
		c.Locals(string(RequestIDKey), id)
		// Also propagate through the user context so context-aware
		// downstream code can retrieve the identifier via RequestIDFrom.
		c.SetUserContext(context.WithValue(c.UserContext(), RequestIDKey, id))
		return c.Next()
	}
}

// RequestIDFrom retrieves the identifier stored by RequestID.
func RequestIDFrom(ctx context.Context) string {
	if v, ok := ctx.Value(RequestIDKey).(string); ok {
		return v
	}
	return ""
}

func randomID() (string, error) {
	buf := make([]byte, 8)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

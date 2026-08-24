package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/alberdjuniawan/adaptive-db-pool/backend/internal/domain/admission"
)

// Strategy identifies the admission strategy selected at boot.
type Strategy string

const (
	StrategyStatic    Strategy = "static"
	StrategyHeuristic Strategy = "heuristic"
	StrategyAdaptive  Strategy = "adaptive"
)

// Config carries every runtime parameter. Values come from the
// environment only — experimental parameters must not be hard-coded
type Config struct {
	Port              int
	AppEnv            string
	DatabaseURL       string
	DBMaxConns        int32
	DBMinConns        int32
	PrometheusEnabled bool

	AdmissionStrategy Strategy
	AdmissionLimit    int

	AdmissionMin        int
	AdmissionMax        int
	AdmissionMaxDelta   int
	AdmissionCooldown   time.Duration
	HeuristicInterval   time.Duration
	ControllerFreshTime time.Duration

	RequestTimeout time.Duration
}

// Load reads configuration from environment variables with safe defaults.
func Load() (*Config, error) {
	cfg := &Config{
		Port:              8080,
		AppEnv:            "development",
		DBMaxConns:        50,
		DBMinConns:        2,
		PrometheusEnabled: true,
		AdmissionStrategy: StrategyStatic,
		AdmissionLimit:    20,

		AdmissionMin:        4,
		AdmissionMax:        64,
		AdmissionMaxDelta:   4,
		AdmissionCooldown:   5 * time.Second,
		HeuristicInterval:   1 * time.Second,
		ControllerFreshTime: 60 * time.Second,
		RequestTimeout:      10 * time.Second,
	}

	var err error
	if v := os.Getenv("PORT"); v != "" {
		if cfg.Port, err = strconv.Atoi(v); err != nil {
			return nil, fmt.Errorf("config PORT: %w", err)
		}
	}
	if v := os.Getenv("APP_ENV"); v != "" {
		cfg.AppEnv = v
	}
	if v := os.Getenv("DATABASE_URL"); v != "" {
		cfg.DatabaseURL = v
	}
	if cfg.DatabaseURL == "" {
		return nil, fmt.Errorf("config DATABASE_URL is required")
	}

	if cfg.DBMaxConns, err = envInt32("DB_MAX_CONNS", cfg.DBMaxConns); err != nil {
		return nil, err
	}
	if cfg.DBMinConns, err = envInt32("DB_MIN_CONNS", cfg.DBMinConns); err != nil {
		return nil, err
	}
	if cfg.PrometheusEnabled, err = envBool("PROMETHEUS_ENABLED", cfg.PrometheusEnabled); err != nil {
		return nil, err
	}

	switch strings.ToLower(os.Getenv("ADMISSION_STRATEGY")) {
	case "", string(StrategyStatic):
		cfg.AdmissionStrategy = StrategyStatic
	case string(StrategyHeuristic):
		cfg.AdmissionStrategy = StrategyHeuristic
	case string(StrategyAdaptive):
		cfg.AdmissionStrategy = StrategyAdaptive
	default:
		return nil, fmt.Errorf("config ADMISSION_STRATEGY: unknown strategy %q", os.Getenv("ADMISSION_STRATEGY"))
	}

	if cfg.AdmissionLimit, err = envInt("ADMISSION_LIMIT", cfg.AdmissionLimit); err != nil {
		return nil, err
	}
	if cfg.AdmissionMin, err = envInt("ADMISSION_MIN_LIMIT", cfg.AdmissionMin); err != nil {
		return nil, err
	}
	if cfg.AdmissionMax, err = envInt("ADMISSION_MAX_LIMIT", cfg.AdmissionMax); err != nil {
		return nil, err
	}
	if cfg.AdmissionMaxDelta, err = envInt("ADMISSION_MAX_DELTA", cfg.AdmissionMaxDelta); err != nil {
		return nil, err
	}
	if v := os.Getenv("ADMISSION_COOLDOWN"); v != "" {
		if cfg.AdmissionCooldown, err = time.ParseDuration(v); err != nil {
			return nil, fmt.Errorf("config ADMISSION_COOLDOWN: %w", err)
		}
	}
	if v := os.Getenv("HEURISTIC_INTERVAL"); v != "" {
		if cfg.HeuristicInterval, err = time.ParseDuration(v); err != nil {
			return nil, fmt.Errorf("config HEURISTIC_INTERVAL: %w", err)
		}
	}
	if v := os.Getenv("CONTROLLER_FRESH_TIMEOUT"); v != "" {
		if cfg.ControllerFreshTime, err = time.ParseDuration(v); err != nil {
			return nil, fmt.Errorf("config CONTROLLER_FRESH_TIMEOUT: %w", err)
		}
	}
	if v := os.Getenv("REQUEST_TIMEOUT"); v != "" {
		if cfg.RequestTimeout, err = time.ParseDuration(v); err != nil {
			return nil, fmt.Errorf("config REQUEST_TIMEOUT: %w", err)
		}
	}

	// Validate bounds early so an unsafe configuration cannot boot.
	if cfg.AdmissionLimit < 1 {
		return nil, fmt.Errorf("config ADMISSION_LIMIT must be >= 1")
	}
	if cfg.AdmissionMin < 1 || cfg.AdmissionMax < cfg.AdmissionMin {
		return nil, fmt.Errorf("config admission bounds invalid: min=%d max=%d", cfg.AdmissionMin, cfg.AdmissionMax)
	}
	if cfg.AdmissionLimit > cfg.AdmissionMax || cfg.AdmissionLimit < cfg.AdmissionMin && cfg.AdmissionStrategy == StrategyAdaptive {
		return nil, fmt.Errorf("config ADMISSION_LIMIT=%d outside bounds [%d,%d]", cfg.AdmissionLimit, cfg.AdmissionMin, cfg.AdmissionMax)
	}

	return cfg, nil
}

// ToDomainStrategy maps the configured name to a domain identifier used
// in telemetry labels.
func (c *Config) ToDomainStrategy() admission.StrategyLabel {
	switch c.AdmissionStrategy {
	case StrategyHeuristic:
		return admission.StrategyLabelHeuristic
	case StrategyAdaptive:
		return admission.StrategyLabelAdaptive
	default:
		return admission.StrategyLabelStatic
	}
}

func envInt(name string, fallback int) (int, error) {
	v := os.Getenv(name)
	if v == "" {
		return fallback, nil
	}
	parsed, err := strconv.Atoi(v)
	if err != nil {
		return 0, fmt.Errorf("config %s: %w", name, err)
	}
	return parsed, nil
}

func envInt32(name string, fallback int32) (int32, error) {
	v := os.Getenv(name)
	if v == "" {
		return fallback, nil
	}
	parsed, err := strconv.ParseInt(v, 10, 32)
	if err != nil {
		return 0, fmt.Errorf("config %s: %w", name, err)
	}
	return int32(parsed), nil
}

func envBool(name string, fallback bool) (bool, error) {
	v := os.Getenv(name)
	if v == "" {
		return fallback, nil
	}
	parsed, err := strconv.ParseBool(v)
	if err != nil {
		return false, fmt.Errorf("config %s: %w", name, err)
	}
	return parsed, nil
}

# Adaptive DB Pool

**Adaptive Database Connection Pool Optimization Using Workload-Aware Machine Learning with Continuous Monitoring and Feedback Control**

A reproducible research system investigating whether database concurrency/admission can be optimized dynamically from observed workload and system state — without sacrificing latency, throughput, stability, or database safety.

## Core Concept

The most important design distinction:

| Component | Role | Adaptive? |
| --- | --- | --- |
| **Physical pool** (`pgxpool.MaxConns`) | Hard capacity boundary to PostgreSQL | No - static during the baseline phase |
| **Logical admission control** | Primary control variable (semaphore in front of the pool) | Yes - this is where the research happens |

Rationale: adaptive *logical* concurrency must be proven to improve the system before touching physical pool resizing. This minimizes experimental noise.

```text
Workload Generator (k6)
       │
       ▼
Backend (Fiber API) ─── Admission Control (control variable)
       │                        ▲
       ▼                        │ new limit
PostgreSQL ◄──── pgxpool        │
       │                        │
       ▼                        │
Telemetry ──► Prometheus ──► Controller Service (Python)
                                │  ML predictor → optimizer → safety layer
                                └────────────────────────────────┘
```

## Repository Layout

```text
backend/        Go API (Fiber + pgx/v5 + sqlc), admission controller
  cmd/api/      Entry point + manual dependency injection assembly
  internal/
    domain/          Business/research concepts (no technology deps)
      admission/     Semaphore controller + 3 interchangeable strategies
      workload/      Workload classes + domain models
      metrics/       Domain telemetry contracts (interfaces)
    application/     Use case orchestration
      workload/      Query execution service under admission control
      admission/     Strategy lifecycle + control plane
    infrastructure/  Technology implementations
      config/        Env parsing with bounds validation
      database/      pgxpool + sqlc repository
      metrics/       Prometheus registry
    delivery/http/   Router, middleware, handlers, error mapping
  sql/queries/    SQL source of truth (sqlc input)
postgres/       Goose migrations + seed data
ml/             Python pipeline (data → features → models → evaluation)
controller/     Closed-loop service (telemetry → ML → optimizer → actuator)
experiments/    k6 scenarios, experiment configs, runner, results
monitoring/     Prometheus config + Grafana provisioning
docs/           Full architecture and flow documentation
data/           DVC-tracked datasets (raw / processed / external)
```

Full documentation lives in [`docs/`](docs):

- **[Architecture & Flow](docs/architecture.md)** — request flow, admission mechanics, safety chain, closed loop, ML pipeline
- **[Operations](docs/operations.md)** — running experiments, training models, monitoring, troubleshooting

## Prerequisites

- Go 1.26+
- Docker + Docker Compose
- sqlc (`go install github.com/sqlc-dev/sqlc/cmd/sqlc@latest`)
- goose (`go install github.com/pressly/goose/v3/cmd/goose@latest`)
- k6 (optional, load testing only)
- Python 3.11+ (optional, ML/controller pipeline)

## Quick Start

```bash
cp .env.example .env

# 1. Infrastructure
docker compose up -d postgres

# 2. Schema + data
make migrate-up
make seed                # small dataset; 'make seed-benchmark' for 10k products

# 3. Generate the type-safe DB layer
make sqlc

# 4. Run the full stack (postgres + backend + prometheus + grafana)
docker compose up -d --build

# 5. Verify
curl localhost:8080/health
curl localhost:8080/api/workload/simple/1
curl "localhost:8080/api/workload/aggregation"
```

Local development mode (backend without Docker):

```bash
DATABASE_URL='postgres://adaptive:adaptive@localhost:5432/adaptive?sslmode=disable' make dev
```

## API Endpoints

| Method | Path | Workload class | Description |
| --- | --- | --- | --- |
| GET | `/health` | — | Liveness + admission state |
| GET | `/metrics` | — | Prometheus exposition |
| GET | `/api/workload/simple/:id` | `simple` | Product point lookup |
| GET | `/api/workload/products` | `simple` | Paged product listing |
| GET | `/api/workload/medium/:id` | `medium` | Orders + order_items join |
| GET | `/api/workload/complex/:id` | `complex` | Multi-join + per-order aggregation |
| GET | `/api/workload/aggregation` | `aggregation` | Analytical group-by per category |
| GET | `/api/admin/admission` | — | Admission state (strategy, limit, active, waiting) |
| POST | `/api/admin/admission/limit` | — | Control plane: apply a candidate limit |

Every database request is wrapped by the admission controller's Acquire/Release — concurrent queries reaching PostgreSQL never exceed the active limit.

## Admission Strategies

Selected via `ADMISSION_STRATEGY`; all three are interchangeable (Strategy Pattern):

| Strategy | Behavior | Research role |
| --- | --- | --- |
| `static` (default) | Fixed limit | Baseline A |
| `heuristic` | Utilization/queue thresholds, periodic evaluation | Baseline B |
| `adaptive` | External decisions from the controller service + dual safety envelope | ML-driven adaptive system |

All strategies pass through the same safety layer: bounds `[ADMISSION_MIN_LIMIT, ADMISSION_MAX_LIMIT]`, maximum change per decision (`ADMISSION_MAX_DELTA`), cooldown (`ADMISSION_COOLDOWN`), hysteresis, and a fail-safe fallback to a safe static limit when the controller disappears (`CONTROLLER_FRESH_TIMEOUT`). Details: [architecture](docs/architecture.md).

## Configuration (Environment Variables)

### Backend

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `8080` | Backend HTTP port |
| `APP_ENV` | `development` | Application environment |
| `DATABASE_URL` | *(required)* | PostgreSQL DSN |
| `DB_MAX_CONNS` | `50` | `pgxpool.MaxConns` — static |
| `DB_MIN_CONNS` | `2` | `pgxpool.MinConns` |
| `ADMISSION_STRATEGY` | `static` | `static` \| `heuristic` \| `adaptive` |
| `ADMISSION_LIMIT` | `20` | Initial limit |
| `ADMISSION_MIN_LIMIT` | `4` | Safety lower bound |
| `ADMISSION_MAX_LIMIT` | `64` | Safety upper bound |
| `ADMISSION_MAX_DELTA` | `4` | Maximum change per decision |
| `ADMISSION_COOLDOWN` | `5s` | Minimum delay between limit changes |
| `HEURISTIC_INTERVAL` | `1s` | Heuristic evaluation frequency |
| `CONTROLLER_FRESH_TIMEOUT` | `60s` | Silence window before fail-safe fallback |
| `REQUEST_TIMEOUT` | `10s` | Per-request timeout (admission + query) |
| `PROMETHEUS_ENABLED` | `true` | Enable metrics |

### Controller service (compose profile `controller`)

`BACKEND_URL`, `PROMETHEUS_URL`, `CONTROLLER_INTERVAL`, `ADMISSION_*` (must match the backend), `MODEL_PATH`, objective weights `W_LATENCY/W_ERROR/W_WAIT/W_RESOURCE`.

Rule: never commit `.env`. Use `.env.example` as the template.

## Monitoring

- Prometheus: <http://localhost:9090> — scrapes the backend every 5s
- Grafana: <http://localhost:3000> (anon-viewer enabled; admin/admin to edit)

Available dashboards ("Adaptive DB Pool" folder):

| Dashboard | Contents |
| --- | --- |
| Overview | RPS, P50/P95/P99, errors, admission limit |
| Admission | active vs waiting, wait duration, limit, limit-change frequency |
| Database Pool | max/acquired/idle, pool utilization, acquisition wait |
| Controller | applied limit, decisions by reason, control quality |

Metrics use `adaptive_db_pool_*` names with low-cardinality labels only (`route`, `status`, `query_class`, `controller_strategy`, `reason`) — no user IDs or raw SQL.

## Testing

```bash
cd backend
go test ./...            # unit tests
go test ./... -race      # race detection
go vet ./...
gofmt -l .               # must print nothing
```

Admission controller test coverage (mandatory per the research protocol):

- Acquire succeeds when a slot is free
- Requests queue when full and proceed after Release
- Context cancellation returns an error without leaking slots
- Acquire→Release→Acquire cycles never deadlock (1000 iterations)
- `active <= limit` invariant under 32 goroutines × 200 cycles
- Adaptive safety chain: clamping, delta bound, cooldown, hysteresis, fallback

Integration tests use real PostgreSQL (not mocked).

## ML Pipeline

```bash
pip install -r ml/requirements.txt

# 1. Collect raw telemetry from Prometheus (requires live load)
./experiments/scripts/collect.sh 300

# 2. Validate + align + feature engineering → data/processed/dataset.csv
python ml/scripts/prepare_data.py

# 3. Train candidates: linear_regression, random_forest, xgboost, mlp
python ml/scripts/train.py

# 4. Evaluate on the temporal test set
python ml/scripts/evaluate.py

# 5. Deploy the best model to the online controller
cp ml/models/random_forest.joblib ml/models/predictor.joblib
```

The split is temporal (never random) to prevent leakage; the MLP scaler is fitted on training data only. Every artifact ships with a provenance file (git commit, feature order, metrics).

Data-flow details: [architecture § ML pipeline](docs/architecture.md).

## Experiments

```bash
# Run one reproducible experiment from YAML
./experiments/scripts/run.sh experiments/configs/baseline.yaml

# Compare against the adaptive strategy
ADMISSION_STRATEGY=adaptive docker compose up -d backend
./experiments/scripts/run.sh experiments/configs/adaptive.yaml
```

Results land in `experiments/results/<id>_<timestamp>/`: `metadata.json` (experiment_id, git commit, configuration), warmup/measurement logs, k6 summary, Prometheus metric snapshot, final state.

k6 scenarios: `low.js`, `medium.js`, `high.js`, `mixed.js` (ramping for workload-transition/stability testing, RQ4). Parameters via `-e TARGET`, `-e DURATION`, `-e BASE_URL`.

## Research Questions

| # | Question | How the system answers it |
| --- | --- | --- |
| RQ1 | Can ML predict the right concurrency level? | Prediction MAE/RMSE vs baseline sweep |
| RQ2 | Does adaptive admission improve tail latency? | P95/P99 adaptive vs static under identical scenarios |
| RQ3 | Is throughput maintained while reducing contention/wait? | Admission wait + pool utilization metrics |
| RQ4 | How stable is the controller across workload transitions? | `mixed.js` ramping scenario + limit oscillation analysis |
| RQ5 | Does adaptive beat baselines without unsafe oscillation? | static vs heuristic vs adaptive comparison |

A model counts as successful **only if it beats meaningful baselines**, not merely because it runs.

## Project Rules

Binding for anyone (human or agent) modifying the repository:

1. No ORM — sqlc only; never edit generated files manually.
2. No business logic in HTTP handlers.
3. No DB access via global variables; manual DI in `main.go`.
4. Experimental parameters must never be hard-coded in source — everything comes from environment/config.
5. `pgxpool.MaxConns` must stay non-adaptive during the baseline phase.
6. Static and adaptive strategies must remain interchangeable.
7. Every experiment must be reproducible from its configuration.
8. Every controller decision must be observable (logs + metrics).
9. Do not add infrastructure/ML complexity without experimental justification.

Full design rationale: [docs/architecture.md](docs/architecture.md).

## Troubleshooting

| Symptom | Common cause | Fix |
| --- | --- | --- |
| Backend fails to boot: `DATABASE_URL is required` | Env not loaded | `cp .env.example .env` or set variables explicitly |
| Port 8080 already in use | Local dev process still running | Kill the stale process before `compose up` |
| `simple/:id` → all 404 | Seed not run | `make migrate-up && make seed` |
| Grafana empty | Prometheus not scraping yet | Wait ~10s; check targets at `localhost:9090/api/v1/targets` |
| `POST /admin/admission/limit` → 409 | `static` strategy rejects changes | Switch to `ADMISSION_STRATEGY=heuristic` or `adaptive` |
| Controller logs `telemetry incomplete` | Prometheus has no samples yet | Ensure traffic exists; check job `adaptive-db-pool-backend` is up |

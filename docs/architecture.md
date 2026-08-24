# Architecture & Flow

This document explains the system end to end: how a request moves through the backend layers, how the admission controller works at the semaphore level, how telemetry becomes control decisions, and how the ML pipeline is trained and plugged back into the system.

---

## 1. Overview: The Closed Loop

```text
            ┌──────────────────────────────────────────────────────────┐
            │                                                          │
            ▼                                                          │
   ┌─────────────────┐        ┌──────────────┐                          │
   │  Workload (k6)  │        │  Controller  │                          │
   └────────┬────────┘        │  (Python)    │                          │
            │                 └──────┬───────┘                          │
            │ HTTP                   │ POST /api/admin/admission/limit  │
            ▼                        │ (candidate limit + reason)       │
   ┌─────────────────────────────────┴──────────────────┐               │
   │                    Backend (Go)                     │              │
   │  HTTP → Middleware → Handler                        │              │
   │      → Application Service                          │              │
   │          → Admission Control ◄── control variable   │              │
   │              → Repository → sqlc → pgxpool          │              │
   └───────────────────────┬─────────────────────────────┘               │
                            ▼                                             │
                     ┌─────────────┐                                      │
                     │  PostgreSQL │                                      │
                     └──────┬──────┘                                      │
                            ▼                                             │
                     Telemetry (in-process metrics)                       │
                            ▼                                             │
                      Prometheus ◄────────────────────────────────────────┘
                        (5s scrape)         controller reads from here
```

Two principles distinguish this design:

1. **Static physical pool** — `pgxpool.MaxConns` stays at 50. What is adaptive is *logical admission* (the semaphore), so control-decision effects are measured cleanly without connection-resizing noise.
2. **Prediction ≠ decision** — ML only produces cost predictions; the optimizer picks a candidate; the safety layer (two layers: Python + Go) decides whether the limit actually changes.

---

## 2. End-to-End Request Flow

Example `GET /api/workload/simple/42`:

```text
k6 / curl
   │
   ▼
[Fiber] ──► middleware.RequestID      request ID (X-Request-ID header or random hex)
   │
   ├──► middleware.Recovery           panic → 500 + stack log (backend survives)
   │
   ├──► middleware.Logging            one structured log line per request:
   │                                  request_id, method, route, status, duration_ms
   │
   ├──► requestMetrics                record start time; observe after completion
   │
   ▼
[Workload handler]                    PARSE ONLY — no business logic
   │  invalid params("id")? ─────────────► 400
   │  locals("query_class") tag = "simple"
   ▼
[Application service]
   │  execute(ctx, strategy, timeout, fn):
   │     acquireCtx = ctx + REQUEST_TIMEOUT (10s default)
   │     strategy.Acquire(acquireCtx)
   │        ├─ slot free    → proceed
   │        ├─ queue full & timeout → AdmissionError → 504/503
   │     defer strategy.Release()   ← GUARANTEED on every path
   ▼
[Repository (infra)]                  translates pgx.ErrNoRows → ErrNotFound
   ▼
[sqlc generated queries]
   ▼
[pgxpool]                             max 50 conns, min 2, health check 10s
   ▼
PostgreSQL
```

Centralized error mapping (`handler/errors.go`):

| Condition | HTTP |
| --- | --- |
| Invalid parameters | 400 |
| Resource not found | 404 |
| Context deadline/cancellation upstream | 504 |
| Rejected by admission controller | 503 |
| Unexpected database error | 500 (details never exposed) |

Per-request metric labels: `method`, `route`, `status` (2xx/3xx/4xx/5xx), `query_class` — all low-cardinality.

---

## 3. Admission Controller Mechanics

`domain/admission/controller.go` — a semaphore with a FIFO queue and a dynamic limit:

```text
Acquire(ctx):
   lock
   ├─ active < limit ?  active++ ; unlock ; return nil        (fast path)
   └─ push channel (buffered 1) onto queue ; waiting++ ; unlock
      select {
        case <-ch:         return nil                          (granted)
        case <-ctx.Done(): abandon(ctx, ch)
      }

abandon(ctx, ch):
   lock
   ├─ still queued?   remove from queue ; waiting--           (clean cancel)
   └─ already granted? active-- ; notify()                    (no slot leaks)
```

Key properties:

- **`active ≤ limit` invariant** always holds (tested by 32 goroutines × 200 cycles).
- **No slot leaks** on cancellation-vs-grant races — the `abandon` path releases an already-granted slot.
- **Shrink is non-preemptive** — lowering the limit never cuts active requests; it only holds newcomers until capacity allows.
- **SetLimit growth admits immediately** — raising the limit automatically grants slots to pending waiters.
- Every successfully acquired slot must be released via `defer Release()` (guaranteed by the `execute()` wrapper).

### Strategies (Strategy Pattern)

```text
AdmissionStrategy { Acquire(ctx) error; Release(); Limit() int }
        ▲
        ├── StaticStrategy      Baseline A: fixed limit
        ├── HeuristicStrategy   Baseline B: periodic threshold rule
        └── AdaptiveStrategy    External decisions + safety envelope
```

**HeuristicStrategy** (evaluated every `HEURISTIC_INTERVAL`, default 1s):

```text
observation: active, waiting, limit (from controller) + pool stats (from pgxpool)

utilisation = active / limit
if waiting > 0 and utilisation ≥ 0.9 → limit += step   (reason: saturation)
if waiting == 0 and utilisation < 0.3 → limit -= step  (reason: underutilization)
always within [min, max]; each change goes to a DecisionSink
```

**AdaptiveStrategy** receives candidates from the controller service and applies the Go-side safety envelope (second layer, *defense in depth*):

```text
Apply(candidate, reason):
   clamped = clamp(candidate, min, max)          # bounds
   clamped == current ? return current           # hysteresis/no-op
   within cooldown? return current               # cooldown
   delta > Δmax? trim to current ± Δmax          # rate limiting
   SetLimit(clamped) → DecisionSink(reason, old, new)

EnsureFresh():                                   # fail-safe
   no signal for > FRESH_TIMEOUT → Apply(fallbackTo, "fallback_stale_controller")
```

Any received candidate refreshes liveness — even one rejected by hysteresis or cooldown. If no candidate ever arrives after boot, EnsureFresh treats the controller as stale and engages the fallback.

Every decision is recorded twice: structured JSON logs (old/new/reason/timestamp) **and** Prometheus metrics `controller_decisions_total{strategy,reason}` + `controller_limit_changes_total`. This satisfies the "every decision must be observable" requirement.

---

## 4. Safety Chain (Two Layers)

```text
Candidate limit from ML
        │
        ▼
[Layer 1 — Python controller/src/safety.py]
   1. bounds [min, max]
   2. hysteresis: improvement < threshold → skip
   3. no-op check
   4. rate limit ±Δmax
   5. cooldown between changes
        │
        ▼  POST /api/admin/admission/limit
[Layer 2 — Go AdaptiveStrategy.Apply]
   re-bound → re-cooldown → re-delta → SetLimit
        │
        ▼
Active semaphore with the new limit
```

Tiered fail-safes:

| Failure | Response |
| --- | --- |
| Controller service dies | Backend `EnsureFresh` falls back to a safe static limit |
| Prediction throws | Cycle skipped, error logged, loop stays alive |
| Actuator POST fails | `actuation_failed` logged; marked stale after `fresh_timeout` |
| Invalid config bounds at boot | Backend **refuses to start** |

---

## 5. Telemetry & Metrics Flow

All metrics are named `adaptive_db_pool_*` (registry in `infrastructure/metrics/prometheus.go`):

```text
Backend (in-process)
 ├─ requestMetrics middleware ──► requests_total, request_duration_seconds, request_errors_total
 ├─ admission collector (1s) ───► admission_active, admission_waiting, admission_limit
 ├─ admission wait histogram ───► admission_wait_seconds (measured around Acquire)
 ├─ pool collector (5s) ────────► db_pool_max/acquired/idle_connections, db_pool_wait_seconds
 └─ decision sink ──────────────► controller_decisions_total, controller_limit_changes_total
        │ /metrics
        ▼
Prometheus (5s scrape) ──► Grafana (4 dashboards) ──► controller service (query API)
                                                 └─► collect.sh (raw dataset export)
```

Label rules: allowed `route`, `status`, `query_class`, `controller_strategy`, `reason`, `workload_type`. Forbidden: user IDs, request IDs, SQL text.

---

## 6. Closed-Loop Flow (Controller Service)

One cycle of `controller/src/main.py` (every `CONTROLLER_INTERVAL`, default 5s):

```text
1. FETCH      11 metrics from Prometheus → Telemetry object
              (the live limit is read from telemetry — the backend is authoritative)

2. PREDICT    JoblibModelPredictor (when ml/models/predictor.joblib exists)
              or the analytic HeuristicPredictor (safe fallback):
                 J(cand) = w_lat · p99 · (1 + 2·u²) + contention
                         + w_err · error_rate
                         + w_wait · waiting / cand
                         + w_res · pool_utilisation
              u = active / cand → the u² term records the contention knee

3. OPTIMIZE   GridOptimizer: evaluates candidates current±8 step 2
              → argmin of predicted J

4. SAFETY     improvement_hint = (J_current − J_best)/|J_current|
              → SafetyLayer.evaluate (bounds, hysteresis, delta, cooldown)

5. ACTUATE    POST to backend → second safety layer → new limit
              OR log no_change (held, best_candidate)

6. ERROR      any exception → log + skip cycle; the loop never dies
```

Default objective weights (`W_LATENCY=1.0, W_ERROR=3.0, W_WAIT=1.5, W_RESOURCE=0.05`): tail latency dominates because RQ2 targets P99 improvement; errors and admission waits are strong penalties; resource cost is mild anti-over-provisioning pressure.

---

## 7. ML Pipeline Flow

```text
[baseline experiments: static limit sweeps + k6]
        │  ./experiments/scripts/collect.sh <duration>
        ▼
data/raw/*.json                    Prometheus query_range exports
        │  ml/scripts/prepare_data.py
        ▼
load_window(): merge all exports per window,
              5s timestamp buckets, ffill alignment
feature engineering: utilization, pool_utilization
TARGET objective_j = observed J per window
validate(): nulls/non-finite/minimum size → validation_report.json
        ▼
data/processed/dataset.csv
        │  ml/scripts/train.py
        ▼
temporal_split 70/15/15  ← NOT random; prevents temporal leakage
fit candidates: linear_regression | random_forest | xgboost | mlp
  - tree models: no scaling
  - MLP: scaler fitted on TRAINING rows only
validation evaluation: MAE, RMSE, MAPE*, prediction latency
persist: <model>.joblib + <model>.provenance.json
        (git_commit, trained_at, FEATURES order, metrics)
        │  ml/scripts/evaluate.py
        ▼
temporal test set (never touched before) → ml/models/test_report.json
        │  cp <best>.joblib predictor.joblib
        ▼
controller service loads the artifact at boot;
load failure → analytic heuristic fallback (system stays safe)
```

\* MAPE reports `NaN` when targets contain values ≤ 0 — meaningless for such data.

The feature order (`ml/src/__init__.py::FEATURES`) is a contract: changing order/composition invalidates every persisted artifact.

Artifacts persist the plain sklearn/xgboost estimator rather than wrapper classes, so unpickling never depends on package naming at load time.

---

## 8. Experiment Flow

```text
experiments/configs/<name>.yaml     ONE file = one reproducible experiment
        │
        ▼
scripts/run.sh:
  1. metadata.json   experiment_id, UTC timestamp, git commit,
                     model/controller version, strategy, limit, scenario,
                     durations, DB/backend/workload config, hardware fingerprint
  2. preflight health check against BASE_URL
  3. set initial limit via the control plane
  4. k6 warmup (duration.duration.warmup)
  5. k6 measurement (arrival rate .workload.arrival_rate)
     + PromQL admission_limit sampler every 2s (oscillation analysis)
  6. capture backend_metrics.prom + final_state.txt
        │
        ▼
experiments/results/<id>_<timestamp>/
```

Experiment variables per the protocol: intensity (low/medium/high), query-class mix (simple/mixed/complex), concurrency sweep (1…64+), and strategy (static/heuristic/adaptive). Workload transitions use the ramping `mixed.js` scenario.

---

## 9. Design Decisions & Rationale

| Decision | Reason |
| --- | --- |
| Modular monolith, not microservices | The research target is DB behavior; extra network hops are experimental noise |
| sqlc + goose, no ORM/Squirrel | Explicit SQL, deterministic workloads, type-safe Go code; version-controlled migrations |
| Logical admission, not physical pool resizing | Prove adaptive concurrency works before touching physical capacity |
| FIFO semaphore + buffered-1 channels | Fair queueing; non-blocking grants; easy deadlock-freedom proof |
| Decisions in Python, application in Go | Python's ML ecosystem; safety envelope duplicated across two languages |
| Temporal split, never random | The system operates over time; random splits leak the future |
| Analytic heuristic fallback | The controller must remain safely functional before any model exists |
| No Kafka/K8s/tracing | Minimal infrastructure until experiments genuinely require it |
| Low-cardinality metric labels | Avoid Prometheus series explosion |

## 10. Non-Goals

This project is deliberately NOT: a generic database proxy, a cloud autoscaler, a Kubernetes operator, a universal query optimizer, a distributed database, an ORM framework, or a SaaS product. The research target is specific: *workload-aware database admission/concurrency optimization using ML and feedback control.*

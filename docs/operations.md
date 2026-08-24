# Operations: Run, Measure, Train

Day-to-day guide for operating the system, running experiments, training models, and driving the closed loop.

---

## 1. Daily Commands

| Command | Purpose |
| --- | --- |
| `make dev` | Run the backend locally (requires `DATABASE_URL`) |
| `make up` / `make down` | Start/stop the full Docker stack |
| `make logs` | Follow backend container logs |
| `make migrate-up` / `make migrate-down` | Goose schema up/down one version |
| `make migrate-status` | Migration status |
| `make seed` | Development seed (200 products, 500 orders) |
| `make seed-benchmark` | Benchmark seed (10k products, 50k orders) — idempotent |
| `make sqlc` | Regenerate sqlc code (mandatory after editing `backend/sql/queries/`) |
| `make test` | All Go tests |
| `make lint` | go vet + gofmt check |
| `make build` | Build binary into `backend/bin/` |
| `make benchmark SCENARIO=mixed` | Run a k6 scenario |

---

## 2. System Operating Modes

### A. Static baseline (default)

```bash
docker compose up -d --build
# backend runs with ADMISSION_STRATEGY=static, limit 20
```

### B. Heuristic baseline (Baseline B)

```bash
ADMISSION_STRATEGY=heuristic \
HEURISTIC_INTERVAL=1s \
ADMISSION_MIN_LIMIT=4 ADMISSION_MAX_LIMIT=64 \
docker compose up -d backend
```

Watch limit oscillation on the **Controller** dashboard.

### C. Adaptive closed loop

```bash
# 1. Backend in adaptive mode
ADMISSION_STRATEGY=adaptive docker compose up -d backend

# 2. Controller service (separate compose profile)
docker compose --profile controller up -d ml-controller

# without a model → analytic heuristic predictor (still safe)
# with a model    → ml/models/predictor.joblib mounted automatically via volume
```

Verify the loop is alive:

```bash
curl localhost:8080/api/admin/admission          # {"limit":..,"strategy":"adaptive",...}
docker compose logs -f ml-controller             # limit_change / no_change events
```

Exercise the safety chain manually:

```bash
curl -X POST localhost:8080/api/admin/admission/limit \
  -H 'Content-Type: application/json' -d '{"limit":60,"reason":"manual_test"}'
# → applied_limit clamped to ±ADMISSION_MAX_DELTA of the current limit,
#   rejected during cooldown; static strategy → HTTP 409
```

---

## 3. Running Experiments

### 3.0 Runner dependencies (once)

```bash
# jq & curl come from distro repos; k6 & yq are not packaged universally.
# Manual install into ~/.local/bin (Linux amd64):
K6_VER=v1.4.0
curl -fL "https://github.com/grafana/k6/releases/download/${K6_VER}/k6-${K6_VER}-linux-amd64.tar.gz" | tar -xz -C /tmp
install -m0755 "/tmp/k6-${K6_VER}-linux-amd64/k6" ~/.local/bin/k6

curl -fL "https://github.com/mikefarah/yq/releases/download/v4.45.4/yq_linux_amd64" -o /tmp/yq
install -m0755 /tmp/yq ~/.local/bin/yq

echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
k6 version && yq --version   # verify
```

### 3.1 Benchmark data preparation

The benchmark seed only fills tables that are **empty**. To switch from development data to benchmark scale:

```bash
docker compose up -d postgres
make migrate-up
PGPASSWORD=adaptive psql -h localhost -U adaptive -d adaptive \
  -c "TRUNCATE order_items, orders, products RESTART IDENTITY CASCADE;"
make seed-benchmark    # → 10,000 products / 50,000 orders / ~200,000 items
```

### 3.2 Running a single experiment

```bash
./experiments/scripts/run.sh experiments/configs/baseline.yaml
```

The runner performs a preflight: it verifies `${BASE_URL}/health` is alive and consistent with the configured `admission.limit` — otherwise it stops before wasting time. Make sure no stale backend instance occupies the same port (restart old instances after rebuilding the binary).

Runner dependencies: `yq`, `jq`, `k6`, `curl`. Results land in `experiments/results/<experiment_id>_<timestamp>/`:

```text
metadata.json            provenance: id, UTC timestamp, git commit, model/controller version,
                         DB/backend/workload configuration, hardware fingerprint (kernel/CPU/RAM/host)
config.resolved.yaml     copy of the experiment config
warmup.log               k6 warmup output
k6_output.log            k6 measurement output (stdout)
k6_summary.json          k6 metrics summary (JSON)
prometheus_limit.jsonl   admission_limit samples every 2s during measurement
                         (input for stability/oscillation analysis RQ4-RQ5)
backend_metrics.prom     final full metric snapshot
final_state.txt          final health state
```

Workload knobs live in the experiment YAML (no code changes needed):

| Key | Default | Meaning |
| --- | --- | --- |
| `workload.scenario` | — | `low` / `medium` / `high` / `mixed` |
| `workload.arrival_rate` | — | target arrival rate per second |
| `workload.products_max` / `orders_max` | 200 / 500 (dev seed) | ID ranges; set 10000/50000 for the benchmark seed |
| `duration.warmup` / `.measurement` | — | duration of each phase |

Creating experiment variants: copy the YAML and change `admission.limit` / `workload.arrival_rate` / `workload.scenario`. Never hard-code parameters in application source.

### 3.3 Phase 4 baseline sweep

Compare static limits to find the safe operating range before training any ML:

```bash
for L in 4 8 16 32 64; do
  sed "s/^  limit: .*/  limit: ${L}/" experiments/configs/baseline.yaml \
    > experiments/configs/baseline_L${L}.yaml
  BASE_URL=http://localhost:8080 ./experiments/scripts/run.sh \
    experiments/configs/baseline_L${L}.yaml
done
```

Quick cross-run analysis (`http_req_duration` in `k6_summary.json` is milliseconds):

```bash
for d in experiments/results/exp_baseline_static_*/; do
  printf "%s  p95=%sms  reqs=%s\n" "$d" \
    "$(jq -r '.metrics["http_req_duration"]["p(95)"]' $d/k6_summary.json)" \
    "$(jq -r '.metrics.http_reqs.count' $d/k6_summary.json)"
done
```

The optimal limit is where P95 rises sharply without a meaningful throughput gain. This sweep dataset feeds the ML pipeline (§4).

---

## 4. Model Training Flow

Prerequisite: baseline telemetry from completed experiments.

```bash
pip install -r ml/requirements.txt

# 1) Export the last 300 seconds of Prometheus telemetry → data/raw/
./experiments/scripts/collect.sh 300

# 2) Validation + alignment + features → data/processed/dataset.csv
python ml/scripts/prepare_data.py
# exit 0 = valid; exit 2 = valid with warnings (check validation_report.json)

# 3) Train all candidates + write artifacts with provenance
python ml/scripts/train.py
# linear_regression vs random_forest vs xgboost vs mlp
# automatic selection by lowest validation RMSE

# 4) Final evaluation on the temporal test set
python ml/scripts/evaluate.py
# → ml/models/test_report.json (also exposed as a DVC metric)

# 5) Deploy the best model to the online controller
cp ml/models/random_forest.joblib ml/models/predictor.joblib
docker compose --profile controller restart ml-controller
```

Manual recommendations without the online controller:

```bash
python ml/scripts/predict.py                 # print recommendation only
python ml/scripts/predict.py --apply         # POST directly to the backend
```

Interpreting model quality:

| Signal | Meaning |
| --- | --- |
| Low MAE/RMSE consistent train→test | Predictions generalize |
| Test RMSE ≫ validation | Overfitting / leakage — check the temporal split |
| Prediction latency ≫ control interval (5s) | Model too heavy for the online loop |
| Feature importances concentrated on 1-2 features | Investigate causality vs artifact |

---

## 5. Monitoring

| URL | Contents |
| --- | --- |
| `localhost:9090/targets` | Backend scrape target health |
| `localhost:9090/graph` | Ad-hoc PromQL queries |
| `localhost:3000` | Grafana ("Adaptive DB Pool" folder) |

### Runtime data flow — who talks to whom

```text
 [k6]  --HTTP-->  [Backend]  --pgxpool-->  [PostgreSQL]
                     |
                     +--/metrics-->  [Prometheus]  --query-->  [Grafana]
                                        ^
                                        |        [Controller] --POST limit--> Backend
                                        +-- fetch
```

1. **k6** hits the workload endpoints (`/api/workload/*`) per the scenario.
2. The **backend** serves each request through the logical admission gate
   (semaphore) *before* touching pgxpool/PostgreSQL.
3. The backend exposes all measurements at `/metrics` (Prometheus format).
4. **Prometheus** pulls `/metrics` every 5 seconds.
5. **Grafana** only reads from Prometheus — if the scrape target is down,
   every dashboard is empty even when the system is healthy.
6. In adaptive mode: the **controller** fetches telemetry from Prometheus,
   predicts the optimal limit (ML), passes the safety layer, and POSTs to
   `/api/admin/admission/limit` — the closed loop.

### Map: what you see in Grafana → originating component

| Dashboard symptom | Metric | Responsible component |
| --- | --- | --- |
| RPS drops, P99 climbs | `request_duration_seconds` | Admission queue or slow DB — check the Admission dashboard |
| `admission_active ≈ limit` sustained | `admission_active`, `admission_limit` | Saturated system; candidate for limit adjustment |
| `admission_waiting > 0` sustained | `admission_waiting` | Limit too small for the load |
| Wait p95 rising while active is low | `admission_wait_seconds` | Anomaly — should be rare |
| Pool utilization > 80% | `db_pool_*` | Physical pool is the bottleneck (static by design) |
| Limit flipping rapidly | `changes(admission_limit[15m])` | Controller oscillation — check cooldown/hysteresis |
| Decisions by reason empty | `controller_decisions_total` | Controller not running (normal in static mode) |

Useful PromQL:

```promql
# Request P99
histogram_quantile(0.99, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[1m])) by (le))

# Admission queue pressure
adaptive_db_pool_admission_waiting

# Limit oscillation (RQ4 stability)
changes(adaptive_db_pool_admission_limit[5m])

# Physical pool utilization
adaptive_db_pool_db_pool_acquired_connections / adaptive_db_pool_db_pool_max_connections
```

---

## 6. Advanced Troubleshooting

**Every request returns 503 "request rejected"**
Admission timeout (`REQUEST_TIMEOUT`) expired because the queue is longer than the wait budget. The limit is too small for the current load — raise it (static baseline) or investigate why the controller is not raising the limit.

**Limit never changes despite adaptive strategy**
1. Is `ml-controller` alive? `docker compose ps`.
2. Does Prometheus have samples? Check `localhost:9090/targets`.
3. Controller logs show `no_change` with `best_candidate == held`? The model genuinely considers the current position optimal.
4. Held back by cooldown/hysteresis? Lower `HYSTERESIS_THRESHOLD` or increase load.

**Experiment results not reproducible**
Ensure `metadata.json` records the same git commit, identical benchmark seed, and unchanged PostgreSQL configuration between runs. Use the benchmark seed (`seed-benchmark`), never the development one.

**sqlc errors after editing queries**
Run `make sqlc`; on type errors make sure aggregate columns use explicit casts (`::float8`) so generated Go code stays deterministic.

---

## 7. Pre-Commit Quality Checklist

```bash
cd backend && gofmt -l . && go vet ./... && go test ./... -race
sqlc generate -f backend/sqlc.yaml        # must produce no diff
python3 -m py_compile $(find ../controller/src ../ml/src ../ml/scripts -name '*.py')
docker compose config -q                  # validate compose
```

#!/usr/bin/env bash
# Run a reproducible experiment from a YAML config (AGENT.md §28).
# Usage: ./experiments/scripts/run.sh experiments/configs/baseline.yaml
set -euo pipefail

CONFIG_PATH="${1:?usage: run.sh <config.yaml>}"
BASE_URL="${BASE_URL:-http://localhost:8080}"
PROM_URL="${PROM_URL:-http://localhost:9090}"
RESULTS_DIR="$(cd "$(dirname "$0")/.." && pwd)/results"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "config not found: $CONFIG_PATH" >&2
  exit 1
fi

require() {
  command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }
}
require yq
require k6
require curl
require jq

EXPERIMENT_ID="$(yq eval '.experiment.id' "$CONFIG_PATH")"
SCENARIO="$(yq eval '.workload.scenario' "$CONFIG_PATH")"
STRATEGY="$(yq eval '.admission.strategy' "$CONFIG_PATH")"
LIMIT="$(yq eval '.admission.limit // .admission.initial_limit // 0' "$CONFIG_PATH")"
WARMUP="$(yq eval '.duration.warmup' "$CONFIG_PATH")"
MEASUREMENT="$(yq eval '.duration.measurement' "$CONFIG_PATH")"
# Workload data ranges (benchmark seed = 10000 products / 50000 orders).
PRODUCTS_MAX="$(yq eval '.workload.products_max // 200' "$CONFIG_PATH")"
ORDERS_MAX="$(yq eval '.workload.orders_max // 500' "$CONFIG_PATH")"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
RUN_DIR="${RESULTS_DIR}/${EXPERIMENT_ID}_${TIMESTAMP}"
mkdir -p "$RUN_DIR"

# Provenance: model/controller versions overridable via environment.
MODEL_VERSION="${MODEL_VERSION:-$(yq eval '.model.version // "unknown"' "$CONFIG_PATH")}"
CONTROLLER_VERSION="${CONTROLLER_VERSION:-unknown}"
DB_MAX_CONNS="${DB_MAX_CONNS:-$(yq eval '.database.max_connections // 50' "$CONFIG_PATH")}"

# Hardware fingerprint so results are comparable across machines.
HW_KERNEL="$(uname -sr)"
HW_CPU="$(nproc 2>/dev/null || echo unknown)"
HW_MEM="$(awk '/MemTotal/ {printf "%.1f GiB", $2/1048576; exit}' /proc/meminfo 2>/dev/null || echo unknown)"
HW_HOST="$(hostname 2>/dev/null || echo unknown)"

echo ">> experiment : $EXPERIMENT_ID"
echo ">> scenario   : $SCENARIO (strategy=$STRATEGY limit=$LIMIT)"
echo ">> results dir: $RUN_DIR"

# --- record provenance metadata ---------------------------------------
cp "$CONFIG_PATH" "$RUN_DIR/config.resolved.yaml"
jq -n \
  --arg experiment_id "$EXPERIMENT_ID" \
  --arg timestamp "$TIMESTAMP" \
  --arg git_commit "$GIT_COMMIT" \
  --arg model_version "$MODEL_VERSION" \
  --arg controller_version "$CONTROLLER_VERSION" \
  --arg controller_strategy "$STRATEGY" \
  --arg scenario "$SCENARIO" \
  --arg warmup "$WARMUP" \
  --arg measurement "$MEASUREMENT" \
  --arg base_url "$BASE_URL" \
  --arg prom_url "$PROM_URL" \
  --arg db_max_conns "$DB_MAX_CONNS" \
  --arg products_max "$PRODUCTS_MAX" \
  --arg orders_max "$ORDERS_MAX" \
  --arg hw_kernel "$HW_KERNEL" \
  --arg hw_cpu "$HW_CPU" \
  --arg hw_mem "$HW_MEM" \
  --arg hw_host "$HW_HOST" \
  '{
    experiment_id: $experiment_id,
    timestamp: $timestamp,
    git_commit: $git_commit,
    model_version: $model_version,
    controller_version: $controller_version,
    controller_strategy: $controller_strategy,
    admission_limit: ($admission_limit | tonumber),
    scenario: $scenario,
    warmup: $warmup,
    measurement: $measurement,
    base_url: $base_url,
    prometheus_url: $prom_url,
    database_configuration: {max_connections: ($db_max_conns | tonumber)},
    workload_configuration: {
      scenario: $scenario,
      arrival_rate: ($arrival_rate | tonumber),
      products_max: ($products_max | tonumber),
      orders_max: ($orders_max | tonumber)
    },
    backend_configuration_file: "config.resolved.yaml",
    hardware: {
      kernel: $hw_kernel,
      cpu_cores: ($hw_cpu | if test("^[0-9]+$") then tonumber else . end),
      memory_total: $hw_mem,
      host: $hw_host
    }
  }' \
  --argjson admission_limit "$LIMIT" \
  --argjson arrival_rate "$(yq eval '.workload.arrival_rate // 0' "$CONFIG_PATH")" \
  > "$RUN_DIR/metadata.json"

# --- preflight: backend reachable & consistent with config --------------
HEALTH="$(curl -fsS -m 5 "${BASE_URL}/health" 2>/dev/null || true)"
if [[ -z "$HEALTH" ]]; then
  echo "error: backend unreachable at ${BASE_URL} — start it first (make dev / docker compose up)" >&2
  exit 1
fi
HEALTH_LIMIT="$(echo "$HEALTH" | jq -r '.limit // -1')"
if [[ "$HEALTH_LIMIT" != "$LIMIT" ]]; then
  echo "warn: live backend admission_limit=${HEALTH_LIMIT} != config limit=${LIMIT} (adaptive strategies may override)" >&2
fi

# --- apply admission strategy configuration ---------------------------
curl -fsS -X POST "${BASE_URL}/api/admin/admission/limit" \
  -H 'Content-Type: application/json' \
  -d "{\"limit\": ${LIMIT}, \"reason\": \"experiment_setup\"}" \
  > /dev/null || echo "warn: could not set initial limit (strategy may not accept changes)"

# --- warmup ------------------------------------------------------------
echo ">> warmup for ${WARMUP}"
k6 run --quiet \
  -e BASE_URL="${BASE_URL}" \
  -e DURATION="${WARMUP}" \
  -e TARGET=40 \
  -e PRODUCTS_MAX="${PRODUCTS_MAX}" \
  -e ORDERS_MAX="${ORDERS_MAX}" \
  "experiments/scenarios/${SCENARIO}.js" \
  > "$RUN_DIR/warmup.log" 2>&1

# --- measurement --------------------------------------------------------
echo ">> measurement for ${MEASUREMENT}"
PROM_SNAPSHOT_PID=""
( while true; do
    curl -fsS "${PROM_URL}/api/v1/query?query=adaptive_db_pool_admission_limit" \
      >> "$RUN_DIR/prometheus_limit.jsonl" 2>/dev/null || true
    sleep 2
  done ) &
PROM_SNAPSHOT_PID=$!

k6 run \
  ${K6_FLAGS:-} \
  -e BASE_URL="${BASE_URL}" \
  -e DURATION="${MEASUREMENT}" \
  -e TARGET="$(yq eval '.workload.arrival_rate' "$CONFIG_PATH")" \
  -e PRODUCTS_MAX="${PRODUCTS_MAX}" \
  -e ORDERS_MAX="${ORDERS_MAX}" \
  --summary-export="$RUN_DIR/k6_summary.json" \
  "experiments/scenarios/${SCENARIO}.js" \
  | tee "$RUN_DIR/k6_output.log" || echo "warn: k6 exited nonzero (thresholds tripped?) — results kept"

kill "$PROM_SNAPSHOT_PID" 2>/dev/null || true

# --- capture final telemetry -------------------------------------------
curl -fsS "${BASE_URL}/metrics" > "$RUN_DIR/backend_metrics.prom"
curl -fsS "${BASE_URL}/health" >> "$RUN_DIR/final_state.txt" 2>/dev/null || true

echo ">> done. results in $RUN_DIR"

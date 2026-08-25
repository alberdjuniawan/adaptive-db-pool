#!/usr/bin/env bash
# Export time-series from Prometheus into data/raw for the ML pipeline.
# Usage: ./experiments/scripts/collect.sh [duration_seconds]
set -euo pipefail

DURATION_SECONDS="${1:-300}"
STEP="${STEP:-5s}"
PROM_URL="${PROM_URL:-http://localhost:9090}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="${REPO_ROOT}/data/raw"
mkdir -p "$OUT_DIR"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
END_EPOCH="$(date +%s)"
START_EPOCH=$((END_EPOCH - DURATION_SECONDS))
GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"

# --- record provenance metadata ---------------------------------------
# Optional experiment identity hooks (set by sweep.sh per limit point).
EXPERIMENT_ID="${EXPERIMENT_ID:-collect_${TIMESTAMP}}"
SCENARIO="${SCENARIO:-unknown}"
ARRIVAL_RATE="${ARRIVAL_RATE:-0}"
jq -n \
  --arg collection_id "collect_${TIMESTAMP}" \
  --arg experiment_id "$EXPERIMENT_ID" \
  --arg scenario "$SCENARIO" \
  --arg timestamp "$TIMESTAMP" \
  --arg git_commit "$GIT_COMMIT" \
  --arg prom_url "$PROM_URL" \
  --arg step "$STEP" \
  --argjson duration_seconds "$DURATION_SECONDS" \
  --argjson arrival_rate "$ARRIVAL_RATE" \
  '{
    collection_id: $collection_id,
    experiment_id: $experiment_id,
    timestamp: $timestamp,
    git_commit: $git_commit,
    source: "prometheus",
    prometheus_url: $prom_url,
    workload_configuration: {scenario: $scenario, arrival_rate: ($arrival_rate | tonumber)},
    window: {duration_seconds: $duration_seconds, step: $step}
  }' > "${OUT_DIR}/${TIMESTAMP}_metadata.json"

# Parallel arrays: explicit output names keep the loader mapping stable
# regardless of PromQL string shape.
QUERY_NAMES=(
  request_rate
  p95_latency
  p99_latency
  error_rate
  admission_active
  admission_waiting
  admission_limit
  pool_acquired
  pool_idle
  pool_max
  rate_simple
  rate_medium
  rate_complex
  rate_aggregation
)

QUERIES=(
  "sum(rate(adaptive_db_pool_requests_total[30s]))"
  "histogram_quantile(0.95, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))"
  "histogram_quantile(0.99, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))"
  "sum(rate(adaptive_db_pool_request_errors_total[30s])) / clamp_min(sum(rate(adaptive_db_pool_requests_total[30s])), 1e-9)"
  "adaptive_db_pool_admission_active"
  "adaptive_db_pool_admission_waiting"
  "adaptive_db_pool_admission_limit"
  "adaptive_db_pool_db_pool_acquired_connections"
  "adaptive_db_pool_db_pool_idle_connections"
  "adaptive_db_pool_db_pool_max_connections"
  "sum(rate(adaptive_db_pool_requests_total{route=\"/api/workload/simple/:id\"}[30s]))"
  "sum(rate(adaptive_db_pool_requests_total{route=\"/api/workload/medium/:id\"}[30s]))"
  "sum(rate(adaptive_db_pool_requests_total{route=\"/api/workload/complex/:id\"}[30s]))"
  "sum(rate(adaptive_db_pool_requests_total{route=\"/api/workload/aggregation\"}[30s]))"
)

if [[ ${#QUERY_NAMES[@]} -ne ${#QUERIES[@]} ]]; then
  echo "query/name array length mismatch" >&2
  exit 1
fi

for i in "${!QUERIES[@]}"; do
  query="${QUERIES[$i]}"
  out_file="${OUT_DIR}/${TIMESTAMP}_${QUERY_NAMES[$i]}.json"
  echo "collecting: ${QUERY_NAMES[$i]} -> $out_file"
  curl -fsSG "${PROM_URL}/api/v1/query_range" \
    --data-urlencode "query=${query}" \
    --data-urlencode "start=${START_EPOCH}" \
    --data-urlencode "end=${END_EPOCH}" \
    --data-urlencode "step=${STEP}" \
    -o "$out_file"
done

echo "raw telemetry written to ${OUT_DIR}"

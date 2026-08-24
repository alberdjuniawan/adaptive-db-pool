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
jq -n \
  --arg collection_id "collect_${TIMESTAMP}" \
  --arg timestamp "$TIMESTAMP" \
  --arg git_commit "$GIT_COMMIT" \
  --arg prom_url "$PROM_URL" \
  --arg step "$STEP" \
  --argjson duration_seconds "$DURATION_SECONDS" \
  '{
    collection_id: $collection_id,
    timestamp: $timestamp,
    git_commit: $git_commit,
    source: "prometheus",
    prometheus_url: $prom_url,
    window: {duration_seconds: $duration_seconds, step: $step}
  }' > "${OUT_DIR}/${TIMESTAMP}_metadata.json"

QUERIES=(
  "rate(adaptive_db_pool_requests_total[30s])"
  "histogram_quantile(0.95, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))"
  "histogram_quantile(0.99, sum(rate(adaptive_db_pool_request_duration_seconds_bucket[30s])) by (le))"
  "adaptive_db_pool_admission_active"
  "adaptive_db_pool_admission_waiting"
  "adaptive_db_pool_admission_wait_seconds_sum"
  "adaptive_db_pool_admission_limit"
  "adaptive_db_pool_db_pool_acquired_connections"
  "adaptive_db_pool_db_pool_idle_connections"
)

for query in "${QUERIES[@]}"; do
  slug="$(echo "$query" | tr -c 'a-zA-Z0-9' '_' | cut -c1-60)"
  out_file="${OUT_DIR}/${TIMESTAMP}_${slug}.json"
  echo "collecting: $query -> $out_file"
  curl -fsSG "${PROM_URL}/api/v1/query_range" \
    --data-urlencode "query=${query}" \
    --data-urlencode "start=${START_EPOCH}" \
    --data-urlencode "end=${END_EPOCH}" \
    --data-urlencode "step=${STEP}" \
    -o "$out_file"
done

echo "raw telemetry written to ${OUT_DIR}"

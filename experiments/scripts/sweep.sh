#!/usr/bin/env bash
# Automated static-limit admission sweep for outcome-model training data.
# Usage: SCENARIO=mixed RATE=400 LIMITS="4 8 12 16 24 32 48 64" \
#          ./experiments/scripts/sweep.sh
set -euo pipefail

SCENARIO="${SCENARIO:-mixed}"
RATE="${RATE:-400}"
MEASUREMENT="${MEASUREMENT:-45s}"
WARMUP="${WARMUP:-10s}"
COOLDOWN="${COOLDOWN:-20}"
read -ra LIMITS <<< "${LIMITS:-4 8 12 16 24 32 48 64}"
export K6_FLAGS="${K6_FLAGS:---no-thresholds}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GEN_DIR="$REPO_ROOT/experiments/configs/generated"
mkdir -p "$GEN_DIR"

command -v k6 >/dev/null || { echo "missing dependency: k6" >&2; exit 1; }
if ! docker compose -f "$REPO_ROOT/docker-compose.yml" ps >/dev/null 2>&1; then
  echo "warning: could not query docker compose state; continuing" >&2
fi

# The controller would fight a static sweep for the limit.
docker compose --profile controller stop ml-controller >/dev/null 2>&1 || true

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MANIFEST="$REPO_ROOT/experiments/results/sweep_manifest_${STAMP}.json"
# One experiment id for the whole sweep: every limit point belongs to
# the same workload regime, forming a single J curve.
SWEEP_EXPERIMENT_ID="exp_sweep_${SCENARIO}_${RATE}_${STAMP}"
RUNS="[]"

echo "== sweep scenario=$SCENARIO rate=$RATE limits=${LIMITS[*]} =="

for LIMIT in "${LIMITS[@]}"; do
  CONFIG="$GEN_DIR/sweep_${SCENARIO}_${RATE}_L${LIMIT}.yaml"
  cat > "$CONFIG" <<EOF
experiment:
  id: exp_sweep_${SCENARIO}_${RATE}_L${LIMIT}
workload:
  scenario: ${SCENARIO}
  arrival_rate: ${RATE}
  products_max: 10000
  orders_max: 50000
database:
  max_connections: 50
admission:
  strategy: static
  limit: ${LIMIT}
duration:
  warmup: ${WARMUP}
  measurement: ${MEASUREMENT}
EOF

  echo "-- limit ${LIMIT}: reconfiguring backend"
  ADMISSION_STRATEGY=static ADMISSION_LIMIT="$LIMIT" \
    docker compose up -d backend >/dev/null
  sleep 5

  echo "-- limit ${LIMIT}: running experiment"
  BASE_URL="${BASE_URL:-http://localhost:8080}" \
    "$SCRIPT_DIR/run.sh" "$CONFIG" | grep -E '^>> (done|warn)' || true

  # Collect this point's telemetry under the sweep-level experiment id
  # so all limit points merge into one regime curve.
  POINT_SECONDS=$(( $(echo "$WARMUP" | tr -dc '0-9' | head -c3) + $(echo "$MEASUREMENT" | tr -dc '0-9' | head -c3) + 25 ))
  EXPERIMENT_ID="$SWEEP_EXPERIMENT_ID" \
    SCENARIO="$SCENARIO" ARRIVAL_RATE="$RATE" \
    "$SCRIPT_DIR/collect.sh" "$POINT_SECONDS" >/dev/null
  echo "-- limit ${LIMIT}: telemetry collected (${POINT_SECONDS}s window)"

  RUNS=$(jq --arg cfg "$(basename "$CONFIG")" '. += [$cfg]' <<< "$RUNS")
  echo "-- cooldown ${COOLDOWN}s"
  sleep "$COOLDOWN"
done

jq -n --arg ts "$STAMP" --arg scenario "$SCENARIO" --argjson rate "$RATE" \
  --argjson limits "$(printf '%s\n' "${LIMITS[@]}" | jq -R . | jq -s .)" \
  --argjson runs "$RUNS" \
  '{sweep_id: ("sweep_" + $ts), timestamp: $ts, scenario: $scenario,
    arrival_rate: $rate, limits: $limits, runs: $runs}' > "$MANIFEST"
echo "== manifest: $MANIFEST =="

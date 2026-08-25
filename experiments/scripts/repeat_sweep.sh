#!/usr/bin/env bash
# Repeat an admission-limit sweep N times so model comparisons gain
# statistical weight (each repetition becomes its own regime session).
# Usage: REPEATS=3 SCENARIO=high RATE=400 ./experiments/scripts/repeat_sweep.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPEATS="${REPEATS:-3}"
SCENARIO="${SCENARIO:-high}"
RATE="${RATE:-400}"
LIMITS="${LIMITS:-8 16 32}"
MEASUREMENT="${MEASUREMENT:-25s}"
WARMUP="${WARMUP:-8s}"
COOLDOWN="${COOLDOWN:-8}"
GAP_BETWEEN_REPEATS="${GAP_BETWEEN_REPEATS:-30}"

for i in $(seq 1 "$REPEATS"); do
  echo "== repetition $i/$REPEATS =="
  SCENARIO="$SCENARIO" RATE="$RATE" LIMITS="$LIMITS" \
    MEASUREMENT="$MEASUREMENT" WARMUP="$WARMUP" COOLDOWN="$COOLDOWN" \
    bash "$SCRIPT_DIR/sweep.sh"
  [[ "$i" -lt "$REPEATS" ]] && { echo "-- gap ${GAP_BETWEEN_REPEATS}s"; sleep "$GAP_BETWEEN_REPEATS"; }
done

echo "== $REPEATS repetitions complete; run collect.sh + prepare_data next =="

// k6 scenario: MIXED load with ramping stages to exercise workload
// transitions — the stability test for the adaptive controller (RQ4).
// Usage: k6 run -e BASE_URL=http://localhost:8080 experiments/scenarios/mixed.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
// ID ranges default to the development seed; set to the benchmark seed
// sizes via -e PRODUCTS_MAX=10000 -e ORDERS_MAX=50000.
const PRODUCTS_MAX = Number(__ENV.PRODUCTS_MAX || 200);
const ORDERS_MAX = Number(__ENV.ORDERS_MAX || 500);

// DURATION scales the total ramp length proportionally; TARGET sets the
// arrival rate at the "medium" stage, with the profile peaking at
// TARGET * 2. Defaults reproduce the original fixed profile.
function durationSeconds(raw) {
  const m = String(raw).trim().match(/^(\d+(?:\.\d+)?)(ms|s|m|h)?$/);
  if (!m) return 420;
  const n = Number(m[1]);
  switch (m[2]) {
    case 'ms': return n / 1000;
    case 's': return n;
    case 'm': return n * 60;
    case 'h': return n * 3600;
    default: return n;
  }
}

const TOTAL_SECONDS = durationSeconds(__ENV.DURATION || '7m');
const PEAK = __ENV.TARGET ? Number(__ENV.TARGET) * 2 : 240;
const PROFILE = [
  { share: 1 / 7, rate: PEAK / 6 },
  { share: 2 / 7, rate: PEAK / 2 },
  { share: 2 / 7, rate: PEAK },
  { share: 1 / 7, rate: PEAK / 4 },
  { share: 1 / 7, rate: PEAK / 12 },
];

const errors = new Counter('workload_errors');

export const options = {
  scenarios: {
    mixed_ramp: {
      executor: 'ramping-arrival-rate',
      startRate: Math.max(1, Math.round(PROFILE[0].rate / 3)),
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 500,
      stages: PROFILE.map((p) => ({
        target: Math.max(1, Math.round(p.rate)),
        duration: `${Math.max(1, Math.round(TOTAL_SECONDS * p.share))}s`,
      })),
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.10'],
  },
};

function pickId(max) {
  return Math.floor(Math.random() * max) + 1;
}

export default function () {
  const roll = Math.random();

  let res;
  if (roll < 0.4) {
    res = http.get(`${BASE_URL}/api/workload/simple/${pickId(PRODUCTS_MAX)}`, {
      tags: { query_class: 'simple' },
    });
  } else if (roll < 0.7) {
    res = http.get(`${BASE_URL}/api/workload/medium/${pickId(ORDERS_MAX)}`, {
      tags: { query_class: 'medium' },
    });
  } else if (roll < 0.92) {
    res = http.get(`${BASE_URL}/api/workload/complex/${pickId(ORDERS_MAX)}`, {
      tags: { query_class: 'complex' },
    });
  } else {
    res = http.get(`${BASE_URL}/api/workload/aggregation`, {
      tags: { query_class: 'aggregation' },
    });
  }

  const ok = check(res, { 'status < 500': (r) => r.status < 500 });
  if (!ok) errors.add(1);

  sleep(0.03);
}

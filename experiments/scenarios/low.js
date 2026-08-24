// k6 scenario: LOW constant load, mostly simple lookups.
// Usage: k6 run -e BASE_URL=http://localhost:8080 -e DURATION=5m experiments/scenarios/low.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const TARGET = Number(__ENV.TARGET || 20);
const DURATION = __ENV.DURATION || '5m';
// ID ranges default to the development seed; set to the benchmark seed
// sizes via -e PRODUCTS_MAX=10000 -e ORDERS_MAX=50000.
const PRODUCTS_MAX = Number(__ENV.PRODUCTS_MAX || 200);

const errors = new Counter('workload_errors');

export const options = {
  scenarios: {
    low_load: {
      executor: 'constant-arrival-rate',
      rate: TARGET,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

function pickId(max) {
  return Math.floor(Math.random() * max) + 1;
}

export default function () {
  const res = http.get(`${BASE_URL}/api/workload/simple/${pickId(PRODUCTS_MAX)}`, {
    tags: { query_class: 'simple' },
  });

  const ok = check(res, { 'status < 500': (r) => r.status < 500 });
  if (!ok) errors.add(1);

  sleep(0.1);
}

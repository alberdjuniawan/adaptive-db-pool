// k6 scenario: MEDIUM constant load across simple + medium queries.
// Usage: k6 run -e BASE_URL=http://localhost:8080 -e DURATION=5m experiments/scenarios/medium.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const TARGET = Number(__ENV.TARGET || 60);
const DURATION = __ENV.DURATION || '5m';
// ID ranges default to the development seed; set to the benchmark seed
// sizes via -e PRODUCTS_MAX=10000 -e ORDERS_MAX=50000.
const PRODUCTS_MAX = Number(__ENV.PRODUCTS_MAX || 200);
const ORDERS_MAX = Number(__ENV.ORDERS_MAX || 500);

const errors = new Counter('workload_errors');

export const options = {
  scenarios: {
    medium_load: {
      executor: 'constant-arrival-rate',
      rate: TARGET,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.max(30, TARGET),
      maxVUs: Math.max(150, TARGET * 4),
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
  },
};

function pickId(max) {
  return Math.floor(Math.random() * max) + 1;
}

export default function () {
  const roll = Math.random();

  let res;
  if (roll < 0.6) {
    res = http.get(`${BASE_URL}/api/workload/simple/${pickId(PRODUCTS_MAX)}`, {
      tags: { query_class: 'simple' },
    });
  } else {
    res = http.get(`${BASE_URL}/api/workload/medium/${pickId(ORDERS_MAX)}`, {
      tags: { query_class: 'medium' },
    });
  }

  const ok = check(res, { 'status < 500': (r) => r.status < 500 });
  if (!ok) errors.add(1);

  sleep(0.05);
}

import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '90s', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],     // under 1% errors
    http_req_duration: ['p(95)<300'],   // p95 under 300 ms
  },
};

export default function () {
  const v = http.get('http://api:8000/version');
  check(v, { 'version 200': (r) => r.status === 200 });
  const j = http.post('http://api:8000/jobs', JSON.stringify({ payload: 'k6' }),
    { headers: { 'Content-Type': 'application/json' } });
  check(j, { 'job accepted': (r) => r.status < 300 });
  sleep(0.2);
}

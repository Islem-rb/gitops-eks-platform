import os
import random
import time

import psycopg
import redis
from prometheus_client import Counter, Histogram, start_http_server

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@localhost:5432/app")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))

PROCESSED = Counter("worker_jobs_processed_total", "Jobs processed", ["result"])
DURATION = Histogram("worker_job_duration_seconds", "Job processing time")

rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def connect():
    while True:
        try:
            conn = psycopg.connect(DATABASE_URL, autocommit=True)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                     id SERIAL PRIMARY KEY,
                     payload TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'queued',
                     created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
            )
            return conn
        except Exception as exc:
            print(f"db not ready: {exc}", flush=True)
            time.sleep(2)


def main():
    start_http_server(METRICS_PORT)  # /metrics, also used as liveness probe
    conn = connect()
    print("worker started", flush=True)
    while True:
        item = rdb.brpop("jobs", timeout=5)
        if not item:
            continue
        job_id = int(item[1])
        with DURATION.time():
            try:
                conn.execute("UPDATE jobs SET status='processing' WHERE id=%s", (job_id,))
                time.sleep(random.uniform(0.2, 1.0))  # simulate work
                conn.execute("UPDATE jobs SET status='done' WHERE id=%s", (job_id,))
                PROCESSED.labels("ok").inc()
            except Exception as exc:
                print(f"job {job_id} failed: {exc}", flush=True)
                PROCESSED.labels("error").inc()
                conn = connect()


if __name__ == "__main__":
    main()

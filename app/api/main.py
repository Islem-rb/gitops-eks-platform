import os
import random
import time
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@localhost:5432/app")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FAIL_RATE = float(os.getenv("FAIL_RATE", "0"))  # 0.0 - 1.0, used for canary rollback demos
VERSION = os.getenv("APP_VERSION", "dev")

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)
rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True)

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["method", "path"])

SKIP_FAILURE = {"/healthz", "/readyz", "/metrics"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True)
    with pool.connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                 id SERIAL PRIMARY KEY,
                 payload TEXT NOT NULL,
                 status TEXT NOT NULL DEFAULT 'queued',
                 created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
    yield
    pool.close()


app = FastAPI(title="platform-demo-api", lifespan=lifespan)


@app.middleware("http")
async def metrics_and_chaos(request: Request, call_next):
    start = time.perf_counter()
    if request.url.path not in SKIP_FAILURE and random.random() < FAIL_RATE:
        response = Response("injected failure", status_code=500)
    else:
        response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
    REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    return response


class JobIn(BaseModel):
    payload: str


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        rdb.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"status": "ready"}


@app.get("/version")
def version():
    return {"version": VERSION}


@app.post("/jobs", status_code=201)
def create_job(job: JobIn):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO jobs (payload) VALUES (%s) RETURNING id", (job.payload,)
        ).fetchone()
    rdb.lpush("jobs", row[0])
    return {"id": row[0], "status": "queued"}


@app.get("/jobs")
def list_jobs():
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, payload, status, created_at FROM jobs ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return [
        {"id": r[0], "payload": r[1], "status": r[2], "created_at": r[3].isoformat()}
        for r in rows
    ]


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
# demo change 1792932520

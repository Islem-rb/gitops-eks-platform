import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import app

# No "with" block: the lifespan (database setup) is not started
client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version():
    r = client.get("/version")
    assert r.status_code == 200
    assert "version" in r.json()


def test_metrics_exposed():
    client.get("/healthz")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text

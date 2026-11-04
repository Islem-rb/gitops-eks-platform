import math

import httpx

from ..models import Evidence
from ..queries import PROMQL
from . import safely


def run_query(client: httpx.Client, name: str) -> list[dict]:
    """Run a query from the catalog by name. There is no way to pass in free-form PromQL."""
    response = client.get("/api/v1/query", params={"query": PROMQL[name]})
    response.raise_for_status()
    series = []
    for item in response.json()["data"]["result"]:
        value = float(item["value"][1])
        series.append(
            {
                "labels": {k: v for k, v in item["metric"].items() if k != "__name__"},
                # NaN (no traffic to divide by) means no value, and it is not valid JSON
                "value": None if math.isnan(value) or math.isinf(value) else value,
            }
        )
    return series


def collect(client: httpx.Client) -> list[Evidence]:
    return [safely(f"prom.{n}", "prometheus", lambda n=n: run_query(client, n)) for n in PROMQL]

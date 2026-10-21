import json, textwrap

P = {"type": "prometheus", "uid": "prometheus"}
L = {"type": "loki", "uid": "loki"}
SEL = 'namespace="demo",app="api"'

ALL = lambda w: f'sum(rate(http_requests_total{{{SEL}}}[{w}]))'
ERR = lambda w: f'(sum(rate(http_requests_total{{{SEL},status=~"5.."}}[{w}])) or vector(0))'
RATIO5 = f'{ERR("5m")} / {ALL("5m")}'
BY = 'rollouts_pod_template_hash'

def timeseries(i, title, x, y, w, h, targets, unit="short"):
    return {"id": i, "type": "timeseries", "title": title, "datasource": P,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "targets": [{"refId": chr(65 + n), "expr": e, "legendFormat": lg, "datasource": P}
                        for n, (e, lg) in enumerate(targets)]}

def stat(i, title, x, expr, unit, steps):
    return {"id": i, "type": "stat", "title": title, "datasource": P,
            "gridPos": {"x": x, "y": 0, "w": 6, "h": 4},
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
            "options": {"colorMode": "background", "reduceOptions": {"calcs": ["lastNotNull"]}},
            "targets": [{"refId": "A", "expr": expr, "datasource": P}]}

panels = [
    stat(1, "Availability (5m, SLO 99%)", 0, f'1 - ({RATIO5})', "percentunit",
         [{"color": "red", "value": None}, {"color": "green", "value": 0.99}]),
    stat(2, "Error budget burn rate (1 = on budget)", 6, f'({RATIO5}) / 0.01', "short",
         [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 6}]),
    stat(3, "p95 latency (5m)", 12,
         f'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{SEL}}}[5m])))', "s",
         [{"color": "green", "value": None}, {"color": "orange", "value": 0.3}, {"color": "red", "value": 0.5}]),
    stat(4, "Requests per second", 18, ALL("1m"), "reqps",
         [{"color": "blue", "value": None}]),
    timeseries(5, "5xx error ratio by version (canary vs stable)", 0, 4, 12, 8,
        [(f'(sum by ({BY}) (rate(http_requests_total{{{SEL},status=~"5.."}}[1m])) or (sum by ({BY}) (rate(http_requests_total{{{SEL}}}[1m])) * 0)) / sum by ({BY}) (rate(http_requests_total{{{SEL}}}[1m]))',
          "revision {{" + BY + "}}")], "percentunit"),
    timeseries(6, "Requests per second by status", 12, 4, 12, 8,
        [(f'sum by (status) (rate(http_requests_total{{{SEL}}}[1m]))', "{{status}}")], "reqps"),
    timeseries(7, "p95 latency", 0, 12, 12, 8,
        [(f'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{SEL}}}[5m])))', "p95")], "s"),
    timeseries(8, "Worker jobs per second by result", 12, 12, 12, 8,
        [('sum by (result) (rate(worker_jobs_processed_total{namespace="demo"}[1m]))', "{{result}}")], "ops"),
    {"id": 9, "type": "logs", "title": "API logs", "datasource": L,
     "gridPos": {"x": 0, "y": 20, "w": 24, "h": 10},
     "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"},
     "targets": [{"refId": "A", "expr": '{namespace="demo", app="api"}', "datasource": L}]},
]

dash = {"uid": "platform-slo", "title": "Platform SLO", "tags": ["platform"],
        "schemaVersion": 39, "version": 1, "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"}, "panels": panels}

head = ("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: dashboard-platform-slo\n"
        "  namespace: monitoring\n  labels:\n    grafana_dashboard: \"1\"\ndata:\n  platform-slo.json: |\n")
open("gitops/apps/monitoring-config/dashboard-platform-slo.yaml", "w").write(
    head + textwrap.indent(json.dumps(dash, indent=2), "    ") + "\n")
print("dashboard written")

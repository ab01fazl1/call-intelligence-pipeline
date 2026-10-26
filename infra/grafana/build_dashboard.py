"""Dashboard as code: python infra/grafana/build_dashboard.py > infra/grafana/dashboards/pipeline.json"""

import json

DS = {"type": "prometheus", "uid": "prometheus"}
_ids = iter(range(1, 100))


def panel(title, exprs, *, x, y, w=12, h=8, kind="timeseries", unit="short", legend=None,
          stack=False):
    targets = [{"datasource": DS, "expr": e, "legendFormat": legend or "", "refId": chr(65 + i)}
               for i, e in enumerate(exprs if isinstance(exprs, list) else [exprs])]
    defaults = {"unit": unit}
    if kind == "timeseries":
        defaults["custom"] = {"lineWidth": 2, "fillOpacity": 15 if stack else 5,
                              "stacking": {"mode": "normal" if stack else "none"}}
    return {"id": next(_ids), "type": kind, "title": title, "datasource": DS, "targets": targets,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                        "reduceOptions": {"calcs": ["lastNotNull"]}}}


def stat(title, expr, *, x, unit="short"):
    return panel(title, expr, x=x, y=0, w=4, h=4, kind="stat", unit=unit)


panels = [
    stat("Calls completed (5m)", "sum(increase(callintel_stage_messages_total"
         '{stage="analysis",outcome="succeeded"}[5m]))', x=0),
    stat("In flight", "sum(callintel_stage_inflight)", x=4),
    stat("Outbox backlog", "max(callintel_outbox_backlog)", x=8),
    stat("Parked (1h)", "sum(increase(callintel_parked_total[1h]))", x=12),
    stat("E2E p95", "histogram_quantile(0.95, sum by (le) "
         "(rate(callintel_call_end_to_end_seconds_bucket[5m])))", x=16, unit="s"),
    stat("ASR workers", 'count(up{job="workers",service="worker-asr"} == 1)', x=20),

    panel("Queue depth (ready)", 'sum by (queue) (rabbitmq_queue_messages_ready'
          '{queue=~"callintel.*"})', x=0, y=4, legend="{{queue}}"),
    panel("Unacked (being processed)", 'sum by (queue) (rabbitmq_queue_messages_unacked'
          '{queue=~"callintel.*"})', x=12, y=4, legend="{{queue}}"),
    panel("Stage outcomes / s", "sum by (stage, outcome) "
          "(rate(callintel_stage_messages_total[30s]))", x=0, y=12, legend="{{stage}} {{outcome}}",
          unit="ops", stack=True),
    panel("Stage latency p95", "histogram_quantile(0.95, sum by (le, stage) "
          "(rate(callintel_stage_seconds_bucket[1m])))", x=12, y=12, legend="{{stage}}", unit="s"),
    panel("End-to-end latency", [
        "histogram_quantile(0.5, sum by (le) (rate(callintel_call_end_to_end_seconds_bucket[2m])))",
        "histogram_quantile(0.95, sum by (le) (rate(callintel_call_end_to_end_seconds_bucket[2m])))",
    ], x=0, y=20, unit="s"),
    panel("ASR real-time factor (p50)", "histogram_quantile(0.5, sum by (le, model) "
          "(rate(callintel_asr_real_time_factor_bucket[2m])))", x=12, y=20, legend="{{model}}"),
    panel("Outbox: backlog and publish lag p95", [
        "max(callintel_outbox_backlog)",
        "histogram_quantile(0.95, sum by (le) (rate(callintel_outbox_lag_seconds_bucket[1m])))",
    ], x=0, y=28),
    panel("Uploads by result / s", "sum by (result) (rate(callintel_uploads_total[30s]))",
          x=12, y=28, legend="{{result}}", unit="ops", stack=True),
    panel("Parked messages by reason (5m)", "sum by (stage, reason) "
          "(increase(callintel_parked_total[5m]))", x=0, y=36, legend="{{stage}} {{reason}}",
          kind="bargauge"),
    panel("QA flags raised (15m)", "sum by (version, flag) (increase(callintel_qa_flags_total[15m]))",
          x=12, y=36, legend="{{version}} {{flag}}", kind="bargauge"),
]

print(json.dumps({
    "uid": "callintel-pipeline",
    "title": "Call Intelligence Pipeline",
    "tags": ["callintel"],
    "timezone": "browser",
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "schemaVersion": 39,
    "panels": panels,
}, indent=1))

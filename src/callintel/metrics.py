"""Prometheus metrics. Every process exposes the subset it touches."""

from prometheus_client import Counter, Gauge, Histogram

_LATENCY = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)

UPLOADS = Counter("callintel_uploads_total", "Upload requests by result", ["result"])
UPLOAD_BYTES = Counter("callintel_upload_bytes_total", "Audio bytes received by the gateway")
UPLOAD_SECONDS = Histogram("callintel_upload_seconds", "Time to stream an upload to storage",
                           buckets=_LATENCY)

OUTBOX_BACKLOG = Gauge("callintel_outbox_backlog", "Outbox rows waiting to be published")
OUTBOX_PUBLISHED = Counter("callintel_outbox_published_total", "Outbox rows published",
                           ["exchange"])
OUTBOX_PUBLISH_FAILURES = Counter("callintel_outbox_publish_failures_total",
                                  "Outbox publishes rejected or failed", ["exchange"])
OUTBOX_LAG = Histogram("callintel_outbox_lag_seconds", "Commit-to-publish delay",
                       buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60))

STAGE_MESSAGES = Counter("callintel_stage_messages_total", "Stage messages by outcome",
                         ["stage", "outcome"])
STAGE_SECONDS = Histogram("callintel_stage_seconds", "Stage processing time", ["stage"],
                          buckets=_LATENCY)
STAGE_INFLIGHT = Gauge("callintel_stage_inflight", "Messages being processed", ["stage"])
ASR_RTF = Histogram("callintel_asr_real_time_factor", "ASR processing time / audio duration",
                    ["model"], buckets=(0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3))
CALL_E2E = Histogram("callintel_call_end_to_end_seconds", "Upload accepted -> analysis committed",
                     buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 3600))
PARKED = Counter("callintel_parked_total", "Messages that reached the parking lot",
                 ["stage", "reason"])
QA_FLAGS = Counter("callintel_qa_flags_total", "QA flags raised", ["version", "flag"])

# Architecture Decision Records

One file per decision: the context, what was decided, what it costs, and what was rejected.
They are short on purpose. If you only read three, read 0002, 0003 and 0007.

| # | Decision | In one line |
|---|---|---|
| [0001](0001-staged-pipeline.md) | Staged pipeline, one queue per stage | ASR is 100x heavier than everything else; scale it alone. |
| [0002](0002-transactional-outbox.md) | Transactional outbox for every publish | A crash between "commit" and "publish" must not lose or orphan a job. |
| [0003](0003-idempotent-stages.md) | At-least-once delivery + idempotent stages | Exactly-once delivery does not exist; exactly-once *effects* do. |
| [0004](0004-retries-and-parking-lot.md) | Retry tiers, delivery limit, parking lot | Transient vs. permanent errors, and a guard against poison messages. |
| [0005](0005-aio-pika-not-celery.md) | aio-pika instead of Celery | We need to control ack timing, queue arguments and dead-lettering. |
| [0006](0006-streaming-uploads.md) | Stream uploads straight to object storage | Never hold a recording in memory; hash and validate on the fly. |
| [0007](0007-inference-off-the-event-loop.md) | ASR runs in a separate process | Blocking the loop kills AMQP heartbeats and causes redelivery storms. |
| [0008](0008-stereo-channels.md) | Stereo channels as speaker separation; 16 kHz | PBX recordings already separate the parties; use that. |
| [0009](0009-persian-normalization.md) | In-house Persian normalization | ي/ی, ك/ک and ZWNJ silently break keyword rules. |
| [0010](0010-pluggable-asr-synthetic-data.md) | Pluggable ASR + synthetic data with ground truth | Measure the system without real customer audio. |
| [0011](0011-versioned-analysis.md) | Versioned analysis results + reprocessing | Changing a rule must never rewrite history silently. |
| [0012](0012-backpressure.md) | End-to-end backpressure | Bounded queues push back to the outbox, the outbox to the gateway. |
| [0013](0013-postgres-search.md) | Postgres trigram search, no Elasticsearch | One less cluster to run until search load proves otherwise. |
| [0014](0014-rules-before-models.md) | Rule-based QA first | Compliance findings must be explainable and auditable. |
| [0015](0015-object-store-and-mirrors.md) | VersityGW object store, registry/package mirrors | Build and run on a constrained network. |

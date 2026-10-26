from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, read from the environment (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "callintel"
    log_level: str = "INFO"

    # --- infrastructure ---
    database_url: str = "postgresql+asyncpg://callintel:callintel@localhost:5432/callintel"
    amqp_url: str = "amqp://callintel:callintel@localhost:5672/"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "callintel"
    s3_secret_key: str = "callintel-secret"
    s3_bucket: str = "calls"
    s3_region: str = "us-east-1"

    # --- gateway ---
    max_upload_bytes: int = 200 * 1024 * 1024
    # S3 multipart part size. Bounds gateway memory to roughly part_size per in-flight upload.
    upload_part_bytes: int = 8 * 1024 * 1024
    # Reject new uploads (503) when this many outbox rows are waiting to be published.
    outbox_backlog_limit: int = 5_000

    # --- broker topology ---
    queue_max_length: int = 10_000
    # Redeliveries (crash / connection loss without ack) before RabbitMQ dead-letters a message.
    delivery_limit: int = 5
    # Application-level retries for transient errors; attempt N waits retry_delays_s[N-1].
    retry_delays_s: list[int] = [5, 30, 120]

    # --- outbox relay ---
    relay_batch_size: int = 100
    relay_poll_interval_s: float = 1.0
    outbox_retention_hours: int = 24

    # --- workers ---
    worker_concurrency: int = 4
    asr_concurrency: int = 1
    shutdown_grace_s: float = 60.0
    metrics_port: int = 9100
    channel_map: list[str] = ["agent", "customer"]  # stereo channel index -> speaker
    min_call_duration_s: float = 1.0
    max_call_duration_s: float = 3 * 3600

    # --- ASR ---
    asr_backend: Literal["mock", "whisper"] = "mock"
    asr_timeout_floor_s: float = 60.0
    asr_timeout_rtf: float = 2.0  # timeout = max(floor, audio_seconds * channels * rtf)
    mock_asr_rtf: float = 0.1
    mock_asr_wer: float = 0.0
    mock_asr_failure_rate: float = 0.0
    ground_truth_dir: str = "data"  # searched recursively for */ground_truth/*.json
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 1
    whisper_cpu_threads: int = 4

    # --- analysis ---
    analyzer_version: str = "v3"

    @property
    def max_attempts(self) -> int:
        return len(self.retry_delays_s) + 1


@lru_cache
def get_settings() -> Settings:
    return Settings()

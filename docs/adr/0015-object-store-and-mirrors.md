# 0015: VersityGW as local object store; registry and package mirrors

Status: accepted · 2026-10-26

## Context
- MinIO, the usual local S3, stopped publishing community container images in 2025, and the
  `minio/minio` repository is no longer available on Docker Hub.
- The project is developed on a bandwidth-constrained network where Docker Hub, PyPI and
  Hugging Face may be slow or blocked, and every hundred megabytes matters.

## Decision
- **Object storage**: VersityGW (`versity/versitygw`, Apache-2.0, ~34 MB), an S3 gateway in
  front of a POSIX volume. The application speaks plain S3 through aiobotocore with path-style
  addressing, so production can point `S3_ENDPOINT` at Ceph RGW, MinIO or AWS S3 without
  code changes.
- **Mirrors are configuration, not code**: `REGISTRY_PREFIX` (image prefix, e.g.
  `docker.arvancloud.ir/`), `PIP_INDEX_URL` / `UV_DEFAULT_INDEX`, `APT_MIRROR` and
  `HF_ENDPOINT` in `.env`. Empty values fall back to upstream.
- **Download hygiene**:
  - one Python image for every service;
  - dependency layer separated from code with a BuildKit pip cache, so code changes never
    re-download wheels;
  - observability (Prometheus/Grafana, ~300 MB) is an opt-in compose profile;
  - real Whisper is an opt-in overlay (`compose.whisper.yaml`);
  - the offline voice synthesizer makes data generation possible without network access.

## Consequences
+ `make up` pulls about 280 MB of base images plus about 60 MB of wheels, once.
+ Works the same on unrestricted networks (clear `.env`).
- Mirror availability is outside our control; the README lists alternatives.
- VersityGW's POSIX backend needs xattr support on the volume (true for Docker volumes on ext4).

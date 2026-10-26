# syntax=docker/dockerfile:1.7
# One image for every Python service (gateway, relay, workers): one download, one build.
ARG REGISTRY_PREFIX=
FROM ${REGISTRY_PREFIX}python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG APT_MIRROR=
# requirements/<name>.txt: "base", or "whisper" to bake in faster-whisper.
ARG REQUIREMENTS=base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# Dependencies first, so code changes never re-download wheels. Exact versions and hashes
# come from uv.lock (make lock); pip verifies every wheel, which matters behind mirrors.
# The BuildKit cache mount keeps wheels across rebuilds even when the lock changes.
COPY requirements/ requirements/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --require-hashes -r requirements/${REQUIREMENTS}.txt

# soundfile ships libsndfile in its wheel on most platforms; fall back to the distro package.
RUN python -c "import soundfile" 2>/dev/null || ( \
      if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; fi \
      && apt-get update && apt-get install -y --no-install-recommends libsndfile1 \
      && rm -rf /var/lib/apt/lists/* )

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

# /app/hf pre-created so the model-cache volume inherits the non-root owner.
RUN useradd --system --uid 10001 --home /app callintel \
    && mkdir -p /app/data /app/hf && chown callintel /app/data /app/hf
USER callintel

EXPOSE 8000 9100
CMD ["uvicorn", "callintel.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]

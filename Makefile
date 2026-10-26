SHELL := /bin/bash
-include .env
export

COMPOSE ?= docker compose
PY      := uv run --no-sync
N       ?= 4

.PHONY: help env install lock test lint data data-load up up-obs down nuke ps logs demo scale \
        chaos chaos-all eval robustness reprocess-v1 bench whisper data-offline

help:  ## show targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env:  ## create .env from the example (mirrors on)
	@test -f .env || cp .env.example .env && echo ".env ready"

install:  ## local dev environment (uv, ~130 MB)
	uv sync --extra dev --extra synth

lock:  ## re-resolve dependencies and export the hashed requirements used by the images
	uv lock
	uv export --frozen --no-emit-project --no-dev -o requirements/base.txt -q
	uv export --frozen --no-emit-project --no-dev --extra whisper -o requirements/whisper.txt -q

test:  ## unit tests (no infrastructure needed)
	$(PY) pytest

lint:
	$(PY) ruff check src tests scripts

data:  ## 30 synthetic Persian calls (edge TTS, ~5 MB download) + edge cases
	$(PY) python -m callintel.synth --out data/synthetic --count 30 --voice edge

data-offline:  ## same dataset shape with the offline voice synth (no network)
	$(PY) python -m callintel.synth --out data/synthetic --count 30 --voice synthetic

data-load:  ## 200 offline-synthesized calls for load tests
	$(PY) python -m callintel.synth --out data/load --count 200 --voice synthetic --seed 99 \
		--prefix load --no-edge-cases

up:  ## build and start the pipeline
	$(COMPOSE) up -d --build --wait

up-obs:  ## pipeline + Prometheus + Grafana (http://localhost:3000)
	$(COMPOSE) --profile obs up -d --build --wait

down:
	$(COMPOSE) --profile obs down

nuke:  ## stop and delete all data volumes
	$(COMPOSE) --profile obs down -v

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=50 gateway outbox-relay worker-preprocess worker-asr worker-analysis worker-deadletter

demo:  ## upload the dataset, edge cases included (UI: http://localhost:8000)
	$(PY) python scripts/upload.py data/synthetic --edge-cases

scale:  ## scale ASR workers: make scale N=4
	$(COMPOSE) up -d --no-recreate --scale worker-asr=$(N)

chaos:  ## fault-injection suite (fast scenarios)
	$(PY) python scripts/chaos.py

chaos-all:  ## including multi-minute scenarios
	$(PY) python scripts/chaos.py --slow

robustness:  ## rule precision/recall vs. injected ASR word errors, no stack needed
	$(PY) python scripts/offline_eval.py data/synthetic --wer 0 0.1 0.2 0.3 --out docs/rule-robustness.md

eval:  ## precision/recall + WER against ground truth -> docs/eval.md
	$(PY) python -m callintel.eval --data data/synthetic --out docs/eval.md

reprocess-v1:  ## re-score completed calls with analyzer v1 (then compare in make eval)
	ANALYZER_VERSION=v1 $(COMPOSE) up -d --no-deps worker-analysis
	curl -s -X POST localhost:8000/v1/reprocess -H 'content-type: application/json' \
		-d '{"stage":"analysis","status":"completed"}'; echo
	@echo "when done: ANALYZER_VERSION=v3 docker compose up -d --no-deps worker-analysis"

bench:  ## load test with the current worker scale -> docs/benchmarks.md
	$(PY) python scripts/loadtest.py --data data/load --label "rtf=$${MOCK_ASR_RTF:-0.1}"

whisper:  ## real ASR (downloads faster-whisper + ~480 MB model; ask first on slow links)
	$(COMPOSE) -f compose.yaml -f compose.whisper.yaml up -d --build --wait worker-asr

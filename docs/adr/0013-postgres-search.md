# 0013: Postgres trigram search instead of Elasticsearch (for now)

Status: accepted · 2026-10-26

## Context
QA staff need "find calls where someone said X". Elasticsearch is the reflexive answer, but it
is another stateful cluster to secure, back up, size and keep in sync (dual writes reintroduce
exactly the consistency problem ADR 0002 solves).

## Decision
- Store a Persian-normalized `search_text` per transcript (ADR 0009) with a `pg_trgm` GIN
  index. `GET /v1/search?q=...` normalizes the query the same way and matches with `ILIKE`,
  which the trigram index accelerates for arbitrary substrings in any script.
- Flags are a `text[]` column with a GIN index for `flags @> ARRAY[...]` filters.

## Consequences
+ Search is transactionally consistent with the transcript: no indexing lag, no sync job.
+ Zero extra infrastructure.
- No relevance ranking, stemming or fuzzy phonetic match. Fine for exact-phrase compliance
  queries; weak for exploratory search.

## When to revisit
When there are millions of transcripts and search latency or relevance matters. Then
Elasticsearch/OpenSearch (with a Persian analyzer) can be fed **from the outbox events**, a
consumer of `call.completed`, keeping Postgres the source of truth.

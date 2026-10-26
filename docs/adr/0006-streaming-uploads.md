# 0006: Stream uploads straight to object storage

Status: accepted · 2026-10-26

## Context
Call recordings are 1-200 MB. Common upload patterns hurt at volume:
- reading the body into memory: N concurrent uploads × file size of RAM;
- multipart form parsing (`UploadFile`): spools to a temp file, then copies it again to storage;
- putting audio on the message bus: brokers are not blob stores.

## Decision
- `POST /v1/calls` takes the **raw body** (`Content-Type: audio/wav|audio/mpeg`) and streams
  it chunk by chunk into an S3 multipart upload (8 MB parts). Memory is bounded at about one
  part per in-flight upload.
- While streaming, the gateway:
  - **sniffs magic bytes** (RIFF/WAVE, ID3, MPEG frame sync). The Content-Type header is not
    trusted, and non-audio is rejected with 415 within the first 12 bytes;
  - **enforces the size limit** on bytes actually received (413), not just on a
    Content-Length header that may be absent (chunked encoding) or false;
  - **hashes** (SHA-256) for deduplication (ADR 0003).
- Any failure (limit, bad type, client disconnect) **aborts the multipart upload**, so no
  orphaned parts accumulate in the bucket.
- The call row is created only after the object is complete. If registering it fails, the
  object is deleted and the client gets 503 with `Retry-After`.
- Brokers carry only IDs; workers download by key.

## Consequences
+ Constant memory regardless of file size (chaos: `upload-validation` streams 260 MB).
+ A duplicate upload costs bandwidth but no storage (object deleted, existing call returned).
- Raw-body uploads are less browser-friendly than multipart forms; the clients here are
  PBX export jobs and scripts, for which raw `PUT/POST --data-binary` is simpler.
- In production a presigned-URL flow (client uploads directly to S3, notifies the API) would
  take bandwidth off the gateway entirely; it needs bucket notifications or a completion call.

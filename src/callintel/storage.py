"""S3-compatible object storage (MinIO locally). Audio never travels through the broker."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import UUID

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError

from callintel.config import Settings
from callintel.errors import ObjectNotFound, TransientError

log = logging.getLogger(__name__)

_READ_CHUNK = 1024 * 1024


def raw_key(call_id: UUID) -> str:
    return f"raw/{call_id}"


def normalized_key(call_id: UUID, channel: int) -> str:
    return f"normalized/{call_id}/ch{channel}.wav"


class ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.s3_bucket
        self._stack = AsyncExitStack()
        self._client: Any = None

    async def start(self) -> None:
        config = AioConfig(
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            # botocore >= 1.36 adds CRC32 checksum trailers to every upload by default; many
            # S3-compatible servers reject them. Only send checksums where S3 requires them.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self._client = await self._stack.enter_async_context(
            get_session().create_client(
                "s3",
                endpoint_url=self._settings.s3_endpoint,
                aws_access_key_id=self._settings.s3_access_key,
                aws_secret_access_key=self._settings.s3_secret_key,
                region_name=self._settings.s3_region,
                config=config,
            )
        )

    async def close(self) -> None:
        await self._stack.aclose()

    async def ensure_bucket(self) -> None:
        try:
            await self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                await self._client.create_bucket(Bucket=self._bucket)
            except ClientError as exc:  # another replica won the race
                code = exc.response.get("Error", {}).get("Code")
                if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise

    async def ping(self) -> None:
        await self._client.head_bucket(Bucket=self._bucket)

    async def upload_stream(self, key: str, chunks: AsyncIterator[bytes], *, content_type: str,
                            part_size: int) -> int:
        """Stream an unknown-length body into S3 without holding it in memory.

        Bodies smaller than one part become a single PUT; larger ones a multipart upload.
        Any exception (client disconnect, size limit, bad media type) aborts the multipart
        upload so no orphaned parts are left behind.
        """
        buffer = bytearray()
        parts: list[dict[str, Any]] = []
        upload_id: str | None = None
        total = 0
        try:
            async for chunk in chunks:
                buffer += chunk
                total += len(chunk)
                while len(buffer) >= part_size:
                    if upload_id is None:
                        created = await self._client.create_multipart_upload(
                            Bucket=self._bucket, Key=key, ContentType=content_type
                        )
                        upload_id = created["UploadId"]
                    await self._upload_part(key, upload_id, parts, bytes(buffer[:part_size]))
                    del buffer[:part_size]

            if upload_id is None:
                await self._client.put_object(
                    Bucket=self._bucket, Key=key, Body=bytes(buffer), ContentType=content_type
                )
            else:
                if buffer:
                    await self._upload_part(key, upload_id, parts, bytes(buffer))
                await self._client.complete_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            return total
        except BaseException:
            if upload_id is not None:
                try:
                    await self._client.abort_multipart_upload(
                        Bucket=self._bucket, Key=key, UploadId=upload_id
                    )
                except Exception:
                    log.warning("failed to abort multipart upload", extra={"key": key})
            raise

    async def _upload_part(self, key: str, upload_id: str, parts: list[dict[str, Any]],
                           body: bytes) -> None:
        number = len(parts) + 1
        resp = await self._client.upload_part(
            Bucket=self._bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
        )
        parts.append({"ETag": resp["ETag"], "PartNumber": number})

    async def download_to_file(self, key: str, path: Path) -> int:
        try:
            resp = await self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFound(key) from exc
            raise TransientError(f"storage error reading {key}: {exc}") from exc
        except BotoCoreError as exc:
            raise TransientError(f"storage unavailable reading {key}: {exc}") from exc
        size = 0
        async with resp["Body"] as stream:
            with path.open("wb") as fh:
                while chunk := await stream.read(_READ_CHUNK):
                    fh.write(chunk)
                    size += len(chunk)
        return size

    async def put_file(self, key: str, path: Path, *, content_type: str,
                       part_size: int = 8 * 1024 * 1024) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as fh:
                while chunk := fh.read(_READ_CHUNK):
                    yield chunk

        try:
            await self.upload_stream(key, chunks(), content_type=content_type,
                                     part_size=part_size)
        except (ClientError, BotoCoreError) as exc:
            raise TransientError(f"storage error writing {key}: {exc}") from exc

    async def delete(self, key: str) -> None:
        await self._client.delete_object(Bucket=self._bucket, Key=key)

    async def pending_multipart_uploads(self, prefix: str = "") -> int:
        resp = await self._client.list_multipart_uploads(Bucket=self._bucket, Prefix=prefix)
        return len(resp.get("Uploads", []))

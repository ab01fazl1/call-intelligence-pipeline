"""Streaming request-body handling: size limit, content sniffing and hashing on the fly."""

import hashlib
from collections.abc import AsyncIterator

_SNIFF_BYTES = 12


class UploadRejected(Exception):
    status_code = 400


class UploadTooLarge(UploadRejected):
    status_code = 413


class UnsupportedMedia(UploadRejected):
    status_code = 415


def sniff_audio(head: bytes) -> str | None:
    """Identify WAV / MP3 from magic bytes. The Content-Type header is not trusted."""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:3] == b"ID3":
        return "audio/mpeg"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:  # MPEG frame sync
        return "audio/mpeg"
    return None


class UploadBody:
    """Wraps the raw request stream. Iterate `chunks()` exactly once; afterwards `size`,
    `sha256` and `media_type` describe what was received."""

    def __init__(self, source: AsyncIterator[bytes], *, max_bytes: int) -> None:
        self._source = source
        self._max = max_bytes
        self._hash = hashlib.sha256()
        self.size = 0
        self.media_type: str | None = None

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()

    async def chunks(self) -> AsyncIterator[bytes]:
        head = b""
        async for chunk in self._source:
            if not chunk:
                continue
            self.size += len(chunk)
            if self.size > self._max:
                raise UploadTooLarge(f"upload exceeds {self._max} bytes")
            self._hash.update(chunk)
            if self.media_type is None:
                head += chunk
                if len(head) < _SNIFF_BYTES:
                    continue
                self.media_type = sniff_audio(head)
                if self.media_type is None:
                    raise UnsupportedMedia("body is not WAV or MP3 audio")
                chunk, head = head, b""
            yield chunk
        if self.size == 0:
            raise UploadRejected("empty body")
        if self.media_type is None:
            raise UnsupportedMedia("body too short to be audio")

"""Original-file storage on Cloudflare R2 (S3-compatible) with an in-memory fake for tests."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings

SIGNED_URL_TTL_S = 300
_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,8}$")


class StorageError(Exception):
    pass


class ObjectNotFoundError(StorageError):
    pass


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, expires_s: int = SIGNED_URL_TTL_S) -> str: ...


def build_object_key(family_id: UUID, document_id: UUID, filename: str) -> str:
    """Tenant-prefixed key; the user filename never becomes part of the path."""
    ext = PurePosixPath(filename).suffix.lower()
    if not _SAFE_EXT.fullmatch(ext):
        ext = ""
    return f"{family_id}/{document_id}/{uuid.uuid4().hex}{ext}"


class R2Storage:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> R2Storage:
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
            region_name="auto",
            config=Config(
                signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}
            ),
        )
        return cls(client, settings.r2_bucket)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        # R2 encrypts every object at rest (AES-256) by default.
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=key
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from exc
            raise StorageError("get_object failed") from exc
        body = response["Body"]
        try:
            return await asyncio.to_thread(body.read)
        finally:
            body.close()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def presign_get(self, key: str, expires_s: int = SIGNED_URL_TTL_S) -> str:
        if not 1 <= expires_s <= SIGNED_URL_TTL_S:
            raise ValueError(f"expires_s must be between 1 and {SIGNED_URL_TTL_S}")
        url: str = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_s,
        )
        return url


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key][0]
        except KeyError as exc:
            raise ObjectNotFoundError(key) from exc

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def presign_get(self, key: str, expires_s: int = SIGNED_URL_TTL_S) -> str:
        return f"memory://{key}?expires={expires_s}"


_fake_storage = InMemoryStorage()


class LocalFileStorage:
    """Dev-only storage on a directory shared by the API and worker containers."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError("Object key escapes the storage root")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def presign_get(self, key: str, expires_s: int = SIGNED_URL_TTL_S) -> str:
        return f"file://{self._path(key)}?expires={expires_s}"


def get_storage(settings: Settings) -> ObjectStorage:
    if settings.env != "prod" and settings.local_storage_dir:
        return LocalFileStorage(settings.local_storage_dir)
    if settings.fake_externals:
        return _fake_storage
    return R2Storage.from_settings(settings)

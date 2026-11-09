from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from moto import mock_aws

from app.config import Settings
from app.storage import (
    InMemoryStorage,
    LocalFileStorage,
    ObjectNotFoundError,
    R2Storage,
    StorageError,
    build_object_key,
    get_storage,
)

BUCKET = "famguard-test"


@pytest.fixture
def r2() -> Iterator[R2Storage]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x",
                              aws_secret_access_key="y",
                              config=Config(signature_version="s3v4"))
        client.create_bucket(Bucket=BUCKET)
        yield R2Storage(client, BUCKET)


async def test_put_get_delete(r2: R2Storage) -> None:
    await r2.put("fam/doc/a.jpg", b"hello", "image/jpeg")
    assert await r2.get("fam/doc/a.jpg") == b"hello"
    await r2.delete("fam/doc/a.jpg")
    with pytest.raises(ObjectNotFoundError):
        await r2.get("fam/doc/a.jpg")


async def test_presigned_url_short_lived(r2: R2Storage) -> None:
    url = await r2.presign_get("fam/doc/a.jpg")
    assert "X-Amz-Expires=300" in url
    with pytest.raises(ValueError):
        await r2.presign_get("fam/doc/a.jpg", expires_s=3600)


def test_object_key_is_tenant_prefixed_and_ignores_filename() -> None:
    fam, doc = uuid4(), uuid4()
    key = build_object_key(fam, doc, "../../etc/passwd.JPG")
    parts = key.split("/")
    assert parts[0] == str(fam) and parts[1] == str(doc)
    assert len(parts) == 3 and parts[2].endswith(".jpg")
    assert ".." not in key
    assert build_object_key(fam, doc, "weird.<script>").count(".") == 0


async def test_in_memory_storage() -> None:
    store = InMemoryStorage()
    await store.put("k", b"v", "text/plain")
    assert await store.get("k") == b"v"
    await store.delete("k")
    with pytest.raises(ObjectNotFoundError):
        await store.get("k")


async def test_local_file_storage(tmp_path: Path) -> None:
    store = LocalFileStorage(tmp_path / "vault")
    await store.put("fam/doc/a.jpg", b"bytes", "image/jpeg")
    assert (tmp_path / "vault" / "fam" / "doc" / "a.jpg").read_bytes() == b"bytes"
    assert await store.get("fam/doc/a.jpg") == b"bytes"
    await store.delete("fam/doc/a.jpg")
    await store.delete("fam/doc/a.jpg")
    with pytest.raises(ObjectNotFoundError):
        await store.get("fam/doc/a.jpg")
    with pytest.raises(StorageError):
        await store.put("../../escape.txt", b"x", "text/plain")


@pytest.mark.parametrize("fake_externals", [True, False])
async def test_local_storage_is_independent_of_ai_mode(
    tmp_path: Path, fake_externals: bool,
) -> None:
    settings = Settings(
        _env_file=None, env="dev", fake_externals=fake_externals,
        database_url="postgresql://localhost/test", openai_api_key="sk-test",
        pinecone_api_key="pc-test", local_storage_dir=str(tmp_path),
    )
    writer = get_storage(settings)
    assert isinstance(writer, LocalFileStorage)
    await writer.put("family/doc/file.pdf", b"original", "application/pdf")
    reader = get_storage(settings)
    assert await reader.get("family/doc/file.pdf") == b"original"

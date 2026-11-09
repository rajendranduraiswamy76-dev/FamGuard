from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.agents.state import UnifiedVaultExtraction
from app.config import Settings
from app.embeddings import FakeEmbedder
from app.vectorstore import (
    MAX_METADATA_TEXT_BYTES,
    DocumentSchemaIndex,
    InMemoryVectorIndex,
    SQLiteVectorIndex,
    TenantFilterError,
    VectorStore,
    assert_tenant_scoped,
    build_filter,
    build_records,
    get_vector_store,
    matches_filter,
)
from tests.helpers import make_principal

NS = "family-vault"


def _document_schema(integrated: bool = True) -> dict[str, Any]:
    searchable = (
        {"type": "string", "store_text": True, "embed": {"dimension": 1536, "metric": "cosine"}}
        if integrated else {"type": "dense_vector", "dimension": 1536, "metric": "cosine"}
    )
    return {"fields": {
        "chunk_text" if integrated else "embedding": searchable,
        "title": {"type": "string", "full_text_search": {"language": "en"}},
    }}


@pytest.mark.parametrize("integrated", [True, False])
async def test_document_schema_upload_search_and_delete(integrated: bool) -> None:
    api = MagicMock()
    schema_model = MagicMock()
    schema_model.to_dict.return_value = _document_schema(integrated)
    adapter = DocumentSchemaIndex(api, schema_model)
    store = VectorStore(adapter, NS)
    family = uuid4()
    doc_id, records = await _records(2, family_id=family)
    for record in records:
        record["metadata"]["document_details"] = "Upload reference DETAILS-42"
        record["metadata"]["member_name"] = "John"
    api.documents.batch_upsert.return_value = {"successful_item_count": 2}
    assert await store.upsert(records) == 2
    documents = api.documents.batch_upsert.call_args.kwargs["documents"]
    assert [d["_id"] for d in documents] == [f"{doc_id}#0", f"{doc_id}#1"]
    assert documents[0]["title"] == "John Passport"
    assert documents[0]["family_id"] == str(family)
    assert documents[0]["document_details"] == "Upload reference DETAILS-42"
    assert documents[0]["member_name"] == "John"
    if integrated:
        assert documents[0]["chunk_text"] == "chunk 0"
        assert "values" not in documents[0]
    else:
        assert documents[0]["embedding"] == records[0]["values"]
    api.documents.search.return_value = {"matches": [{
        **documents[0], "_score": 0.94,
    }]}
    vector = None if integrated else records[0]["values"]
    flt = build_filter(make_principal("admin", family_id=family))
    matches = await store.query(vector, filter=flt, query_text="John's passport")
    assert matches[0].id == f"{doc_id}#0"
    assert matches[0].score == 0.94
    assert matches[0].metadata["text"] == "chunk 0"
    assert matches[0].metadata["document_details"] == "Upload reference DETAILS-42"
    assert "_id" not in matches[0].metadata
    kwargs = api.documents.search.call_args.kwargs
    assert kwargs["filter"] == flt
    assert kwargs["include_fields"] == ["*"]
    assert kwargs["score_by"] == ([{
        "type": "embed", "field": "chunk_text", "query": "John's passport",
    }] if integrated else [{
        "type": "dense_vector", "field": "embedding", "values": records[0]["values"],
    }])
    api.documents.list.return_value = [{"_id": f"{doc_id}#0"}, {"_id": f"{doc_id}#1"}]
    assert await store.delete_document(doc_id) == 2
    api.documents.list.assert_called_once_with(namespace=NS, prefix=f"{doc_id}#")
    api.documents.delete.assert_called_once_with(
        namespace=NS, ids=[f"{doc_id}#0", f"{doc_id}#1"],
    )
    api.upsert.assert_not_called()
    api.query.assert_not_called()
    api.delete.assert_not_called()


async def test_integrated_chunks_need_no_openai_vectors() -> None:
    api = MagicMock()
    store = VectorStore(DocumentSchemaIndex(api, _document_schema()), NS)
    api.documents.batch_upsert.return_value = {"successful_item_count": 1}
    records = build_records(
        family_id=uuid4(), family_member_id=uuid4(), document_id=uuid4(),
        extraction=_extraction(), chunks=["full text"], vectors=None, source="upload",
    )
    assert await store.upsert(records) == 1
    with pytest.raises(ValueError, match="require 1536"):
        await VectorStore(InMemoryVectorIndex(), NS).upsert(records)
    with pytest.raises(ValueError, match="requires query text"):
        await store.query(None, filter=build_filter(make_principal()))
    with pytest.raises(TenantFilterError):
        await store.query(None, filter={}, query_text="passport")


def test_document_schema_rejects_incompatible_or_unsafe_fields() -> None:
    schema = _document_schema()
    schema["fields"]["chunk_text"]["embed"]["dimension"] = 1024
    with pytest.raises(ValueError, match="dimension"):
        DocumentSchemaIndex(MagicMock(), schema)
    schema = _document_schema()
    schema["fields"]["family_id"] = {"type": "string", "filterable": False}
    with pytest.raises(ValueError, match="metadata filtering"):
        DocumentSchemaIndex(MagicMock(), schema)
    schema = _document_schema()
    schema["fields"]["chunk_text"]["store_text"] = False
    with pytest.raises(ValueError, match="store text"):
        DocumentSchemaIndex(MagicMock(), schema)
    with pytest.raises(ValueError, match="exactly one"):
        DocumentSchemaIndex(MagicMock(), {"fields": {}})


async def test_document_schema_rejects_incomplete_upsert() -> None:
    api = MagicMock()
    store = VectorStore(DocumentSchemaIndex(api, _document_schema()), NS)
    _, records = await _records(2)
    api.documents.batch_upsert.return_value = {"successful_item_count": 1}
    with pytest.raises(RuntimeError, match="all vault chunks"):
        await store.upsert(records)


async def test_fake_store_shares_records_between_processes(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, env="dev", fake_externals=True,
        database_url="postgresql://localhost/test",
        openai_api_key="fake", pinecone_api_key="fake", local_storage_dir=str(tmp_path),
    )
    principal = make_principal("admin")
    _, records = await _records(1, family_id=principal.family_id)
    await get_vector_store(settings).upsert(records)
    code = """
import asyncio
import sys
from uuid import UUID
from app.config import Settings
from app.embeddings import FakeEmbedder
from app.vectorstore import build_filter, get_vector_store
from tests.helpers import make_principal
async def main():
    settings = Settings(_env_file=None, env="dev", fake_externals=True,
        database_url="postgresql://localhost/test", openai_api_key="fake",
        pinecone_api_key="fake", local_storage_dir=sys.argv[1])
    vector = (await FakeEmbedder().embed(["chunk 0"]))[0]
    matches = await get_vector_store(settings).query(vector,
        filter=build_filter(make_principal("admin", family_id=UUID(sys.argv[2]))))
    print(len(matches))
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code, str(tmp_path), str(principal.family_id)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "1"


async def test_sqlite_index_preserves_namespaces_filters_and_deletes(tmp_path: Path) -> None:
    path = tmp_path / "vectors.sqlite3"
    writer = VectorStore(SQLiteVectorIndex(path), NS)
    family_a, family_b = uuid4(), uuid4()
    doc_a, records_a = await _records(2, family_id=family_a)
    _, records_b = await _records(2, family_id=family_b)
    await writer.upsert(records_a + records_b)
    other_namespace = VectorStore(SQLiteVectorIndex(path), "other")
    await other_namespace.upsert(records_a)
    reader = VectorStore(SQLiteVectorIndex(path), NS)
    vector = (await FakeEmbedder().embed(["chunk 0"]))[0]
    flt = build_filter(make_principal("admin", family_id=family_a))
    matches = await reader.query(vector, filter=flt)
    assert len(matches) == 2
    assert matches[0].metadata["text"] == "chunk 0"
    assert all(match.metadata["family_id"] == str(family_a) for match in matches)
    assert await reader.delete_document(doc_a) == 2
    assert await writer.query(vector, filter=flt) == []
    assert len(await other_namespace.query(vector, filter=flt)) == 2
    assert SQLiteVectorIndex(path).describe_index_stats()["total_vector_count"] == 4


def _extraction(**overrides: Any) -> UnifiedVaultExtraction:
    values: dict[str, Any] = {
        "category": "identitycards", "document_name": "John Passport",
        "expiration_date": None, "requires_alert": True, "extracted_text": "x",
        "financial_value": None, "confidence_score": 0.9,
    }
    values.update(overrides)
    return UnifiedVaultExtraction.model_validate(values)


async def _records(n: int, **kwargs: Any) -> tuple[Any, list[Any]]:
    fam, member, doc = kwargs.pop("family_id", uuid4()), kwargs.pop("member_id", uuid4()), uuid4()
    chunks = [f"chunk {i}" for i in range(n)]
    vectors = await FakeEmbedder().embed(chunks)
    records = build_records(
        family_id=fam, family_member_id=member, document_id=doc,
        extraction=_extraction(**kwargs), chunks=chunks, vectors=vectors, source="upload",
    )
    return doc, records


async def test_records_omit_null_metadata_and_mark_primary() -> None:
    doc, records = await _records(3)
    assert [r["id"] for r in records] == [f"{doc}#0", f"{doc}#1", f"{doc}#2"]
    assert [r["metadata"]["is_primary"] for r in records] == [True, False, False]
    for r in records:
        assert None not in r["metadata"].values()
        assert "expiration_date" not in r["metadata"]
        assert "financial_value" not in r["metadata"]


async def test_records_include_optional_metadata_when_present() -> None:
    _, records = await _records(1, expiration_date="2027-01-07", financial_value=99.5)
    meta = records[0]["metadata"]
    assert meta["expiration_date"] == "2027-01-07"
    assert meta["financial_value"] == 99.5
    assert meta["category"] == "identitycards"


async def test_metadata_text_truncated_on_utf8_boundary() -> None:
    chunks = ["é" * 10_000]
    records = build_records(
        family_id=uuid4(), family_member_id=uuid4(), document_id=uuid4(),
        extraction=_extraction(), chunks=chunks, vectors=await FakeEmbedder().embed(chunks),
        source="bulk",
    )
    assert len(records[0]["metadata"]["text"].encode()) <= MAX_METADATA_TEXT_BYTES


def test_build_records_rejects_wrong_dims() -> None:
    with pytest.raises(ValueError):
        build_records(family_id=uuid4(), family_member_id=uuid4(), document_id=uuid4(),
                      extraction=_extraction(), chunks=["a"], vectors=[[0.1] * 3],
                      source="upload")


def test_filter_admin_can_target_any_member() -> None:
    admin = make_principal("admin")
    target = uuid4()
    assert build_filter(admin, member_id=target, category="medical") == {
        "family_id": {"$eq": str(admin.family_id)},
        "family_member_id": {"$eq": str(target)},
        "category": {"$eq": "medical"},
    }
    assert build_filter(admin) == {"family_id": {"$eq": str(admin.family_id)}}


def test_filter_member_is_forced_to_own_id() -> None:
    member = make_principal("member")
    flt = build_filter(member, member_id=uuid4())
    assert flt["family_member_id"] == {"$eq": str(member.member_id)}
    assert build_filter(member)["family_member_id"] == {"$eq": str(member.member_id)}


@pytest.mark.parametrize("role", ["admin", "member"])
def test_filter_always_pins_family(role: str) -> None:
    for _ in range(20):
        principal = make_principal(role)
        flt = build_filter(principal, member_id=uuid4(), category="photo")
        assert flt["family_id"] == {"$eq": str(principal.family_id)}
        assert_tenant_scoped(flt)


@pytest.mark.parametrize(
    "flt",
    [None, {}, {"category": {"$eq": "x"}}, {"family_id": {"$in": ["a", "b"]}},
     {"family_id": {"$eq": ""}}, {"family_id": "a"}],
)
def test_assert_tenant_scoped_rejects(flt: dict[str, Any] | None) -> None:
    with pytest.raises(TenantFilterError):
        assert_tenant_scoped(flt)


async def test_upsert_batches_of_100() -> None:
    index = InMemoryVectorIndex()
    _, records = await _records(250)
    assert await VectorStore(index, NS).upsert(records) == 250
    assert index.upsert_calls == 3
    assert len(index.namespaces[NS]) == 250


async def test_query_isolates_families_and_requires_scope() -> None:
    index = InMemoryVectorIndex()
    store = VectorStore(index, NS)
    fam_a, fam_b = uuid4(), uuid4()
    _, rec_a = await _records(2, family_id=fam_a)
    _, rec_b = await _records(2, family_id=fam_b)
    await store.upsert(rec_a + rec_b)

    principal = make_principal("admin", family_id=fam_a)
    query = (await FakeEmbedder().embed(["chunk 0"]))[0]
    matches = await store.query(query, filter=build_filter(principal), top_k=10)
    assert len(matches) == 2
    assert {m.metadata["family_id"] for m in matches} == {str(fam_a)}
    assert index.query_calls[-1]["namespace"] == NS
    assert index.query_calls[-1]["filter"] == {"family_id": {"$eq": str(fam_a)}}

    with pytest.raises(TenantFilterError):
        await store.query(query, filter={"category": {"$eq": "identitycards"}})
    unscoped = await store.query(query, filter={"category": {"$eq": "identitycards"}},
                                 top_k=10, tenant_scoped=False)
    assert len(unscoped) == 4


async def test_query_validates_inputs() -> None:
    store = VectorStore(InMemoryVectorIndex(), NS)
    flt = build_filter(make_principal())
    with pytest.raises(ValueError):
        await store.query([0.1] * 3, filter=flt)
    with pytest.raises(ValueError):
        await store.query([0.1] * 1536, filter=flt, top_k=1001)


async def test_delete_document_by_prefix_only() -> None:
    index = InMemoryVectorIndex()
    store = VectorStore(index, NS)
    doc_a, rec_a = await _records(120)
    _, rec_b = await _records(2)
    await store.upsert(rec_a + rec_b)
    assert await store.delete_document(doc_a) == 120
    assert len(index.namespaces[NS]) == 2


@pytest.mark.parametrize(
    ("flt", "expected"),
    [
        ({"expiration_date": {"$eq": "2027-01-07"}, "is_primary": {"$eq": True}}, True),
        ({"expiration_date": "2027-01-08"}, False),
        ({"category": {"$in": ["medical", "identitycards"]}}, True),
        ({"category": {"$ne": "identitycards"}}, False),
        ({"financial_value": {"$gte": 10}}, False),
        ({"missing": {"$exists": False}}, True),
        ({"$or": [{"category": "x"}, {"is_primary": True}]}, True),
    ],
)
def test_fake_filter_semantics(flt: dict[str, Any], expected: bool) -> None:
    meta = {"expiration_date": "2027-01-07", "is_primary": True, "category": "identitycards"}
    assert matches_filter(meta, flt) is expected


def test_fake_index_rejects_zero_vector_like_pinecone() -> None:
    with pytest.raises(ValueError):
        InMemoryVectorIndex().query(top_k=1, vector=[0.0] * 1536, namespace=NS, filter=None,
                                    include_metadata=True)


def test_date_metadata_round_trip_type() -> None:
    assert _extraction(expiration_date="2027-01-07").expiration_date == date(2027, 1, 7)

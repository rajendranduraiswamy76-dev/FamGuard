"""Pinecone access: record building, batched upserts, tenant-safe filters and queries."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, TypedDict
from uuid import UUID

from app.config import Settings
from app.embeddings import EMBEDDING_DIMENSIONS

if TYPE_CHECKING:
    from app.agents.state import UnifiedVaultExtraction
    from app.security import Principal

UPSERT_BATCH_SIZE: Final = 100
DELETE_BATCH_SIZE: Final = 1000
MAX_METADATA_TEXT_BYTES: Final = 8 * 1024
MAX_TOP_K_WITH_METADATA: Final = 1000

DocumentSource = Literal["upload", "bulk", "chat"]


class VectorRecord(TypedDict):
    id: str
    values: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorMatch:
    id: str
    score: float
    metadata: dict[str, Any]


class TenantFilterError(ValueError):
    """Raised when a query filter would not be scoped to a single family."""


class VectorIndex(Protocol):
    """Subset of `pinecone.Index` used by FamGuard (sync client, called via threads)."""

    def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str) -> Any: ...

    def query(
        self,
        *,
        top_k: int,
        vector: Sequence[float],
        namespace: str,
        filter: Mapping[str, Any] | None,
        include_metadata: bool,
    ) -> Any: ...

    def list(self, *, prefix: str | None, namespace: str) -> Iterator[Any]: ...

    def delete(self, *, ids: Sequence[str], namespace: str) -> Any: ...

    def describe_index_stats(self) -> Any: ...


def vector_id(document_id: UUID | str, chunk_index: int) -> str:
    return f"{document_id}#{chunk_index}"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def build_metadata(
    *,
    family_id: UUID | str,
    family_member_id: UUID | str,
    document_id: UUID | str,
    category: str,
    document_name: str,
    requires_alert: bool,
    expiration_date: date | None,
    financial_value: float | None,
    chunk_index: int,
    text: str,
    source: DocumentSource,
    document_details: str = "",
    member_name: str = "",
    index_version: str | None = None,
    currency: str | None = None,
    financial_subcategory: str | None = None,
) -> dict[str, Any]:
    """Pinecone rejects null metadata, so optional keys are omitted instead."""
    metadata: dict[str, Any] = {
        "family_id": str(family_id),
        "family_member_id": str(family_member_id),
        "document_id": str(document_id),
        "category": category,
        "document_name": document_name,
        "requires_alert": requires_alert,
        "chunk_index": chunk_index,
        "is_primary": chunk_index == 0,
        "source": source,
        "text": _truncate_utf8(text, MAX_METADATA_TEXT_BYTES),
    }
    if expiration_date is not None:
        metadata["expiration_date"] = expiration_date.isoformat()
    if financial_value is not None and math.isfinite(financial_value):
        metadata["financial_value"] = float(financial_value)
    if document_details:
        metadata["document_details"] = document_details
    if member_name:
        metadata["member_name"] = member_name
    if index_version:
        metadata["index_version"] = index_version
    if currency:
        metadata["currency"] = currency
    if financial_subcategory:
        metadata["financial_subcategory"] = financial_subcategory
    return metadata


def build_records(
    *,
    family_id: UUID | str,
    family_member_id: UUID | str,
    document_id: UUID | str,
    extraction: UnifiedVaultExtraction,
    chunks: Sequence[str],
    vectors: Sequence[Sequence[float]] | None,
    source: DocumentSource,
    document_details: str = "",
    member_name: str = "",
    index_version: str | None = None,
    currency: str | None = None,
    financial_subcategory: str | None = None,
) -> list[VectorRecord]:
    if vectors is not None and len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must have the same length")
    records: list[VectorRecord] = []
    for index, chunk in enumerate(chunks):
        values = vectors[index] if vectors is not None else []
        if vectors is not None and len(values) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"Vector {index} has {len(values)} dims, expected 1536")
        records.append(
            VectorRecord(
                id=vector_id(document_id, index),
                values=list(values),
                metadata=build_metadata(
                    family_id=family_id,
                    family_member_id=family_member_id,
                    document_id=document_id,
                    category=extraction.category,
                    document_name=extraction.document_name,
                    requires_alert=extraction.requires_alert,
                    expiration_date=extraction.expiration_date,
                    financial_value=extraction.financial_value,
                    chunk_index=index,
                    text=chunk,
                    source=source,
                    document_details=document_details,
                    member_name=member_name,
                    index_version=index_version,
                    currency=currency,
                    financial_subcategory=financial_subcategory,
                ),
            )
        )
    return records


def build_filter(
    principal: Principal,
    *,
    member_id: UUID | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Single choke point for retrieval filters. Always scoped to the caller's family."""
    flt: dict[str, Any] = {"family_id": {"$eq": str(principal.family_id)}}
    if not principal.is_admin:
        member_id = principal.member_id
    if member_id is not None:
        flt["family_member_id"] = {"$eq": str(member_id)}
    if category:
        flt["category"] = {"$eq": category}
    return flt


def assert_tenant_scoped(flt: Mapping[str, Any] | None) -> None:
    family = (flt or {}).get("family_id")
    if not isinstance(family, Mapping) or set(family) != {"$eq"} or not family["$eq"]:
        raise TenantFilterError("Query filter must pin family_id with $eq")


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


class DocumentSchemaIndex:
    """Adapt Pinecone's Documents API to the vault's chunk/metadata contract."""

    def __init__(self, index: Any, schema: Any) -> None:
        self._index = index
        if hasattr(schema, "to_dict"):
            schema = schema.to_dict()
        self._fields: Mapping[str, Any] = _field(schema, "fields", {})
        candidates = [
            (name, spec) for name, spec in self._fields.items()
            if _field(spec, "type") == "dense_vector" or _field(spec, "embed") is not None
        ]
        if len(candidates) != 1:
            raise ValueError("FamGuard requires exactly one dense or embedded schema field")
        self._vector_field, spec = candidates[0]
        embedding = _field(spec, "embed")
        self.integrated_embeddings = embedding is not None
        vector_spec = embedding if self.integrated_embeddings else spec
        if _field(vector_spec, "dimension") != EMBEDDING_DIMENSIONS:
            raise ValueError("Pinecone schema embedding dimension must be 1536")
        if _field(vector_spec, "metric") != "cosine":
            raise ValueError("Pinecone schema embedding metric must be cosine")
        if self.integrated_embeddings and _field(spec, "store_text", True) is False:
            raise ValueError("Pinecone embedded field must store text for vault answers")
        if any(_field(field_spec, "sparse_embed") is not None
               for field_spec in self._fields.values()):
            raise ValueError("Pinecone sparse-embedding schemas are not supported by FamGuard")
        for name in ("family_id", "family_member_id", "category", "document_id",
                     "expiration_date", "requires_alert", "is_primary"):
            metadata_spec = self._fields.get(name)
            if metadata_spec is not None and (
                _field(metadata_spec, "filterable") is not True
                or _field(metadata_spec, "embed") is not None
                or _field(metadata_spec, "full_text_search") is not None
            ):
                raise ValueError(f"Pinecone schema field {name} must allow metadata filtering")
        if self._vector_field in ("text", "document_name", "family_id", "document_id"):
            raise ValueError("Pinecone vector field must not replace vault metadata fields")

    def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str) -> Any:
        documents = []
        for record in vectors:
            document = {**record["metadata"], "_id": record["id"]}
            for name, spec in self._fields.items():
                if _field(spec, "type") == "string" and (
                    _field(spec, "embed") is not None
                    or _field(spec, "full_text_search") is not None
                ):
                    document[name] = (
                        record["metadata"]["document_name"] if name == "title"
                        else record["metadata"]["text"]
                    )
            if not self.integrated_embeddings:
                document[self._vector_field] = list(record["values"])
            documents.append(document)
        response = self._index.documents.batch_upsert(
            namespace=namespace, documents=documents, batch_size=50,
            max_concurrency=1, show_progress=False,
        )
        if (
            _field(response, "successful_item_count") != len(documents)
            or _field(response, "failed_item_count", 0)
            or _field(response, "timed_out", False)
        ):
            raise RuntimeError("Pinecone did not accept all vault chunks; document batch failed")
        return response

    def query(
        self, *, top_k: int, vector: Sequence[float], namespace: str,
        filter: Mapping[str, Any] | None, include_metadata: bool, query_text: str | None = None,
    ) -> dict[str, Any]:
        if self.integrated_embeddings:
            if not query_text or not query_text.strip():
                raise ValueError("Pinecone integrated search requires query text")
            clause = {"type": "embed", "field": self._vector_field, "query": query_text}
        else:
            clause = {
                "type": "dense_vector", "field": self._vector_field, "values": list(vector),
            }
        response = self._index.documents.search(
            namespace=namespace, top_k=top_k, score_by=[clause],
            filter=dict(filter) if filter else None,
            include_fields=["*"] if include_metadata else [],
        )
        matches = []
        for match in _field(response, "matches", []):
            fields = match.to_dict() if hasattr(match, "to_dict") else dict(match)
            record_id = fields.pop("_id")
            score = fields.pop("_score")
            fields.pop(self._vector_field, None)
            matches.append({"id": record_id, "score": score, "metadata": fields})
        return {"matches": matches}

    def list(self, *, prefix: str | None, namespace: str) -> Iterator[list[str]]:
        for record in self._index.documents.list(namespace=namespace, prefix=prefix):
            yield [str(_field(record, "id", _field(record, "_id")))]

    def delete(self, *, ids: Sequence[str], namespace: str) -> Any:
        return self._index.documents.delete(namespace=namespace, ids=list(ids))

    def describe_index_stats(self) -> Any:
        return self._index.describe_index_stats()


class VectorStore:
    def __init__(self, index: VectorIndex, namespace: str) -> None:
        self._index = index
        self._namespace = namespace

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def integrated_embeddings(self) -> bool:
        return isinstance(self._index, DocumentSchemaIndex) and self._index.integrated_embeddings

    async def ping(self) -> bool:
        await asyncio.to_thread(self._index.describe_index_stats)
        return True

    async def upsert(self, records: Sequence[VectorRecord]) -> int:
        if not self.integrated_embeddings and any(
            len(record["values"]) != EMBEDDING_DIMENSIONS for record in records
        ):
            raise ValueError("Vault chunks require 1536-dimensional vectors")
        for start in range(0, len(records), UPSERT_BATCH_SIZE):
            batch = [dict(r) for r in records[start : start + UPSERT_BATCH_SIZE]]
            await asyncio.to_thread(self._index.upsert, vectors=batch, namespace=self._namespace)
        return len(records)

    async def query(
        self,
        vector: Sequence[float] | None,
        *,
        filter: Mapping[str, Any],
        top_k: int = 8,
        tenant_scoped: bool = True,
        query_text: str | None = None,
    ) -> list[VectorMatch]:
        """`tenant_scoped=False` is reserved for system jobs such as the expiry sweep."""
        if tenant_scoped:
            assert_tenant_scoped(filter)
        if not 1 <= top_k <= MAX_TOP_K_WITH_METADATA:
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K_WITH_METADATA}")
        if not self.integrated_embeddings and (
            vector is None or len(vector) != EMBEDDING_DIMENSIONS
        ):
            raise ValueError(f"Query vector must have {EMBEDDING_DIMENSIONS} dimensions")
        if isinstance(self._index, DocumentSchemaIndex):
            response = await asyncio.to_thread(
                self._index.query, top_k=top_k, vector=list(vector) if vector is not None else [],
                namespace=self._namespace, filter=dict(filter), include_metadata=True,
                query_text=query_text,
            )
        else:
            assert vector is not None
            response = await asyncio.to_thread(
                self._index.query, top_k=top_k, vector=list(vector),
                namespace=self._namespace, filter=dict(filter), include_metadata=True,
            )
        return [
            VectorMatch(
                id=str(_field(m, "id")),
                score=float(_field(m, "score", 0.0)),
                metadata=dict(_field(m, "metadata") or {}),
            )
            for m in _field(response, "matches", []) or []
        ]

    async def delete_document(self, document_id: UUID | str) -> int:
        """Serverless indexes cannot delete by metadata, so ids are listed by prefix."""
        prefix = f"{document_id}#"

        def _collect() -> list[str]:
            ids: list[str] = []
            for page in self._index.list(prefix=prefix, namespace=self._namespace):
                ids.extend(_page_ids(page))
            return ids

        ids = await asyncio.to_thread(_collect)
        for start in range(0, len(ids), DELETE_BATCH_SIZE):
            await asyncio.to_thread(
                self._index.delete,
                ids=ids[start : start + DELETE_BATCH_SIZE],
                namespace=self._namespace,
            )
        return len(ids)


def _page_ids(page: Any) -> Iterable[str]:
    if isinstance(page, str):
        return [page]
    if isinstance(page, list):
        return [p if isinstance(p, str) else str(_field(p, "id")) for p in page]
    return [str(_field(v, "id")) for v in _field(page, "vectors", []) or []]


# ---------------------------------------------------------------------------
# In-memory index used for tests and FAKE_EXTERNALS mode.
# ---------------------------------------------------------------------------


@dataclass
class _FakeMatch:
    id: str
    score: float
    metadata: dict[str, Any]


@dataclass
class _FakeQueryResponse:
    matches: list[_FakeMatch]


@dataclass
class InMemoryVectorIndex:
    namespaces: dict[str, dict[str, VectorRecord]] = field(default_factory=dict)
    query_calls: list[dict[str, Any]] = field(default_factory=list)
    upsert_calls: int = 0

    def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str) -> dict[str, int]:
        self.upsert_calls += 1
        store = self.namespaces.setdefault(namespace, {})
        for v in vectors:
            for key, value in v["metadata"].items():
                if value is None:
                    raise ValueError(f"metadata '{key}' is null")
            store[v["id"]] = VectorRecord(
                id=v["id"], values=list(v["values"]), metadata=dict(v["metadata"])
            )
        return {"upserted_count": len(vectors)}

    def query(
        self,
        *,
        top_k: int,
        vector: Sequence[float],
        namespace: str,
        filter: Mapping[str, Any] | None,
        include_metadata: bool,
    ) -> _FakeQueryResponse:
        self.query_calls.append(
            {"top_k": top_k, "vector": list(vector), "namespace": namespace,
             "filter": dict(filter or {}), "include_metadata": include_metadata}
        )
        if not any(vector):
            raise ValueError("Dense vectors must contain at least one non-zero value")
        hits = [
            _FakeMatch(
                id=r["id"],
                score=_cosine(vector, r["values"]),
                metadata=dict(r["metadata"]) if include_metadata else {},
            )
            for r in self.namespaces.get(namespace, {}).values()
            if matches_filter(r["metadata"], filter or {})
        ]
        hits.sort(key=lambda m: m.score, reverse=True)
        return _FakeQueryResponse(matches=hits[:top_k])

    def list(self, *, prefix: str | None, namespace: str) -> Iterator[list[str]]:
        ids = sorted(
            i for i in self.namespaces.get(namespace, {}) if prefix is None or i.startswith(prefix)
        )
        for start in range(0, len(ids), 100):
            yield ids[start : start + 100]

    def delete(self, *, ids: Sequence[str], namespace: str) -> dict[str, Any]:
        store = self.namespaces.get(namespace, {})
        for i in ids:
            store.pop(i, None)
        return {}

    def describe_index_stats(self) -> dict[str, Any]:
        return {"dimension": EMBEDDING_DIMENSIONS,
                "total_vector_count": sum(len(ns) for ns in self.namespaces.values())}


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def matches_filter(metadata: Mapping[str, Any], flt: Mapping[str, Any]) -> bool:
    """Evaluate the subset of Pinecone filter syntax FamGuard uses."""
    for key, condition in flt.items():
        if key == "$and":
            if not all(matches_filter(metadata, sub) for sub in condition):
                return False
            continue
        if key == "$or":
            if not any(matches_filter(metadata, sub) for sub in condition):
                return False
            continue
        ops = condition if isinstance(condition, Mapping) else {"$eq": condition}
        present = key in metadata
        value = metadata.get(key)
        for op, expected in ops.items():
            if op == "$eq" and not (present and value == expected):
                return False
            if op == "$ne" and present and value == expected:
                return False
            if op == "$in" and not (present and value in expected):
                return False
            if op == "$nin" and present and value in expected:
                return False
            if op == "$exists" and present != bool(expected):
                return False
            if op in ("$gt", "$gte", "$lt", "$lte"):
                if not present or not isinstance(value, int | float):
                    return False
                if op == "$gt" and not value > expected:
                    return False
                if op == "$gte" and not value >= expected:
                    return False
                if op == "$lt" and not value < expected:
                    return False
                if op == "$lte" and not value <= expected:
                    return False
    return True


class SQLiteVectorIndex:
    """Dev index shared by API and worker processes on the local vault volume."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vectors ("
                "namespace TEXT NOT NULL, id TEXT NOT NULL, record TEXT NOT NULL, "
                "PRIMARY KEY (namespace, id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30)

    def _snapshot(self, namespace: str) -> InMemoryVectorIndex:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT record FROM vectors WHERE namespace = ?", (namespace,)
            ).fetchall()
        index = InMemoryVectorIndex()
        index.upsert(vectors=[json.loads(row[0]) for row in rows], namespace=namespace)
        return index

    def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str) -> dict[str, int]:
        # Reuse the offline index's validation before committing the batch atomically.
        validated = InMemoryVectorIndex()
        result = validated.upsert(vectors=vectors, namespace=namespace)
        rows = [
            (namespace, record["id"], json.dumps(record, allow_nan=False))
            for record in validated.namespaces[namespace].values()
        ]
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO vectors (namespace, id, record) VALUES (?, ?, ?) "
                "ON CONFLICT(namespace, id) DO UPDATE SET record = excluded.record",
                rows,
            )
        return result

    def query(
        self, *, top_k: int, vector: Sequence[float], namespace: str,
        filter: Mapping[str, Any] | None, include_metadata: bool,
    ) -> _FakeQueryResponse:
        return self._snapshot(namespace).query(
            top_k=top_k, vector=vector, namespace=namespace,
            filter=filter, include_metadata=include_metadata,
        )

    def list(self, *, prefix: str | None, namespace: str) -> Iterator[list[str]]:
        yield from self._snapshot(namespace).list(prefix=prefix, namespace=namespace)

    def delete(self, *, ids: Sequence[str], namespace: str) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "DELETE FROM vectors WHERE namespace = ? AND id = ?",
                [(namespace, record_id) for record_id in ids],
            )
        return {}

    def describe_index_stats(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            count = conn.execute("SELECT count(*) FROM vectors").fetchone()[0]
        return {"dimension": EMBEDDING_DIMENSIONS, "total_vector_count": count}


_fake_index = InMemoryVectorIndex()


def get_vector_store(settings: Settings) -> VectorStore:
    if settings.fake_externals:
        if settings.local_storage_dir:
            return VectorStore(
                SQLiteVectorIndex(Path(settings.local_storage_dir) / ".vectors.sqlite3"),
                settings.pinecone_namespace,
            )
        return VectorStore(_fake_index, settings.pinecone_namespace)
    from pinecone import Pinecone

    client = Pinecone(api_key=settings.pinecone_api_key.get_secret_value())
    descriptor = client.describe_index(settings.pinecone_index)
    index = client.Index(settings.pinecone_index)
    schema = _field(descriptor, "schema")
    if schema is not None:
        return VectorStore(DocumentSchemaIndex(index, schema), settings.pinecone_namespace)
    return VectorStore(index, settings.pinecone_namespace)

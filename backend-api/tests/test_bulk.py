from __future__ import annotations

import csv
import io
import tracemalloc
from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from app.bulk import (
    EXPECTED_HEADERS,
    BulkHeaderError,
    BulkImporter,
    RowValidationError,
    parse_row,
)
from app.embeddings import FakeEmbedder
from app.repositories import FamilyMember, FamilyRepo, MemberRepo
from app.security import Principal
from app.vectorstore import InMemoryVectorIndex, VectorStore
from tests.helpers import make_principal

NS = "family-vault"
CATEGORIES = {"identitycards", "medical", "expense", "vehicle"}


def csv_bytes(rows: Sequence[Sequence[Any]], headers: Sequence[str] = EXPECTED_HEADERS) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _member(role: str = "admin") -> FamilyMember:
    return FamilyMember.model_validate(
        {"id": uuid4(), "family_id": uuid4(), "clerk_user_id": None, "display_name": "X",
         "email": None, "role": role, "created_at": "2026-01-01T00:00:00Z"}
    )


# ---------------------------------------------------------------------------
# Row validation (pure)
# ---------------------------------------------------------------------------


def _row(member_id: str, **overrides: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "FamilyMemberID": member_id, "Category": "identitycards",
        "DocumentName": "John Passport", "ExtractedText": "Passport text",
        "ExpirationDate": "2031-05-04", "FinancialValue": "",
    }
    values.update(overrides)
    return values


def test_parse_valid_row_derives_alert_flag() -> None:
    m = _member()
    principal = make_principal("admin", family_id=m.family_id, member_id=m.id)
    row = parse_row(_row(str(m.id), Category=" IdentityCards ", FinancialValue="1,250.50"),
                    7, members={m.id: m}, principal=principal, categories=CATEGORIES)
    assert row.row == 7
    assert row.extraction.category == "identitycards"
    assert row.extraction.expiration_date == date(2031, 5, 4)
    assert row.extraction.requires_alert is True
    assert row.extraction.financial_value == 1250.5

    no_expiry = parse_row(_row(str(m.id), ExpirationDate="", ExtractedText=""), 2,
                          members={m.id: m}, principal=principal, categories=CATEGORIES)
    assert no_expiry.extraction.requires_alert is False
    assert no_expiry.extraction.extracted_text == "John Passport"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"FamilyMemberID": "nope"}, "not a valid id"),
        ({"FamilyMemberID": str(uuid4())}, "Unknown family member"),
        ({"Category": "spaceship"}, "Unknown category"),
        ({"DocumentName": "  "}, "DocumentName is required"),
        ({"DocumentName": "x" * 201}, "exceeds 200"),
        ({"ExpirationDate": "04/05/2031"}, "YYYY-MM-DD"),
        ({"ExpirationDate": "2031-02-30"}, "YYYY-MM-DD"),
        ({"FinancialValue": "abc"}, "must be a number"),
        ({"FinancialValue": "nan"}, "out of range"),
        ({"FinancialValue": "1e13"}, "out of range"),
    ],
)
def test_invalid_rows(overrides: dict[str, str], reason: str) -> None:
    m = _member()
    principal = make_principal("admin", family_id=m.family_id, member_id=m.id)
    values = _row(str(m.id))
    values.update(overrides)
    with pytest.raises(RowValidationError, match=reason):
        parse_row(values, 2, members={m.id: m}, principal=principal, categories=CATEGORIES)


def test_member_role_can_only_import_for_self() -> None:
    me, other = _member("member"), _member("dependent")
    principal = make_principal("member", family_id=me.family_id, member_id=me.id)
    members = {me.id: me, other.id: other}
    parse_row(_row(str(me.id)), 2, members=members, principal=principal, categories=CATEGORIES)
    with pytest.raises(RowValidationError, match="Not allowed"):
        parse_row(_row(str(other.id)), 2, members=members, principal=principal,
                  categories=CATEGORIES)


# ---------------------------------------------------------------------------
# Importer (Postgres + in-memory vectors)
# ---------------------------------------------------------------------------


async def _family(pool: asyncpg.Pool) -> tuple[Principal, FamilyMember, FamilyMember]:
    family = await FamilyRepo(pool).create("Bulk")
    admin = await MemberRepo(pool).create(family.id, display_name="Ann", role="admin")
    john = await MemberRepo(pool).create(family.id, display_name="John", role="dependent")
    principal = make_principal("admin", family_id=family.id, member_id=admin.id)
    return principal, admin, john


def _importer(pool: asyncpg.Pool, index: InMemoryVectorIndex | None = None,
              embedder: FakeEmbedder | None = None) -> tuple[BulkImporter, InMemoryVectorIndex]:
    index = index or InMemoryVectorIndex()
    return BulkImporter(pool, embedder or FakeEmbedder(), VectorStore(index, NS)), index


@pytest.mark.integration
async def test_import_200_rows_with_3_bad(db_pool: asyncpg.Pool) -> None:
    principal, _, john = await _family(db_pool)
    rows: list[list[Any]] = [
        [john.id, "identitycards", f"Doc {i}", f"Text {i}", "2030-01-01" if i % 2 else "", i]
        for i in range(200)
    ]
    rows[10][1] = "spaceship"
    rows[50][4] = "2030/01/01"
    rows[150][0] = uuid4()
    importer, index = _importer(db_pool)

    report = await importer.run(io.BytesIO(csv_bytes(rows)), principal)

    assert (report.accepted, report.rejected) == (197, 3)
    assert [(e.row, e.reason.split(" ")[0]) for e in report.errors] == [
        (12, "Unknown"), (52, "ExpirationDate"), (152, "Unknown")]
    assert len(index.namespaces[NS]) == 197
    assert index.upsert_calls == 2
    assert await db_pool.fetchval(
        "SELECT count(*) FROM documents WHERE source = 'bulk' AND status = 'completed'"
    ) == 197
    alertable = await db_pool.fetchval("SELECT count(*) FROM documents WHERE requires_alert")
    assert alertable == 100
    meta = next(iter(index.namespaces[NS].values()))["metadata"]
    assert meta["family_id"] == str(principal.family_id)
    assert meta["source"] == "bulk"


@pytest.mark.integration
async def test_headers_validated(db_pool: asyncpg.Pool) -> None:
    principal, _, _ = await _family(db_pool)
    importer, _ = _importer(db_pool)
    with pytest.raises(BulkHeaderError, match="FamilyMemberID"):
        await importer.run(io.BytesIO(csv_bytes([], headers=["Name", "Category"])), principal)
    # Case/whitespace/order-insensitive and BOM tolerant.
    shuffled = [" category", "familymemberid", "DocumentName", "ExtractedText",
                "ExpirationDate", "FinancialValue"]
    data = "\ufeff".encode() + csv_bytes([], headers=shuffled)
    report = await importer.run(io.BytesIO(data), principal)
    assert report.accepted == 0 and report.rejected == 0


@pytest.mark.integration
async def test_quoted_multiline_fields_and_blank_rows(db_pool: asyncpg.Pool) -> None:
    principal, _, john = await _family(db_pool)
    rows = [[john.id, "medical", 'Policy, "Gold"', "Line one\nLine two", "", ""],
            ["", "", "", "", "", ""],
            [john.id, "medical", "Second", "x", "", "oops"]]
    importer, index = _importer(db_pool)
    report = await importer.run(io.BytesIO(csv_bytes(rows)), principal)
    assert report.accepted == 1
    assert [e.row for e in report.errors] == [4]
    meta = next(iter(index.namespaces[NS].values()))["metadata"]
    assert meta["document_name"] == 'Policy, "Gold"'
    assert "Line two" in meta["text"]


@pytest.mark.integration
async def test_row_limit(db_pool: asyncpg.Pool) -> None:
    principal, _, john = await _family(db_pool)
    rows = [[john.id, "expense", f"R{i}", "", "", ""] for i in range(15)]
    importer, _ = _importer(db_pool)
    report = await importer.run(io.BytesIO(csv_bytes(rows)), principal, max_rows=10)
    assert report.accepted == 10
    assert "Row limit of 10 exceeded" in report.errors[-1].reason


@pytest.mark.integration
async def test_embedding_failure_stops_import_without_writes(db_pool: asyncpg.Pool) -> None:
    class Broken(FakeEmbedder):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise ConnectionError("openai down")

    principal, _, john = await _family(db_pool)
    rows = [[john.id, "expense", f"R{i}", "", "", ""] for i in range(150)]
    importer, index = _importer(db_pool, embedder=Broken())
    report = await importer.run(io.BytesIO(csv_bytes(rows)), principal)
    assert report.accepted == 0 and report.rejected == 100
    assert "Embedding service unavailable" in report.errors[0].reason
    assert await db_pool.fetchval("SELECT count(*) FROM documents") == 0
    assert index.namespaces == {}


@pytest.mark.integration
async def test_upsert_failure_rolls_back_documents(db_pool: asyncpg.Pool) -> None:
    class BrokenIndex(InMemoryVectorIndex):
        def upsert(self, *, vectors: Sequence[Any], namespace: str) -> dict[str, int]:
            raise ConnectionError("pinecone down")

    principal, _, john = await _family(db_pool)
    importer, _ = _importer(db_pool, index=BrokenIndex())
    report = await importer.run(
        io.BytesIO(csv_bytes([[john.id, "expense", "R", "", "", ""]])), principal
    )
    assert report.accepted == 0
    assert await db_pool.fetchval("SELECT count(*) FROM documents") == 0


@pytest.mark.integration
async def test_streaming_keeps_memory_bounded(db_pool: asyncpg.Pool) -> None:
    class DiscardingIndex(InMemoryVectorIndex):
        def upsert(self, *, vectors: Sequence[Any], namespace: str) -> dict[str, int]:
            self.upsert_calls += 1
            return {"upserted_count": len(vectors)}

    principal, _, john = await _family(db_pool)
    rows = [[john.id, "expense", f"Receipt {i}", "Grocery receipt " * 20, "", "12.5"]
            for i in range(1000)]
    data = csv_bytes(rows)

    importer, index = _importer(db_pool, index=DiscardingIndex())
    tracemalloc.start()
    report = await importer.run(io.BytesIO(data), principal)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert report.accepted == 1000
    assert index.upsert_calls == 10
    # One batch of 100 rows/vectors stays in memory, not the whole file's vectors.
    assert peak < 40 * 1024 * 1024

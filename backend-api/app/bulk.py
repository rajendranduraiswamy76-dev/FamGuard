"""Streaming CSV import: validate row-by-row, embed in batches, upsert to Pinecone."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import IO, Final
from uuid import UUID

import asyncpg

from app.agents.state import UnifiedVaultExtraction, parse_iso_date
from app.embeddings import Embedder, chunk_text
from app.repositories import (
    BulkDocumentRow,
    CategoryRepo,
    DocumentRepo,
    FamilyMember,
    MemberRepo,
)
from app.security import Principal
from app.vectorstore import VectorRecord, VectorStore, build_records

logger = logging.getLogger(__name__)

EXPECTED_HEADERS: Final = (
    "FamilyMemberID", "Category", "DocumentName", "ExtractedText", "ExpirationDate",
    "FinancialValue",
)
MAX_BULK_BYTES: Final = 5 * 1024 * 1024
MAX_BULK_ROWS: Final = 5_000
BATCH_SIZE: Final = 100
MAX_REPORTED_ERRORS: Final = 200
MAX_TEXT_CHARS: Final = 50_000
MAX_FINANCIAL_VALUE: Final = 1e12  # NUMERIC(14, 2)


class BulkHeaderError(ValueError):
    pass


class RowValidationError(ValueError):
    pass


@dataclass
class RowError:
    row: int
    reason: str


@dataclass
class BulkReport:
    accepted: int = 0
    rejected: int = 0
    errors: list[RowError] = field(default_factory=list)
    errors_truncated: bool = False

    def reject(self, row: int, reason: str) -> None:
        self.rejected += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(RowError(row=row, reason=reason))
        else:
            self.errors_truncated = True


@dataclass(frozen=True, slots=True)
class ValidRow:
    row: int
    member_id: UUID
    extraction: UnifiedVaultExtraction
    currency: str | None = None
    subcategory: str | None = None
    details: str = ""
    amount: Decimal | None = None
    transaction_date: date | None = None
    payment_due_date: date | None = None
    is_paid: bool = True


def _cell(raw: dict[str, str | None], name: str) -> str:
    return (raw.get(name) or "").strip()


def parse_row(
    raw: dict[str, str | None],
    row_number: int,
    *,
    members: dict[UUID, FamilyMember],
    principal: Principal,
    categories: set[str],
) -> ValidRow:
    try:
        member_id = UUID(_cell(raw, "FamilyMemberID"))
    except ValueError as exc:
        raise RowValidationError("FamilyMemberID is not a valid id") from exc
    if member_id not in members:
        raise RowValidationError("Unknown family member")
    if not principal.is_admin and member_id != principal.member_id:
        raise RowValidationError("Not allowed to import documents for this member")

    category = _cell(raw, "Category").lower()
    if category not in categories:
        raise RowValidationError(f"Unknown category '{category[:48]}'")

    name = _cell(raw, "DocumentName")
    if not name:
        raise RowValidationError("DocumentName is required")
    if len(name) > 200:
        raise RowValidationError("DocumentName exceeds 200 characters")

    text = _cell(raw, "ExtractedText")
    if len(text) > MAX_TEXT_CHARS:
        raise RowValidationError(f"ExtractedText exceeds {MAX_TEXT_CHARS} characters")

    expiry_raw = _cell(raw, "ExpirationDate")
    expiration: date | None = None
    if expiry_raw:
        expiration = parse_iso_date(expiry_raw)
        if expiration is None:
            raise RowValidationError("ExpirationDate must be YYYY-MM-DD")

    value_raw = _cell(raw, "FinancialValue").replace(",", "")
    value: float | None = None
    if value_raw:
        try:
            value = float(value_raw)
        except ValueError as exc:
            raise RowValidationError("FinancialValue must be a number") from exc
        if not math.isfinite(value) or abs(value) >= MAX_FINANCIAL_VALUE:
            raise RowValidationError("FinancialValue is out of range")

    extraction = UnifiedVaultExtraction(
        category=category,
        document_name=name,
        expiration_date=expiration,
        requires_alert=expiration is not None,
        extracted_text=text or name,
        financial_value=value,
        confidence_score=1.0,
    )
    return ValidRow(row=row_number, member_id=member_id, extraction=extraction)


def _open_reader(stream: IO[bytes]) -> tuple[Iterator[dict[str, str | None]], dict[str, str]]:
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    found = [h.strip() for h in (reader.fieldnames or [])]
    by_lower = {h.lower(): h for h in found}
    if len(found) != len(EXPECTED_HEADERS) or set(by_lower) != {
        h.lower() for h in EXPECTED_HEADERS
    }:
        raise BulkHeaderError("CSV headers must be exactly: " + ", ".join(EXPECTED_HEADERS))
    reader.fieldnames = found
    mapping = {expected: by_lower[expected.lower()] for expected in EXPECTED_HEADERS}
    return reader, mapping


class BulkImporter:
    def __init__(self, pool: asyncpg.Pool, embedder: Embedder, vectors: VectorStore) -> None:
        self._pool = pool
        self._embedder = embedder
        self._vectors = vectors

    async def run(
        self, stream: IO[bytes], principal: Principal, *, max_rows: int = MAX_BULK_ROWS
    ) -> BulkReport:
        reader, mapping = await asyncio.to_thread(_open_reader, stream)
        members = {m.id: m for m in await MemberRepo(self._pool).list_all(principal.family_id)}
        categories = set(await CategoryRepo(self._pool).list_slugs(principal.family_id))
        report = BulkReport()
        row_number = 1  # header line; data rows are numbered like a spreadsheet

        def next_batch() -> list[tuple[int, dict[str, str | None]]]:
            nonlocal row_number
            batch: list[tuple[int, dict[str, str | None]]] = []
            for raw in reader:
                row_number += 1
                normalized = {name: raw.get(col) for name, col in mapping.items()}
                if not any((v or "").strip() for v in normalized.values()):
                    continue
                batch.append((row_number, normalized))
                if len(batch) >= BATCH_SIZE:
                    break
            return batch

        processed = 0
        while True:
            try:
                batch = await asyncio.to_thread(next_batch)
            except (csv.Error, UnicodeDecodeError) as exc:
                report.reject(row_number, f"Malformed CSV, import stopped: {type(exc).__name__}")
                break
            if not batch:
                break

            valid: list[ValidRow] = []
            limit_hit = False
            for number, raw in batch:
                if processed >= max_rows:
                    limit_hit = True
                    break
                processed += 1
                try:
                    valid.append(parse_row(raw, number, members=members, principal=principal,
                                           categories=categories))
                except RowValidationError as exc:
                    report.reject(number, str(exc))
            if valid and not await self.import_rows(valid, principal, report):
                break
            if limit_hit:
                report.reject(row_number, f"Row limit of {max_rows} exceeded; "
                                          "remaining rows were not imported")
                break

        logger.info("bulk import finished",
                    extra={"accepted": report.accepted, "rejected": report.rejected})
        return report

    async def import_rows(self, rows: Sequence[ValidRow], principal: Principal,
                      report: BulkReport) -> bool:
        """Embed, persist and upsert one batch. Returns False if the import must stop."""
        chunked = [chunk_text(r.extraction.extracted_text) or [r.extraction.document_name]
                   for r in rows]
        try:
            vectors = (
                None if self._vectors.integrated_embeddings
                else await self._embedder.embed([c for chunks in chunked for c in chunks])
            )
        except Exception:
            logger.exception("bulk embedding failed")
            for r in rows:
                report.reject(r.row, "Embedding service unavailable; import stopped")
            return False

        doc_ids = [uuid.uuid4() for _ in rows]
        records: list[VectorRecord] = []
        offset = 0
        for row, chunks, doc_id in zip(rows, chunked, doc_ids, strict=True):
            records += build_records(
                family_id=principal.family_id, family_member_id=row.member_id,
                document_id=doc_id, extraction=row.extraction, chunks=chunks,
                vectors=vectors[offset: offset + len(chunks)] if vectors is not None else None,
                source="bulk",
                currency=row.currency, financial_subcategory=row.subcategory,
                document_details=row.details,
            )
            offset += len(chunks)

        await DocumentRepo(self._pool).create_completed_bulk(
            principal.family_id,
            [
                BulkDocumentRow(
                    document_id=doc_id,
                    family_member_id=row.member_id,
                    category_slug=row.extraction.category,
                    document_name=row.extraction.document_name,
                    expiration_date=row.extraction.expiration_date,
                    requires_alert=row.extraction.requires_alert,
                    financial_value=row.amount if row.amount is not None
                    else row.extraction.financial_value,
                    chunk_count=len(chunks),
                    currency=row.currency, financial_subcategory=row.subcategory,
                    document_details=row.details,
                    transaction_date=row.transaction_date,
                    payment_due_date=row.payment_due_date, is_paid=row.is_paid,
                )
                for row, chunks, doc_id in zip(rows, chunked, doc_ids, strict=True)
            ],
        )
        try:
            await self._vectors.upsert(records)
        except Exception:
            logger.exception("bulk upsert failed")
            documents = DocumentRepo(self._pool)
            for doc_id in doc_ids:
                await documents.delete(principal.family_id, doc_id)
                try:
                    await self._vectors.delete_document(doc_id)
                except Exception:  # best effort: a partial batch may have been written
                    logger.warning("orphan vector cleanup failed",
                                   extra={"document_id": str(doc_id)})
            for r in rows:
                report.reject(r.row, "Vector store unavailable; import stopped")
            return False
        report.accepted += len(rows)
        return True

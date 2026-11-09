from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from app.api.schemas import BulkImportOut, BulkRowError
from app.bulk import MAX_BULK_BYTES, BulkImporter, BulkReport
from app.dependencies import provide_bulk_importer, provide_embedder, provide_vector_store
from app.embeddings import Embedder, chunk_text
from app.financial import (
    VALID_CURRENCIES,
    FinancialRow,
    FinancialTable,
    build_excel_template,
    financial_extraction,
    import_financial_rows,
    read_excel,
)
from app.financial_dashboard import (
    DateCorrection,
    FinanceDashboard,
    FinanceOptions,
    FinanceRecord,
    FinancialPage,
    records,
    summarize,
)
from app.parsers import FileParseError, UploadValidationError
from app.ratelimit import BULK_LIMIT, limiter
from app.repositories import FamilyRepo, MemberRepo
from app.security import CurrentPrincipal, assert_member_access, get_db
from app.vectorstore import VectorStore, build_records

router = APIRouter(prefix="/api/vault/financial", tags=["financial"])
Db = Annotated[asyncpg.Pool, Depends(get_db)]
Importer = Annotated[BulkImporter, Depends(provide_bulk_importer)]
Vectors = Annotated[VectorStore, Depends(provide_vector_store)]
Embedding = Annotated[Embedder, Depends(provide_embedder)]
logger = logging.getLogger(__name__)


@router.get("/entries", response_model=FinancialPage)
async def list_entries(
    principal: CurrentPrincipal, pool: Db,
    person_name: Annotated[str | None, Query(max_length=80)] = None,
    category: Literal["income", "expense"] | None = None,
    subcategory: Annotated[str | None, Query(max_length=80)] = None,
    currency: Annotated[str | None, Query(max_length=3)] = None,
    transaction_from: date | None = None, transaction_to: date | None = None,
    due_from: date | None = None, due_to: date | None = None,
    is_paid: bool | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> FinancialPage:
    if ((transaction_from and transaction_to and transaction_from > transaction_to)
            or (due_from and due_to and due_from > due_to)):
        raise HTTPException(422, "Date range start must not be after its end")
    if not any((person_name, category, subcategory, currency, transaction_from,
                transaction_to, due_from, due_to)) and is_paid is None:
        return FinancialPage(entries=[], has_next=False)
    raw = await pool.fetch(
        """SELECT d.id, m.display_name AS person_name, d.category_slug AS category,
                  COALESCE(d.financial_subcategory, '') AS subcategory,
                  d.financial_value AS amount, d.currency, d.document_details AS details,
                  d.transaction_date, d.payment_due_date, d.is_paid
           FROM documents d JOIN family_members m ON m.id = d.family_member_id
           WHERE d.family_id = $1 AND d.source = 'bulk' AND d.status = 'completed'
             AND d.currency IS NOT NULL AND d.financial_value IS NOT NULL
             AND d.category_slug IN ('income', 'expense')
             AND ($2::uuid IS NULL OR d.family_member_id = $2)
             AND ($3::text IS NULL OR m.display_name = $3)
             AND ($4::text IS NULL OR d.category_slug = $4)
             AND ($5::text IS NULL OR lower(d.financial_subcategory) = lower($5))
             AND ($6::text IS NULL OR d.currency = $6)
             AND ($7::date IS NULL OR d.transaction_date >= $7)
             AND ($8::date IS NULL OR d.transaction_date <= $8)
             AND ($9::date IS NULL OR d.payment_due_date >= $9)
             AND ($10::date IS NULL OR d.payment_due_date <= $10)
             AND ($11::boolean IS NULL OR d.is_paid = $11)
           ORDER BY d.created_at DESC, d.id LIMIT $12 OFFSET $13""",
        principal.family_id, None if principal.is_admin else principal.member_id,
        person_name, category, subcategory, currency, transaction_from, transaction_to,
        due_from, due_to, is_paid, limit + 1, offset,
    )
    return FinancialPage(entries=records(raw[:limit]), has_next=len(raw) > limit)


@router.put("/entries/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_entry(
    document_id: UUID, body: FinancialRow, principal: CurrentPrincipal, pool: Db,
    vectors: Vectors, embedder: Embedding,
) -> Response:
    members = await MemberRepo(pool).list_all(principal.family_id)
    matches = [member for member in members
               if member.display_name.casefold() == body.person_name.casefold()]
    if len(matches) != 1:
        raise HTTPException(422, "Person must uniquely match an available family member")
    member = matches[0]
    await assert_member_access(pool, principal, member.id)
    async with pool.acquire() as conn, conn.transaction():
        old = await conn.fetchrow(
            """SELECT family_member_id FROM documents
               WHERE id = $1 AND family_id = $2 AND source = 'bulk' AND status = 'completed'
                 AND currency IS NOT NULL AND financial_value IS NOT NULL
                 AND category_slug IN ('income', 'expense') FOR UPDATE""",
            document_id, principal.family_id,
        )
        if old is None:
            raise HTTPException(404, "Financial entry not found")
        await assert_member_access(pool, principal, old["family_member_id"])
        extraction = financial_extraction(body, member.display_name)
        chunks = chunk_text(extraction.extracted_text) or [extraction.document_name]
        version = str(uuid4())
        try:
            values = None if vectors.integrated_embeddings else await embedder.embed(chunks)
            indexed = build_records(
                family_id=principal.family_id, family_member_id=member.id,
                document_id=document_id, extraction=extraction, chunks=chunks, vectors=values,
                source="bulk", currency=body.currency, financial_subcategory=body.subcategory,
                document_details=body.details, member_name=member.display_name,
                index_version=version,
            )
            # New IDs preserve the live version if an upsert fails partway through.
            for record in indexed:
                record["id"] = f"{record['id']}-{version}"
            await vectors.upsert(indexed)
        except Exception as exc:
            logger.exception("financial entry indexing failed",
                             extra={"document_id": str(document_id)})
            raise HTTPException(503, "Search indexing unavailable; entry was not updated") from exc
        await conn.execute(
            """UPDATE documents SET family_member_id = $3, category_slug = $4,
               document_name = $5, financial_value = $6, currency = $7,
               financial_subcategory = $8, document_details = $9,
               transaction_date = $10, payment_due_date = $11, is_paid = $12,
               chunk_count = $13, index_version = $14, updated_at = now()
               WHERE id = $1 AND family_id = $2""",
            document_id, principal.family_id, member.id, body.category,
            extraction.document_name, body.amount, body.currency, body.subcategory, body.details,
            body.transaction_date, body.payment_due_date, body.is_paid, len(chunks), version,
        )
    return Response(status_code=204)


async def financial_records(
    principal: CurrentPrincipal, pool: asyncpg.Pool,
) -> list[FinanceRecord]:
    rows = await pool.fetch(
        """SELECT d.id, m.display_name AS person_name, d.category_slug AS category,
                  COALESCE(d.financial_subcategory, '') AS subcategory,
                  d.financial_value AS amount, d.currency, d.document_details AS details,
                  d.transaction_date, d.payment_due_date, d.is_paid
           FROM documents d JOIN family_members m ON m.id = d.family_member_id
           WHERE d.family_id = $1 AND d.source = 'bulk' AND d.status = 'completed'
             AND d.currency IS NOT NULL AND d.category_slug IN ('income', 'expense')
             AND d.financial_value IS NOT NULL
             AND ($2::uuid IS NULL OR d.family_member_id = $2)
           ORDER BY d.created_at DESC, d.id""",
        principal.family_id, None if principal.is_admin else principal.member_id,
    )
    return records(rows)


@router.get("/options", response_model=FinanceOptions)
async def options(principal: CurrentPrincipal, pool: Db) -> FinanceOptions:
    rows = await financial_records(principal, pool)
    defaults = {
        "income": ["Salary", "Bonus", "Interest", "Rental income", "Other income"],
        "expense": ["Groceries", "Rent", "Utilities", "Insurance", "Transport", "Other expense"],
    }
    for category in defaults:
        defaults[category] = sorted(set(defaults[category]) | {
            row.subcategory for row in rows if row.category == category and row.subcategory
        })
    return FinanceOptions(
        subcategories=defaults, currencies=sorted(VALID_CURRENCIES),
    )


@router.get("/dashboard", response_model=FinanceDashboard)
async def dashboard(principal: CurrentPrincipal, pool: Db) -> FinanceDashboard:
    family = await FamilyRepo(pool).get(principal.family_id)
    today = datetime.now(ZoneInfo(family.timezone if family else "UTC")).date()
    return summarize(await financial_records(principal, pool), today)


@router.put("/entries/{document_id}/date", status_code=status.HTTP_204_NO_CONTENT)
async def set_transaction_date(
    document_id: UUID, body: DateCorrection, principal: CurrentPrincipal, pool: Db,
) -> Response:
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """SELECT family_member_id FROM documents
               WHERE id = $1 AND family_id = $2 AND source = 'bulk' AND status = 'completed'
                 AND currency IS NOT NULL AND category_slug IN ('income', 'expense') FOR UPDATE""",
            document_id, principal.family_id,
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Financial entry not found")
        await assert_member_access(pool, principal, row["family_member_id"])
        await conn.execute(
            """UPDATE documents SET transaction_date = $3, updated_at = now()
               WHERE id = $1 AND family_id = $2""",
            document_id, principal.family_id, body.transaction_date,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def result(report: BulkReport) -> BulkImportOut:
    return BulkImportOut(
        accepted=report.accepted, rejected=report.rejected,
        errors=[BulkRowError(row=error.row, reason=error.reason) for error in report.errors],
        errors_truncated=report.errors_truncated,
    )


@router.get("/template")
async def template(principal: CurrentPrincipal, pool: Db) -> Response:
    members = await MemberRepo(pool).list_all(principal.family_id)
    if not principal.is_admin:
        members = [member for member in members if member.id == principal.member_id]
    family = await FamilyRepo(pool).get(principal.family_id)
    currency = family.currency if family and family.currency in VALID_CURRENCIES else "SGD"
    choices = await options(principal, pool)
    data = await asyncio.to_thread(build_excel_template, members, currency, choices.subcategories)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="famguard-income-expense.xlsx"'},
    )


@router.post("/entries", response_model=BulkImportOut)
@limiter.limit(BULK_LIMIT)
async def entries(
    request: Request, body: FinancialTable, principal: CurrentPrincipal,
    pool: Db, importer: Importer,
) -> BulkImportOut:
    return result(await import_financial_rows(
        list(enumerate(body.rows, start=1)), principal, pool, importer,
    ))


@router.post("/excel", response_model=BulkImportOut)
@limiter.limit(BULK_LIMIT)
async def excel(
    request: Request, principal: CurrentPrincipal, pool: Db, importer: Importer,
    file: Annotated[UploadFile, File()],
) -> BulkImportOut:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Choose an Excel .xlsx file")
    data = await file.read(MAX_BULK_BYTES + 1)
    if len(data) > MAX_BULK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Excel file must be 5 MB or smaller")
    try:
        rows = await asyncio.to_thread(read_excel, data)
    except (FileParseError, UploadValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return result(await import_financial_rows(rows, principal, pool, importer))

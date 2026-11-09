from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.schemas import CategoryMetric, DocumentUrlOut, MetricsOut, UpcomingExpiration
from app.dependencies import provide_storage, provide_vector_store
from app.repositories import DocumentRepo, FamilyRepo, PendingReviewRepo
from app.security import CurrentPrincipal, assert_member_access, get_db
from app.storage import SIGNED_URL_TTL_S, ObjectStorage
from app.vectorstore import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vault", tags=["documents"])

Db = Annotated[asyncpg.Pool, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(provide_storage)]
Vectors = Annotated[VectorStore, Depends(provide_vector_store)]

UPCOMING_WINDOW_DAYS = 30


@router.get("/metrics", response_model=MetricsOut)
async def metrics(principal: CurrentPrincipal, pool: Db) -> MetricsOut:
    scope = None if principal.is_admin else principal.member_id
    documents = DocumentRepo(pool)
    family = await FamilyRepo(pool).get(principal.family_id)
    today = datetime.now(ZoneInfo(family.timezone if family else "UTC")).date()

    by_category = await documents.category_counts(principal.family_id, family_member_id=scope)
    statuses = await documents.status_counts(principal.family_id, family_member_id=scope)
    upcoming = await documents.upcoming_expirations(
        principal.family_id, start=today, end=today + timedelta(days=UPCOMING_WINDOW_DAYS),
        family_member_id=scope,
    )
    year_upcoming = await documents.upcoming_expirations(
        principal.family_id, start=today + timedelta(days=30),
        end=today + timedelta(days=364), family_member_id=scope, limit=None,
    )
    return MetricsOut(
        documents_total=sum(n for _, n, _ in by_category),
        vectors_total=sum(v for _, _, v in by_category),
        by_category=[CategoryMetric(category=c, documents=n, vectors=v)
                     for c, n, v in by_category],
        upcoming_expirations=[
            UpcomingExpiration(
                document_id=doc.id, document_name=doc.document_name, member_name=member,
                category=doc.category_slug, expiration_date=doc.expiration_date,
                days_remaining=(doc.expiration_date - today).days,
            )
            for doc, member in upcoming if doc.expiration_date is not None
        ],
        year_expirations=[
            UpcomingExpiration(
                document_id=doc.id, document_name=doc.document_name, member_name=member,
                category=doc.category_slug, expiration_date=doc.expiration_date,
                days_remaining=(doc.expiration_date - today).days,
            )
            for doc, member in year_upcoming if doc.expiration_date is not None
        ],
        open_reviews=await PendingReviewRepo(pool).count_open(
            principal.family_id, family_member_id=scope
        ),
        processing=statuses.get("processing", 0) + statuses.get("review_required", 0),
        failed=statuses.get("failed", 0),
    )


@router.get("/documents/{document_id}/url", response_model=DocumentUrlOut)
async def document_url(document_id: UUID, principal: CurrentPrincipal, pool: Db,
                       storage: Storage) -> DocumentUrlOut:
    document = await DocumentRepo(pool).get(principal.family_id, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await assert_member_access(pool, principal, document.family_member_id)
    if not document.r2_object_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This document has no original file")
    url = await storage.presign_get(document.r2_object_key, SIGNED_URL_TTL_S)
    return DocumentUrlOut(url=url, expires_in=SIGNED_URL_TTL_S)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: UUID, principal: CurrentPrincipal, pool: Db,
                          storage: Storage, vectors: Vectors) -> Response:
    async with pool.acquire() as conn, conn.transaction():
        # Serialize against opening a correction review before touching the original.
        await conn.execute(
            "SELECT id FROM documents WHERE family_id = $1 AND id = $2 FOR UPDATE",
            principal.family_id, document_id,
        )
        documents = DocumentRepo(conn)
        document = await documents.get(principal.family_id, document_id)
        if document is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
        await assert_member_access(pool, principal, document.family_member_id)
        # Vectors go first: if Pinecone is down nothing is deleted and the user can retry.
        if document.status in ("processing", "review_required"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Document is still processing or awaiting review. Resolve its review and "
                "wait for processing to finish before deleting.",
            )
        try:
            await vectors.delete_document(document.id)
        except Exception as exc:
            logger.exception("vector delete failed", extra={"document_id": str(document.id)})
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Vector store unavailable, please retry") from exc
        if document.r2_object_key:
            await storage.delete(document.r2_object_key)
        await documents.delete(principal.family_id, document.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from __future__ import annotations

import logging
import uuid
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError

from app.agents.graph import IngestionService, IngestionStateError, ReviewResume
from app.agents.state import UnifiedVaultExtraction
from app.api.schemas import (
    BulkImportOut,
    BulkRowError,
    CategoryIn,
    CategoryOut,
    PendingReviewOut,
    ResolveReviewAccepted,
    ResolveReviewIn,
    UploadAccepted,
    UploadItem,
    UploadSource,
    UploadStatusOut,
)
from app.bulk import MAX_BULK_BYTES, BulkHeaderError, BulkImporter
from app.dependencies import (
    provide_bulk_importer,
    provide_ingestion_service,
    provide_storage,
    provide_task_queue,
)
from app.parsers import (
    MAX_UPLOAD_BYTES,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    ValidatedUpload,
    validate_upload,
)
from app.ratelimit import BULK_LIMIT, UPLOAD_LIMIT, limiter
from app.repositories import (
    CategoryRepo,
    Document,
    DocumentRepo,
    DuplicateDocumentError,
    PendingReviewRepo,
    ReviewDecision,
)
from app.security import AdminPrincipal, CurrentPrincipal, assert_member_access, get_db
from app.storage import ObjectStorage, build_object_key
from app.tasks import TaskQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vault", tags=["vault"])

Db = Annotated[asyncpg.Pool, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(provide_storage)]
Queue = Annotated[TaskQueue, Depends(provide_task_queue)]

MAX_FILES_PER_UPLOAD = 10
REPLACEABLE_STATUSES = ("failed", "rejected")


async def _read_and_validate(file: UploadFile) -> tuple[bytes, ValidatedUpload]:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        return data, validate_upload(data, file.filename)
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"{file.filename}: {exc}") from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"{file.filename}: {exc}") from exc
    except EmptyFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{file.filename}: {exc}") from exc


@router.post("/upload", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(UPLOAD_LIMIT)
async def upload(
    request: Request,
    principal: CurrentPrincipal,
    pool: Db,
    storage: Storage,
    queue: Queue,
    files: Annotated[list[UploadFile], File(description="Images, PDF, DOCX or XLSX")],
    family_member_id: Annotated[UUID, Form()],
    source: Annotated[UploadSource, Form()] = "upload",
    category: Annotated[str | None, Form(max_length=48)] = None,
    document_details: Annotated[str, Form(max_length=2000)] = "",
) -> UploadAccepted:
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No files provided")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"At most {MAX_FILES_PER_UPLOAD} files per request")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in document_details):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Document details cannot contain control characters",
        )
    member = await assert_member_access(pool, principal, family_member_id)
    if category is not None and not await CategoryRepo(pool).exists(principal.family_id, category):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown upload category")

    # Validate everything first so a bad file rejects the batch before anything is stored.
    validated = [await _read_and_validate(f) for f in files]
    documents = DocumentRepo(pool)
    for _, meta in validated:
        existing = await documents.find_by_sha256(principal.family_id, meta.sha256)
        if existing is not None and existing.status not in REPLACEABLE_STATUSES:
            raise DuplicateDocumentError(existing.id)

    accepted: list[UploadItem] = []
    for data, meta in validated:
        existing = await documents.find_by_sha256(principal.family_id, meta.sha256)
        if existing is not None:
            await documents.delete(principal.family_id, existing.id)
            if existing.r2_object_key:
                await storage.delete(existing.r2_object_key)

        document_id = uuid.uuid4()
        thread_id = f"ing-{uuid.uuid4().hex}"
        key = build_object_key(principal.family_id, document_id, meta.filename)
        await storage.put(key, data, meta.mime_type)
        try:
            document = await documents.create(
                principal.family_id,
                family_member_id=member.id,
                category_slug=category or "miscellaneous",
                requested_category=category,
                document_details=document_details.strip(),
                document_name=meta.filename,
                source=source,
                thread_id=thread_id,
                original_filename=meta.filename,
                r2_object_key=key,
                sha256=meta.sha256,
            )
        except Exception:
            await storage.delete(key)
            raise
        try:
            await queue.enqueue_ingestion(thread_id)
        except Exception as exc:
            logger.exception("enqueue failed", extra={"thread_id": thread_id})
            await documents.delete(principal.family_id, document.id)
            await storage.delete(key)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Processing queue unavailable, please retry") from exc
        accepted.append(UploadItem(thread_id=thread_id, document_id=document.id,
                                   filename=meta.filename))
    return UploadAccepted(uploads=accepted)


@router.post("/bulk-upload", response_model=BulkImportOut)
@limiter.limit(BULK_LIMIT)
async def bulk_upload(
    request: Request,
    principal: CurrentPrincipal,
    importer: Annotated[BulkImporter, Depends(provide_bulk_importer)],
    file: Annotated[UploadFile, File(description="CSV using the FamGuard template")],
) -> BulkImportOut:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a .csv file")
    if file.size is not None and file.size > MAX_BULK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"CSV exceeds {MAX_BULK_BYTES // (1024 * 1024)} MB")
    try:
        report = await importer.run(file.file, principal)
    except BulkHeaderError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "CSV must be UTF-8 encoded") from exc
    return BulkImportOut(
        accepted=report.accepted,
        rejected=report.rejected,
        errors=[BulkRowError(row=e.row, reason=e.reason) for e in report.errors],
        errors_truncated=report.errors_truncated,
    )


def _upload_status(document: Document) -> UploadStatusOut:
    assert document.thread_id is not None
    return UploadStatusOut(
        thread_id=document.thread_id, document_id=document.id,
        family_member_id=document.family_member_id, status=document.status,
        filename=document.original_filename, category=document.category_slug,
        document_name=document.document_name, expiration_date=document.expiration_date,
        requires_alert=document.requires_alert, error=document.error,
        document_details=document.document_details,
    )


@router.get("/uploads", response_model=list[UploadStatusOut])
async def recent_uploads(
    principal: CurrentPrincipal, pool: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    family_member_id: UUID | None = None,
    category: Annotated[str | None, Query(max_length=48)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UploadStatusOut]:
    if family_member_id is not None:
        await assert_member_access(pool, principal, family_member_id)
    if category is not None and not await CategoryRepo(pool).exists(principal.family_id, category):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown upload category")
    documents = await DocumentRepo(pool).recent_uploads(
        principal.family_id,
        family_member_id=family_member_id if principal.is_admin else principal.member_id,
        category=category, limit=limit, offset=offset,
    )
    return [_upload_status(document) for document in documents]


@router.get("/uploads/{thread_id}", response_model=UploadStatusOut)
async def upload_status(thread_id: str, principal: CurrentPrincipal, pool: Db) -> UploadStatusOut:
    document = await DocumentRepo(pool).get_by_thread(principal.family_id, thread_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    await assert_member_access(pool, principal, document.family_member_id)
    review = await PendingReviewRepo(pool).get_by_thread(principal.family_id, thread_id)
    result = _upload_status(document)
    result.pending_review_id = review.id if review and review.status == "open" else None
    return result


@router.post("/documents/{document_id}/review", response_model=UploadStatusOut)
async def review_extraction(
    document_id: UUID, principal: CurrentPrincipal, pool: Db,
    service: Annotated[IngestionService, Depends(provide_ingestion_service)],
) -> UploadStatusOut:
    document = await DocumentRepo(pool).get(principal.family_id, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await assert_member_access(pool, principal, document.family_member_id)
    if document.status != "completed" or not document.thread_id or not document.r2_object_key:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only completed uploads with originals can be reviewed",
        )
    try:
        await service.request_review(document.thread_id)
    except IngestionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "opening extraction review failed", extra={"document_id": str(document_id)},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not open extraction review. Refresh uploads and retry.",
        ) from exc
    updated = await DocumentRepo(pool).get(principal.family_id, document_id)
    assert updated is not None
    return _upload_status(updated)


@router.get("/pending-reviews", response_model=list[PendingReviewOut])
async def pending_reviews(principal: CurrentPrincipal, pool: Db) -> list[PendingReviewOut]:
    reviews = await PendingReviewRepo(pool).list_open(
        principal.family_id,
        family_member_id=None if principal.is_admin else principal.member_id,
    )
    return [PendingReviewOut.from_review(r) for r in reviews]


@router.post(
    "/resolve-review", response_model=ResolveReviewAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def resolve_review(
    body: ResolveReviewIn, principal: CurrentPrincipal, pool: Db, queue: Queue
) -> ResolveReviewAccepted:
    reviews = PendingReviewRepo(pool)
    review = await reviews.get_by_thread(principal.family_id, body.thread_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review not found")
    await assert_member_access(pool, principal, review.family_member_id)
    if review.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Review already resolved")

    corrected: UnifiedVaultExtraction | None = None
    if body.decision == "approve" and body.corrected_metadata is not None:
        if not await CategoryRepo(pool).exists(principal.family_id,
                                               body.corrected_metadata.category):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"Unknown category '{body.corrected_metadata.category}'")
        corrected = UnifiedVaultExtraction.model_validate(
            {**body.corrected_metadata.model_dump(), "confidence_score": 1.0}
        )
    try:
        resume = ReviewResume(decision=body.decision, corrected=corrected,
                              resolved_by=principal.member_id)
    except ValidationError as exc:
        message = "; ".join(str(e["msg"]) for e in exc.errors())
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from exc

    decision: ReviewDecision = "approved" if body.decision == "approve" else "rejected"
    await reviews.resolve(principal.family_id, body.thread_id, decision=decision,
                          resolved_by=principal.member_id)
    await queue.enqueue_resume(body.thread_id, resume.model_dump(mode="json"))
    return ResolveReviewAccepted(thread_id=body.thread_id, status=decision)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(principal: CurrentPrincipal, pool: Db) -> list[CategoryOut]:
    return [CategoryOut.from_category(c)
            for c in await CategoryRepo(pool).list_all(principal.family_id)]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(body: CategoryIn, principal: AdminPrincipal, pool: Db) -> CategoryOut:
    try:
        category = await CategoryRepo(pool).create_custom(
            principal.family_id, slug=body.category_slug, display_name=body.category_display_name
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return CategoryOut.from_category(category)

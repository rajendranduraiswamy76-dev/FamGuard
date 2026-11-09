"""Tenant-scoped repositories. Every query that reads family data is filtered by family_id."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

from app.database import BASELINE_CATEGORIES, Executor

Role = Literal["admin", "member", "dependent"]
DocumentStatus = Literal["processing", "review_required", "completed", "failed", "rejected"]
DocumentSource = Literal["upload", "bulk", "chat"]
ReviewStatus = Literal["open", "approved", "rejected"]
ReviewDecision = Literal["approved", "rejected"]
AlertOffset = Literal[30, 7, 1]

BASELINE_SLUGS = frozenset(slug for slug, _ in BASELINE_CATEGORIES)


class RepositoryError(Exception):
    """Base class for repository errors."""


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


class CategoryAlreadyExistsError(ConflictError):
    pass


class DuplicateDocumentError(ConflictError):
    def __init__(self, existing_document_id: UUID) -> None:
        super().__init__(f"Document already exists: {existing_document_id}")
        self.existing_document_id = existing_document_id


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Self:
        return cls.model_validate(dict(record))


class Family(_Row):
    id: UUID
    name: str
    timezone: str
    currency: str
    created_at: datetime


class FamilyMember(_Row):
    id: UUID
    family_id: UUID
    clerk_user_id: str | None
    display_name: str
    email: str | None
    role: Role
    relationship: Literal["spouse", "child", "others"] | None = None
    created_at: datetime


class VaultCategory(_Row):
    id: UUID
    family_id: UUID | None
    category_slug: str
    category_display_name: str
    is_baseline: bool
    created_at: datetime


class Document(_Row):
    id: UUID
    family_id: UUID
    family_member_id: UUID
    thread_id: str | None
    category_slug: str
    requested_category: str | None = None
    document_details: str = ""
    index_version: str | None = None
    currency: str | None = None
    financial_subcategory: str | None = None
    document_name: str
    original_filename: str | None
    r2_object_key: str | None
    sha256: str | None
    expiration_date: date | None
    requires_alert: bool
    financial_value: Decimal | None
    chunk_count: int
    source: DocumentSource
    status: DocumentStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BulkDocumentRow:
    document_id: UUID
    family_member_id: UUID
    category_slug: str
    document_name: str
    expiration_date: date | None
    requires_alert: bool
    financial_value: Decimal | float | None
    chunk_count: int
    currency: str | None = None
    financial_subcategory: str | None = None
    document_details: str = ""
    transaction_date: date | None = None
    payment_due_date: date | None = None
    is_paid: bool = True


class PendingReview(_Row):
    id: UUID
    thread_id: str
    family_id: UUID
    family_member_id: UUID
    document_id: UUID | None
    original_filename: str
    raw_text: str
    suggested_metadata: dict[str, Any]
    confidence_score: float
    review_reasons: list[str]
    status: ReviewStatus
    resolved_by: UUID | None
    resolved_at: datetime | None
    created_at: datetime


class FamilyRepo:
    def __init__(self, db: Executor) -> None:
        self._db = db

    async def create(self, name: str, *, timezone: str = "UTC", currency: str = "USD") -> Family:
        row = await self._db.fetchrow(
            """INSERT INTO families (name, timezone, currency)
               VALUES ($1, $2, $3) RETURNING *""",
            name, timezone, currency.upper(),
        )
        assert row is not None
        return Family.from_record(row)

    async def get(self, family_id: UUID) -> Family | None:
        row = await self._db.fetchrow("SELECT * FROM families WHERE id = $1", family_id)
        return Family.from_record(row) if row else None


class MemberRepo:
    def __init__(self, db: Executor) -> None:
        self._db = db

    async def create(
        self,
        family_id: UUID,
        *,
        display_name: str,
        role: Role,
        email: str | None = None,
        clerk_user_id: str | None = None,
        relationship: Literal["spouse", "child", "others"] | None = None,
    ) -> FamilyMember:
        try:
            row = await self._db.fetchrow(
                """INSERT INTO family_members
                   (family_id, display_name, role, email, clerk_user_id, relationship)
                   VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
                family_id, display_name, role, _normalize_email(email), clerk_user_id, relationship,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictError("Member name or Clerk user already exists") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise NotFoundError("Family not found") from exc
        assert row is not None
        return FamilyMember.from_record(row)

    async def get(self, family_id: UUID, member_id: UUID) -> FamilyMember | None:
        row = await self._db.fetchrow(
            "SELECT * FROM family_members WHERE family_id = $1 AND id = $2", family_id, member_id
        )
        return FamilyMember.from_record(row) if row else None

    async def get_by_clerk_user_id(self, clerk_user_id: str) -> FamilyMember | None:
        # Identity lookup used by authentication; the result establishes the tenant.
        row = await self._db.fetchrow(
            "SELECT * FROM family_members WHERE clerk_user_id = $1", clerk_user_id
        )
        return FamilyMember.from_record(row) if row else None

    async def claim_by_email(self, email: str, clerk_user_id: str) -> FamilyMember | None:
        """Link a login to the oldest unclaimed admin/member invite with this email."""
        row = await self._db.fetchrow(
            """UPDATE family_members SET clerk_user_id = $2
               WHERE id = (
                   SELECT id FROM family_members
                   WHERE email = $1 AND clerk_user_id IS NULL AND role IN ('admin', 'member')
                   ORDER BY created_at LIMIT 1
                   FOR UPDATE SKIP LOCKED)
               AND clerk_user_id IS NULL
               RETURNING *""",
            _normalize_email(email), clerk_user_id,
        )
        return FamilyMember.from_record(row) if row else None

    async def list_all(self, family_id: UUID) -> list[FamilyMember]:
        rows = await self._db.fetch(
            "SELECT * FROM family_members WHERE family_id = $1 ORDER BY display_name", family_id
        )
        return [FamilyMember.from_record(r) for r in rows]

    async def list_admins(self, family_id: UUID) -> list[FamilyMember]:
        rows = await self._db.fetch(
            """SELECT * FROM family_members
               WHERE family_id = $1 AND role = 'admin' ORDER BY created_at""",
            family_id,
        )
        return [FamilyMember.from_record(r) for r in rows]

    async def update(
        self, family_id: UUID, member_id: UUID, *, display_name: str, role: Role,
        email: str | None,
        relationship: Literal["spouse", "child", "others"] | None = None,
    ) -> FamilyMember:
        row = await self._db.fetchrow(
            "SELECT * FROM family_members WHERE family_id = $1 AND id = $2 FOR UPDATE",
            family_id, member_id,
        )
        if row is None:
            raise NotFoundError("Family member not found")
        if row["clerk_user_id"] is not None and role == "dependent":
            raise ConflictError("A member with a linked login cannot become a dependent")
        try:
            updated = await self._db.fetchrow(
                """UPDATE family_members SET display_name = $3, role = $4, email = $5,
                   relationship = $6
                   WHERE family_id = $1 AND id = $2 RETURNING *""",
                family_id, member_id, display_name, role, _normalize_email(email), relationship,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictError("Member name already exists in this family") from exc
        assert updated is not None
        return FamilyMember.from_record(updated)

    async def delete(self, family_id: UUID, member_id: UUID) -> None:
        # Hold this lock in the caller's transaction so concurrent FK inserts cannot
        # slip between the ownership check and a cascading delete.
        row = await self._db.fetchrow(
            "SELECT id FROM family_members WHERE family_id = $1 AND id = $2 FOR UPDATE",
            family_id, member_id,
        )
        if row is None:
            raise NotFoundError("Family member not found")
        has_records = await self._db.fetchval(
            """SELECT EXISTS (
                   SELECT 1 FROM documents WHERE family_id = $1 AND family_member_id = $2
               ) OR EXISTS (
                   SELECT 1 FROM pending_reviews
                   WHERE family_id = $1 AND family_member_id = $2
               )""",
            family_id, member_id,
        )
        if has_records:
            raise ConflictError("Cannot delete a member who owns documents or review records")
        await self._db.execute(
            "DELETE FROM family_members WHERE family_id = $1 AND id = $2",
            family_id, member_id,
        )


class CategoryRepo:
    def __init__(self, db: Executor) -> None:
        self._db = db

    async def list_all(self, family_id: UUID) -> list[VaultCategory]:
        rows = await self._db.fetch(
            """SELECT * FROM vault_categories
               WHERE family_id IS NULL OR family_id = $1
               ORDER BY is_baseline DESC, category_slug""",
            family_id,
        )
        return [VaultCategory.from_record(r) for r in rows]

    async def list_slugs(self, family_id: UUID) -> list[str]:
        return [c.category_slug for c in await self.list_all(family_id)]

    async def exists(self, family_id: UUID, slug: str) -> bool:
        return bool(
            await self._db.fetchval(
                """SELECT EXISTS (
                       SELECT 1 FROM vault_categories
                       WHERE category_slug = $2 AND (family_id IS NULL OR family_id = $1))""",
                family_id, slug,
            )
        )

    async def create_custom(
        self, family_id: UUID, *, slug: str, display_name: str
    ) -> VaultCategory:
        if slug in BASELINE_SLUGS:
            raise CategoryAlreadyExistsError(f"'{slug}' is a baseline category")
        try:
            row = await self._db.fetchrow(
                """INSERT INTO vault_categories (family_id, category_slug, category_display_name)
                   SELECT $1, $2, $3
                   WHERE NOT EXISTS (
                       SELECT 1 FROM vault_categories
                       WHERE category_slug = $2 AND (family_id IS NULL OR family_id = $1))
                   RETURNING *""",
                family_id, slug, display_name,
            )
        except asyncpg.UniqueViolationError as exc:
            raise CategoryAlreadyExistsError(f"Category '{slug}' already exists") from exc
        except asyncpg.CheckViolationError as exc:
            raise ValueError(f"Invalid category slug or display name: '{slug}'") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise NotFoundError("Family not found") from exc
        if row is None:
            raise CategoryAlreadyExistsError(f"Category '{slug}' already exists")
        return VaultCategory.from_record(row)


class DocumentRepo:
    def __init__(self, db: Executor) -> None:
        self._db = db

    async def create(
        self,
        family_id: UUID,
        *,
        family_member_id: UUID,
        category_slug: str,
        document_name: str,
        source: DocumentSource,
        status: DocumentStatus = "processing",
        thread_id: str | None = None,
        original_filename: str | None = None,
        r2_object_key: str | None = None,
        sha256: str | None = None,
        expiration_date: date | None = None,
        requires_alert: bool = False,
        financial_value: Decimal | float | None = None,
        chunk_count: int = 0,
        document_id: UUID | None = None,
        requested_category: str | None = None,
        document_details: str = "",
    ) -> Document:
        try:
            row = await self._db.fetchrow(
                """INSERT INTO documents (
                       id, family_id, family_member_id, thread_id, category_slug, document_name,
                       original_filename, r2_object_key, sha256, expiration_date, requires_alert,
                       financial_value, chunk_count, source, status, requested_category,
                       document_details)
                   VALUES (COALESCE($15, gen_random_uuid()),
                           $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$16,$17)
                   RETURNING *""",
                family_id, family_member_id, thread_id, category_slug, document_name,
                original_filename, r2_object_key, sha256, expiration_date, requires_alert,
                _to_decimal(financial_value), chunk_count, source, status, document_id,
                requested_category, document_details,
            )
        except asyncpg.UniqueViolationError as exc:
            if sha256 is not None:
                existing = await self.find_by_sha256(family_id, sha256)
                if existing is not None:
                    raise DuplicateDocumentError(existing.id) from exc
            raise ConflictError("Document already exists") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise NotFoundError("Family member not found in this family") from exc
        assert row is not None
        return Document.from_record(row)

    async def create_completed_bulk(
        self, family_id: UUID, rows: Sequence[BulkDocumentRow]
    ) -> int:
        """Insert completed bulk-imported documents in a single round trip."""
        if not rows:
            return 0
        try:
            result = await self._db.execute(
                """INSERT INTO documents (
                       id, family_id, family_member_id, category_slug, document_name,
                       expiration_date, requires_alert, financial_value, chunk_count,
                       source, status, currency, financial_subcategory, document_details,
                       transaction_date, payment_due_date, is_paid)
                   SELECT r.id, $1, r.member_id, r.category, r.name, r.expires, r.alert,
                          r.value, r.chunks, 'bulk', 'completed',
                          r.currency, r.subcategory, r.details, r.transacted, r.due, r.paid
                   FROM unnest($2::uuid[], $3::uuid[], $4::text[], $5::text[], $6::date[],
                               $7::bool[], $8::numeric[], $9::int[], $10::text[], $11::text[],
                               $12::text[], $13::date[], $14::date[], $15::bool[])
                        AS r(id, member_id, category, name, expires, alert, value, chunks,
                             currency, subcategory, details, transacted, due, paid)""",
                family_id,
                [r.document_id for r in rows],
                [r.family_member_id for r in rows],
                [r.category_slug for r in rows],
                [r.document_name for r in rows],
                [r.expiration_date for r in rows],
                [r.requires_alert for r in rows],
                [_to_decimal(r.financial_value) for r in rows],
                [r.chunk_count for r in rows],
                [r.currency for r in rows],
                [r.financial_subcategory for r in rows],
                [r.document_details for r in rows],
                [r.transaction_date for r in rows],
                [r.payment_due_date for r in rows],
                [r.is_paid for r in rows],
            )
        except asyncpg.ForeignKeyViolationError as exc:
            raise NotFoundError("Family member not found in this family") from exc
        return int(result.split()[-1])

    async def get(self, family_id: UUID, document_id: UUID) -> Document | None:
        row = await self._db.fetchrow(
            "SELECT * FROM documents WHERE family_id = $1 AND id = $2", family_id, document_id
        )
        return Document.from_record(row) if row else None

    async def get_by_thread(self, family_id: UUID, thread_id: str) -> Document | None:
        row = await self._db.fetchrow(
            "SELECT * FROM documents WHERE family_id = $1 AND thread_id = $2", family_id, thread_id
        )
        return Document.from_record(row) if row else None

    async def recent_uploads(
        self, family_id: UUID, *, family_member_id: UUID | None = None, limit: int = 50,
        category: str | None = None, offset: int = 0,
    ) -> list[Document]:
        rows = await self._db.fetch(
            """SELECT * FROM documents WHERE family_id = $1 AND thread_id IS NOT NULL
               AND ($2::uuid IS NULL OR family_member_id = $2)
               AND ($4::text IS NULL OR category_slug = $4)
               ORDER BY created_at DESC, id DESC LIMIT $3 OFFSET $5""",
            family_id, family_member_id, limit, category, offset,
        )
        return [Document.from_record(row) for row in rows]

    async def get_by_thread_system(self, thread_id: str) -> Document | None:
        """Background-job lookup; the returned row establishes the tenant for the job."""
        row = await self._db.fetchrow("SELECT * FROM documents WHERE thread_id = $1", thread_id)
        return Document.from_record(row) if row else None

    async def find_by_sha256(self, family_id: UUID, sha256: str) -> Document | None:
        row = await self._db.fetchrow(
            "SELECT * FROM documents WHERE family_id = $1 AND sha256 = $2", family_id, sha256
        )
        return Document.from_record(row) if row else None

    async def update_status(
        self,
        family_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        *,
        error: str | None = None,
    ) -> Document:
        row = await self._db.fetchrow(
            """UPDATE documents SET status = $3, error = $4, updated_at = now()
               WHERE family_id = $1 AND id = $2 RETURNING *""",
            family_id, document_id, status, error,
        )
        if row is None:
            raise NotFoundError("Document not found")
        return Document.from_record(row)

    async def mark_completed(
        self,
        family_id: UUID,
        document_id: UUID,
        *,
        category_slug: str,
        document_name: str,
        expiration_date: date | None,
        requires_alert: bool,
        financial_value: Decimal | float | None,
        chunk_count: int,
    ) -> Document:
        row = await self._db.fetchrow(
            """UPDATE documents
               SET category_slug = $3, document_name = $4, expiration_date = $5,
                   requires_alert = $6, financial_value = $7, chunk_count = $8,
                   status = 'completed', error = NULL, updated_at = now()
               WHERE family_id = $1 AND id = $2 RETURNING *""",
            family_id, document_id, category_slug, document_name, expiration_date,
            requires_alert, _to_decimal(financial_value), chunk_count,
        )
        if row is None:
            raise NotFoundError("Document not found")
        return Document.from_record(row)

    async def delete(self, family_id: UUID, document_id: UUID) -> bool:
        result = await self._db.execute(
            "DELETE FROM documents WHERE family_id = $1 AND id = $2", family_id, document_id
        )
        return result.endswith(" 1")

    async def list_alertable_expiring_on_all_families(self, target: date) -> list[Document]:
        """System-level query for the expiry sweep reconciliation (not user-facing)."""
        rows = await self._db.fetch(
            """SELECT * FROM documents
               WHERE requires_alert AND status = 'completed' AND expiration_date = $1
               ORDER BY family_id, id""",
            target,
        )
        return [Document.from_record(r) for r in rows]

    async def get_many_system(self, document_ids: Sequence[UUID]) -> list[Document]:
        """System-level bulk lookup for background jobs; rows carry their own family_id."""
        if not document_ids:
            return []
        rows = await self._db.fetch(
            "SELECT * FROM documents WHERE id = ANY($1::uuid[])", list(document_ids)
        )
        return [Document.from_record(r) for r in rows]

    async def searchable_documents(
        self, family_id: UUID, document_ids: Sequence[UUID], *,
        family_member_id: UUID | None = None, category: str | None = None,
    ) -> list[Document]:
        if not document_ids:
            return []
        rows = await self._db.fetch(
            """SELECT * FROM documents WHERE family_id = $1 AND id = ANY($2::uuid[])
               AND status = 'completed'
               AND ($3::uuid IS NULL OR family_member_id = $3)
               AND ($4::text IS NULL OR category_slug = $4)""",
            family_id, list(document_ids), family_member_id, category,
        )
        return [Document.from_record(row) for row in rows]

    async def category_counts(
        self, family_id: UUID, *, family_member_id: UUID | None = None
    ) -> list[tuple[str, int, int]]:
        """(category_slug, completed documents, vectors) per category."""
        rows = await self._db.fetch(
            """SELECT category_slug, count(*) AS documents, COALESCE(sum(chunk_count), 0) AS vectors
               FROM documents
               WHERE family_id = $1 AND status = 'completed'
                 AND ($2::uuid IS NULL OR family_member_id = $2)
               GROUP BY category_slug ORDER BY category_slug""",
            family_id, family_member_id,
        )
        return [(r["category_slug"], int(r["documents"]), int(r["vectors"])) for r in rows]

    async def status_counts(
        self, family_id: UUID, *, family_member_id: UUID | None = None
    ) -> dict[str, int]:
        rows = await self._db.fetch(
            """SELECT status, count(*) AS n FROM documents
               WHERE family_id = $1 AND ($2::uuid IS NULL OR family_member_id = $2)
               GROUP BY status""",
            family_id, family_member_id,
        )
        return {r["status"]: int(r["n"]) for r in rows}

    async def upcoming_expirations(
        self,
        family_id: UUID,
        *,
        start: date,
        end: date,
        family_member_id: UUID | None = None,
        limit: int | None = 50,
    ) -> list[tuple[Document, str]]:
        """Alertable completed documents expiring in [start, end], with the owner's name."""
        rows = await self._db.fetch(
            """SELECT d.*, m.display_name AS member_name
               FROM documents d JOIN family_members m ON m.id = d.family_member_id
               WHERE d.family_id = $1 AND d.status = 'completed' AND d.requires_alert
                 AND d.expiration_date BETWEEN $2 AND $3
                 AND ($4::uuid IS NULL OR d.family_member_id = $4)
               ORDER BY d.expiration_date, d.document_name
               LIMIT $5""",
            family_id, start, end, family_member_id, limit,
        )
        result = []
        for r in rows:
            values = dict(r)
            member_name = str(values.pop("member_name"))
            result.append((Document.model_validate(values), member_name))
        return result


class PendingReviewRepo:
    def __init__(self, db: Executor) -> None:
        self._db = db

    async def create(
        self,
        family_id: UUID,
        *,
        thread_id: str,
        family_member_id: UUID,
        original_filename: str,
        raw_text: str,
        suggested_metadata: dict[str, Any],
        confidence_score: float,
        review_reasons: list[str],
        document_id: UUID | None = None,
    ) -> PendingReview:
        try:
            row = await self._db.fetchrow(
                """INSERT INTO pending_reviews (
                       thread_id, family_id, family_member_id, document_id, original_filename,
                       raw_text, suggested_metadata, confidence_score, review_reasons)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
                thread_id, family_id, family_member_id, document_id, original_filename,
                raw_text, suggested_metadata, confidence_score, review_reasons,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictError(f"Review already exists for thread {thread_id}") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise NotFoundError("Family member or document not found in this family") from exc
        assert row is not None
        return PendingReview.from_record(row)

    async def list_open(
        self, family_id: UUID, *, family_member_id: UUID | None = None
    ) -> list[PendingReview]:
        rows = await self._db.fetch(
            """SELECT * FROM pending_reviews
               WHERE family_id = $1 AND status = 'open'
                 AND ($2::uuid IS NULL OR family_member_id = $2)
               ORDER BY created_at""",
            family_id, family_member_id,
        )
        return [PendingReview.from_record(r) for r in rows]

    async def count_open(self, family_id: UUID, *, family_member_id: UUID | None = None) -> int:
        return int(
            await self._db.fetchval(
                """SELECT count(*) FROM pending_reviews
                   WHERE family_id = $1 AND status = 'open'
                     AND ($2::uuid IS NULL OR family_member_id = $2)""",
                family_id, family_member_id,
            )
        )

    async def get_by_thread(self, family_id: UUID, thread_id: str) -> PendingReview | None:
        row = await self._db.fetchrow(
            "SELECT * FROM pending_reviews WHERE family_id = $1 AND thread_id = $2",
            family_id, thread_id,
        )
        return PendingReview.from_record(row) if row else None

    async def resolve(
        self,
        family_id: UUID,
        thread_id: str,
        *,
        decision: ReviewDecision,
        resolved_by: UUID,
    ) -> PendingReview:
        row = await self._db.fetchrow(
            """UPDATE pending_reviews
               SET status = $3, resolved_by = $4, resolved_at = now()
               WHERE family_id = $1 AND thread_id = $2 AND status = 'open'
               RETURNING *""",
            family_id, thread_id, decision, resolved_by,
        )
        if row is not None:
            return PendingReview.from_record(row)
        if await self.get_by_thread(family_id, thread_id) is None:
            raise NotFoundError("Review not found")
        raise ConflictError("Review already resolved")


class AlertLogRepo:
    def __init__(self, db: Executor) -> None:
        self._db = db

    async def was_sent(
        self, family_id: UUID, document_id: UUID, days_before: AlertOffset, recipient: str
    ) -> bool:
        return bool(
            await self._db.fetchval(
                """SELECT EXISTS (
                       SELECT 1 FROM alert_log a JOIN documents d ON d.id = a.document_id
                       WHERE d.family_id = $1 AND a.document_id = $2
                         AND a.days_before = $3 AND a.recipient = $4)""",
                family_id, document_id, days_before, recipient.lower(),
            )
        )

    async def record(
        self,
        family_id: UUID,
        document_id: UUID,
        days_before: AlertOffset,
        recipient: str,
        *,
        resend_id: str | None = None,
    ) -> bool:
        """Returns True if newly recorded, False if already present or document not in family."""
        row = await self._db.fetchrow(
            """INSERT INTO alert_log (document_id, days_before, recipient, resend_id)
               SELECT d.id, $3, $4, $5 FROM documents d WHERE d.id = $2 AND d.family_id = $1
               ON CONFLICT (document_id, days_before, recipient) DO NOTHING
               RETURNING id""",
            family_id, document_id, days_before, recipient.lower(), resend_id,
        )
        return row is not None


def _normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    email = email.strip().lower()
    return email or None


def _to_decimal(value: Decimal | float | None) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))

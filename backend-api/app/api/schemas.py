from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.database import CATEGORY_SLUG_PATTERN
from app.repositories import (
    DocumentStatus,
    Family,
    FamilyMember,
    PendingReview,
    Role,
    VaultCategory,
)


class MemberOut(BaseModel):
    id: UUID
    display_name: str
    email: str | None
    role: Role
    relationship: Literal["spouse", "child", "others"] | None = None
    has_login: bool
    created_at: datetime

    @classmethod
    def from_member(cls, m: FamilyMember) -> MemberOut:
        return cls(
            id=m.id, display_name=m.display_name, email=m.email, role=m.role,
            has_login=m.clerk_user_id is not None, created_at=m.created_at,
            relationship=m.relationship,
        )


class FamilyOut(BaseModel):
    id: UUID
    name: str
    timezone: str
    currency: str

    @classmethod
    def from_family(cls, f: Family) -> FamilyOut:
        return cls(id=f.id, name=f.name, timezone=f.timezone, currency=f.currency)


class MeOut(BaseModel):
    member: MemberOut
    family: FamilyOut
    members: list[MemberOut]


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    role: Literal["admin", "member", "dependent"] = "dependent"
    relationship: Literal["spouse", "child", "others"] | None = None

    @field_validator("display_name")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(ord(c) < 32 for c in value):
            raise ValueError("display_name contains control characters")
        return value


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

UploadSource = Literal["upload", "chat"]


class UploadItem(BaseModel):
    thread_id: str
    document_id: UUID
    filename: str


class UploadAccepted(BaseModel):
    uploads: list[UploadItem]


class UploadStatusOut(BaseModel):
    thread_id: str
    document_id: UUID
    family_member_id: UUID
    status: DocumentStatus
    filename: str | None
    category: str
    document_name: str
    document_details: str = ""
    expiration_date: date | None
    requires_alert: bool
    pending_review_id: UUID | None = None
    error: str | None = None


class PendingReviewOut(BaseModel):
    id: UUID
    thread_id: str
    family_member_id: UUID
    document_id: UUID | None
    original_filename: str
    raw_text_preview: str
    suggested_metadata: dict[str, Any]
    confidence_score: float
    review_reasons: list[str]
    created_at: datetime

    @classmethod
    def from_review(cls, r: PendingReview) -> PendingReviewOut:
        return cls(
            id=r.id, thread_id=r.thread_id, family_member_id=r.family_member_id,
            document_id=r.document_id, original_filename=r.original_filename,
            raw_text_preview=r.raw_text[:2000], suggested_metadata=r.suggested_metadata,
            confidence_score=r.confidence_score, review_reasons=r.review_reasons,
            created_at=r.created_at,
        )


class CorrectedMetadata(BaseModel):
    """Reviewer-confirmed values; mirrors UnifiedVaultExtraction without model confidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str = Field(min_length=1, max_length=48)
    document_name: str = Field(min_length=1, max_length=200)
    expiration_date: str | None = None
    requires_alert: bool
    extracted_text: str = Field(max_length=200_000)
    financial_value: float | None = None


class ResolveReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=100)
    decision: Literal["approve", "reject"]
    corrected_metadata: CorrectedMetadata | None = None


class ResolveReviewAccepted(BaseModel):
    thread_id: str
    status: Literal["approved", "rejected"]


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category_slug: str = Field(min_length=1, max_length=48, pattern=CATEGORY_SLUG_PATTERN)
    category_display_name: str = Field(min_length=1, max_length=80)


class CategoryOut(BaseModel):
    id: UUID
    category_slug: str
    category_display_name: str
    is_baseline: bool

    @classmethod
    def from_category(cls, c: VaultCategory) -> CategoryOut:
        return cls(id=c.id, category_slug=c.category_slug,
                   category_display_name=c.category_display_name, is_baseline=c.is_baseline)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=100)


class BulkRowError(BaseModel):
    row: int
    reason: str


class BulkImportOut(BaseModel):
    accepted: int
    rejected: int
    errors: list[BulkRowError]
    errors_truncated: bool


class CategoryMetric(BaseModel):
    category: str
    documents: int
    vectors: int


class UpcomingExpiration(BaseModel):
    document_id: UUID
    document_name: str
    member_name: str
    category: str
    expiration_date: date
    days_remaining: int


class MetricsOut(BaseModel):
    documents_total: int
    vectors_total: int
    by_category: list[CategoryMetric]
    upcoming_expirations: list[UpcomingExpiration]
    year_expirations: list[UpcomingExpiration] = Field(default_factory=list)
    open_reviews: int
    processing: int
    failed: int


class DocumentUrlOut(BaseModel):
    url: str
    expires_in: int

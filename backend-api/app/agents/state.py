"""Shared state schemas for the ingestion (HITL) and RAG graphs."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal, TypedDict
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    field_validator,
    model_validator,
)

REVIEW_CONFIDENCE_THRESHOLD = 0.75

ReviewReason = Literal[
    "expiration_unparsed",
    "expiration_missing",
    "low_confidence",
    "unknown_category",
    "user_requested",
]
IngestionStatus = Literal["processing", "review_required", "completed", "failed", "rejected"]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NULL_LIKE = frozenset({"", "null", "none", "n/a", "na", "unknown"})


def parse_iso_date(value: object) -> date | None:
    """Strict YYYY-MM-DD parsing; anything else returns None."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


class UnifiedVaultExtraction(BaseModel):
    """Structured output of the Vision extraction node."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(description="Lower-case category slug from the allowed list.")
    document_name: str = Field(min_length=1, max_length=200,
                               description="Short human-readable title, e.g. 'John Passport'.")
    expiration_date: date | None = Field(
        default=None, description="Expiry/renewal date as YYYY-MM-DD, or null if none."
    )
    requires_alert: bool = Field(
        description="True if the document expires or needs renewal and should trigger alerts."
    )
    extracted_text: str = Field(
        description="Full OCR transcription, or a rich semantic caption for photos."
    )
    financial_value: float | None = Field(
        default=None, description="Monetary amount (total, salary, premium) or null."
    )
    confidence_score: float = Field(
        ge=0, le=1, description="Self-assessed confidence (0-1) in the extracted fields."
    )

    _expiration_raw: str | None = PrivateAttr(default=None)

    @model_validator(mode="wrap")
    @classmethod
    def _lenient_expiration(
        cls, data: Any, handler: ModelWrapValidatorHandler[UnifiedVaultExtraction]
    ) -> UnifiedVaultExtraction:
        raw: str | None = None
        if isinstance(data, dict) and "expiration_date" in data:
            value = data["expiration_date"]
            if isinstance(value, str) and value.strip().lower() in _NULL_LIKE:
                data = {**data, "expiration_date": None}
            elif value is not None and parse_iso_date(value) is None:
                raw = str(value)[:64]
                data = {**data, "expiration_date": None}
        model = handler(data)
        if raw is not None:
            model._expiration_raw = raw
        return model

    @field_validator("category")
    @classmethod
    def _normalize_category(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def expiration_unparsed(self) -> bool:
        return self._expiration_raw is not None

    @property
    def expiration_raw(self) -> str | None:
        return self._expiration_raw


class HITLState(BaseModel):
    review_required: bool = False
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    pending_review_id: UUID | None = None
    reviewer_decision: Literal["approve", "reject"] | None = None
    corrected: UnifiedVaultExtraction | None = None


class ThreadMetrics(TypedDict, total=False):
    started_at: str
    extraction_ms: int
    embedding_ms: int
    prompt_tokens: int
    completion_tokens: int
    chunks_upserted: int


class IngestionState(TypedDict, total=False):
    """Checkpointed graph state; models are stored as JSON dicts for any checkpointer."""

    thread_id: str
    family_id: str
    family_member_id: str
    document_id: str
    original_filename: str
    mime_type: str
    r2_object_key: str
    source: Literal["upload", "bulk", "chat"]
    allowed_categories: list[str]
    requested_category: str | None
    document_details: str
    member_name: str
    extraction: dict[str, Any] | None
    expiration_raw: str | None
    hitl: dict[str, Any]
    metrics: ThreadMetrics
    status: IngestionStatus
    error: str | None


class ChatFilter(BaseModel):
    """What the UI shows as `Context Filtered: <member> | <category>`."""

    member_id: UUID | None = None
    member: str | None = None
    category: str | None = None


class SourceRef(BaseModel):
    document_id: str
    document_name: str
    category: str
    score: float


class RAGState(TypedDict, total=False):
    question: str
    family_id: str
    principal_member_id: str
    principal_role: str
    chat_filter: ChatFilter
    pinecone_filter: dict[str, Any]
    contexts: list[dict[str, Any]]
    sources: list[SourceRef]
    answer: str

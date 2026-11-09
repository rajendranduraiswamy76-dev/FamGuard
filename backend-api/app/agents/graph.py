"""LangGraph ingestion pipeline with human-in-the-loop review.

extract_metadata -> validate -> [human_review: approve | reject]
    -> embed_and_upsert | discard -> finalize
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, model_validator

from app.agents.extractor import MetadataExtractor
from app.agents.state import (
    REVIEW_CONFIDENCE_THRESHOLD,
    HITLState,
    IngestionState,
    ReviewReason,
    ThreadMetrics,
    UnifiedVaultExtraction,
)
from app.config import Settings
from app.embeddings import Embedder, chunk_text
from app.parsers import (
    FileParseError,
    UnsupportedFileTypeError,
    detect_file_kind,
    prepare_for_extraction,
)
from app.repositories import (
    CategoryRepo,
    ConflictError,
    DocumentRepo,
    MemberRepo,
    PendingReviewRepo,
)
from app.storage import ObjectNotFoundError, ObjectStorage
from app.vectorstore import VectorStore, build_records

logger = logging.getLogger(__name__)

FALLBACK_CATEGORY = "miscellaneous"

IngestionOutcomeStatus = Literal["completed", "review_required", "failed", "rejected"]


class IngestionStateError(Exception):
    """The thread is not in a state that allows the requested operation."""


class ReviewResume(BaseModel):
    """Payload used to resume a thread paused in `human_review`."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    corrected: UnifiedVaultExtraction | None = None
    resolved_by: UUID | None = None

    @model_validator(mode="after")
    def _corrected_is_complete(self) -> ReviewResume:
        c = self.corrected
        if self.decision == "approve" and c is not None:
            if c.expiration_unparsed:
                raise ValueError("corrected.expiration_date must be YYYY-MM-DD")
            if c.requires_alert and c.expiration_date is None:
                raise ValueError("corrected.expiration_date is required when requires_alert")
        return self


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    thread_id: str
    status: IngestionOutcomeStatus
    pending_review_id: str | None = None
    error: str | None = None


@dataclass
class IngestionDeps:
    pool: asyncpg.Pool
    storage: ObjectStorage
    extractor: MetadataExtractor
    embedder: Embedder
    vectors: VectorStore
    max_extraction_attempts: int = 3
    retry_base_delay_s: float = 1.0
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)


def evaluate_review(
    extraction: dict[str, Any], *, expiration_raw: str | None, allowed_categories: Sequence[str]
) -> tuple[dict[str, Any], list[ReviewReason]]:
    """Pure HITL routing rules. Returns the (possibly re-categorized) extraction and reasons."""
    reasons: list[ReviewReason] = []
    if extraction["requires_alert"] and extraction.get("expiration_date") is None:
        reasons.append("expiration_unparsed" if expiration_raw else "expiration_missing")
    if float(extraction["confidence_score"]) < REVIEW_CONFIDENCE_THRESHOLD:
        reasons.append("low_confidence")
    if extraction["category"] not in allowed_categories:
        reasons.append("unknown_category")
        extraction = {**extraction, "category": FALLBACK_CATEGORY}
    return extraction, reasons


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def build_ingestion_graph(
    deps: IngestionDeps, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[Any, Any, Any, Any]:
    async def _fail(state: IngestionState, message: str) -> dict[str, Any]:
        logger.warning(
            "ingestion failed", extra={"thread_id": state["thread_id"], "error": message}
        )
        await DocumentRepo(deps.pool).update_status(
            UUID(state["family_id"]), UUID(state["document_id"]), "failed", error=message[:500]
        )
        return {"status": "failed", "error": message}

    async def extract_metadata(state: IngestionState) -> dict[str, Any]:
        try:
            data = await deps.storage.get(state["r2_object_key"])
            kind = detect_file_kind(data)
            prepared = await asyncio.to_thread(prepare_for_extraction, data, kind)
        except (ObjectNotFoundError, UnsupportedFileTypeError, FileParseError) as exc:
            return await _fail(state, f"{type(exc).__name__}: {exc}")

        started = time.perf_counter()
        attempts = deps.max_extraction_attempts
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = await deps.extractor.extract(
                    prepared,
                    categories=state["allowed_categories"],
                    filename=state["original_filename"],
                )
                break
            except Exception as exc:  # model, network and validation errors are all retryable
                last_error = exc
                logger.warning(
                    "extraction attempt failed",
                    extra={"thread_id": state["thread_id"], "attempt": attempt,
                           "error_type": type(exc).__name__},
                )
                if attempt < attempts:
                    await deps.sleep(deps.retry_base_delay_s * 2 ** (attempt - 1))
        else:
            return await _fail(
                state,
                f"Extraction failed after {attempts} attempts: {type(last_error).__name__}",
            )

        metrics: ThreadMetrics = {
            **state.get("metrics", {}),
            "extraction_ms": _ms_since(started),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
        }
        return {
            "extraction": result.extraction.model_dump(mode="json"),
            "expiration_raw": result.expiration_raw,
            "mime_type": kind.mime_type,
            "metrics": metrics,
        }

    async def validate(state: IngestionState) -> dict[str, Any]:
        assert state.get("extraction") is not None
        extracted = state["extraction"] or {}
        if state.get("requested_category"):
            extracted = {**extracted, "category": state["requested_category"]}
        extraction, reasons = evaluate_review(
            extracted,
            expiration_raw=state.get("expiration_raw"),
            allowed_categories=state["allowed_categories"],
        )
        hitl = HITLState(review_required=bool(reasons), review_reasons=reasons)
        return {"extraction": extraction, "hitl": hitl.model_dump(mode="json")}

    async def human_review(state: IngestionState) -> dict[str, Any]:
        # Runs again from the top on resume, so every write here must be idempotent.
        family_id = UUID(state["family_id"])
        extraction = state["extraction"] or {}
        hitl = HITLState.model_validate(state["hitl"])
        reviews = PendingReviewRepo(deps.pool)
        review = await reviews.get_by_thread(family_id, state["thread_id"])
        if review is None:
            try:
                review = await reviews.create(
                    family_id,
                    thread_id=state["thread_id"],
                    family_member_id=UUID(state["family_member_id"]),
                    document_id=UUID(state["document_id"]),
                    original_filename=state["original_filename"],
                    raw_text=str(extraction.get("extracted_text", "")),
                    suggested_metadata=extraction,
                    confidence_score=float(extraction.get("confidence_score", 0.0)),
                    review_reasons=list(hitl.review_reasons),
                )
            except ConflictError:
                review = await reviews.get_by_thread(family_id, state["thread_id"])
        assert review is not None
        await DocumentRepo(deps.pool).update_status(
            family_id, UUID(state["document_id"]), "review_required"
        )

        payload = interrupt(
            {
                "thread_id": state["thread_id"],
                "pending_review_id": str(review.id),
                "review_reasons": list(hitl.review_reasons),
                "suggested_metadata": extraction,
            }
        )
        resume = ReviewResume.model_validate(payload)
        if resume.corrected is not None and (
            resume.corrected.category not in state["allowed_categories"]
        ):
            raise ValueError(f"Unknown category '{resume.corrected.category}'")

        hitl.pending_review_id = review.id
        hitl.reviewer_decision = resume.decision
        hitl.corrected = resume.corrected
        update: dict[str, Any] = {"hitl": hitl.model_dump(mode="json")}
        if resume.decision == "approve" and resume.corrected is not None:
            update["extraction"] = resume.corrected.model_dump(mode="json")
        return update

    async def embed_and_upsert(state: IngestionState) -> dict[str, Any]:
        extraction = UnifiedVaultExtraction.model_validate(state["extraction"])
        details = state.get("document_details", "")
        member_name = state.get("member_name", "")
        context = (
            f"Document owner: {member_name}\nCategory: {extraction.category}\n"
            f"Document name: {extraction.document_name}\n"
        )
        if details:
            context += f"Uploader-provided document details (not extracted):\n{details}\n"
        chunks = chunk_text(context + "\nExtracted document content:\n" + extraction.extracted_text)
        started = time.perf_counter()
        vectors = None if deps.vectors.integrated_embeddings else await deps.embedder.embed(chunks)
        records = build_records(
            family_id=state["family_id"],
            family_member_id=state["family_member_id"],
            document_id=state["document_id"],
            extraction=extraction,
            chunks=chunks,
            vectors=vectors,
            source=state.get("source", "upload"),
            document_details=details,
            member_name=member_name,
            index_version=state["thread_id"],
        )
        # Clear chunks from any earlier partial run so stale ids cannot linger.
        await deps.vectors.delete_document(state["document_id"])
        await deps.vectors.upsert(records)
        await DocumentRepo(deps.pool).mark_completed(
            UUID(state["family_id"]),
            UUID(state["document_id"]),
            category_slug=extraction.category,
            document_name=extraction.document_name,
            expiration_date=extraction.expiration_date,
            requires_alert=extraction.requires_alert,
            financial_value=extraction.financial_value,
            chunk_count=len(records),
        )
        metrics: ThreadMetrics = {
            **state.get("metrics", {}),
            "embedding_ms": _ms_since(started),
            "chunks_upserted": len(records),
        }
        return {"status": "completed", "metrics": metrics, "error": None}

    async def discard(state: IngestionState) -> dict[str, Any]:
        await deps.storage.delete(state["r2_object_key"])
        await DocumentRepo(deps.pool).update_status(
            UUID(state["family_id"]), UUID(state["document_id"]), "rejected"
        )
        return {"status": "rejected"}

    async def finalize(state: IngestionState) -> dict[str, Any]:
        logger.info(
            "ingestion finished",
            extra={"thread_id": state["thread_id"], "status": state.get("status"),
                   "metrics": state.get("metrics", {})},
        )
        return {}

    def route_after_extract(state: IngestionState) -> str:
        return "finalize" if state.get("status") == "failed" else "validate"

    def route_after_validate(state: IngestionState) -> str:
        return "human_review" if state["hitl"]["review_required"] else "embed_and_upsert"

    def route_after_review(state: IngestionState) -> str:
        return "embed_and_upsert" if state["hitl"]["reviewer_decision"] == "approve" else "discard"

    graph: StateGraph[Any, Any, Any, Any] = StateGraph(IngestionState)
    graph.add_node("extract_metadata", extract_metadata)
    graph.add_node("validate", validate)
    graph.add_node("human_review", human_review)
    graph.add_node("embed_and_upsert", embed_and_upsert)
    graph.add_node("discard", discard)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "extract_metadata")
    graph.add_conditional_edges(
        "extract_metadata", route_after_extract, ["validate", "finalize"]
    )
    graph.add_conditional_edges(
        "validate", route_after_validate, ["human_review", "embed_and_upsert"]
    )
    graph.add_conditional_edges(
        "human_review", route_after_review, ["embed_and_upsert", "discard"]
    )
    graph.add_edge("embed_and_upsert", "finalize")
    graph.add_edge("discard", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


class IngestionService:
    """Entry points used by the API and the Celery worker."""

    def __init__(self, deps: IngestionDeps, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self._deps = deps
        self.graph = build_ingestion_graph(deps, checkpointer)

    @staticmethod
    def _config(thread_id: str) -> Any:
        return {"configurable": {"thread_id": thread_id}}

    async def run(self, thread_id: str) -> IngestionOutcome:
        """Start a thread, continue it after a crash, or report its settled status."""
        config = self._config(thread_id)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            if snapshot.interrupts:
                return self._outcome(thread_id, snapshot)
            if snapshot.next:
                logger.info("continuing interrupted ingestion", extra={"thread_id": thread_id})
                await self.graph.ainvoke(None, config)
            return self._outcome(thread_id, await self.graph.aget_state(config))

        # Guards against duplicate deliveries when the checkpoint is gone but work is settled.
        document = await DocumentRepo(self._deps.pool).get_by_thread_system(thread_id)
        if document is not None and document.status in ("completed", "rejected"):
            return IngestionOutcome(thread_id=thread_id, status=document.status)

        initial = await self._initial_state(thread_id)
        await self.graph.ainvoke(initial, config)
        return self._outcome(thread_id, await self.graph.aget_state(config))

    async def resume(self, thread_id: str, payload: ReviewResume) -> IngestionOutcome:
        config = self._config(thread_id)
        snapshot = await self.graph.aget_state(config)
        if not snapshot.interrupts and snapshot.next == ("human_review",):
            await self.graph.ainvoke(None, config)
            snapshot = await self.graph.aget_state(config)
        if not snapshot.interrupts or "human_review" not in snapshot.next:
            raise IngestionStateError(f"Thread {thread_id} is not awaiting review")
        await self.graph.ainvoke(
            Command(resume=payload.model_dump(mode="json")), config
        )
        return self._outcome(thread_id, await self.graph.aget_state(config))

    async def request_review(
        self, thread_id: str, *, suggested: UnifiedVaultExtraction | None = None,
    ) -> IngestionOutcome:
        """Reopen saved extraction without re-running OCR; retain the old thread for history."""
        document = await DocumentRepo(self._deps.pool).get_by_thread_system(thread_id)
        if document is None or document.status != "completed" or not document.r2_object_key:
            raise IngestionStateError("Only completed uploads with originals can be reviewed")
        snapshot = await self.graph.aget_state(self._config(thread_id))
        if snapshot.next or not snapshot.values.get("extraction"):
            raise IngestionStateError("Saved extraction is unavailable or still processing")
        extraction = suggested or UnifiedVaultExtraction.model_validate(
            snapshot.values["extraction"],
        )
        initial = await self._initial_state(thread_id)
        new_thread = f"review-{uuid4().hex}"
        initial.update({
            "thread_id": new_thread,
            "extraction": extraction.model_dump(mode="json"),
            "hitl": HITLState(review_required=True, review_reasons=["user_requested"]).model_dump(
                mode="json",
            ),
        })
        # Claim the completed row before removing vectors so concurrent review requests
        # cannot fork the same document. A failed operation rolls back the DB changes.
        async with self._deps.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """SELECT status, thread_id FROM documents
                   WHERE family_id = $1 AND id = $2 FOR UPDATE""",
                document.family_id, document.id,
            )
            if row is None or row["status"] != "completed" or row["thread_id"] != thread_id:
                raise IngestionStateError("Document is no longer available for a new review")
            await self._deps.vectors.delete_document(document.id)
            await conn.execute(
                """UPDATE documents SET thread_id = $3, status = 'review_required',
                   index_version = $3, chunk_count = 0, error = NULL, updated_at = now()
                   WHERE family_id = $1 AND id = $2""",
                document.family_id, document.id, new_thread,
            )
            await PendingReviewRepo(conn).create(
                document.family_id, thread_id=new_thread,
                family_member_id=document.family_member_id, document_id=document.id,
                original_filename=document.original_filename or document.document_name,
                raw_text=extraction.extracted_text,
                suggested_metadata=extraction.model_dump(mode="json"),
                confidence_score=extraction.confidence_score, review_reasons=["user_requested"],
            )
            await self.graph.aupdate_state(self._config(new_thread), initial, as_node="validate")
        # The persisted next node also lets resume recover if the process stops here.
        await self.graph.ainvoke(None, self._config(new_thread))
        logger.info("extraction review opened", extra={"thread_id": new_thread})
        return self._outcome(new_thread, await self.graph.aget_state(self._config(new_thread)))

    async def mark_failed(
        self,
        thread_id: str,
        error: str,
        *,
        from_statuses: tuple[str, ...] = ("processing",),
    ) -> None:
        document = await DocumentRepo(self._deps.pool).get_by_thread_system(thread_id)
        if document is not None and document.status in from_statuses:
            await DocumentRepo(self._deps.pool).update_status(
                document.family_id, document.id, "failed", error=error[:500]
            )

    async def _initial_state(self, thread_id: str) -> IngestionState:
        document = await DocumentRepo(self._deps.pool).get_by_thread_system(thread_id)
        if document is None:
            raise IngestionStateError(f"No document registered for thread {thread_id}")
        if document.r2_object_key is None:
            raise IngestionStateError(f"Document for thread {thread_id} has no stored file")
        categories = await CategoryRepo(self._deps.pool).list_slugs(document.family_id)
        member = await MemberRepo(self._deps.pool).get(
            document.family_id, document.family_member_id,
        )
        if member is None:
            raise IngestionStateError(f"Document owner not found for thread {thread_id}")
        return IngestionState(
            thread_id=thread_id,
            family_id=str(document.family_id),
            family_member_id=str(document.family_member_id),
            document_id=str(document.id),
            original_filename=document.original_filename or "upload",
            r2_object_key=document.r2_object_key,
            source=document.source,
            allowed_categories=categories,
            requested_category=document.requested_category,
            document_details=document.document_details,
            member_name=member.display_name,
            extraction=None,
            expiration_raw=None,
            hitl=HITLState().model_dump(mode="json"),
            metrics={"started_at": datetime.now(UTC).isoformat()},
            status="processing",
            error=None,
        )

    @staticmethod
    def _outcome(thread_id: str, snapshot: Any) -> IngestionOutcome:
        values = snapshot.values or {}
        if snapshot.interrupts:
            interrupt_value = snapshot.interrupts[0].value or {}
            return IngestionOutcome(
                thread_id=thread_id,
                status="review_required",
                pending_review_id=interrupt_value.get("pending_review_id"),
            )
        status = values.get("status", "failed")
        if status not in ("completed", "failed", "rejected"):
            raise IngestionStateError(f"Thread {thread_id} stopped in unexpected state {status}")
        return IngestionOutcome(thread_id=thread_id, status=status, error=values.get("error"))


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """MemorySaver for tests; Postgres everywhere else so API and worker share paused threads."""
    if settings.env == "test":
        yield InMemorySaver()
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=4,
        open=False,
        # prepare_threshold=None keeps it compatible with Supabase/pgbouncer poolers.
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(conn=pool)  # type: ignore[arg-type]
        await saver.setup()
        yield saver

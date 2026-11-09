from __future__ import annotations

import io
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from PIL import Image
from pydantic import ValidationError

from app.agents.extractor import FakeExtractor
from app.agents.graph import (
    IngestionDeps,
    IngestionService,
    IngestionStateError,
    ReviewResume,
    evaluate_review,
    open_checkpointer,
)
from app.agents.state import UnifiedVaultExtraction
from app.config import Settings
from app.database import BASELINE_CATEGORIES
from app.embeddings import FakeEmbedder
from app.repositories import (
    CategoryRepo,
    Document,
    DocumentRepo,
    FamilyMember,
    FamilyRepo,
    MemberRepo,
    PendingReviewRepo,
)
from app.storage import InMemoryStorage
from app.vectorstore import DocumentSchemaIndex, InMemoryVectorIndex, VectorStore

NS = "family-vault"
ALLOWED = [slug for slug, _ in BASELINE_CATEGORIES]

PASSPORT = {
    "category": "identitycards", "document_name": "John Passport",
    "expiration_date": "2031-05-04", "requires_alert": True,
    "extracted_text": "PASSPORT JOHN SMITH valid until 04 MAY 2031",
    "financial_value": None, "confidence_score": 0.93,
}
BLURRY_ID = {**PASSPORT, "document_name": "John ID", "expiration_date": "3?/0?/20??",
             "confidence_score": 0.8}


async def _noop_sleep(_: float) -> None:
    return None


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pure routing rules (no database)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expiration_raw", "reasons", "category"),
    [
        ({}, None, [], "identitycards"),
        ({"expiration_date": None}, "3?/0?/20??", ["expiration_unparsed"], "identitycards"),
        ({"expiration_date": None}, None, ["expiration_missing"], "identitycards"),
        ({"expiration_date": None, "requires_alert": False}, None, [], "identitycards"),
        ({"confidence_score": 0.5}, None, ["low_confidence"], "identitycards"),
        ({"category": "spaceship"}, None, ["unknown_category"], "miscellaneous"),
    ],
)
def test_evaluate_review(
    overrides: dict[str, Any], expiration_raw: str | None, reasons: list[str], category: str
) -> None:
    extraction, got = evaluate_review(
        {**PASSPORT, **overrides}, expiration_raw=expiration_raw, allowed_categories=ALLOWED
    )
    assert got == reasons
    assert extraction["category"] == category


def test_review_resume_validation() -> None:
    corrected = UnifiedVaultExtraction.model_validate({**PASSPORT, "expiration_date": None})
    with pytest.raises(ValidationError, match="required when requires_alert"):
        ReviewResume(decision="approve", corrected=corrected)
    assert ReviewResume(decision="reject", corrected=corrected).decision == "reject"
    with pytest.raises(ValidationError):
        ReviewResume.model_validate({"decision": "maybe"})


# ---------------------------------------------------------------------------
# Graph runs (Postgres for repositories; MemorySaver checkpointer)
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    pool: asyncpg.Pool
    member: FamilyMember
    storage: InMemoryStorage
    index: InMemoryVectorIndex
    extractor: FakeExtractor
    service: IngestionService

    async def register(
        self, filename: str = "passport.jpg", data: bytes | None = None,
        requested_category: str | None = None,
        document_details: str = "",
    ) -> Document:
        thread_id = f"th-{uuid.uuid4().hex}"
        document_id = uuid.uuid4()
        key = f"{self.member.family_id}/{document_id}/{uuid.uuid4().hex}.jpg"
        await self.storage.put(key, data if data is not None else _jpeg(), "image/jpeg")
        return await DocumentRepo(self.pool).create(
            self.member.family_id, family_member_id=self.member.id,
            category_slug="miscellaneous", document_name=filename, source="upload",
            thread_id=thread_id, original_filename=filename, r2_object_key=key,
            requested_category=requested_category,
            document_details=document_details,
        )

    async def document(self, doc: Document) -> Document:
        found = await DocumentRepo(self.pool).get(doc.family_id, doc.id)
        assert found is not None
        return found

    def vectors_for(self, doc: Document) -> list[dict[str, Any]]:
        return [dict(r) for r in self.index.namespaces.get(NS, {}).values()
                if r["metadata"]["document_id"] == str(doc.id)]


def _make_service(pool: asyncpg.Pool, extractor: FakeExtractor, storage: InMemoryStorage,
                  index: InMemoryVectorIndex, checkpointer: Any) -> IngestionService:
    deps = IngestionDeps(pool=pool, storage=storage, extractor=extractor,
                         embedder=FakeEmbedder(), vectors=VectorStore(index, NS),
                         sleep=_noop_sleep)
    return IngestionService(deps, checkpointer)


async def _harness(pool: asyncpg.Pool, responses: list[Any]) -> Harness:
    family = await FamilyRepo(pool).create("Smith")
    member = await MemberRepo(pool).create(family.id, display_name="John", role="admin")
    storage, index, extractor = InMemoryStorage(), InMemoryVectorIndex(), FakeExtractor(responses)
    service = _make_service(pool, extractor, storage, index, InMemorySaver())
    return Harness(pool, member, storage, index, extractor, service)


@pytest.mark.integration
async def test_happy_path_upserts_vectors(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [PASSPORT])
    doc = await h.register()
    outcome = await h.service.run(doc.thread_id or "")

    assert outcome.status == "completed"
    stored = await h.document(doc)
    assert stored.status == "completed"
    assert stored.category_slug == "identitycards"
    assert str(stored.expiration_date) == "2031-05-04"
    vectors = h.vectors_for(doc)
    assert len(vectors) == stored.chunk_count == 1
    meta = vectors[0]["metadata"]
    assert meta["family_id"] == str(h.member.family_id)
    assert meta["family_member_id"] == str(h.member.id)
    assert meta["expiration_date"] == "2031-05-04"
    assert meta["is_primary"] is True
    assert set(h.extractor.calls[0]["categories"]) == set(ALLOWED)

    # Re-running a settled thread is a no-op.
    again = await h.service.run(doc.thread_id or "")
    assert again.status == "completed"
    assert len(h.extractor.calls) == 1


@pytest.mark.integration
async def test_selected_category_overrides_ai_and_is_indexed(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [PASSPORT])
    doc = await h.register(requested_category="medical")
    outcome = await h.service.run(doc.thread_id or "")
    assert outcome.status == "completed"
    assert (await h.document(doc)).category_slug == "medical"
    assert h.vectors_for(doc)[0]["metadata"]["category"] == "medical"


@pytest.mark.integration
async def test_upload_context_is_embedded_and_available_to_retrieval(db_pool: asyncpg.Pool) -> None:
    from app.agents.prompts import render_context_block
    from app.vectorstore import build_filter
    from tests.helpers import make_principal

    h = await _harness(db_pool, [PASSPORT])
    details = "Renewal for study abroad, reference TRAVEL-42"
    doc = await h.register(requested_category="medical", document_details=details)
    outcome = await h.service.run(doc.thread_id or "")
    assert outcome.status == "completed"
    records = h.vectors_for(doc)
    metadata = records[0]["metadata"]
    assert metadata["document_details"] == details
    assert metadata["member_name"] == "John"
    assert "Document owner: John" in metadata["text"]
    assert "Category: medical" in metadata["text"]
    assert details in metadata["text"]
    assert PASSPORT["extracted_text"] in metadata["text"]
    # Prove the description is in the embedding input, not just stored as metadata.
    expected = (await FakeEmbedder().embed([metadata["text"]]))[0]
    assert records[0]["values"] == expected
    principal = make_principal(
        "member", family_id=doc.family_id, member_id=doc.family_member_id,
    )
    matches = await VectorStore(h.index, NS).query(expected, filter=build_filter(principal))
    assert matches[0].metadata["document_id"] == str(doc.id)
    context = render_context_block(matches)
    assert details in context and 'owner="John"' in context
    assert "not extracted" in context
    assert (await h.document(doc)).document_details == details
    assert await h.storage.get(doc.r2_object_key or "") == _jpeg()


@pytest.mark.integration
async def test_integrated_upload_preserves_context_on_every_chunk(db_pool: asyncpg.Pool) -> None:
    text = ("Document line for a trip abroad. " * 500).strip()
    h = await _harness(db_pool, [{**PASSPORT, "extracted_text": text}])
    details = "Travel note " * 160
    doc = await h.register(document_details=details)
    api = MagicMock()
    api.documents.list.return_value = []
    api.documents.batch_upsert.side_effect = lambda **kwargs: {
        "successful_item_count": len(kwargs["documents"]),
    }
    adapter = DocumentSchemaIndex(api, {"fields": {
        "title": {"type": "string", "full_text_search": {"language": "en"}},
        "chunk_text": {
            "type": "string", "store_text": True,
            "embed": {"dimension": 1536, "metric": "cosine"},
        },
    }})
    embedder = AsyncMock(spec=FakeEmbedder)
    service = IngestionService(IngestionDeps(
        pool=db_pool, storage=h.storage, extractor=h.extractor,
        embedder=embedder, vectors=VectorStore(adapter, NS), sleep=_noop_sleep,
    ), InMemorySaver())
    assert (await service.run(doc.thread_id or "")).status == "completed"
    embedder.embed.assert_not_awaited()
    indexed = api.documents.batch_upsert.call_args.kwargs["documents"]
    assert len(indexed) > 1
    assert details in indexed[0]["chunk_text"]
    assert all(record["document_details"] == details for record in indexed)
    assert all(record["member_name"] == "John" for record in indexed)
    assert all(record["category"] == "identitycards" for record in indexed)
    assert any("Document line for a trip abroad." in record["chunk_text"] for record in indexed)
    snapshot = await service.graph.aget_state(
        {"configurable": {"thread_id": doc.thread_id or ""}},
    )
    assert snapshot.values["extraction"]["extracted_text"] == text


@pytest.mark.integration
async def test_custom_category_is_offered_to_extractor(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [{**PASSPORT, "category": "vehicle", "requires_alert": False}])
    await CategoryRepo(db_pool).create_custom(h.member.family_id, slug="vehicle",
                                              display_name="Vehicle")
    doc = await h.register("car-title.jpg")
    outcome = await h.service.run(doc.thread_id or "")
    assert outcome.status == "completed"
    assert "vehicle" in h.extractor.calls[0]["categories"]
    assert h.vectors_for(doc)[0]["metadata"]["category"] == "vehicle"


@pytest.mark.integration
async def test_unparsed_expiry_halts_for_review(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [BLURRY_ID])
    doc = await h.register("id.jpg")
    thread = doc.thread_id or ""

    outcome = await h.service.run(thread)
    assert outcome.status == "review_required"
    assert outcome.pending_review_id is not None

    snapshot = await h.service.graph.aget_state({"configurable": {"thread_id": thread}})
    assert snapshot.next == ("human_review",)
    assert h.vectors_for(doc) == []
    assert (await h.document(doc)).status == "review_required"

    review = await PendingReviewRepo(db_pool).get_by_thread(h.member.family_id, thread)
    assert review is not None
    assert review.review_reasons == ["expiration_unparsed"]
    assert review.suggested_metadata["expiration_date"] is None
    assert str(review.id) == outcome.pending_review_id

    # Running again while paused does not duplicate the review or call the model again.
    assert (await h.service.run(thread)).status == "review_required"
    assert len(h.extractor.calls) == 1
    assert await PendingReviewRepo(db_pool).count_open(h.member.family_id) == 1


@pytest.mark.integration
async def test_approve_with_correction_upserts(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [BLURRY_ID])
    doc = await h.register("id.jpg", document_details="Travel renewal reference HOLD-7")
    thread = doc.thread_id or ""
    await h.service.run(thread)

    corrected = UnifiedVaultExtraction.model_validate({**PASSPORT, "expiration_date": "2029-09-30"})
    outcome = await h.service.resume(
        thread, ReviewResume(decision="approve", corrected=corrected, resolved_by=h.member.id)
    )
    assert outcome.status == "completed"
    assert h.vectors_for(doc)[0]["metadata"]["expiration_date"] == "2029-09-30"
    stored = await h.document(doc)
    assert stored.status == "completed" and str(stored.expiration_date) == "2029-09-30"
    assert stored.document_details == "Travel renewal reference HOLD-7"
    assert h.vectors_for(doc)[0]["metadata"]["document_details"] == stored.document_details

    with pytest.raises(IngestionStateError):
        await h.service.resume(thread, ReviewResume(decision="reject"))


@pytest.mark.integration
async def test_completed_upload_can_be_corrected_without_rerunning_ocr(
    db_pool: asyncpg.Pool,
) -> None:
    h = await _harness(db_pool, [PASSPORT])
    doc = await h.register(document_details="Travel renewal")
    await h.service.run(doc.thread_id or "")
    original_key = doc.r2_object_key
    outcome = await h.service.request_review(doc.thread_id or "")
    assert outcome.status == "review_required"
    assert outcome.thread_id != doc.thread_id
    assert h.vectors_for(doc) == []
    stored = await h.document(doc)
    assert stored.status == "review_required"
    assert stored.thread_id == outcome.thread_id
    assert stored.r2_object_key == original_key
    assert await h.storage.get(original_key or "") == _jpeg()
    review = await PendingReviewRepo(db_pool).get_by_thread(doc.family_id, outcome.thread_id)
    assert review is not None
    assert review.review_reasons == ["user_requested"]
    corrected = UnifiedVaultExtraction.model_validate({
        **PASSPORT, "expiration_date": "2036-08-20",
        "extracted_text": "Birth: 1985-04-12\nIssue: 2026-08-20\nExpiry: 2036-08-20",
    })
    result = await h.service.resume(outcome.thread_id, ReviewResume(
        decision="approve", corrected=corrected, resolved_by=h.member.id,
    ))
    assert result.status == "completed"
    assert len(h.extractor.calls) == 1
    stored = await h.document(doc)
    assert str(stored.expiration_date) == "2036-08-20"
    assert stored.document_details == "Travel renewal"
    meta = h.vectors_for(doc)[0]["metadata"]
    assert corrected.extracted_text in meta["text"]
    assert "2031-05-04" not in meta["text"]
    assert meta["index_version"] == stored.index_version == outcome.thread_id
    # A second correction gets its own review; the old review remains as audit history.
    await PendingReviewRepo(db_pool).resolve(
        doc.family_id, outcome.thread_id, decision="approved", resolved_by=h.member.id,
    )
    second = await h.service.request_review(outcome.thread_id)
    assert second.thread_id != outcome.thread_id
    old_review = await PendingReviewRepo(db_pool).get_by_thread(doc.family_id, outcome.thread_id)
    assert old_review is not None and old_review.status == "approved"


@pytest.mark.integration
async def test_correction_review_failure_does_not_change_document(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await _harness(db_pool, [PASSPORT])
    doc = await h.register()
    await h.service.run(doc.thread_id or "")
    with pytest.raises(IngestionStateError):
        await h.service.request_review("missing-thread")
    vectors = h.service._deps.vectors
    monkeypatch.setattr(vectors, "delete_document", AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        await h.service.request_review(doc.thread_id or "")
    stored = await h.document(doc)
    assert stored.status == "completed" and stored.thread_id == doc.thread_id
    assert await PendingReviewRepo(db_pool).count_open(doc.family_id) == 0
    assert h.vectors_for(doc)


@pytest.mark.integration
async def test_correction_review_recovers_after_checkpoint_is_prepared(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await _harness(db_pool, [PASSPORT])
    doc = await h.register()
    await h.service.run(doc.thread_id or "")
    with monkeypatch.context() as patch:
        patch.setattr(h.service.graph, "ainvoke", AsyncMock(side_effect=RuntimeError("stopped")))
        with pytest.raises(RuntimeError, match="stopped"):
            await h.service.request_review(doc.thread_id or "")
    stored = await h.document(doc)
    assert stored.status == "review_required" and stored.thread_id
    result = await h.service.resume(stored.thread_id, ReviewResume(
        decision="approve", corrected=UnifiedVaultExtraction.model_validate(PASSPORT),
    ))
    assert result.status == "completed"
    assert len(h.extractor.calls) == 1


@pytest.mark.integration
async def test_reject_discards_file(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [BLURRY_ID])
    doc = await h.register("id.jpg")
    await h.service.run(doc.thread_id or "")

    outcome = await h.service.resume(doc.thread_id or "", ReviewResume(decision="reject"))
    assert outcome.status == "rejected"
    assert doc.r2_object_key not in h.storage.objects
    assert h.vectors_for(doc) == []
    assert (await h.document(doc)).status == "rejected"


@pytest.mark.integration
async def test_resume_rejects_unknown_category(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [BLURRY_ID])
    doc = await h.register("id.jpg")
    await h.service.run(doc.thread_id or "")
    corrected = UnifiedVaultExtraction.model_validate({**PASSPORT, "category": "spaceship"})
    with pytest.raises(ValueError, match="Unknown category"):
        await h.service.resume(doc.thread_id or "",
                               ReviewResume(decision="approve", corrected=corrected))


@pytest.mark.integration
async def test_extraction_retries_then_succeeds(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [TimeoutError("slow"), RuntimeError("500"), PASSPORT])
    doc = await h.register()
    assert (await h.service.run(doc.thread_id or "")).status == "completed"
    assert len(h.extractor.calls) == 3


@pytest.mark.integration
async def test_extraction_failure_marks_document_failed(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [RuntimeError("down")])
    doc = await h.register()
    outcome = await h.service.run(doc.thread_id or "")
    assert outcome.status == "failed"
    assert "3 attempts" in (outcome.error or "")
    assert len(h.extractor.calls) == 3
    stored = await h.document(doc)
    assert stored.status == "failed" and stored.error
    assert h.vectors_for(doc) == []


@pytest.mark.integration
async def test_missing_or_corrupt_file_fails_without_llm(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [PASSPORT])
    corrupt = await h.register("x.jpg", data=b"not an image at all")
    assert (await h.service.run(corrupt.thread_id or "")).status == "failed"

    missing = await h.register("y.jpg")
    h.storage.objects.clear()
    assert (await h.service.run(missing.thread_id or "")).status == "failed"
    assert h.extractor.calls == []


@pytest.mark.integration
async def test_unknown_thread_raises(db_pool: asyncpg.Pool) -> None:
    h = await _harness(db_pool, [PASSPORT])
    with pytest.raises(IngestionStateError):
        await h.service.run("th-does-not-exist")


# ---------------------------------------------------------------------------
# Durable checkpointer: pause, "restart" (new saver + graph), resume.
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_settings() -> Settings:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return Settings(  # type: ignore[call-arg]
        _env_file=None, env="dev", database_url=url, openai_api_key="x", pinecone_api_key="y"
    )


@pytest.fixture
async def restart_harness(
    db_pool: asyncpg.Pool,
) -> AsyncIterator[tuple[asyncpg.Pool, FamilyMember]]:
    family = await FamilyRepo(db_pool).create("Durable")
    member = await MemberRepo(db_pool).create(family.id, display_name="Ann", role="admin")
    yield db_pool, member


@pytest.mark.integration
async def test_postgres_checkpointer_survives_restart(
    postgres_settings: Settings, restart_harness: tuple[asyncpg.Pool, FamilyMember]
) -> None:
    pool, member = restart_harness
    storage, index = InMemoryStorage(), InMemoryVectorIndex()
    thread = f"th-{uuid.uuid4().hex}"
    doc_id = uuid.uuid4()
    key = f"{member.family_id}/{doc_id}/a.jpg"
    await storage.put(key, _jpeg(), "image/jpeg")
    doc = await DocumentRepo(pool).create(
        member.family_id, family_member_id=member.id, category_slug="miscellaneous",
        document_name="id.jpg", source="upload", thread_id=thread, original_filename="id.jpg",
        r2_object_key=key,
    )

    async with open_checkpointer(postgres_settings) as saver:
        service = _make_service(pool, FakeExtractor([BLURRY_ID]), storage, index, saver)
        assert (await service.run(thread)).status == "review_required"

    # Simulated process restart: brand-new pool, saver and compiled graph.
    async with open_checkpointer(postgres_settings) as saver:
        service = _make_service(pool, FakeExtractor([]), storage, index, saver)
        corrected = UnifiedVaultExtraction.model_validate(
            {**PASSPORT, "expiration_date": "2030-12-31"}
        )
        outcome = await service.resume(thread, ReviewResume(decision="approve",
                                                            corrected=corrected))
    assert outcome.status == "completed"
    stored = await DocumentRepo(pool).get(member.family_id, doc.id)
    assert stored is not None and str(stored.expiration_date) == "2030-12-31"

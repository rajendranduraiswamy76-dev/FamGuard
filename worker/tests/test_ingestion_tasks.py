from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agents.state import UnifiedVaultExtraction
from app.repositories import PendingReviewRepo
from famguard_worker.tasks.ingestion import ingest_document, resume_ingestion

if TYPE_CHECKING:
    from tests.conftest import World

pytestmark = pytest.mark.integration

PASSPORT = {
    "category": "identitycards", "document_name": "Ann Passport",
    "expiration_date": "2031-05-04", "requires_alert": True,
    "extracted_text": "PASSPORT ANN", "financial_value": None, "confidence_score": 0.95,
}
BLURRY = {**PASSPORT, "expiration_date": "??/??/2031"}


def _run(task: Any, *args: Any) -> dict[str, Any]:
    result: dict[str, Any] = task.delay(*args).get()
    return result


@pytest.mark.parametrize("world", [{"responses": [PASSPORT]}], indirect=True)
def test_ingest_document_completes(world: World) -> None:
    doc = world.register()
    result = _run(ingest_document, doc.thread_id)
    assert result["status"] == "completed"
    assert world.document(doc).status == "completed"
    assert len(world.vectors_for(doc)) == 1


@pytest.mark.parametrize("world", [{"responses": [PASSPORT]}], indirect=True)
def test_duplicate_delivery_is_idempotent(world: World) -> None:
    doc = world.register()
    assert _run(ingest_document, doc.thread_id)["status"] == "completed"
    assert _run(ingest_document, doc.thread_id)["status"] == "completed"
    assert len(world.extractor.calls) == 1
    assert world.index.upsert_calls == 1


@pytest.mark.parametrize("world", [{"responses": [PASSPORT], "embed_failures": 2}],
                         indirect=True)
def test_transient_failures_are_retried(world: World) -> None:
    doc = world.register()
    result = _run(ingest_document, doc.thread_id)
    assert result["status"] == "completed"
    # Retries continue from the checkpoint: the model was called only once.
    assert len(world.extractor.calls) == 1
    assert len(world.vectors_for(doc)) == 1


@pytest.mark.parametrize("world", [{"responses": [PASSPORT], "embed_failures": 99}],
                         indirect=True)
def test_permanent_failure_marks_document_failed(world: World) -> None:
    doc = world.register()
    with pytest.raises(ConnectionError):
        _run(ingest_document, doc.thread_id)
    stored = world.document(doc)
    assert stored.status == "failed"
    assert stored.error == "Processing failed: ConnectionError"


@pytest.mark.parametrize("world", [{"responses": [PASSPORT]}], indirect=True)
def test_unknown_thread_is_not_retried(world: World) -> None:
    result = _run(ingest_document, "ing-missing")
    assert result["status"] == "failed"


@pytest.mark.parametrize("world", [{"responses": [BLURRY]}], indirect=True)
def test_review_then_resume(world: World) -> None:
    from famguard_worker.runtime import runtime

    doc = world.register("id.jpg")
    assert _run(ingest_document, doc.thread_id)["status"] == "review_required"
    review = runtime.run(PendingReviewRepo(world.pool).get_by_thread(doc.family_id,
                                                                    doc.thread_id or ""))
    assert review is not None

    corrected = UnifiedVaultExtraction.model_validate(
        {**PASSPORT, "expiration_date": "2030-02-28"}
    ).model_dump(mode="json")
    payload = {"decision": "approve", "corrected": corrected, "resolved_by": None}
    assert _run(resume_ingestion, doc.thread_id, payload)["status"] == "completed"
    assert world.vectors_for(doc)[0]["metadata"]["expiration_date"] == "2030-02-28"

    # Duplicate delivery of the same resume is a no-op.
    assert _run(resume_ingestion, doc.thread_id, payload)["status"] == "not_waiting"


@pytest.mark.parametrize("world", [{"responses": [BLURRY]}], indirect=True)
def test_invalid_resume_payload_is_rejected_without_retry(world: World) -> None:
    doc = world.register("id.jpg")
    _run(ingest_document, doc.thread_id)
    result = _run(resume_ingestion, doc.thread_id, {"decision": "maybe"})
    assert result["status"] == "failed"
    assert world.document(doc).status == "review_required"

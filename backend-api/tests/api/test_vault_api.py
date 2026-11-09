from __future__ import annotations

import io
from typing import Any
from uuid import UUID

import pytest
from PIL import Image

from app.parsers import MAX_UPLOAD_BYTES
from app.repositories import DocumentRepo, PendingReviewRepo
from app.tasks import INGEST_TASK, RESUME_TASK
from tests.api_harness import ApiHarness

pytestmark = pytest.mark.integration


def _png(color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


async def _upload(api: ApiHarness, headers: dict[str, str], member_id: str,
                  files: list[tuple[str, bytes]], source: str = "upload") -> Any:
    return await api.client.post(
        "/api/vault/upload",
        headers=headers,
        data={"family_member_id": member_id, "source": source},
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
    )


async def _family(api: ApiHarness) -> dict[str, Any]:
    """Admin Ann, member Bob (with login), dependent John."""
    admin_h, me = await api.login("user_ann", email="ann@example.com", name="Ann")
    await api.client.post("/api/members", headers=admin_h,
                          json={"display_name": "Bob", "role": "member",
                                "email": "bob@example.com"})
    john = (await api.client.post("/api/members", headers=admin_h,
                                  json={"display_name": "John", "role": "dependent"})).json()
    bob_h, bob = await api.login("user_bob", email="bob@example.com", name="Bob")
    return {"admin_h": admin_h, "admin_id": me["member"]["id"], "family_id": me["family"]["id"],
            "bob_h": bob_h, "bob_id": bob["member"]["id"], "john_id": john["id"]}


async def test_upload_accepts_and_enqueues(api: ApiHarness) -> None:
    fam = await _family(api)
    resp = await _upload(api, fam["admin_h"], fam["john_id"],
                         [("passport.png", _png("red")), ("../evil name.png", _png("blue"))])
    assert resp.status_code == 202, resp.text
    uploads = resp.json()["uploads"]
    assert [u["filename"] for u in uploads] == ["passport.png", "evil name.png"]
    assert [t for t, _ in api.queue.sent] == [INGEST_TASK, INGEST_TASK]
    assert [args[0] for _, args in api.queue.sent] == [u["thread_id"] for u in uploads]
    assert all(k.startswith(fam["family_id"] + "/") for k in api.storage.objects)

    status = await api.client.get(f"/api/vault/uploads/{uploads[0]['thread_id']}",
                                  headers=fam["admin_h"])
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "processing"
    assert body["family_member_id"] == fam["john_id"]


async def test_upload_rejections(api: ApiHarness) -> None:
    fam = await _family(api)
    big = _png() + b"\x00" * MAX_UPLOAD_BYTES
    assert (await _upload(api, fam["admin_h"], fam["john_id"], [("a.png", big)])).status_code == 413
    spoof = await _upload(api, fam["admin_h"], fam["john_id"], [("a.jpg", b"MZ\x90\x00" * 10)])
    assert spoof.status_code == 415
    assert spoof.json()["error"]["code"] == "unsupported_media_type"
    # A bad file rejects the whole batch: nothing stored, nothing queued.
    mixed = await _upload(api, fam["admin_h"], fam["john_id"],
                          [("ok.png", _png()), ("bad.jpg", b"garbage")])
    assert mixed.status_code == 415
    assert api.storage.objects == {} and api.queue.sent == []


async def test_upload_preserves_selected_category_and_rejects_unknown(api: ApiHarness) -> None:
    fam = await _family(api)
    for category, expected in (("not-a-category", 422), ("medical", 202)):
        response = await api.client.post(
            "/api/vault/upload", headers=fam["admin_h"],
            data={"family_member_id": fam["john_id"], "category": category},
            files=[("files", ("selected.png", _png(), "image/png"))],
        )
        assert response.status_code == expected
        if expected == 202:
            doc = await DocumentRepo(api.pool).get(
                UUID(fam["family_id"]), UUID(response.json()["uploads"][0]["document_id"]),
            )
            assert doc is not None
            assert doc.requested_category == doc.category_slug == "medical"


async def test_upload_details_persist_for_all_files_and_are_scoped(api: ApiHarness) -> None:
    fam = await _family(api)
    details = "  Annual cover\nReference POLICY-42  "
    response = await api.client.post(
        "/api/vault/upload", headers=fam["admin_h"],
        data={"family_member_id": fam["john_id"], "category": "medical",
              "document_details": details},
        files=[("files", ("one.png", _png("red"), "image/png")),
               ("files", ("two.png", _png("blue"), "image/png"))],
    )
    assert response.status_code == 202
    for uploaded in response.json()["uploads"]:
        doc = await DocumentRepo(api.pool).get(
            UUID(fam["family_id"]), UUID(uploaded["document_id"]),
        )
        assert doc is not None
        assert doc.document_details == details.strip()
        assert doc.family_member_id == UUID(fam["john_id"])
        assert doc.requested_category == "medical"
        status = await api.client.get(
            f"/api/vault/uploads/{uploaded['thread_id']}", headers=fam["admin_h"],
        )
        assert status.json()["document_details"] == details.strip()
    history = (await api.client.get("/api/vault/uploads", headers=fam["admin_h"])).json()
    assert len(history) == 2
    assert all(item["document_details"] == details.strip() for item in history)
    own = (await api.client.get("/api/vault/uploads", headers=fam["bob_h"])).json()
    assert own == []
    outsider, _ = await api.login("details-outsider", name="Other")
    assert (await api.client.get("/api/vault/uploads", headers=outsider)).json() == []


@pytest.mark.parametrize(("details", "expected"), [
    ("x" * 2000, 202), ("x" * 2001, 422), (" \n\t ", 202), ("bad\x00note", 422),
])
async def test_document_details_validation(
    api: ApiHarness, details: str, expected: int,
) -> None:
    headers, me = await api.login("details-admin", name="Admin")
    response = await api.client.post(
        "/api/vault/upload", headers=headers,
        data={"family_member_id": me["member"]["id"], "document_details": details},
        files=[("files", ("one.png", _png(), "image/png"))],
    )
    assert response.status_code == expected, response.text
    if expected == 422:
        assert api.storage.objects == {}
        assert api.queue.sent == []
    else:
        item = response.json()["uploads"][0]
        status = await api.client.get(f"/api/vault/uploads/{item['thread_id']}", headers=headers)
        assert status.json()["document_details"] == details.strip()


async def test_duplicate_upload_and_replacement_after_reject(api: ApiHarness) -> None:
    fam = await _family(api)
    data = _png("green")
    first = await _upload(api, fam["admin_h"], fam["john_id"], [("bill.png", data)])
    assert first.status_code == 202
    dup = await _upload(api, fam["admin_h"], fam["john_id"], [("copy.png", data)])
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "duplicate_document"
    doc_id = first.json()["uploads"][0]["document_id"]
    assert dup.json()["error"]["details"]["existing_document_id"] == doc_id

    await DocumentRepo(api.pool).update_status(UUID(fam["family_id"]), UUID(doc_id), "rejected")
    again = await _upload(api, fam["admin_h"], fam["john_id"], [("bill.png", data)])
    assert again.status_code == 202
    assert len(api.storage.objects) == 1


async def test_upload_access_control(api: ApiHarness) -> None:
    fam = await _family(api)
    # Members may only upload for themselves.
    resp = await _upload(api, fam["bob_h"], fam["john_id"], [("a.png", _png())])
    assert resp.status_code == 403
    resp = await _upload(api, fam["bob_h"], fam["bob_id"], [("a.png", _png())])
    assert resp.status_code == 202
    # Another family cannot target this family's members.
    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    resp = await _upload(api, other_h, fam["john_id"], [("b.png", _png("blue"))])
    assert resp.status_code == 404


async def test_upload_status_isolation(api: ApiHarness) -> None:
    fam = await _family(api)
    thread = (await _upload(api, fam["admin_h"], fam["john_id"],
                            [("a.png", _png())])).json()["uploads"][0]["thread_id"]
    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    url = f"/api/vault/uploads/{thread}"
    assert (await api.client.get(url, headers=other_h)).status_code == 404
    assert (await api.client.get(url, headers=fam["bob_h"])).status_code == 403


async def test_recent_uploads_are_persistent_and_member_scoped(api: ApiHarness) -> None:
    fam = await _family(api)
    john = (await _upload(api, fam["admin_h"], fam["john_id"],
                          [("john.png", _png("red"))])).json()["uploads"][0]
    bob = (await _upload(api, fam["bob_h"], fam["bob_id"],
                         [("bob.png", _png("blue"))])).json()["uploads"][0]
    repo = DocumentRepo(api.pool)
    await repo.update_status(
        UUID(fam["family_id"]), UUID(john["document_id"]), "failed", error="Extraction failed",
    )
    admin_list = (await api.client.get("/api/vault/uploads", headers=fam["admin_h"])).json()
    assert [u["thread_id"] for u in admin_list] == [bob["thread_id"], john["thread_id"]]
    assert admin_list[1]["error"] == "Extraction failed"
    own = (await api.client.get("/api/vault/uploads", headers=fam["bob_h"])).json()
    assert [u["thread_id"] for u in own] == [bob["thread_id"]]
    limited = (await api.client.get("/api/vault/uploads?limit=1", headers=fam["admin_h"])).json()
    assert len(limited) == 1
    invalid = await api.client.get("/api/vault/uploads?limit=101", headers=fam["admin_h"])
    assert invalid.status_code == 422
    other_h, _ = await api.login("user_other", email="other@example.com", name="Other")
    assert (await api.client.get("/api/vault/uploads", headers=other_h)).json() == []


async def test_review_completed_extraction_and_access_controls(api: ApiHarness) -> None:
    fam = await _family(api)
    upload = (await _upload(api, fam["admin_h"], fam["john_id"],
                             [("passport.png", _png())])).json()["uploads"][0]
    url = f"/api/vault/documents/{upload['document_id']}/review"
    assert (await api.client.post(url, headers=fam["admin_h"])).status_code == 409
    await api.ingestion.run(upload["thread_id"])
    outsider, _ = await api.login("review-outsider", name="Other")
    assert (await api.client.post(url, headers=outsider)).status_code == 404
    assert (await api.client.post(url, headers=fam["bob_h"])).status_code == 403
    response = await api.client.post(url, headers=fam["admin_h"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "review_required"
    assert response.json()["thread_id"] != upload["thread_id"]
    assert (await api.client.post(url, headers=fam["admin_h"])).status_code == 409
    reviews = (await api.client.get("/api/vault/pending-reviews", headers=fam["admin_h"])).json()
    assert len(reviews) == 1
    assert reviews[0]["review_reasons"] == ["user_requested"]
    assert api.storage.objects
    assert not api.index.namespaces["family-vault"]



async def test_upload_filters_and_pages_include_older_documents(api: ApiHarness) -> None:
    fam = await _family(api)
    repo = DocumentRepo(api.pool)
    for index in range(52):
        await repo.create(
            UUID(fam["family_id"]), family_member_id=UUID(fam["john_id"]),
            category_slug="medical", document_name=f"Policy {index}", source="upload",
            thread_id=f"policy-{index}", document_details=f"Reference {index}",
        )
    await repo.create(
        UUID(fam["family_id"]), family_member_id=UUID(fam["bob_id"]),
        category_slug="medical", document_name="Bob policy", source="chat", thread_id="bob-policy",
    )
    await repo.create(
        UUID(fam["family_id"]), family_member_id=UUID(fam["john_id"]),
        category_slug="certificate", document_name="John degree", source="upload",
        thread_id="john-degree",
    )
    filters = {"family_member_id": fam["john_id"], "category": "medical", "limit": 51}
    first = await api.client.get("/api/vault/uploads", headers=fam["admin_h"], params=filters)
    assert first.status_code == 200
    assert len(first.json()) == 51
    assert all(item["category"] == "medical" for item in first.json())
    assert all(item["family_member_id"] == fam["john_id"] for item in first.json())
    second = await api.client.get(
        "/api/vault/uploads", headers=fam["admin_h"], params={**filters, "offset": 50},
    )
    assert [item["thread_id"] for item in second.json()] == ["policy-1", "policy-0"]
    assert second.json()[-1]["document_details"] == "Reference 0"
    owner_only = await api.client.get(
        "/api/vault/uploads", headers=fam["admin_h"],
        params={"family_member_id": fam["john_id"], "limit": 100},
    )
    assert len(owner_only.json()) == 53
    category_only = await api.client.get(
        "/api/vault/uploads", headers=fam["admin_h"], params={"category": "certificate"},
    )
    assert [item["thread_id"] for item in category_only.json()] == ["john-degree"]
    own = await api.client.get(
        "/api/vault/uploads", headers=fam["bob_h"], params={"category": "medical"},
    )
    assert [item["thread_id"] for item in own.json()] == ["bob-policy"]
    denied = await api.client.get(
        "/api/vault/uploads", headers=fam["bob_h"],
        params={"family_member_id": fam["john_id"]},
    )
    assert denied.status_code == 403
    outsider, _ = await api.login("filter-outsider", name="Other")
    denied = await api.client.get(
        "/api/vault/uploads", headers=outsider, params={"family_member_id": fam["john_id"]},
    )
    assert denied.status_code == 404
    for invalid in ({"offset": -1}, {"family_member_id": "bad-id"}, {"category": "unknown"}):
        response = await api.client.get(
            "/api/vault/uploads", headers=fam["admin_h"], params=invalid,
        )
        assert response.status_code == 422


async def test_enqueue_failure_rolls_back(api: ApiHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    fam = await _family(api)

    async def boom(thread_id: str) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(api.queue, "enqueue_ingestion", boom)
    resp = await _upload(api, fam["admin_h"], fam["john_id"], [("a.png", _png())])
    assert resp.status_code == 503
    assert api.storage.objects == {}
    assert await api.pool.fetchval("SELECT count(*) FROM documents") == 0


async def _pending_review(api: ApiHarness, fam: dict[str, Any], member_key: str) -> str:
    color = {"john_id": "yellow", "bob_id": "purple"}[member_key]
    resp = await _upload(api, fam["admin_h"], fam[member_key], [(f"{member_key}.png",
                                                                  _png(color))])
    item = resp.json()["uploads"][0]
    await PendingReviewRepo(api.pool).create(
        UUID(fam["family_id"]), thread_id=item["thread_id"],
        family_member_id=UUID(fam[member_key]), document_id=UUID(item["document_id"]),
        original_filename="id.png", raw_text="ID CARD",
        suggested_metadata={"category": "identitycards", "document_name": "ID",
                            "expiration_date": None, "requires_alert": True,
                            "extracted_text": "ID CARD", "financial_value": None,
                            "confidence_score": 0.5},
        confidence_score=0.5, review_reasons=["expiration_missing"],
    )
    return str(item["thread_id"])


async def test_pending_reviews_visibility(api: ApiHarness) -> None:
    fam = await _family(api)
    john_thread = await _pending_review(api, fam, "john_id")
    bob_thread = await _pending_review(api, fam, "bob_id")

    admin_list = (await api.client.get("/api/vault/pending-reviews", headers=fam["admin_h"])).json()
    assert {r["thread_id"] for r in admin_list} == {john_thread, bob_thread}
    bob_list = (await api.client.get("/api/vault/pending-reviews", headers=fam["bob_h"])).json()
    assert [r["thread_id"] for r in bob_list] == [bob_thread]
    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    assert (await api.client.get("/api/vault/pending-reviews", headers=other_h)).json() == []


def _corrected(**overrides: Any) -> dict[str, Any]:
    values = {"category": "identitycards", "document_name": "John ID",
              "expiration_date": "2030-06-30", "requires_alert": True,
              "extracted_text": "ID CARD", "financial_value": None}
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"expiration_date": "2030-13-01"}, "YYYY-MM-DD"),
        ({"expiration_date": None}, "required when requires_alert"),
        ({"category": "spaceship"}, "Unknown category"),
    ],
)
async def test_resolve_review_validation(api: ApiHarness, overrides: dict[str, Any],
                                         fragment: str) -> None:
    fam = await _family(api)
    thread = await _pending_review(api, fam, "john_id")
    resp = await api.client.post(
        "/api/vault/resolve-review", headers=fam["admin_h"],
        json={"thread_id": thread, "decision": "approve",
              "corrected_metadata": _corrected(**overrides)},
    )
    assert resp.status_code == 422
    assert fragment in resp.json()["error"]["message"]


async def test_resolve_review_approve_enqueues_resume(api: ApiHarness) -> None:
    fam = await _family(api)
    thread = await _pending_review(api, fam, "john_id")
    resp = await api.client.post(
        "/api/vault/resolve-review", headers=fam["admin_h"],
        json={"thread_id": thread, "decision": "approve", "corrected_metadata": _corrected()},
    )
    assert resp.status_code == 202
    assert resp.json() == {"thread_id": thread, "status": "approved"}
    name, (sent_thread, payload) = api.queue.sent[-1]
    assert name == RESUME_TASK and sent_thread == thread
    assert payload["decision"] == "approve"
    assert payload["corrected"]["expiration_date"] == "2030-06-30"
    assert payload["resolved_by"] == fam["admin_id"]

    again = await api.client.post("/api/vault/resolve-review", headers=fam["admin_h"],
                                  json={"thread_id": thread, "decision": "reject"})
    assert again.status_code == 409


async def test_resolve_review_access(api: ApiHarness) -> None:
    fam = await _family(api)
    thread = await _pending_review(api, fam, "john_id")
    bob = await api.client.post("/api/vault/resolve-review", headers=fam["bob_h"],
                                json={"thread_id": thread, "decision": "reject"})
    assert bob.status_code == 403
    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    eve = await api.client.post("/api/vault/resolve-review", headers=other_h,
                                json={"thread_id": thread, "decision": "reject"})
    assert eve.status_code == 404
    ok = await api.client.post("/api/vault/resolve-review", headers=fam["admin_h"],
                               json={"thread_id": thread, "decision": "reject"})
    assert ok.status_code == 202 and ok.json()["status"] == "rejected"


async def test_categories(api: ApiHarness) -> None:
    fam = await _family(api)
    created = await api.client.post(
        "/api/vault/categories", headers=fam["admin_h"],
        json={"category_slug": "vehicle", "category_display_name": "Vehicle"},
    )
    assert created.status_code == 201
    assert created.json()["is_baseline"] is False

    dup = await api.client.post("/api/vault/categories", headers=fam["admin_h"],
                                json={"category_slug": "vehicle",
                                      "category_display_name": "Again"})
    assert dup.status_code == 409
    bad = await api.client.post("/api/vault/categories", headers=fam["admin_h"],
                                json={"category_slug": "Vehicle Docs",
                                      "category_display_name": "x"})
    assert bad.status_code == 422
    member = await api.client.post("/api/vault/categories", headers=fam["bob_h"],
                                   json={"category_slug": "boats",
                                         "category_display_name": "Boats"})
    assert member.status_code == 403

    listed = (await api.client.get("/api/vault/categories", headers=fam["bob_h"])).json()
    slugs = [c["category_slug"] for c in listed]
    assert "vehicle" in slugs and len(slugs) == 8
    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    other = (await api.client.get("/api/vault/categories", headers=other_h)).json()
    assert "vehicle" not in [c["category_slug"] for c in other]

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from app.repositories import DocumentRepo, MemberRepo, NotFoundError, PendingReviewRepo
from tests.api_harness import ApiHarness

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("relationship", ["spouse", "child", "others"])
async def test_relationship_persists_without_changing_permissions(
    api: ApiHarness, relationship: str,
) -> None:
    headers, _ = await api.login("admin", email="admin@example.com", name="Admin")
    response = await api.client.post(
        "/api/members", headers=headers,
        json={"display_name": "Relative", "role": "dependent", "relationship": relationship},
    )
    assert response.status_code == 201
    member = response.json()
    assert member["role"] == "dependent"
    assert member["relationship"] == relationship
    roster = (await api.client.get("/api/me", headers=headers)).json()["members"]
    saved = next(item for item in roster if item["id"] == member["id"])
    assert saved["relationship"] == relationship
    updated = await api.client.put(
        f"/api/members/{member['id']}", headers=headers,
        json={"display_name": "Relative", "role": "dependent", "relationship": None},
    )
    assert updated.status_code == 200
    assert updated.json()["relationship"] is None


async def test_member_crud_persists_and_preserves_login(api: ApiHarness) -> None:
    admin, me = await api.login("admin", email="admin@example.com", name="Admin")
    created = await api.client.post(
        "/api/members", headers=admin,
        json={"display_name": "Bob", "role": "member", "email": "Bob@Example.com"},
    )
    assert created.status_code == 201
    member_id = created.json()["id"]
    bob, _ = await api.login("bob", email="bob@example.com")
    updated = await api.client.put(
        f"/api/members/{member_id}", headers=admin,
        json={"display_name": "Robert", "role": "admin", "email": "robert@example.com"},
    )
    assert updated.status_code == 200
    assert updated.json()["has_login"] is True
    assert updated.json()["email"] == "robert@example.com"
    assert (await api.client.get("/api/me", headers=bob)).json()["member"]["role"] == "admin"
    roster = (await api.client.get("/api/me", headers=admin)).json()["members"]
    assert any(m["display_name"] == "Robert" for m in roster)

    deleted = await api.client.delete(f"/api/members/{member_id}", headers=admin)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert await MemberRepo(api.pool).get(UUID(me["family"]["id"]), UUID(member_id)) is None
    assert (await api.client.get("/api/me", headers=admin)).json()["members"] == [me["member"]]


async def test_management_is_admin_only_and_family_scoped(api: ApiHarness) -> None:
    admin, _ = await api.login("admin", email="admin@example.com", name="Admin")
    created = await api.client.post(
        "/api/members", headers=admin,
        json={"display_name": "Bob", "role": "member", "email": "bob@example.com"},
    )
    member_id = created.json()["id"]
    bob, _ = await api.login("bob", email="bob@example.com")
    outsider, _ = await api.login("outsider", name="Other")
    for headers, expected in [(bob, 403), (outsider, 404)]:
        updated = await api.client.put(
            f"/api/members/{member_id}", headers=headers,
            json={"display_name": "Changed", "role": "member", "email": "bob@example.com"},
        )
        deleted = await api.client.delete(f"/api/members/{member_id}", headers=headers)
        assert updated.status_code == deleted.status_code == expected
    missing = await api.client.delete(f"/api/members/{uuid4()}", headers=admin)
    assert missing.status_code == 404
    assert (await api.client.get("/api/me", headers=bob)).json()["member"]["display_name"] == "Bob"


async def test_self_protection_and_linked_dependent_guard(api: ApiHarness) -> None:
    admin, me = await api.login("admin", email="admin@example.com", name="Admin")
    own_id = me["member"]["id"]
    assert (await api.client.delete(f"/api/members/{own_id}", headers=admin)).status_code == 409
    demotion = await api.client.put(
        f"/api/members/{own_id}", headers=admin,
        json={"display_name": "Admin", "role": "member", "email": "admin@example.com"},
    )
    assert demotion.status_code == 409
    renamed = await api.client.put(
        f"/api/members/{own_id}", headers=admin,
        json={"display_name": "Renamed admin", "role": "admin", "email": "admin@example.com"},
    )
    assert renamed.status_code == 200
    created = await api.client.post(
        "/api/members", headers=admin,
        json={"display_name": "Bob", "role": "member", "email": "bob@example.com"},
    )
    await api.login("bob", email="bob@example.com")
    result = await api.client.put(
        f"/api/members/{created.json()['id']}", headers=admin,
        json={"display_name": "Bob", "role": "dependent"},
    )
    assert result.status_code == 409
    assert "linked login" in result.json()["error"]["message"]


@pytest.mark.parametrize("payload", [
    {"display_name": "", "role": "dependent"},
    {"display_name": "B", "role": "owner"},
    {"display_name": "B", "role": "member"},
    {"display_name": "B", "role": "admin", "email": "not-an-email"},
    {"display_name": "B\nC", "role": "dependent"},
    {"display_name": "B", "role": "dependent", "family_id": str(uuid4())},
])
async def test_update_validates_payload(api: ApiHarness, payload: dict[str, str]) -> None:
    admin, me = await api.login("admin", email="admin@example.com", name="Admin")
    response = await api.client.put(
        f"/api/members/{me['member']['id']}", headers=admin, json=payload,
    )
    assert response.status_code == 422


async def test_duplicate_name_rolls_back(api: ApiHarness) -> None:
    admin, _ = await api.login("admin", email="admin@example.com", name="Admin")
    created = await api.client.post(
        "/api/members", headers=admin, json={"display_name": "Child"},
    )
    response = await api.client.put(
        f"/api/members/{created.json()['id']}", headers=admin,
        json={"display_name": "Admin", "role": "dependent"},
    )
    assert response.status_code == 409
    roster = (await api.client.get("/api/me", headers=admin)).json()["members"]
    assert {m["display_name"] for m in roster} == {"Admin", "Child"}


@pytest.mark.parametrize("record_type", ["document", "review"])
async def test_delete_blocks_owned_records(api: ApiHarness, record_type: str) -> None:
    admin, me = await api.login("admin", name="Admin")
    created = await api.client.post(
        "/api/members", headers=admin, json={"display_name": "Child"},
    )
    member_id = UUID(created.json()["id"])
    family_id = UUID(me["family"]["id"])
    if record_type == "document":
        await DocumentRepo(api.pool).create(
            family_id, family_member_id=member_id, category_slug="miscellaneous",
            document_name="Document", source="upload",
        )
    else:
        await PendingReviewRepo(api.pool).create(
            family_id, thread_id="review-only", family_member_id=member_id,
            original_filename="id.jpg", raw_text="ID", suggested_metadata={},
            confidence_score=0.5, review_reasons=["low_confidence"],
        )
    response = await api.client.delete(f"/api/members/{member_id}", headers=admin)
    assert response.status_code == 409
    assert "owns documents or review records" in response.json()["error"]["message"]
    assert await MemberRepo(api.pool).get(family_id, member_id) is not None
    query = (
        "SELECT count(*) FROM documents" if record_type == "document"
        else "SELECT count(*) FROM pending_reviews"
    )
    assert await api.pool.fetchval(query) == 1


async def test_concurrent_upload_and_delete_do_not_cascade_documents(api: ApiHarness) -> None:
    admin, me = await api.login("admin", name="Admin")
    created = await api.client.post(
        "/api/members", headers=admin, json={"display_name": "Child"},
    )
    family_id, member_id = UUID(me["family"]["id"]), UUID(created.json()["id"])

    async def upload() -> bool:
        try:
            await DocumentRepo(api.pool).create(
                family_id, family_member_id=member_id, category_slug="miscellaneous",
                document_name="Racing upload", source="upload",
            )
            return True
        except NotFoundError:
            return False

    deleted, uploaded = await asyncio.gather(
        api.client.delete(f"/api/members/{member_id}", headers=admin), upload(),
    )
    assert deleted.status_code == (409 if uploaded else 204)
    assert await api.pool.fetchval("SELECT count(*) FROM documents") == int(uploaded)


async def test_put_cors_preflight(api: ApiHarness) -> None:
    response = await api.client.options(
        f"/api/members/{uuid4()}",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


async def test_concurrent_admin_deletions_preserve_an_admin(api: ApiHarness) -> None:
    ann, me = await api.login("ann", email="ann@example.com", name="Ann")
    created = await api.client.post(
        "/api/members", headers=ann,
        json={"display_name": "Bob", "role": "admin", "email": "bob@example.com"},
    )
    bob, _ = await api.login("bob", email="bob@example.com")
    responses = await asyncio.gather(
        api.client.delete(f"/api/members/{created.json()['id']}", headers=ann),
        api.client.delete(f"/api/members/{me['member']['id']}", headers=bob),
    )
    assert sorted(r.status_code for r in responses) in ([204, 403], [204, 404])
    assert len(await MemberRepo(api.pool).list_admins(UUID(me["family"]["id"]))) == 1

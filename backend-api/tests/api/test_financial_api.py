from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from openpyxl import Workbook, load_workbook

from app.financial import DATE_HEADERS, HEADERS
from app.ratelimit import limiter
from tests.api_harness import ApiHarness

pytestmark = pytest.mark.integration


def entry(name: str = "Ann", **changes: Any) -> dict[str, Any]:
    return {
        "person_name": name, "category": "income", "subcategory": "Salary",
        "amount": "1234.56", "currency": "SGD", "details": "Monthly salary reference PAY-42",
        **changes,
    }


def excel(rows: list[tuple[Any, ...]], headers: tuple[str, ...] = HEADERS) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Entries"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


async def test_table_stores_exact_amount_currency_and_details_for_search(api: ApiHarness) -> None:
    headers, _ = await api.login("ann", name="Ann")
    response = await api.client.post(
        "/api/vault/financial/entries", headers=headers, json={"rows": [
            entry(), entry(category="expense", subcategory="Groceries", amount="29.99",
                           currency="USD", details="Weekly grocery shop"),
        ]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 2
    assert response.json()["rejected"] == 0
    rows = await api.pool.fetch("SELECT * FROM documents ORDER BY category_slug")
    assert rows[0]["financial_value"] == Decimal("29.99")
    assert rows[0]["currency"] == "USD"
    assert rows[0]["financial_subcategory"] == "Groceries"
    assert rows[0]["document_details"] == "Weekly grocery shop"
    assert rows[1]["financial_value"] == Decimal("1234.56")
    assert rows[1]["currency"] == "SGD"
    metadata = [record["metadata"] for record in api.index.namespaces["family-vault"].values()]
    assert all(meta["currency"] in ("SGD", "USD") for meta in metadata)
    assert any("Currency: SGD" in meta["text"] and "PAY-42" in meta["text"] for meta in metadata)
    # Financial records do not change the uploaded-original maintenance list.
    history = await api.client.get("/api/vault/uploads", headers=headers)
    assert history.json() == []
    chat = await api.client.post(
        "/api/vault/chat", headers=headers, json={"message": "Ann salary income details"},
    )
    assert "Ann - Salary" in chat.text


@pytest.mark.parametrize("changes", [
    {"amount": "0"}, {"amount": "-1"}, {"amount": "1.234"},
    {"amount": "1000000000000"}, {"amount": "NaN"}, {"amount": "Infinity"},
    {"currency": "XYZ"}, {"category": "medical"}, {"details": ""},
    {"subcategory": ""}, {"details": "x" * 2001},
])
async def test_invalid_financial_entries_are_not_saved(
    api: ApiHarness, changes: dict[str, str],
) -> None:
    headers, _ = await api.login("ann", name="Ann")
    response = await api.client.post(
        "/api/vault/financial/entries", headers=headers, json={"rows": [entry(**changes)]},
    )
    assert response.status_code == 422
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 0


async def test_owner_access_and_unique_names(api: ApiHarness) -> None:
    admin, _ = await api.login("ann", name="Ann", email="ann@example.com")
    await api.client.post("/api/members", headers=admin, json={
        "display_name": "Bob", "role": "member", "email": "bob@example.com",
    })
    bob, _ = await api.login("bob", name="Bob", email="bob@example.com")
    response = await api.client.post(
        "/api/vault/financial/entries", headers=bob, json={"rows": [entry(), entry("Bob")]},
    )
    assert response.json()["accepted"] == response.json()["rejected"] == 1
    other, _ = await api.login("outsider", name="Other")
    response = await api.client.post(
        "/api/vault/financial/entries", headers=other, json={"rows": [entry()]},
    )
    assert response.json()["accepted"] == 0
    duplicate = await api.client.post("/api/members", headers=admin, json={
        "display_name": "Bob", "role": "dependent",
    })
    assert duplicate.status_code == 409
    response = await api.client.post(
        "/api/vault/financial/entries", headers=admin, json={"rows": [entry("Bob")]},
    )
    assert response.json()["accepted"] == 1


async def test_excel_template_is_valid_and_can_be_filled_and_uploaded(api: ApiHarness) -> None:
    headers, _ = await api.login("ann", name="Ann")
    await api.pool.execute("UPDATE families SET currency = 'NOK'")
    template = await api.client.get("/api/vault/financial/template", headers=headers)
    assert template.status_code == 200
    assert "spreadsheetml.sheet" in template.headers["content-type"]
    assert "famguard-income-expense.xlsx" in template.headers["content-disposition"]
    workbook = load_workbook(io.BytesIO(template.content))
    assert tuple(cell.value for cell in workbook["Entries"][1]) == HEADERS + DATE_HEADERS
    assert workbook["Entries"]["A2"].value == "Ann"
    assert workbook["Entries"]["E2"].value == "NOK"
    assert workbook["Lists"].sheet_state == "hidden"
    assert len(workbook["Entries"].data_validations.dataValidation) == 5
    workbook["Entries"]["F2"] = "Filled salary details"
    workbook["Entries"]["E2"] = "SGD"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    response = await api.client.post(
        "/api/vault/financial/excel", headers=headers,
        files={"file": ("filled.xlsx", output.getvalue())},
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 2
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 2


async def test_excel_reports_row_errors_without_importing_invalid_rows(api: ApiHarness) -> None:
    headers, _ = await api.login("ann", name="Ann")
    data = excel([
        ("Ann", "income", "Salary", 100, "SGD", "Monthly salary"),
        ("Ann", "expense", "Groceries", "=1+2", "SGD", "Formula not permitted"),
        ("Ann", "expense", "Groceries", 12.345, "SGD", "Too many decimal places"),
        ("Ann", "expense", "Groceries", 20, "XXX", "Unsupported currency"),
        ("Foreign person", "expense", "Groceries", 20, "USD", "Not a family member"),
    ])
    response = await api.client.post(
        "/api/vault/financial/excel", headers=headers, files={"file": ("data.xlsx", data)},
    )
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 4
    assert [error["row"] for error in body["errors"]] == [3, 4, 5, 6]
    assert "Formulas" in body["errors"][0]["reason"]
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 1


async def test_excel_headers_empty_files_limits_and_auth(api: ApiHarness) -> None:
    assert (await api.client.get("/api/vault/financial/template")).status_code == 401
    headers, _ = await api.login("ann", name="Ann")
    for filename, data, expected in (
        ("data.csv", b"bad", 415),
        ("data.xlsx", b"", 422),
        ("data.xlsx", b"not a workbook", 422),
        ("data.xlsx", excel([], ("Wrong",)), 422),
        ("data.xlsx", excel([]), 422),
        ("data.xlsx", b"x" * (5 * 1024 * 1024 + 1), 413),
        ("data.xlsx", excel([("Ann", "income", "Salary", 1, "SGD", "Entry")] * 5001), 422),
    ):
        limiter.reset()
        response = await api.client.post(
            "/api/vault/financial/excel", headers=headers, files={"file": (filename, data)},
        )
        assert response.status_code == expected, response.text
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 0


async def test_vector_failure_is_reported_and_records_rolled_back(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, _ = await api.login("ann", name="Ann")
    monkeypatch.setattr(api.vectors, "upsert", AsyncMock(side_effect=RuntimeError("offline")))
    response = await api.client.post(
        "/api/vault/financial/entries", headers=headers, json={"rows": [entry()]},
    )
    assert response.json()["accepted"] == 0
    assert response.json()["rejected"] == 1
    assert "Vector store unavailable" in response.json()["errors"][0]["reason"]
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 0


async def test_finance_dashboard_dates_options_and_tenant_scoping(api: ApiHarness) -> None:
    admin, me = await api.login("ann", name="Ann", email="ann@example.com")
    response = await api.client.get("/api/vault/financial/dashboard", headers=admin)
    today = date.fromisoformat(response.json()["today"])
    previous = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    previous_due = previous.replace(day=min(today.day, (
        today.replace(day=1) - timedelta(days=1)
    ).day))
    await api.client.post("/api/members", headers=admin, json={
        "display_name": "Bob", "role": "member", "email": "bob@example.com",
    })
    response = await api.client.post("/api/vault/financial/entries", headers=admin, json={"rows": [
        entry(amount="300", transaction_date=str(previous),
              subcategory="Consulting", currency="NOK", payment_due_date=str(previous_due)),
        entry(amount="200", transaction_date=str(today), subcategory="Salary"),
        entry(amount="25", category="expense", subcategory="Power bill",
              payment_due_date=str(today + timedelta(days=30)), is_paid=False),
        entry(amount="50", details="Undated record"),
        entry("Bob", amount="888", transaction_date=str(previous), subcategory="Bob private",
              payment_due_date=str(previous_due)),
    ]})
    assert response.json()["accepted"] == 5
    options = (await api.client.get("/api/vault/financial/options", headers=admin)).json()
    assert "Consulting" in options["subcategories"]["income"]
    assert "NOK" in options["currencies"]
    summary = (await api.client.get("/api/vault/financial/dashboard", headers=admin)).json()
    assert summary["undated_count"] == 2  # unpaid bill and old entry
    assert len(summary["upcoming_bills"]) == 1
    assert summary["upcoming_bills"][0]["subcategory"] == "Power bill"
    assert {row["person_name"] for row in summary["expected_cashflows"]} == {"Ann", "Bob"}
    consulting = next(row for row in summary["expected_cashflows"]
                      if row["subcategory"] == "Consulting")
    assert consulting["previous_due_date"] == str(previous_due)
    assert Decimal(consulting["amount"]) == Decimal("300")
    assert consulting["currency"] == "NOK"
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 5
    repeated = (await api.client.get("/api/vault/financial/dashboard", headers=admin)).json()
    assert repeated["expected_cashflows"] == summary["expected_cashflows"]
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 5
    bob, _ = await api.login("bob", name="Bob", email="bob@example.com")
    own = (await api.client.get("/api/vault/financial/dashboard", headers=bob)).json()
    assert own["undated_count"] == 0 and own["upcoming_bills"] == []
    assert {row["person_name"] for row in own["expected_cashflows"]} == {"Bob"}
    own_options = (await api.client.get("/api/vault/financial/options", headers=bob)).json()
    assert "Consulting" not in own_options["subcategories"]["income"]
    undated = next(row for row in summary["undated"] if row["details"] == "Undated record")
    url = f"/api/vault/financial/entries/{undated['id']}/date"
    denied = await api.client.put(url, headers=bob, json={"transaction_date": str(today)})
    assert denied.status_code == 403
    outsider, _ = await api.login("outsider", name="Other")
    external = (await api.client.get("/api/vault/financial/dashboard", headers=outsider)).json()
    assert external["expected_cashflows"] == []
    denied = await api.client.put(url, headers=outsider, json={"transaction_date": str(today)})
    assert denied.status_code == 404
    corrected = await api.client.put(url, headers=admin, json={"transaction_date": str(today)})
    assert corrected.status_code == 204
    summary = (await api.client.get("/api/vault/financial/dashboard", headers=admin)).json()
    assert summary["undated_count"] == 1
    current_sgd = next(row for row in summary["months"]
                       if row["view"] == "current" and row["currency"] == "SGD")
    assert Decimal(current_sgd["income"]) == Decimal("250")
    assert me["family"]["id"]


async def test_unpaid_bill_requires_expense_and_date_and_excel_dates_round_trip(
    api: ApiHarness,
) -> None:
    headers, _ = await api.login("ann", name="Ann")
    for invalid in (
        entry(is_paid=False),
        entry(is_paid=False, payment_due_date="2026-12-01"),
        entry(category="expense", transaction_date="not-a-date"),
    ):
        response = await api.client.post(
            "/api/vault/financial/entries", headers=headers, json={"rows": [invalid]},
        )
        assert response.status_code == 422
    data = excel([
        ("Ann", "expense", "Rent", 1200, "SGD", "Next rent",
         date(2026, 11, 9), date(2026, 12, 1), False),
    ], HEADERS + DATE_HEADERS)
    response = await api.client.post(
        "/api/vault/financial/excel", headers=headers, files={"file": ("dated.xlsx", data)},
    )
    assert response.json()["accepted"] == 1
    row = await api.pool.fetchrow(
        "SELECT transaction_date, payment_due_date, is_paid FROM documents",
    )
    assert row is not None
    assert row["transaction_date"] == date(2026, 11, 9)
    assert row["payment_due_date"] == date(2026, 12, 1)
    assert row["is_paid"] is False


async def test_financial_maintenance_filters_updates_search_and_deletes(api: ApiHarness) -> None:
    headers, _ = await api.login("ann", name="Ann")
    await api.client.post("/api/vault/financial/entries", headers=headers, json={"rows": [
        entry(category="expense", subcategory="Rent", amount="125.50",
              transaction_date="2026-10-10", payment_due_date="2026-10-20", is_paid=False),
        entry(category="expense", subcategory="Rent", currency="USD", amount="80",
              transaction_date="2026-10-12", payment_due_date="2026-10-21"),
        entry(subcategory="Salary"),
    ]})
    empty = await api.client.get("/api/vault/financial/entries", headers=headers)
    assert empty.json() == {"entries": [], "has_next": False}
    filters = {
        "person_name": "Ann", "category": "expense", "subcategory": "rent", "currency": "SGD",
        "transaction_from": "2026-10-10", "transaction_to": "2026-10-10",
        "due_from": "2026-10-20", "due_to": "2026-10-20", "is_paid": "false",
    }
    response = await api.client.get("/api/vault/financial/entries",
                                    headers=headers, params=filters)
    assert response.status_code == 200, response.text
    assert len(response.json()["entries"]) == 1
    original = response.json()["entries"][0]
    doc_id = original["id"]
    changed = entry(category="expense", subcategory="School fee", amount="99.99",
                    currency="NOK", details="Corrected fee NEW-FEE-42",
                    transaction_date="2026-10-15", payment_due_date="2026-11-15", is_paid=True)
    updated = await api.client.put(f"/api/vault/financial/entries/{doc_id}",
                                   headers=headers, json=changed)
    assert updated.status_code == 204, updated.text
    saved = await api.pool.fetchrow("SELECT * FROM documents WHERE id = $1", UUID(doc_id))
    assert saved is not None
    assert saved["financial_value"] == Decimal("99.99")
    assert saved["currency"] == "NOK" and saved["financial_subcategory"] == "School fee"
    assert saved["document_details"] == "Corrected fee NEW-FEE-42"
    assert saved["transaction_date"] == date(2026, 10, 15)
    assert saved["is_paid"] is True and saved["index_version"]
    index_records = [record["metadata"] for record in api.index.namespaces["family-vault"].values()
                     if record["metadata"]["document_id"] == doc_id]
    assert any(meta.get("index_version") == saved["index_version"] and "NEW-FEE-42" in meta["text"]
               for meta in index_records)
    matches = await api.client.get("/api/vault/financial/entries", headers=headers, params=filters)
    assert matches.json()["entries"] == []
    result = await api.client.get("/api/vault/financial/entries",
                                  headers=headers, params={"subcategory": "School fee"})
    assert result.json()["entries"][0]["id"] == doc_id
    deleted = await api.client.delete(f"/api/vault/documents/{doc_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 2
    assert all(record["metadata"]["document_id"] != doc_id
               for record in api.index.namespaces["family-vault"].values())


async def test_financial_maintenance_authorization_validation_and_pagination(
    api: ApiHarness,
) -> None:
    admin, _ = await api.login("ann", name="Ann", email="ann@example.com")
    await api.client.post("/api/members", headers=admin, json={
        "display_name": "Bob", "role": "member", "email": "bob@example.com",
    })
    imported = await api.client.post("/api/vault/financial/entries", headers=admin,
                                     json={"rows": [entry()] * 51 + [entry("Bob")]})
    assert imported.json()["accepted"] == 52
    response = await api.client.get("/api/vault/financial/entries",
                                    headers=admin, params={"person_name": "Ann"})
    assert len(response.json()["entries"]) == 50 and response.json()["has_next"]
    doc_id = response.json()["entries"][0]["id"]
    last = await api.client.get("/api/vault/financial/entries",
                                headers=admin, params={"person_name": "Ann", "offset": 50})
    assert len(last.json()["entries"]) == 1 and not last.json()["has_next"]
    bob, _ = await api.login("bob", name="Bob", email="bob@example.com")
    own = await api.client.get("/api/vault/financial/entries",
                               headers=bob, params={"category": "income"})
    assert {row["person_name"] for row in own.json()["entries"]} == {"Bob"}
    denied = await api.client.put(f"/api/vault/financial/entries/{doc_id}",
                                  headers=bob, json=entry("Bob"))
    assert denied.status_code == 403
    outsider, _ = await api.login("outsider", name="Other")
    external = await api.client.get("/api/vault/financial/entries",
                                    headers=outsider, params={"category": "income"})
    assert external.json()["entries"] == []
    denied = await api.client.put(f"/api/vault/financial/entries/{doc_id}",
                                  headers=outsider, json=entry("Other"))
    assert denied.status_code == 404
    invalid = await api.client.put(f"/api/vault/financial/entries/{doc_id}",
                                   headers=admin, json=entry(amount="0"))
    assert invalid.status_code == 422
    invalid = await api.client.get("/api/vault/financial/entries", headers=admin,
                                   params={"transaction_from": "2026-11-20",
                                           "transaction_to": "2026-11-01"})
    assert invalid.status_code == 422
    assert (await api.client.get("/api/vault/financial/entries",
                                params={"category": "income"})).status_code == 401


async def test_financial_update_failure_keeps_live_record_and_search_version(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, _ = await api.login("ann", name="Ann")
    await api.client.post("/api/vault/financial/entries", headers=headers, json={"rows": [entry()]})
    doc_id = await api.pool.fetchval("SELECT id FROM documents")
    before = dict(await api.pool.fetchrow("SELECT * FROM documents WHERE id = $1", doc_id))
    original_upsert = api.vectors.upsert

    async def partial_upsert(records: Any) -> None:
        await original_upsert(records)
        raise RuntimeError("connection lost after partial upsert")

    monkeypatch.setattr(api.vectors, "upsert", partial_upsert)
    response = await api.client.put(f"/api/vault/financial/entries/{doc_id}",
                                    headers=headers, json=entry(details="INVALID-UPDATED-CONTEXT"))
    assert response.status_code == 503
    assert dict(await api.pool.fetchrow("SELECT * FROM documents WHERE id = $1", doc_id)) == before
    chat = await api.client.post("/api/vault/chat", headers=headers,
                                 json={"message": "Ann salary income details"})
    assert "INVALID-UPDATED-CONTEXT" not in chat.text
    assert "Ann - Salary" in chat.text


async def test_dashboard_collapses_duplicate_history_without_deleting_data(api: ApiHarness) -> None:
    headers, _ = await api.login("ann", name="Ann")
    today = date.fromisoformat(
        (await api.client.get("/api/vault/financial/dashboard", headers=headers)).json()["today"],
    )
    previous = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    prior_due = previous.replace(day=min(today.day, (
        today.replace(day=1) - timedelta(days=1)
    ).day))
    duplicate = entry(category="expense", subcategory="Utilities", amount="25.50",
                      details="Monthly electricity", transaction_date=str(previous),
                      payment_due_date=str(prior_due))
    imported = await api.client.post("/api/vault/financial/entries", headers=headers,
                                     json={"rows": [duplicate] * 3})
    assert imported.json()["accepted"] == 3
    summary = (await api.client.get("/api/vault/financial/dashboard", headers=headers)).json()
    due_dates = [row["payment_due_date"] for row in summary["expected_cashflows"]]
    assert due_dates and len(due_dates) == len(set(due_dates))
    assert await api.pool.fetchval("SELECT COUNT(*) FROM documents") == 3
    past = next(month for month in summary["months"]
                if month["month"] == previous.strftime("%Y-%m"))
    assert Decimal(past["expense"]) == Decimal("76.50")

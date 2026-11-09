from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.financial_dashboard import FinanceRecord, month_start, summarize


def record(amount: str, when: date | None, **values: object) -> FinanceRecord:
    return FinanceRecord.model_validate({
        "id": uuid4(), "person_name": "Ann", "category": "income", "subcategory": "Salary",
        "amount": amount, "currency": "SGD", "details": "Entry", "transaction_date": when,
        "payment_due_date": None, "is_paid": True, **values,
    })


def test_month_rollover_and_zero_month_average() -> None:
    today = date(2026, 1, 15)
    assert month_start(today, -1) == date(2025, 12, 1)
    assert month_start(date(2026, 12, 20), 1) == date(2027, 1, 1)
    summary = summarize([
        record("300", date(2025, 10, 5)),
        record("600", date(2025, 12, 5)),
        record("150", date(2025, 12, 5), category="expense"),
        record("999", date(2026, 1, 1)),
        record("9999", date(2026, 1, 20)),
        record("99", None),
        record("9000", date(2025, 11, 1), category="expense", is_paid=False,
               payment_due_date=date(2026, 1, 20)),
    ], today)
    projected = next(row for row in summary.months if row.month == "2026-02")
    assert projected.sample_months == 3  # October, zero November, December
    assert projected.income == Decimal("300.00")
    assert projected.expense == Decimal("50.00")
    assert projected.net == Decimal("250.00")
    current = next(row for row in summary.months if row.view == "current")
    assert current.income == Decimal("999")
    assert summary.undated_count == 1


def test_currencies_and_insufficient_history_are_separate() -> None:
    summary = summarize([
        record("100", date(2026, 10, 1)),
        record("200", date(2026, 10, 1), currency="USD"),
        record("300", None, currency="NOK"),
    ], date(2026, 11, 9))
    projection = {row.currency: row for row in summary.months if row.month == "2026-12"}
    assert projection["SGD"].income == Decimal("100")
    assert projection["USD"].income == Decimal("200")
    assert projection["NOK"].income is None
    assert projection["NOK"].sample_months == 0


def test_projection_caps_history_at_six_months_and_rounds_exact_amounts() -> None:
    summary = summarize([
        record("9000", date(2025, 6, 1)),
        record("100", date(2025, 7, 1)),
        record("1", date(2025, 12, 1)),
    ], date(2026, 1, 15))
    assert len([row for row in summary.months if row.view == "past"]) == 6
    assert len([row for row in summary.months if row.view == "current"]) == 1
    projected = [row for row in summary.months if row.view == "projected"]
    assert len(projected) == 3
    assert all(row.sample_months == 6 and row.income == Decimal("16.83")
               and row.expense == Decimal("0.00") and row.net == Decimal("16.83")
               for row in projected)


def test_bill_window_includes_today_and_day_30_only_for_unpaid_expenses() -> None:
    today = date(2026, 11, 9)
    rows = [
        record("20", None, category="expense", is_paid=False, payment_due_date=when)
        for when in (date(2026, 11, 8), today, date(2026, 12, 9), date(2026, 12, 10))
    ]
    rows.append(record("100", None, category="expense", payment_due_date=today))
    summary = summarize(rows, today)
    assert [row.payment_due_date for row in summary.upcoming_bills] == [today, date(2026, 12, 9)]


def test_expected_cashflows_repeat_previous_due_month_and_preserve_originals() -> None:
    rows = [
        record("2000.25", date(2026, 10, 1), payment_due_date=date(2026, 10, 9)),
        record("100", date(2026, 10, 1), category="expense", subcategory="Rent",
               currency="NOK", is_paid=False, payment_due_date=date(2026, 10, 20)),
        record("200", date(2026, 10, 1), payment_due_date=date(2026, 10, 8)),
        record("300", date(2026, 10, 1), payment_due_date=date(2026, 10, 10)),
        record("400", date(2026, 9, 1), payment_due_date=date(2026, 10, 20)),
        record("500", None),
    ]
    before = [row.model_dump() for row in rows]
    summary = summarize(rows, date(2026, 11, 9))
    expected = summary.expected_cashflows
    assert [(item.payment_due_date, item.amount) for item in expected] == [
        (date(2026, 11, 9), Decimal("2000.25")),
        (date(2026, 11, 10), Decimal("300")),
        (date(2026, 11, 20), Decimal("100")),
        (date(2026, 12, 8), Decimal("200")),
        (date(2026, 12, 9), Decimal("2000.25")),
    ]
    expense = next(item for item in expected if item.category == "expense")
    assert expense.previous_due_date == date(2026, 10, 20)
    assert expense.currency == "NOK" and expense.source_id == rows[1].id
    assert summary.upcoming_bills == []
    assert all(month.income == 0 and month.expense == 0
               for month in summary.months if month.view == "current")
    assert [row.model_dump() for row in rows] == before
    assert summarize(rows, date(2026, 11, 9)) == summary


@pytest.mark.parametrize(("today", "prior", "expected"), [
    (date(2027, 2, 1), date(2027, 1, 31), [date(2027, 2, 28)]),
    (date(2028, 2, 1), date(2028, 1, 31), [date(2028, 2, 29)]),
    (date(2027, 1, 1), date(2026, 12, 31), [date(2027, 1, 31)]),
    (date(2027, 2, 1), date(2027, 1, 1), [date(2027, 2, 1), date(2027, 3, 1)]),
])
def test_expected_due_dates_handle_years_short_months_and_inclusive_window(
    today: date, prior: date, expected: list[date],
) -> None:
    summary = summarize([record("1", prior, payment_due_date=prior)], today)
    assert [item.payment_due_date for item in summary.expected_cashflows] == expected


def test_recorded_occurrences_replace_estimates_one_for_one_paid_or_unpaid() -> None:
    previous = [
        record("100", date(2026, 10, 1), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), details="First rental property"),
        record("100", date(2026, 10, 1), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), details="Second rental property"),
    ]
    current = record("125", None, category="expense", subcategory="RENT",
                     payment_due_date=date(2026, 11, 20))
    summary = summarize([*previous, current], date(2026, 11, 9))
    assert len(summary.expected_cashflows) == 1
    current.is_paid = False
    summary = summarize([*previous, current], date(2026, 11, 9))
    assert len(summary.expected_cashflows) == 1
    assert summary.upcoming_bills == [current]
    current.currency = "USD"
    assert len(summarize([*previous, current], date(2026, 11, 9)).expected_cashflows) == 2


def test_transaction_history_uses_due_day_or_transaction_day_and_ignores_undated() -> None:
    rows = [
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 11, 20)),
        record("200", date(2026, 10, 15), category="expense", subcategory="Power"),
        record("300", None, payment_due_date=date(2026, 10, 25)),
    ]
    summary = summarize(rows, date(2026, 11, 9))
    assert [(item.subcategory, item.payment_due_date) for item in summary.expected_cashflows] == [
        ("Power", date(2026, 11, 15)), ("Rent", date(2026, 11, 20)),
    ]
    rows[0].payment_due_date = date(2026, 9, 20)
    expected = summarize(rows, date(2026, 11, 9)).expected_cashflows
    assert [(item.subcategory, item.payment_due_date) for item in expected] == [
        ("Power", date(2026, 11, 15)), ("Rent", date(2026, 11, 20)),
    ]


def test_identical_history_collapses_estimates_without_changing_records_or_actuals() -> None:
    rows = [
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), details="Monthly rent")
        for _ in range(3)
    ]
    before = [row.model_dump() for row in rows]
    summary = summarize(rows, date(2026, 11, 9))
    assert len(summary.expected_cashflows) == 1
    assert summary.expected_cashflows[0].amount == Decimal("100")
    october = next(month for month in summary.months if month.month == "2026-10")
    assert october.expense == Decimal("300")
    assert [row.model_dump() for row in rows] == before
    rows.extend([
        record("101", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), details="Monthly rent"),
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 21), details="Monthly rent"),
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), details="Different property"),
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), currency="NOK", details="Monthly rent"),
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 10, 20), person_name="Bob", details="Monthly rent"),
    ])
    assert len(summarize(rows, date(2026, 11, 9)).expected_cashflows) == 6


def test_unpaid_bill_from_previous_transaction_month_is_not_also_estimated() -> None:
    rows = [
        record("100", date(2026, 10, 10), category="expense", subcategory="Rent",
               payment_due_date=date(2026, 11, 20), is_paid=False),
    ]
    summary = summarize(rows, date(2026, 11, 9))
    assert summary.upcoming_bills == rows
    assert summary.expected_cashflows == []

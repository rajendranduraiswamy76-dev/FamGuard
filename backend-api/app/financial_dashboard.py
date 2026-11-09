"""Currency-separated monthly summaries and explicitly estimated projections."""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class MonthlyFinance(BaseModel):
    month: str
    view: Literal["past", "current", "projected"]
    currency: str
    income: Decimal | None
    expense: Decimal | None
    net: Decimal | None
    sample_months: int


class FinanceRecord(BaseModel):
    id: UUID
    person_name: str
    category: str
    subcategory: str
    amount: Decimal
    currency: str
    details: str
    transaction_date: date | None
    payment_due_date: date | None
    is_paid: bool


class ExpectedCashflow(BaseModel):
    source_id: UUID
    person_name: str
    category: Literal["income", "expense"]
    subcategory: str
    amount: Decimal
    currency: str
    details: str
    payment_due_date: date
    previous_due_date: date


class FinanceDashboard(BaseModel):
    today: date
    months: list[MonthlyFinance]
    upcoming_bills: list[FinanceRecord]
    expected_cashflows: list[ExpectedCashflow]
    undated: list[FinanceRecord]
    undated_count: int


class FinanceOptions(BaseModel):
    subcategories: dict[str, list[str]]
    currencies: list[str]


class FinancialPage(BaseModel):
    entries: list[FinanceRecord]
    has_next: bool


class DateCorrection(BaseModel):
    transaction_date: date


def month_start(today: date, offset: int = 0) -> date:
    ordinal = today.year * 12 + today.month - 1 + offset
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def expected_cashflows(rows: list[FinanceRecord], today: date) -> list[ExpectedCashflow]:
    previous, current = month_start(today, -1), month_start(today)
    end = today + timedelta(days=30)
    recorded = Counter(
        (row.person_name, row.category, row.subcategory.casefold(), row.currency,
         row.payment_due_date)
        for row in rows if row.payment_due_date is not None and row.payment_due_date >= current
        and (not row.is_paid or row.transaction_date is None
             or not previous <= row.transaction_date < current)
    )
    expected = []
    seen: set[tuple[str, str, str, str, date, Decimal, str]] = set()
    for row in rows:
        when = row.transaction_date
        if (when is None or not previous <= when < current
                or row.category not in ("income", "expense")):
            continue
        due = row.payment_due_date or when
        offset = 0
        while (month := month_start(today, offset)) <= end:
            next_due = month.replace(day=min(due.day, monthrange(month.year, month.month)[1]))
            offset += 1
            if not today <= next_due <= end:
                continue
            key = (
                row.person_name, row.category, row.subcategory.casefold(), row.currency, next_due,
            )
            fingerprint = (*key, row.amount, row.details.strip())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            # One recorded occurrence replaces one estimate, even if its amount changed.
            if recorded[key]:
                recorded[key] -= 1
                continue
            expected.append(ExpectedCashflow(
                source_id=row.id, person_name=row.person_name, category=row.category,
                subcategory=row.subcategory, amount=row.amount, currency=row.currency,
                details=row.details, payment_due_date=next_due, previous_due_date=due,
            ))
    return sorted(expected, key=lambda item: (item.payment_due_date, item.category,
                                             item.person_name, str(item.source_id)))


def summarize(rows: list[FinanceRecord], today: date) -> FinanceDashboard:
    currencies = sorted({row.currency for row in rows})
    totals: dict[tuple[str, date, str], Decimal] = {}
    first: dict[str, date] = {}
    current = month_start(today)
    for row in rows:
        if row.transaction_date is None or not row.is_paid or row.transaction_date > today:
            continue
        month = month_start(row.transaction_date)
        key = (row.currency, month, row.category)
        totals[key] = totals.get(key, Decimal(0)) + row.amount
        if month < current:
            first[row.currency] = min(first.get(row.currency, month), month)
    monthly = []
    for offset in range(-6, 4):
        month = month_start(today, offset)
        for currency in currencies:
            samples = [
                month_start(today, shift) for shift in range(-6, 0)
                if currency in first and month_start(today, shift) >= first[currency]
            ]
            values: dict[str, Decimal | None] = {}
            for category in ("income", "expense"):
                if offset > 0:
                    values[category] = (
                        (sum((totals.get((currency, sample, category), Decimal(0))
                              for sample in samples), Decimal(0)) / len(samples)).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP,
                        ) if samples else None
                    )
                else:
                    values[category] = totals.get((currency, month, category), Decimal(0))
            income, expense = values["income"], values["expense"]
            monthly.append(MonthlyFinance(
                month=month.strftime("%Y-%m"),
                view="projected" if offset > 0 else "current" if offset == 0 else "past",
                currency=currency, income=income, expense=expense,
                net=income - expense if income is not None and expense is not None else None,
                sample_months=len(samples) if offset > 0 else 0,
            ))
    bills = sorted(
        (row for row in rows if row.category == "expense" and not row.is_paid
         and row.payment_due_date is not None
         and today <= row.payment_due_date <= today + timedelta(days=30)),
        key=lambda row: (row.payment_due_date or today, str(row.id)),
    )
    undated = [row for row in rows if row.transaction_date is None]
    return FinanceDashboard(
        today=today, months=monthly, upcoming_bills=bills,
        expected_cashflows=expected_cashflows(rows, today),
        undated=undated[:50], undated_count=len(undated),
    )


def records(raw: list[Any]) -> list[FinanceRecord]:
    return [FinanceRecord.model_validate(dict(row)) for row in raw]

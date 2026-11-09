from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.state import HITLState, UnifiedVaultExtraction, parse_iso_date

BASE: dict[str, Any] = {
    "category": "IdentityCards ",
    "document_name": "John Passport",
    "requires_alert": True,
    "extracted_text": "PASSPORT ...",
    "financial_value": None,
    "confidence_score": 0.9,
}


def test_valid_date_roundtrip() -> None:
    ex = UnifiedVaultExtraction.model_validate({**BASE, "expiration_date": "2031-05-04"})
    assert ex.expiration_date == date(2031, 5, 4)
    assert ex.category == "identitycards"
    assert not ex.expiration_unparsed
    assert json.loads(ex.model_dump_json())["expiration_date"] == "2031-05-04"


@pytest.mark.parametrize("bad", ["2027-02-30", "04/05/2031", "2031-5-4", "20310504", "soon"])
def test_unparseable_date_becomes_none_and_is_flagged(bad: str) -> None:
    ex = UnifiedVaultExtraction.model_validate({**BASE, "expiration_date": bad})
    assert ex.expiration_date is None
    assert ex.expiration_unparsed
    assert ex.expiration_raw == bad


@pytest.mark.parametrize("nullish", [None, "", "null", "N/A", "unknown"])
def test_null_like_dates_are_not_flagged(nullish: str | None) -> None:
    ex = UnifiedVaultExtraction.model_validate({**BASE, "expiration_date": nullish})
    assert ex.expiration_date is None
    assert not ex.expiration_unparsed


def test_financial_value_and_missing_date_default() -> None:
    ex = UnifiedVaultExtraction.model_validate({**BASE, "financial_value": 1234.5})
    assert ex.financial_value == 1234.5
    assert ex.expiration_date is None


@pytest.mark.parametrize("score", [-0.1, 1.5])
def test_confidence_bounds(score: float) -> None:
    with pytest.raises(ValidationError):
        UnifiedVaultExtraction.model_validate({**BASE, "confidence_score": score})


def test_schema_exposes_spec_fields_to_llm() -> None:
    props = UnifiedVaultExtraction.model_json_schema()["properties"]
    assert {"category", "document_name", "expiration_date", "requires_alert", "extracted_text",
            "financial_value", "confidence_score"} == set(props)


def test_hitl_state_defaults() -> None:
    state = HITLState()
    assert state.review_required is False and state.review_reasons == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2030-01-31", date(2030, 1, 31)), (date(2030, 1, 1), date(2030, 1, 1)), (5, None),
     (" 2030-01-31 ", date(2030, 1, 31))],
)
def test_parse_iso_date(value: object, expected: date | None) -> None:
    assert parse_iso_date(value) == expected

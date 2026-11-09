from __future__ import annotations

import re
from datetime import date

import pytest

from famguard_worker.email.templates import AlertItem, render_alert_email


def _item(days: int, name: str = "John Passport", **kw: object) -> AlertItem:
    values: dict[str, object] = {
        "document_id": f"doc-{days}-{name}", "document_name": name, "member_name": "John",
        "category": "identitycards", "expiration_date": date(2027, 1, 1), "days_remaining": days,
    }
    values.update(kw)
    return AlertItem(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(("days", "danger"), [(30, False), (8, False), (7, True), (1, True)])
def test_danger_banner_only_within_7_days(days: int, danger: bool) -> None:
    email = render_alert_email("Ann", [_item(days)])
    assert ("background:#fee2e2" in email.html) is danger
    assert ("background:#fef3c7" in email.html) is not danger
    assert email.subject.startswith("Action needed") is danger


def test_any_urgent_item_makes_digest_urgent() -> None:
    email = render_alert_email("Ann", [_item(30, "Insurance"), _item(1, "Passport")])
    assert "background:#fee2e2" in email.html
    assert email.subject == "Action needed: 2 documents expiring soon (next tomorrow)"
    # Most urgent first.
    assert email.html.index("Passport") < email.html.index("Insurance")


def test_values_are_html_escaped() -> None:
    email = render_alert_email('<b>Eve</b>', [_item(7, name='<script>alert(1)</script>',
                                                   member_name='"Bob" & co')])
    assert "<script>" not in email.html
    assert "&lt;script&gt;" in email.html
    assert "&lt;b&gt;Eve&lt;/b&gt;" in email.html
    assert "&quot;Bob&quot; &amp; co" in email.html


def test_inline_styles_only() -> None:
    html = render_alert_email(None, [_item(7)]).html
    assert "<style" not in html
    assert not re.search(r"\sclass=", html)
    assert 'width="600"' in html
    assert "Hi," in html


def test_plain_text_alternative() -> None:
    email = render_alert_email("Ann", [_item(1), _item(30, "Ann Insurance", member_name="Ann")])
    assert email.text.startswith("Hi Ann,")
    assert "- John Passport (John, identitycards): expires 2027-01-01 (tomorrow)" in email.text
    assert "Ann Insurance" in email.text and "in 30 days" in email.text


def test_single_item_subject() -> None:
    assert render_alert_email("Ann", [_item(30)]).subject == "John Passport expires in 30 days"


def test_empty_items_rejected() -> None:
    with pytest.raises(ValueError):
        render_alert_email("Ann", [])

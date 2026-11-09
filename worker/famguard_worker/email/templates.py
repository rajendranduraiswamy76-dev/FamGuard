"""Inline-styled transactional HTML for expiry alerts (email clients ignore <style> blocks)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Final

DANGER_THRESHOLD_DAYS: Final = 7

DANGER_BANNER_STYLE: Final = (
    "background:#fee2e2;color:#991b1b;border-left:4px solid #dc2626;"
    "padding:16px 20px;font-size:16px;font-weight:600;"
)
NOTICE_BANNER_STYLE: Final = (
    "background:#fef3c7;color:#92400e;border-left:4px solid #f59e0b;"
    "padding:16px 20px;font-size:16px;font-weight:600;"
)
_FONT: Final = "font-family:Arial,Helvetica,sans-serif;"


@dataclass(frozen=True, slots=True)
class AlertItem:
    document_id: str
    document_name: str
    member_name: str
    category: str
    expiration_date: date
    days_remaining: int

    @property
    def is_urgent(self) -> bool:
        return self.days_remaining <= DANGER_THRESHOLD_DAYS


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def _days_label(days: int) -> str:
    if days <= 0:
        return "today"
    return "tomorrow" if days == 1 else f"in {days} days"


def _badge(item: AlertItem) -> str:
    if item.is_urgent:
        colors = "background:#dc2626;color:#ffffff;"
    else:
        colors = "background:#f59e0b;color:#111827;"
    return (
        f'<span style="{colors}border-radius:9999px;padding:2px 10px;font-size:12px;'
        f'font-weight:700;white-space:nowrap;">{escape(_days_label(item.days_remaining))}</span>'
    )


def _row(item: AlertItem) -> str:
    cell = "padding:12px 16px;border-bottom:1px solid #e5e7eb;vertical-align:top;"
    return (
        "<tr>"
        f'<td style="{cell}{_FONT}color:#111827;font-size:14px;">'
        f"<strong>{escape(item.document_name)}</strong><br>"
        f'<span style="color:#6b7280;font-size:12px;">{escape(item.member_name)} &middot; '
        f"{escape(item.category)}</span></td>"
        f'<td style="{cell}{_FONT}color:#111827;font-size:14px;white-space:nowrap;">'
        f"{item.expiration_date.isoformat()}</td>"
        f'<td style="{cell}{_FONT}text-align:right;">{_badge(item)}</td>'
        "</tr>"
    )


def render_alert_email(recipient_name: str | None, items: Sequence[AlertItem]) -> RenderedEmail:
    if not items:
        raise ValueError("An alert email needs at least one item")
    ordered = sorted(items, key=lambda i: (i.days_remaining, i.document_name.lower()))
    soonest = ordered[0]
    urgent = any(i.is_urgent for i in ordered)

    if len(ordered) == 1:
        subject = (f"{'Action needed: ' if urgent else ''}{soonest.document_name} expires "
                   f"{_days_label(soonest.days_remaining)}")
    else:
        subject = (f"{'Action needed: ' if urgent else ''}{len(ordered)} documents expiring soon "
                   f"(next {_days_label(soonest.days_remaining)})")

    banner_style = DANGER_BANNER_STYLE if urgent else NOTICE_BANNER_STYLE
    banner_text = (
        f"Urgent: a document expires {_days_label(soonest.days_remaining)}. Renew it now."
        if urgent
        else "Heads up: these documents expire within the next 30 days."
    )
    greeting = f"Hi {escape(recipient_name)}," if recipient_name else "Hi,"
    rows = "".join(_row(i) for i in ordered)
    th = (f'style="{_FONT}text-align:left;padding:10px 16px;font-size:12px;color:#6b7280;'
          'text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid #e5e7eb;"')

    html = (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(subject)}</title></head>"
        '<body style="margin:0;padding:0;background:#f3f4f6;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f3f4f6;padding:24px 0;"><tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:100%;background:#ffffff;border-radius:8px;'
        'overflow:hidden;border:1px solid #e5e7eb;">'
        f'<tr><td style="{_FONT}background:#111827;color:#ffffff;padding:20px 24px;'
        'font-size:20px;font-weight:700;">FamGuard AI</td></tr>'
        f'<tr><td style="{_FONT}{banner_style}">{escape(banner_text)}</td></tr>'
        f'<tr><td style="{_FONT}padding:20px 24px 8px;color:#111827;font-size:15px;">'
        f"{greeting}<br><br>The following family documents are approaching their "
        "expiration date:</td></tr>"
        '<tr><td style="padding:8px 8px 16px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"<tr><th {th}>Document</th><th {th}>Expires</th><th {th}></th></tr>"
        f"{rows}</table></td></tr>"
        f'<tr><td style="{_FONT}padding:0 24px 24px;color:#374151;font-size:14px;">'
        "Open your FamGuard vault to review the documents and start renewals early.</td></tr>"
        f'<tr><td style="{_FONT}background:#f9fafb;color:#9ca3af;padding:16px 24px;'
        'font-size:12px;">You receive this because you are a member or admin of this family '
        "vault. Document contents are never included in alert emails.</td></tr>"
        "</table></td></tr></table></body></html>"
    )

    lines = [f"Hi {recipient_name}," if recipient_name else "Hi,", "", banner_text, ""]
    for item in ordered:
        lines.append(
            f"- {item.document_name} ({item.member_name}, {item.category}): expires "
            f"{item.expiration_date.isoformat()} ({_days_label(item.days_remaining)})"
        )
    lines += ["", "Open your FamGuard vault to review and renew."]
    return RenderedEmail(subject=subject, html=html, text="\n".join(lines))

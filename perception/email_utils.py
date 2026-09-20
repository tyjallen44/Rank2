from __future__ import annotations

import os
from typing import Optional

import httpx

ADMIN_EMAIL: str = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "ty.allen@rldatix.com")
APP_URL: str = os.environ.get("APP_URL", "https://careclimb.com")

from .strings import EMAIL_BRAND as _BRAND
_FROM_NAME = _BRAND
_FROM_DOMAIN = os.environ.get("RESEND_FROM_DOMAIN", "careclimb.com")

_CARD_CSS = (
    "font-family:'Inter',-apple-system,sans-serif;"
    "color:#0F4146;max-width:480px;margin:0 auto;padding:32px 24px;"
    "background:#fff;border-radius:12px;"
)


def _send(to: str, subject: str, html: str, attachments: Optional[list] = None) -> None:
    """attachments: [{"filename": str, "content": <base64 str>}] (Resend format)."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_addr = f"{_FROM_NAME} <noreply@{_FROM_DOMAIN}>"
    print(f"[email] Attempting send to={to} subject={subject!r} from={from_addr}")
    if not api_key:
        msg = "RESEND_API_KEY env var not set"
        print(f"[email] FAILED: {msg}")
        raise RuntimeError(msg)
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": from_addr, "to": [to], "subject": f"{_BRAND} — {subject}", "html": html,
                  **({"attachments": attachments} if attachments else {})},
            timeout=15,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text}")
        print(f"[email] Sent OK to={to} id={resp.json().get('id')}")
    except Exception as exc:
        print(f"[email] FAILED to={to} error={type(exc).__name__}: {exc}")
        raise


def _btn(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;background:#0F4146;color:#fff;'
        f'padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600;'
        f'font-size:14px;">{label}</a>'
    )


def _wrap(body: str) -> str:
    return (
        f'<div style="background:#F2F8F6;padding:32px 16px;">'
        f'<div style="{_CARD_CSS}">{body}</div></div>'
    )


def notify_admin_access_request(
    email: str, name: Optional[str], request_type: str, req_id: str
) -> None:
    label = "Google SSO" if request_type == "google" else "RLDatix Native Account"
    display = name or email
    body = f"""
    <h2 style="margin:0 0 16px;font-size:20px;">New Access Request</h2>
    <p style="margin:6px 0"><strong>Name:</strong> {display}</p>
    <p style="margin:6px 0"><strong>Email:</strong> {email}</p>
    <p style="margin:6px 0 20px"><strong>Auth type:</strong> {label}</p>
    {_btn(APP_URL, "Review in Admin Panel")}
    """
    _send(ADMIN_EMAIL, f"Access Request — {display}", _wrap(body))


def send_set_password_link(email: str, name: Optional[str], token: str) -> None:
    display = name or email
    link = f"{APP_URL}/?set_password_token={token}"
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">You're approved!</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:24px">Your {_BRAND} access request has been approved.
    Click below to set your password and get started:</p>
    {_btn(link, "Set Your Password")}
    <p style="margin-top:20px;color:#7a9095;font-size:13px">
    This link expires in 48 hours. If you did not request access, you can ignore this email.</p>
    """
    _send(email, "Set Your Password", _wrap(body))


def send_reset_password_link(email: str, name: Optional[str], token: str) -> None:
    display = name or email
    link = f"{APP_URL}/?set_password_token={token}"
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">Password Reset Request</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:24px">We received a request to reset your {_BRAND} password.
    Click below to choose a new one:</p>
    {_btn(link, "Reset My Password")}
    <p style="margin-top:20px;color:#7a9095;font-size:13px">
    This link expires in 48 hours. If you did not request a password reset, you can safely ignore this email.</p>
    """
    _send(email, "Reset Your Password", _wrap(body))


def send_access_denied(email: str, name: Optional[str]) -> None:
    display = name or email
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">Access Request Update</h2>
    <p>Hi {display},</p>
    <p>Thank you for your interest in {_BRAND}. Your access request has not been approved at this time.</p>
    <p>If you believe this is an error, please contact your administrator.</p>
    """
    _send(email, "Access Request Update", _wrap(body))


def send_google_access_approved(email: str, name: Optional[str]) -> None:
    display = name or email
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">Access Approved</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:24px">Your {_BRAND} access has been approved.
    Sign in with your Google account to get started:</p>
    {_btn(APP_URL, f"Sign In to {_BRAND}")}
    """
    _send(email, "Access Approved", _wrap(body))


def send_public_report_ready(email: str, name: Optional[str], organization: str,
                             download_url: str) -> None:
    """Deliver a requested Hospital Network report as a secure download link."""
    display = name or "there"
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">Your AI Reputation Report is ready</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:8px">Your Hospital Network AI Reputation report for
    <strong>{organization}</strong> has been generated. Use the secure link below to
    download it:</p>
    <p style="margin:20px 0">{_btn(download_url, "Download Your Report")}</p>
    <p style="font-size:12px;color:#5A6E72;margin-bottom:0">This link is unique to you and
    will expire in 14 days. If you have questions about your results, just reply to this email.</p>
    """
    _send(email, "Your AI Reputation Report", _wrap(body))


def send_public_report_followup(email: str, name: Optional[str], organization: str) -> None:
    """Sent when we can't confirm the requester's affiliation with the organization —
    routes them to a specialist instead of auto-generating the report."""
    display = name or "there"
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">We're preparing your report</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:8px">Thanks for requesting a Hospital Network AI Reputation
    report for <strong>{organization}</strong>. To make sure it reaches the right person,
    a specialist from our team will follow up with you shortly to confirm a few details
    and deliver your report.</p>
    <p style="font-size:12px;color:#5A6E72;margin-bottom:0">No action is needed on your part —
    we'll be in touch.</p>
    """
    _send(email, "Your report request", _wrap(body))


def notify_admin_public_request(organization: str, requester_email: str,
                                status: str, reason: str = "") -> None:
    """Notify the admin/sales inbox of a public report request outcome
    (esp. follow-ups that need a human)."""
    extra = f"<p style=\"margin:6px 0 0;color:#5A6E72;font-size:13px\">{reason}</p>" if reason else ""
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">Public report request — {status}</h2>
    <p style="margin:0"><strong>{organization}</strong></p>
    <p style="margin:4px 0 0">Requester: {requester_email}</p>
    {extra}
    """
    _send(ADMIN_EMAIL, f"Public report request — {status}", _wrap(body))


def send_trend_report(email: str, entity_name: str, pdf_path: str, *, latest_score=None,
                      delta=None, snapshots: int = 0, sender: str = "",
                      period: tuple = ("", "")) -> None:
    """Email the AI Reputation Trend Report PDF (attached). Written for an OUTSIDE reader:
    the attachment is the deliverable, the sender is named, and the app sign-in is only a
    quiet footer line for Pulse users — no call-to-action button (recipients usually have
    no Pulse login and would land on the login screen)."""
    import base64
    import html as _html
    from pathlib import Path
    data = base64.b64encode(Path(pdf_path).read_bytes()).decode()
    slug = "".join(ch if ch.isalnum() else "-" for ch in entity_name).strip("-")[:60]
    ent = _html.escape(entity_name)
    who = _html.escape(sender or "")
    p0, p1 = (period or ("", ""))
    span = f" covering {_html.escape(p0)} to {_html.escape(p1)}" if (p0 and p1 and p0 != p1) else ""
    score_line = ""
    if latest_score is not None:
        d = ""
        if delta is not None and delta != 0:
            d = f' <span style="color:#5A6E72;font-weight:400">({"+" if delta > 0 else ""}{delta} since the previous snapshot)</span>'
        score_line = (f'<p style="margin:10px 0 14px;font-size:15px"><strong>Latest Pulse Score: '
                      f'{latest_score}</strong>{d}</p>')
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">AI Reputation Trend Report — {ent}</h2>
    <p style="margin-bottom:6px">Attached is the AI Reputation Trend Report for <strong>{ent}</strong>{span}
    {f"({snapshots} snapshots)" if snapshots else ""}. It shows how AI assistants currently present the
    organization, how that has moved over time, and which pillars are driving the change.</p>
    {score_line}
    <p style="margin:0 0 4px">Questions about the report? Just reply to this email{f" and it will reach {who}" if who else ""}.</p>
    <hr style="border:0;border-top:1px solid #d7e7e2;margin:22px 0 12px">
    <p style="font-size:11px;color:#8a9aaa;margin:0">Sent from Pulse{f" by {who}" if who else ""}.
    Pulse users can <a href="{APP_URL}" style="color:#8a9aaa">sign in</a> to see the full trend.</p>
    """
    _send(email, f"Trend Report — {entity_name}", _wrap(body),
          attachments=[{"filename": f"{slug}_AI_Reputation_Trend_Report.pdf", "content": data}])


def _attach(paths: list) -> list:
    import base64
    from pathlib import Path
    out = []
    total = 0
    for p in paths or []:
        try:
            pp = Path(p)
            if not pp.exists():
                continue
            size = pp.stat().st_size
            if total + size > 25 * 1024 * 1024:     # keep well under Resend's message limit
                continue
            total += size
            out.append({"filename": pp.name, "content": base64.b64encode(pp.read_bytes()).decode()})
        except Exception:
            continue
    return out


def send_run_complete(email: str, kind: str, title: str, files: list, minutes: Optional[float] = None) -> None:
    """'Your report is ready' — sent to the person who started a long run."""
    import html as _html
    atts = _attach(files)
    took = f" It took about {int(round(minutes))} minute{'s' if int(round(minutes)) != 1 else ''}." if minutes and minutes >= 1 else ""
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">Your {_html.escape(kind)} is ready</h2>
    <p style="margin-bottom:8px"><strong>{_html.escape(title)}</strong> has finished.{took}</p>
    <p style="margin-bottom:8px">{'The report is attached.' if atts else 'Sign in to download it.'} It is also under <strong>History</strong> in Pulse.</p>
    <p style="margin:20px 0">{_btn(APP_URL, "Open Pulse")}</p>
    <p style="font-size:11px;color:#8a9aaa;margin:0">You can turn these notifications off on the Pulse Home page.</p>
    """
    _send(email, f"Ready — {title}", _wrap(body), attachments=atts)


def send_report_copy(email: str, kind: str, title: str, files: list, sender: str = "", note: str = "") -> None:
    """A report forwarded to a customer or colleague by a Pulse user."""
    import html as _html
    atts = _attach(files)
    who = _html.escape(sender or "")
    note_html = f'<p style="margin:0 0 14px;white-space:pre-wrap">{_html.escape(note.strip())}</p>' if note and note.strip() else ""
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">{_html.escape(kind)} — {_html.escape(title)}</h2>
    {note_html}
    <p style="margin-bottom:6px">Attached is the {_html.escape(kind)} for <strong>{_html.escape(title)}</strong>: how AI assistants currently present
    the organization, scored on public signals, with what to change.</p>
    <p style="margin:0 0 4px">Questions about the report? Just reply to this email{f" and it will reach {who}" if who else ""}.</p>
    <hr style="border:0;border-top:1px solid #d7e7e2;margin:22px 0 12px">
    <p style="font-size:11px;color:#8a9aaa;margin:0">Sent from Pulse{f" by {who}" if who else ""}.</p>
    """
    _send(email, f"{kind} — {title}", _wrap(body), attachments=atts)


def send_trend_alert(email: str, entity_name: str, *, latest: int, previous: int, delta: int,
                     quartile_prev: str = "", quartile_now: str = "", snapshot_date: str = "",
                     pdf_path: Optional[str] = None) -> None:
    """Same-day alert when a tracked entity's score moved 5+ points or changed quartile."""
    import html as _html
    ent = _html.escape(entity_name)
    up = delta > 0
    col = "#2e9e5b" if up else "#d94f4f"
    q = ""
    if quartile_prev and quartile_now and quartile_prev != quartile_now:
        q = f'<p style="margin:0 0 12px">It {"moved up into" if up else "dropped into"} the <strong>{_html.escape(quartile_now)}</strong> quartile (was {_html.escape(quartile_prev)}).</p>'
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">{ent}: score {"up" if up else "down"} {abs(delta)} points</h2>
    <p style="margin:0 0 10px;font-size:15px"><strong style="color:{col}">{"▲" if up else "▼"} {abs(delta)}</strong> &nbsp; {previous} → <strong>{latest}</strong>
      <span style="color:#5A6E72">on {_html.escape(snapshot_date)}</span></p>
    {q}
    <p style="margin:0 0 6px">This is the latest AI Reputation snapshot for <strong>{ent}</strong>. {"The Trend Report is attached." if pdf_path else "The Trend Report with the full history is available in Pulse."}</p>
    <hr style="border:0;border-top:1px solid #d7e7e2;margin:22px 0 12px">
    <p style="font-size:11px;color:#8a9aaa;margin:0">You receive this because change alerts are on for this entity in Pulse → Trends. Turn them off in the entity's configuration panel.</p>
    """
    atts = _attach([pdf_path]) if pdf_path else None
    _send(email, f"{'▲' if up else '▼'} {abs(delta)} — {entity_name} AI Reputation score now {latest}", _wrap(body), attachments=atts)

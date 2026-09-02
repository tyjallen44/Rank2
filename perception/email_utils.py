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


def _send(to: str, subject: str, html: str) -> None:
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
            json={"from": from_addr, "to": [to], "subject": f"{_BRAND} — {subject}", "html": html},
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
    <h2 style="margin:0 0 12px;font-size:20px;">Your AI Visibility Report is ready</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:8px">Your Hospital Network AI Visibility report for
    <strong>{organization}</strong> has been generated. Use the secure link below to
    download it:</p>
    <p style="margin:20px 0">{_btn(download_url, "Download Your Report")}</p>
    <p style="font-size:12px;color:#5A6E72;margin-bottom:0">This link is unique to you and
    will expire in 14 days. If you have questions about your results, just reply to this email.</p>
    """
    _send(email, "Your AI Visibility Report", _wrap(body))


def send_public_report_followup(email: str, name: Optional[str], organization: str) -> None:
    """Sent when we can't confirm the requester's affiliation with the organization —
    routes them to a specialist instead of auto-generating the report."""
    display = name or "there"
    body = f"""
    <h2 style="margin:0 0 12px;font-size:20px;">We're preparing your report</h2>
    <p>Hi {display},</p>
    <p style="margin-bottom:8px">Thanks for requesting a Hospital Network AI Visibility
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

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

ADMIN_EMAIL: str = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "ty.allen@rldatix.com")
APP_URL: str = os.environ.get("APP_URL", "https://careclimb.com")

_BRAND = "CareClimb"
_CARD_CSS = (
    "font-family:'Inter',-apple-system,sans-serif;"
    "color:#0F4146;max-width:480px;margin:0 auto;padding:32px 24px;"
    "background:#fff;border-radius:12px;"
)


def _send(to: str, subject: str, html: str) -> None:
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pw   = os.environ.get("GMAIL_APP_PASSWORD", "")
    print(f"[email] Attempting send to={to} subject={subject!r} from={gmail_user or '(not set)'}")
    if not gmail_user or not gmail_pw:
        print(f"[email] GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{_BRAND} — {subject}"
        msg["From"]    = f"{_BRAND} <{gmail_user}>"
        msg["To"]      = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_pw)
            smtp.sendmail(gmail_user, to, msg.as_string())
        print(f"[email] Sent OK to={to}")
    except Exception as exc:
        print(f"[email] FAILED to={to} error={type(exc).__name__}: {exc}")


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

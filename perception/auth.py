from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from .db import get_connection


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 300_000)
    return key.hex(), salt.hex()


def create_user(
    email: str,
    name: Optional[str],
    role: str,
    auth_type: str,
    password: Optional[str] = None,
    invited_by: Optional[str] = None,
) -> dict:
    user_id = str(uuid.uuid4())
    pw_hash = pw_salt = None
    if password:
        pw_hash, pw_salt = _hash_password(password)
    con = get_connection()
    con.execute(
        """
        INSERT INTO users
            (id, email, name, role, auth_type, password_hash, password_salt,
             is_active, created_at, invited_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
        """,
        [user_id, email.lower(), name, role, auth_type, pw_hash, pw_salt,
         datetime.now(timezone.utc), invited_by],
    )
    con.close()
    return get_user_by_id(user_id)


def get_user_by_email(email: str) -> Optional[dict]:
    con = get_connection()
    con.execute("SELECT * FROM users WHERE email = ?", [email.lower()])
    row = con.fetchone()
    if not row:
        con.close()
        return None
    cols = [d[0] for d in con.description]
    con.close()
    return dict(zip(cols, row))


def get_user_by_id(user_id: str) -> Optional[dict]:
    con = get_connection()
    con.execute("SELECT * FROM users WHERE id = ?", [user_id])
    row = con.fetchone()
    if not row:
        con.close()
        return None
    cols = [d[0] for d in con.description]
    con.close()
    return dict(zip(cols, row))


def verify_password(user: dict, password: str) -> bool:
    if not user.get("password_hash") or not user.get("password_salt"):
        return False
    try:
        salt = bytes.fromhex(user["password_salt"])
        expected_hash, _ = _hash_password(password, salt)
        return hmac.compare_digest(expected_hash, user["password_hash"])
    except Exception:
        return False


def set_password(user_id: str, password: str) -> None:
    pw_hash, pw_salt = _hash_password(password)
    con = get_connection()
    con.execute(
        "UPDATE users SET password_hash=?, password_salt=?, is_active=TRUE WHERE id=?",
        [pw_hash, pw_salt, user_id],
    )
    con.close()


def update_last_login(user_id: str) -> None:
    con = get_connection()
    con.execute(
        "UPDATE users SET last_login=? WHERE id=?",
        [datetime.now(timezone.utc), user_id],
    )
    con.close()


def list_users() -> list[dict]:
    con = get_connection()
    con.execute("""
        SELECT id, email, name, role, auth_type, is_active, created_at, last_login
        FROM users ORDER BY created_at DESC
    """)
    rows = con.fetchall()
    cols = ["id", "email", "name", "role", "auth_type", "is_active", "created_at", "last_login"]
    con.close()
    return [dict(zip(cols, r)) for r in rows]


def deactivate_user(user_id: str) -> None:
    con = get_connection()
    con.execute("UPDATE users SET is_active=FALSE WHERE id=?", [user_id])
    con.close()


def reactivate_user(user_id: str) -> None:
    con = get_connection()
    con.execute("UPDATE users SET is_active=TRUE WHERE id=?", [user_id])
    con.close()


def update_user_role(user_id: str, role: str) -> None:
    con = get_connection()
    con.execute("UPDATE users SET role=? WHERE id=?", [role, user_id])
    con.close()


def create_access_request(email: str, name: Optional[str], request_type: str) -> dict:
    req_id = str(uuid.uuid4())
    con = get_connection()
    con.execute(
        """
        INSERT INTO access_requests (id, email, name, request_type, status, requested_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        [req_id, email.lower(), name, request_type, datetime.now(timezone.utc)],
    )
    con.close()
    return get_access_request(req_id)


def get_access_request(req_id: str) -> Optional[dict]:
    con = get_connection()
    con.execute("SELECT * FROM access_requests WHERE id=?", [req_id])
    row = con.fetchone()
    if not row:
        con.close()
        return None
    cols = [d[0] for d in con.description]
    con.close()
    return dict(zip(cols, row))


def get_access_request_by_email(email: str) -> Optional[dict]:
    con = get_connection()
    con.execute(
        "SELECT * FROM access_requests WHERE email=? ORDER BY requested_at DESC LIMIT 1",
        [email.lower()],
    )
    row = con.fetchone()
    if not row:
        con.close()
        return None
    cols = [d[0] for d in con.description]
    con.close()
    return dict(zip(cols, row))


def list_access_requests(status: Optional[str] = None) -> list[dict]:
    con = get_connection()
    if status:
        con.execute(
            "SELECT * FROM access_requests WHERE status=? ORDER BY requested_at DESC",
            [status],
        )
    else:
        con.execute("SELECT * FROM access_requests ORDER BY requested_at DESC")
    rows = con.fetchall()
    cols = [d[0] for d in con.description]
    con.close()
    return [dict(zip(cols, r)) for r in rows]


def handle_access_request(req_id: str, status: str, handled_by: str) -> Optional[dict]:
    con = get_connection()
    con.execute(
        "UPDATE access_requests SET status=?, handled_at=?, handled_by=? WHERE id=?",
        [status, datetime.now(timezone.utc), handled_by, req_id],
    )
    con.close()
    return get_access_request(req_id)


def create_password_token(user_id: str, ttl_hours: int = 48) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    con = get_connection()
    con.execute(
        "INSERT INTO password_tokens (token, user_id, expires_at, used) VALUES (?, ?, ?, FALSE)",
        [token, user_id, expires],
    )
    con.close()
    return token


def consume_password_token(token: str) -> Optional[str]:
    """Returns user_id if token is valid and unused, else None. Marks token used."""
    con = get_connection()
    con.execute(
        "SELECT user_id, expires_at, used FROM password_tokens WHERE token=?", [token]
    )
    row = con.fetchone()
    if not row:
        con.close()
        return None
    user_id, expires_at, used = row
    if used:
        con.close()
        return None
    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        con.close()
        return None
    con.execute("UPDATE password_tokens SET used=TRUE WHERE token=?", [token])
    con.close()
    return user_id

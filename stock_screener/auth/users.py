"""User account storage + admin approval workflow.

Hybrid model:
- The YAML auth_config.yaml provides cookie config and bootstrap admin usernames
- Actual accounts live in the `users` SQLite table (pending/approved/rejected)
- streamlit-authenticator's credentials dict is built from DB at request time
"""

from datetime import datetime
from typing import Optional

import bcrypt

from stock_screener.data.db import get_connection


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def signup(username: str, email: str, name: str, password: str) -> tuple[bool, str]:
    """Register a pending user. Returns (ok, message)."""
    username = username.strip().lower()
    email = email.strip()
    name = name.strip()
    if not username or not password:
        return False, "Username and password required."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not username.isalnum():
        return False, "Username must be alphanumeric."

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE username = ?", (username,))
    if cur.fetchone():
        conn.close()
        return False, "Username already taken."
    cur.execute(
        """INSERT INTO users (username, email, name, password_hash, status, is_admin, created_at)
           VALUES (?, ?, ?, ?, 'pending', 0, ?)""",
        (username, email, name or username, hash_password(password), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return True, "Account created. Awaiting admin approval."


def list_users(status: Optional[str] = None) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM users WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def set_status(username: str, status: str) -> None:
    """status: 'approved' | 'rejected' | 'pending'."""
    conn = get_connection()
    cur = conn.cursor()
    approved_at = datetime.now().isoformat() if status == "approved" else None
    cur.execute(
        "UPDATE users SET status = ?, approved_at = ? WHERE username = ?",
        (status, approved_at, username),
    )
    conn.commit()
    conn.close()


def delete_user(username: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def is_admin(username: str) -> bool:
    if not username:
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_admin FROM users WHERE username = ? AND status = 'approved'", (username,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row[0])


def get_approved_credentials() -> dict:
    """Return a streamlit-authenticator-compatible credentials dict for approved users."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, email, name, password_hash FROM users WHERE status = 'approved'")
    rows = cur.fetchall()
    conn.close()
    return {
        "usernames": {
            r["username"]: {
                "email": r["email"] or "",
                "name": r["name"] or r["username"],
                "password": r["password_hash"],
                "logged_in": False,
                "failed_login_attempts": 0,
            }
            for r in rows
        }
    }


def seed_from_yaml(yaml_credentials: dict, admin_usernames: list[str]) -> int:
    """One-time bootstrap: load YAML-defined users into the DB if the table is empty.
    Returns count of users seeded."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] > 0:
        conn.close()
        return 0
    seeded = 0
    now = datetime.now().isoformat()
    for username, data in (yaml_credentials.get("usernames") or {}).items():
        cur.execute(
            """INSERT INTO users (username, email, name, password_hash, status, is_admin, created_at, approved_at)
               VALUES (?, ?, ?, ?, 'approved', ?, ?, ?)""",
            (
                username, data.get("email", ""), data.get("name", username),
                data.get("password", ""),
                1 if username in admin_usernames else 0,
                now, now,
            ),
        )
        seeded += 1
    conn.commit()
    conn.close()
    return seeded

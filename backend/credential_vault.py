"""Site logins WebPilot may use on the user's behalf.

* Passwords are stored ONLY in the operating system's credential store through `keyring`
  (Windows Credential Manager, macOS Keychain, Secret Service on Linux) — never in SQLite, logs,
  task records, LLM prompts or MCP traffic. SQLite keeps just the site, username and provenance.
* Nothing here is reachable from MCP tools: only the local web UI's REST endpoints (Origin-guarded) and the
  autopilot's own login step read or write it, so the language model can never see or exfiltrate a password.
* Accounts WebPilot creates for the user get a strong random password (`generate_password`).
"""
from __future__ import annotations

import logging
import re
import secrets
import string
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend import db

logger = logging.getLogger("webpilot.vault")

SERVICE = "WebPilot"
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "gen", "firm", "ind"}


class VaultUnavailable(RuntimeError):
    pass


def site_key(url_or_host: str) -> str:
    """Registrable-domain-ish key so accounts.example.co.in and www.example.co.in share one login."""
    raw = url_or_host or ""
    host = (urlparse(raw).hostname if "//" in raw else raw.split("/")[0].split(":")[0]) or ""
    host = host.lower().strip(".")
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _keyring() -> Any:
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            raise VaultUnavailable("No OS credential store is available on this machine.")
        return keyring
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise VaultUnavailable("The 'keyring' package is not installed.") from exc


def available() -> bool:
    try:
        _keyring()
        return True
    except VaultUnavailable:
        return False


def save(site: str, username: str, password: str, source: str = "user", verified: bool = True) -> Dict[str, Any]:
    key = site_key(site)
    if not key or not username or not password:
        raise ValueError("site, username and password are required")
    _keyring().set_password(SERVICE, f"{key}|{username}", password)
    record = db.upsert_site_account(key, username, source, verified)
    logger.info("Saved a login for %s (%s, %s).", key, "created by WebPilot" if source == "created" else "added by you",
                "verified" if verified else "pending verification")
    return record


def get(site: str) -> Optional[Dict[str, str]]:
    key = site_key(site)
    account = db.get_site_account(key)
    if not account:
        return None
    try:
        password = _keyring().get_password(SERVICE, f"{key}|{account['username']}")
    except VaultUnavailable:
        return None
    if not password:
        return None
    return {"site": key, "username": account["username"], "password": password, "source": account["source"]}


def has(site: str) -> bool:
    return db.get_site_account(site_key(site)) is not None


def list_accounts() -> List[Dict[str, Any]]:
    return db.list_site_accounts()  # metadata only — never passwords


def delete(site: str) -> bool:
    key = site_key(site)
    account = db.get_site_account(key)
    if not account:
        return False
    try:
        _keyring().delete_password(SERVICE, f"{key}|{account['username']}")
    except Exception:  # already gone from the OS store
        pass
    return db.delete_site_account(key)


def mark_used(site: str, verified: Optional[bool] = None) -> None:
    db.touch_site_account(site_key(site), verified)


def generate_password(length: int = 20) -> str:
    """Strong random password with upper, lower, digit and symbol (symbols most sites accept)."""
    symbols = "!@#$%^&*-_=+?"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw) and re.search(r"\d", pw)
                and any(c in symbols for c in pw)):
            return pw


# ------------------------------------------------------------------------------------- user profile

def user_profile() -> Dict[str, Optional[str]]:
    """Details the user chose to let WebPilot use when signing up for sites (set in the UI)."""
    import os

    return {
        "email": db.get_setting("user_email") or os.getenv("WEBPILOT_USER_EMAIL") or None,
        "full_name": db.get_setting("user_full_name") or os.getenv("WEBPILOT_USER_FULL_NAME") or None,
    }


def set_user_profile(email: Optional[str], full_name: Optional[str]) -> Dict[str, Optional[str]]:
    if email is not None:
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("That doesn't look like an email address.")
        db.set_setting("user_email", email.strip() or None)
    if full_name is not None:
        db.set_setting("user_full_name", full_name.strip()[:120] or None)
    return user_profile()

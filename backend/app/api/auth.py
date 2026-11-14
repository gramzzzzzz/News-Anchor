"""Single-user session authentication (HMAC-signed cookie), scrypt password hash, CSRF header, login rate limit.
Same scheme as Portfolio Tracker."""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, Request

COOKIE = "na_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "news-anchor"


def hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return {"salt": salt.hex(), "hash": h.hex()}


def check_password(password: str, stored: dict) -> bool:
    h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(stored["salt"]), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(h.hex(), stored["hash"])


def _sign(key: bytes, payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_token(key: bytes, hours: int) -> str:
    payload = json.dumps({"exp": int(time.time() + hours * 3600)}).encode()
    return f"{base64.urlsafe_b64encode(payload).decode().rstrip('=')}.{_sign(key, payload)}"


def verify_token(key: bytes, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    p64, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4))
        return hmac.compare_digest(_sign(key, payload), sig) and json.loads(payload).get("exp", 0) > time.time()
    except (ValueError, TypeError):
        return False


_attempts: dict[str, collections.deque] = collections.defaultdict(collections.deque)


def rate_limit(ip: str, limit: int = 8, window: int = 300) -> None:
    q = _attempts[ip]
    now = time.time()
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    q.append(now)


def require_session(request: Request) -> None:
    c = request.app.state.container
    if not verify_token(c.config.secret_key, request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing CSRF header")

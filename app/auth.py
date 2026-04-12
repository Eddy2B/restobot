# app/auth.py — JWT, bcrypt, auth helpers
# Depends only on app.config (pure constants). No shared state.

import json
import hmac
import hashlib
import base64
import time as time_mod

import bcrypt
from fastapi import Request

from app.config import JWT_SECRET, ADMIN_SECRET


def jwt_encode(payload: dict) -> str:
    """Simple JWT encode (HS256)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload["iat"] = int(time_mod.time())
    payload["exp"] = int(time_mod.time()) + 3600 * 8  # 8 hours
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig_input = f"{header}.{body}"
    sig = base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), sig_input.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def jwt_decode(token: str) -> dict | None:
    """Decode and verify a JWT token. Returns payload or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        sig_input = f"{header}.{body}"
        expected_sig = base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), sig_input.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        if sig != expected_sig:
            return None
        padded = body + "=" * (4 - len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get("exp", 0) < time_mod.time():
            return None
        return payload
    except Exception:
        return None


def get_auth(request: Request) -> dict | None:
    """Extract and verify JWT from cookie, Authorization header, or query param."""
    token = request.cookies.get("gs_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        token = request.query_params.get("token", "")
    if not token:
        return None
    return jwt_decode(token)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def verify_admin(request: Request) -> bool:
    """Verify admin access via ADMIN_SECRET in query param or header."""
    secret = request.query_params.get("secret", "") or request.headers.get("x-admin-secret", "")
    return secret == ADMIN_SECRET and ADMIN_SECRET != ""


# Rate limiting + login lockout (extracted from main.py Phase 3b)

import time as time_mod
from app.config import RATE_LIMITS
import app.state as _state


def check_rate_limit(ip: str, endpoint: str) -> tuple:
    """Returns (allowed, limit, remaining, window)."""
    now = time_mod.time()
    key = endpoint if endpoint in RATE_LIMITS else "default"
    max_requests, window = RATE_LIMITS[key]
    if ip not in _state.rate_limit_store:
        _state.rate_limit_store[ip] = {}
    if key not in _state.rate_limit_store[ip]:
        _state.rate_limit_store[ip][key] = []
    _state.rate_limit_store[ip][key] = [t for t in _state.rate_limit_store[ip][key] if now - t < window]
    remaining = max(0, max_requests - len(_state.rate_limit_store[ip][key]))
    if len(_state.rate_limit_store[ip][key]) >= max_requests:
        return False, max_requests, 0, window
    _state.rate_limit_store[ip][key].append(now)
    return True, max_requests, remaining - 1, window


def check_login_lockout(ip: str) -> bool:
    """Returns True if IP is locked out from login attempts."""
    now = time_mod.time()
    if ip in _state.login_failures:
        f = _state.login_failures[ip]
        if f.get("locked_until", 0) > now:
            return True
        if f.get("locked_until", 0) > 0 and f["locked_until"] <= now:
            _state.login_failures[ip] = {"count": 0, "locked_until": 0}
    return False


def record_login_failure(ip: str):
    """Record a failed login attempt and apply progressive lockout."""
    now = time_mod.time()
    if ip not in _state.login_failures:
        _state.login_failures[ip] = {"count": 0, "locked_until": 0}
    _state.login_failures[ip]["count"] += 1
    count = _state.login_failures[ip]["count"]
    if count >= 15:
        _state.login_failures[ip]["locked_until"] = now + 7200
    elif count >= 10:
        _state.login_failures[ip]["locked_until"] = now + 1800
    elif count >= 5:
        _state.login_failures[ip]["locked_until"] = now + 300


def record_login_success(ip: str):
    """Reset failure counter on successful login."""
    if ip in _state.login_failures:
        del _state.login_failures[ip]

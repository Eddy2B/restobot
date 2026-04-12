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

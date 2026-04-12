# app/routes/auth_routes.py — /api/register, /api/login, /api/forgot-password, /api/reset-password, /api/logout

import json
import logging
import secrets
import re as re_mod
import time as time_mod
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import app.state as _state
from app.state import limiter
from app.config import BREVO_API_KEY
from app.auth import (
    get_auth, jwt_encode, hash_password, verify_password,
    check_rate_limit, check_login_lockout, record_login_failure, record_login_success,
)
from app.utils.text_utils import sanitize_input, safe_json, is_valid_email
from app.utils.date_utils import today_paris
from app.services.db_helpers import init_daily_slots
from app.services.brevo_service import send_brevo_welcome, send_admin_notification_email

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.post("/api/register")
@limiter.limit("5/minute")  # AUDIT FIX 2026-04-12
async def api_register(request: Request):
    """Register a new restaurant + owner user. Self-service from guestscale.com."""
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    rl_ok, rl_limit, rl_remain, rl_window = check_rate_limit(client_ip, "/api/register")
    if not rl_ok:
        r = JSONResponse(status_code=429, content={"error": "Trop de tentatives. Veuillez réessayer dans quelques minutes."})
        r.headers["X-RateLimit-Limit"] = str(rl_limit); r.headers["X-RateLimit-Remaining"] = "0"; r.headers["Retry-After"] = str(rl_window)
        return r
    data = await safe_json(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    email = (data.get("email") or "").strip().lower()
    if email and not is_valid_email(email):
        return JSONResponse(status_code=400, content={"error": "Email invalide"})
    password = data.get("password", "")
    first_name = sanitize_input(data.get("first_name", ""), 100)
    last_name = sanitize_input(data.get("last_name", ""), 100)
    phone = sanitize_input(data.get("phone", ""), 30)
    restaurant_name = sanitize_input(data.get("restaurant_name", ""), 200)
    restaurant_address = sanitize_input(data.get("restaurant_address", ""), 300)

    if not email or not restaurant_name:
        return JSONResponse(status_code=400, content={"error": "Email et nom du restaurant requis"})
    # Generate password if not provided (simplified signup flow)
    generated_password = False
    if not password:
        password = secrets.token_urlsafe(12)[:14] + "!A1"  # 17 chars, guaranteed mixed
        generated_password = True
    if len(password) < 12:
        return JSONResponse(status_code=400, content={"error": "Le mot de passe doit contenir au moins 12 caractères"})

    if not _state.db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de données non disponible"})

    # Generate slug from restaurant name
    slug = re_mod.sub(r'[^a-z0-9]+', '', restaurant_name.lower().replace(" ", ""))[:30] or "restaurant"

    try:
        async with _state.db_pool.acquire() as conn:
            # Check if email already exists
            existing = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
            if existing:
                return JSONResponse(status_code=409, content={"error": "Un compte avec cet email existe déjà"})

            # Check slug uniqueness, add suffix if needed
            base_slug = slug
            suffix = 1
            while await conn.fetchval("SELECT id FROM restaurants WHERE slug = $1", slug):
                slug = f"{base_slug}{suffix}"
                suffix += 1

            # Create restaurant
            rid = await conn.fetchval("""
                INSERT INTO restaurants (slug, name, settings, status, trial_ends_at)
                VALUES ($1, $2, $3::jsonb, 'trial', NOW() + INTERVAL '30 days')
                RETURNING id
            """, slug, restaurant_name, json.dumps({
                "description": "", "menu": "", "hours": "",
                "address": restaurant_address, "phone": phone,
                "tone": "Professionnel mais chaleureux",
                "languages": "français, anglais",
                "special_info": "", "booking_link": "",
                "allergens_policy": "Nous prenons les allergies très au sérieux. Merci de préciser vos allergies.",
            }))

            # Create user
            pwd_hash = hash_password(password)
            uid = await conn.fetchval("""
                INSERT INTO users (email, password_hash, first_name, last_name, phone, restaurant_id, role)
                VALUES ($1, $2, $3, $4, $5, $6, 'owner')
                RETURNING id
            """, email, pwd_hash, first_name, last_name, phone, rid)

        rid_str = str(rid)

        # Init in-memory
        _state.restaurants_cache[rid_str] = {
            "id": rid_str, "slug": slug, "name": restaurant_name,
            "owner_phone": "", "whatsapp_phone_number_id": "",
            "whatsapp_access_token": "", "whatsapp_verify_token": "guestscale-verify",
            "google_review_link": "", "settings": {
                "description": "", "menu": "", "hours": "",
                "address": restaurant_address, "phone": phone,
                "tone": "Professionnel mais chaleureux", "languages": "français, anglais",
                "special_info": "", "booking_link": "",
                "allergens_policy": "Nous prenons les allergies très au sérieux.",
            },
            "floor_tables": [], "status": "trial",
            "trial_ends_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        }
        _state.bookings[rid_str] = []
        _state.floor_tables[rid_str] = []
        _state.table_slots[rid_str] = {}
        _state.review_queue[rid_str] = []
        _state.contacts[rid_str] = {}
        _state.stats[rid_str] = {"messages_today": 0, "bookings_today": 0, "languages": {}, "last_reset": today_paris().isoformat()}
        _state.daily_stats_history[rid_str] = []
        _state.waitlist[rid_str] = []
        _state.data_versions[rid_str] = 0
        _state.restaurant_status[rid_str] = {"status": "open", "message": "", "closed_dates": [], "full_dates": {}, "temp_message": "", "updated_at": datetime.utcnow().isoformat()}
        init_daily_slots(rid_str)

        # Generate JWT
        token = jwt_encode({"user_id": str(uid), "restaurant_id": rid_str, "email": email, "role": "owner"})

        logger.info(f"New restaurant registered: {restaurant_name} ({rid_str[:8]}...) by {email}")

        # Send welcome email + add to Brevo list + admin notification (async, don't block registration)
        import asyncio
        asyncio.create_task(send_brevo_welcome(email, first_name or restaurant_name, restaurant_name, password if generated_password else ""))
        asyncio.create_task(send_admin_notification_email(email, first_name, last_name, restaurant_name, phone))

        return {
            "status": "ok",
            "token": token,
            "user": {
                "email": email, "first_name": first_name, "last_name": last_name,
                "restaurant_name": restaurant_name, "restaurant_id": rid_str,
                "restaurant_status": "trial", "role": "owner",
            }
        }
    except Exception as e:
        logger.error(f"Register error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur lors de la création du compte"})


@router.post("/api/login")
@limiter.limit("10/minute")  # AUDIT FIX 2026-04-12
async def api_login(request: Request):
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    if check_login_lockout(client_ip):
        return JSONResponse(status_code=429, content={"error": "Compte temporairement verrouillé suite à trop de tentatives. Réessayez plus tard."})
    rl_ok, rl_limit, rl_remain, rl_window = check_rate_limit(client_ip, "/api/login")
    if not rl_ok:
        r = JSONResponse(status_code=429, content={"error": "Trop de tentatives. Veuillez réessayer dans quelques minutes."})
        r.headers["X-RateLimit-Limit"] = str(rl_limit); r.headers["X-RateLimit-Remaining"] = "0"; r.headers["Retry-After"] = str(rl_window)
        return r
    data = await safe_json(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "Email et mot de passe requis"})
    if not is_valid_email(email):
        return JSONResponse(status_code=400, content={"error": "Email invalide"})
    if not _state.db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de données non disponible"})
    try:
        async with _state.db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT u.id, u.email, u.password_hash, u.first_name, u.last_name, u.role,
                       u.restaurant_id, r.name as restaurant_name, r.status as restaurant_status,
                       r.trial_ends_at, r.slug
                FROM users u
                JOIN restaurants r ON u.restaurant_id = r.id
                WHERE u.email = $1
            """, email)
            if not row:
                # Timing attack prevention: always hash to equalize response time
                verify_password(password, "$2b$12$sL6SHbaVCcFgv.8NKeFSx.lARMSr.J00ZSER03mkreo1vKcib8iEa")
                record_login_failure(client_ip)
                return JSONResponse(status_code=401, content={"error": "Email ou mot de passe incorrect"})
            if not verify_password(password, row["password_hash"]):
                record_login_failure(client_ip)
                return JSONResponse(status_code=401, content={"error": "Email ou mot de passe incorrect"})
            record_login_success(client_ip)
            rid_str = str(row["restaurant_id"])
            token = jwt_encode({
                "user_id": str(row["id"]), "restaurant_id": rid_str,
                "email": row["email"], "role": row["role"],
            })
            response = JSONResponse(content={
                "status": "ok",
                "token": token,  # Keep for backwards compat
                "user": {
                    "email": row["email"],
                    "first_name": row["first_name"] or "",
                    "last_name": row["last_name"] or "",
                    "restaurant_name": row["restaurant_name"],
                    "restaurant_id": rid_str,
                    "restaurant_status": row["restaurant_status"],
                    "trial_ends_at": row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None,
                    "role": row["role"],
                    "slug": row["slug"],
                }
            })
            response.set_cookie(
                key="gs_token", value=token, httponly=True, secure=True,
                samesite="lax", max_age=86400 * 7, path="/"
            )
            return response
    except Exception as e:
        logger.error(f"Login error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


# In-memory password reset tokens (token -> {email, expires})
# _state.password_reset_tokens now imported from app.state


@router.post("/api/forgot-password")
async def api_forgot_password(request: Request):
    """Send a password reset email with a temporary code."""
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    rl_ok, *_ = check_rate_limit(client_ip, "/api/forgot-password")
    if not rl_ok:
        return JSONResponse(status_code=429, content={"error": "Trop de tentatives. Veuillez réessayer dans quelques minutes."})
    data = await safe_json(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    email = (data.get("email") or "").strip().lower()
    if not email or not is_valid_email(email):
        return JSONResponse(status_code=400, content={"error": "Email invalide"})
    if not _state.db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de donnees non disponible"})

    try:
        async with _state.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, first_name FROM users WHERE email = $1", email)
            if not row:
                # Don't reveal if email exists or not
                return {"status": "ok", "message": "Si un compte existe avec cet email, un lien de reinitialisation a ete envoye."}

        # Generate a 6-digit code
        import random
        code = f"{random.randint(100000, 999999)}"
        _state.password_reset_tokens[code] = {
            "email": email,
            "expires": time_mod.time() + 900,  # 15 minutes
        }

        # Send reset email via Brevo
        first_name = row["first_name"] or email
        if BREVO_API_KEY:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    await client.post(
                        "https://api.brevo.com/v3/smtp/email",
                        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                        json={
                            "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                            "to": [{"email": email, "name": first_name}],
                            "subject": "GuestScale — Reinitialisation de votre mot de passe",
                            "htmlContent": f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<div style="text-align:center;margin-bottom:24px">
<svg viewBox="0 0 32 32" fill="none" width="40" height="40" xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
<h1 style="font-size:22px;font-weight:800;color:#111827;margin:12px 0 4px">Reinitialisation du mot de passe</h1>
</div>
<p style="font-size:14px;color:#374151;margin-bottom:16px">Bonjour {first_name},</p>
<p style="font-size:14px;color:#374151;margin-bottom:20px">Vous avez demande la reinitialisation de votre mot de passe GuestScale. Voici votre code :</p>
<div style="text-align:center;margin-bottom:20px">
<div style="display:inline-block;padding:16px 32px;background:#F3F4F6;border-radius:12px;font-size:32px;font-weight:800;letter-spacing:8px;color:#111827">{code}</div>
</div>
<p style="font-size:13px;color:#6B7280;text-align:center;margin-bottom:20px">Ce code est valable 15 minutes.</p>
<p style="font-size:13px;color:#6B7280;text-align:center">Si vous n'avez pas demande cette reinitialisation, ignorez cet email.</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Restaurant AI Platform</p>
</div>""",
                        }
                    )
            except Exception as e:
                logger.error(f"Reset email error: {e}")

        logger.info(f"Password reset requested for {email}, code sent")
        return {"status": "ok", "message": "Si un compte existe avec cet email, un code de reinitialisation a ete envoye."}

    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


@router.post("/api/reset-password")
async def api_reset_password(request: Request):
    """Reset password using the code from email."""
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    rl_ok, *_ = check_rate_limit(client_ip, "/api/reset-password")
    if not rl_ok:
        return JSONResponse(status_code=429, content={"error": "Trop de tentatives. Veuillez réessayer dans quelques minutes."})
    data = await safe_json(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    code = (data.get("code") or "").strip()
    new_password = data.get("new_password", "")

    if not code or not new_password:
        return JSONResponse(status_code=400, content={"error": "Code et nouveau mot de passe requis"})
    if len(new_password) < 12:
        return JSONResponse(status_code=400, content={"error": "Le mot de passe doit contenir au moins 12 caracteres"})

    token_data = _state.password_reset_tokens.get(code)
    if not token_data:
        return JSONResponse(status_code=401, content={"error": "Code invalide ou expiré"})
    if time_mod.time() > token_data["expires"]:
        _state.password_reset_tokens.pop(code, None)
        return JSONResponse(status_code=401, content={"error": "Code expiré. Veuillez en demander un nouveau."})

    email = token_data["email"]
    _state.password_reset_tokens.pop(code, None)

    if not _state.db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de donnees non disponible"})

    try:
        async with _state.db_pool.acquire() as conn:
            pwd_hash = hash_password(new_password)
            result = await conn.execute("UPDATE users SET password_hash = $1 WHERE email = $2", pwd_hash, email)
            if "UPDATE 0" in result:
                return JSONResponse(status_code=404, content={"error": "Utilisateur non trouve"})
        logger.info(f"Password reset for {email}")
        return {"status": "ok", "message": "Mot de passe modifie avec succes. Vous pouvez vous connecter."}
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


@router.post("/api/logout")
async def api_logout():
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie("gs_token", path="/")
    return response



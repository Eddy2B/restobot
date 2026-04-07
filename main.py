"""
GuestScale — Multi-Tenant Restaurant AI Platform
Version 5.0 — Multi-tenant, JWT auth, PostgreSQL

DNS EMAIL CONFIG REQUISE (dans Cloudflare DNS) :
SPF : TXT guestscale.com -> "v=spf1 include:spf.brevo.com ~all"
DKIM : TXT brevo._domainkey.guestscale.com -> (cle fournie par Brevo)
DMARC : TXT _dmarc.guestscale.com -> "v=DMARC1; p=quarantine; rua=mailto:contact@guestscale.com"

PRICING AVRIL 2026 :
Fondateur : 99 EUR/mois, 500 msgs inclus, 0.08 EUR depassement
Standard : 149 EUR/mois, 1500 msgs inclus, 0.06 EUR depassement
Option vocal : +79 EUR/mois, 100 min, 0.50 EUR/min
Option broadcast WhatsApp : 0.15 EUR/msg prepaye
Option multi-etablissements : +49 EUR/mois/resto
Option white label : +99 EUR/mois
"""

import os
import json
import logging
import hashlib
import secrets
import re as re_mod
import uuid
import time as time_mod
from datetime import datetime, date, time, timedelta
from contextlib import asynccontextmanager
from collections import Counter
from html import escape as html_escape


def sanitize_input(value, max_length: int = 500):
    """Escape dangerous HTML characters and limit length."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    value = html_escape(value, quote=True)
    return value[:max_length]


def sanitize_dict(data: dict, fields: list, max_length: int = 500):
    """Sanitize multiple string fields in a dict in-place."""
    for f in fields:
        if f in data and isinstance(data[f], str):
            data[f] = sanitize_input(data[f], max_length)
    return data

import anthropic
import httpx
import asyncpg
import bcrypt

TONE_PROMPTS = {
    "premium": "STYLE : Vouvoiement obligatoire. Langage soutenu et elegant. Formulations raffinées. Pas d'emojis. Ton d'un maitre d'hotel de palace.",
    "casual": "STYLE : Ton chaleureux et decontracte. Tutoiement accepte si le client tutoie. Emojis moderes (1-2 par message max).",
    "beach": "STYLE : Tres decontracte et amical. Tutoiement naturel. Emojis frequents. Ton leger et ensoleille.",
    "classic": "STYLE : Vouvoiement systematique. Sobre et professionnel. Pas d'emojis. Phrases courtes et precises.",
}
from pathlib import Path
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ==============================================================
# CONFIG
# ==============================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
DATABASE_URL = os.getenv("DATABASE_URL", "")
PORT = int(os.getenv("PORT", 8000))
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", secrets.token_urlsafe(16))
APP_DOMAIN = os.getenv("APP_DOMAIN", "app.guestscale.com")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.getenv("BREVO_LIST_ID", "6"))

# Stripe
import stripe as stripe_mod
stripe_mod.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_FOUNDER = os.getenv("STRIPE_PRICE_FOUNDER", "")
STRIPE_PRICE_STANDARD = os.getenv("STRIPE_PRICE_STANDARD", "")

def get_restaurant_stripe_config(rid: str, key: str):
    rest = restaurants_cache.get(rid)
    if not rest:
        return None
    return rest.get("settings", {}).get(key)

def set_restaurant_stripe_config(rid: str, key: str, value):
    rest = restaurants_cache.get(rid)
    if rest:
        rest.setdefault("settings", {})[key] = value

def find_restaurant_by_stripe_customer(customer_id: str):
    for rid, rest in restaurants_cache.items():
        if rest.get("settings", {}).get("stripe_customer_id") == customer_id:
            return rid
    return None

# Twilio (missed call detection)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")  # The shared Twilio number for call forwarding

# Legacy support — kept for initial migration only
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("guestscale")

# ==============================================================
# RATE LIMITING
# ==============================================================

rate_limit_store = {}  # ip -> {endpoint -> [timestamps]}
login_failures = {}   # ip -> {"count": int, "locked_until": float}
RATE_LIMITS = {
    "/api/login": (5, 300),        # 5 attempts per 5 minutes (strict)
    "/api/register": (3, 300),     # 3 registrations per 5 minutes
    "/api/forgot-password": (3, 300),  # 3 reset requests per 5 minutes
    "/api/reset-password": (5, 300),   # 5 attempts per 5 minutes
    "default": (60, 60),           # 60 requests per minute for other endpoints
}

def check_rate_limit(ip: str, endpoint: str) -> tuple:
    """Returns (allowed: bool, limit: int, remaining: int, window: int)."""
    now = time_mod.time()
    key = endpoint if endpoint in RATE_LIMITS else "default"
    max_requests, window = RATE_LIMITS[key]

    if ip not in rate_limit_store:
        rate_limit_store[ip] = {}
    if key not in rate_limit_store[ip]:
        rate_limit_store[ip][key] = []

    # Clean old entries
    rate_limit_store[ip][key] = [t for t in rate_limit_store[ip][key] if now - t < window]

    remaining = max(0, max_requests - len(rate_limit_store[ip][key]))
    if len(rate_limit_store[ip][key]) >= max_requests:
        return False, max_requests, 0, window

    rate_limit_store[ip][key].append(now)
    return True, max_requests, remaining - 1, window

def check_login_lockout(ip: str) -> bool:
    """Returns True if IP is locked out from login attempts."""
    now = time_mod.time()
    if ip in login_failures:
        f = login_failures[ip]
        if f.get("locked_until", 0) > now:
            return True
        # Reset if lock expired
        if f.get("locked_until", 0) > 0 and f["locked_until"] <= now:
            login_failures[ip] = {"count": 0, "locked_until": 0}
    return False

def record_login_failure(ip: str):
    """Record a failed login attempt and apply progressive lockout."""
    now = time_mod.time()
    if ip not in login_failures:
        login_failures[ip] = {"count": 0, "locked_until": 0}
    login_failures[ip]["count"] += 1
    count = login_failures[ip]["count"]
    # Progressive lockout: 5 fails = 5min, 10 fails = 30min, 15+ = 2h
    if count >= 15:
        login_failures[ip]["locked_until"] = now + 7200
    elif count >= 10:
        login_failures[ip]["locked_until"] = now + 1800
    elif count >= 5:
        login_failures[ip]["locked_until"] = now + 300

def record_login_success(ip: str):
    """Reset failure counter on successful login."""
    if ip in login_failures:
        del login_failures[ip]

# ==============================================================
# IN-MEMORY CACHES (keyed by restaurant_id)
# ==============================================================

restaurants_cache = {}      # restaurant_id (UUID str): {id, slug, name, owner_phone, whatsapp_phone_number_id, whatsapp_access_token, whatsapp_verify_token, google_review_link, settings, floor_tables, status, trial_ends_at}
pid_to_restaurant = {}      # whatsapp_phone_number_id: restaurant_id (for webhook routing)
phone_to_restaurant = {}    # normalized phone number: restaurant_id (for Twilio missed call routing)
conversations = {}          # "restaurant_id:phone": [messages]
bookings = {}               # restaurant_id: [bookings]
floor_tables = {}           # restaurant_id: [{id, seats, zone, x, y, w, h, shape}]
table_slots = {}            # restaurant_id: {"12:30": {"T1": "available"}}
table_statuses = {}         # rid -> { "date:service:table_id": status }
table_groups = {}           # rid -> [{"tables": ["T3","T4"], "name": "T3+T4"}]
review_queue = {}           # restaurant_id: [reviews]
contacts = {}               # restaurant_id: {phone: contact_data}
campaigns_store = {}        # rid -> [campaign dicts]
restaurant_status = {}      # restaurant_id: {status, message, closed_dates, full_dates, temp_message, ...}
stats = {}                  # restaurant_id: {messages_today, bookings_today, languages, last_reset}
daily_stats_history = {}    # restaurant_id: [snapshots]
ai_paused_conversations = {}  # rid -> {phone: pause_until_iso}
escalations = {}            # rid -> [escalation dicts]
missed_call_tracker = {}    # rid -> {phone: {wa_sent_at, call_sent_at, date}}
usage_counters = {}  # rid -> {"2026-04": {"total": 0, "missed_call": 0, "reminder": 0, "review": 0, "other": 0}}

# Waitlist per restaurant
# waitlist[rid] = [{"id": "W1", "phone": ..., "name": ..., "covers": 2, "service": "soir", "date": "2026-03-26", "added_at": ..., "status": "waiting"|"notified"|"accepted"|"declined"|"expired", "notified_at": None, "position": 1}]
waitlist = {}               # restaurant_id: [entries]

PLAN_LIMITS = {"founder": 500, "standard": 500, "trial": 500}
PLAN_RATES = {"founder": 0.08, "standard": 0.06, "trial": 0.0}

async def increment_message_count(rid: str, msg_type: str = "other"):
    month = now_paris().strftime("%Y-%m")
    counters = usage_counters.setdefault(rid, {})
    if month not in counters:
        counters[month] = {"total": 0, "missed_call": 0, "reminder": 0, "review": 0, "other": 0}
    counters[month]["total"] += 1
    counters[month][msg_type] = counters[month].get(msg_type, 0) + 1
    # Check thresholds for alerts
    rest = restaurants_cache.get(rid, {})
    plan = rest.get("settings", {}).get("subscription_plan", "trial")
    limit = PLAN_LIMITS.get(plan, 500)
    total = counters[month]["total"]
    if total == int(limit * 0.8):
        logger.info(f"Usage alert 80%: {rid[:8]}... {total}/{limit}")
    elif total == limit:
        logger.info(f"Usage alert 100%: {rid[:8]}... {total}/{limit}")

# Data version counter per restaurant
data_versions = {}          # restaurant_id: int
def bump_version(restaurant_id: str):
    data_versions[restaurant_id] = data_versions.get(restaurant_id, 0) + 1


def today_paris() -> date:
    """Get today's date in Europe/Paris timezone."""
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Europe/Paris")).date()
    except Exception:
        return today_paris()


def now_paris() -> datetime:
    """Get current datetime in Europe/Paris timezone."""
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Europe/Paris"))
    except Exception as e:
        logger.warning(f"zoneinfo failed, falling back to UTC: {e}")
        return datetime.utcnow()


def format_date_fr(d) -> str:
    """Format a date in French: 'mercredi 1 avril 2026'."""
    jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
    mois_fr = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin',
               'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
    if isinstance(d, datetime):
        d = d.date()
    return f"{jours[d.weekday()]} {d.day} {mois_fr[d.month - 1]} {d.year}"


def normalize_phone(phone: str) -> str:
    """Normalize a phone number to international format (e.g. 33612345678)."""
    p = re_mod.sub(r'[^\d+]', '', phone.strip())
    if p.startswith('+'):
        p = p[1:]
    if p.startswith('00'):
        p = p[2:]
    if p.startswith('0') and len(p) == 10:
        p = '33' + p[1:]
    return p

# ==============================================================
# JWT AUTH
# ==============================================================

import hmac
import base64

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
        # Decode payload
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


# ==============================================================
# DATABASE
# ==============================================================

db_pool = None


async def init_db():
    """Initialize database pool and create/migrate tables."""
    global db_pool
    if not DATABASE_URL:
        logger.warning("No DATABASE_URL — running in-memory only")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        async with db_pool.acquire() as conn:
            # === NEW MULTI-TENANT TABLES ===
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS restaurants (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    owner_phone TEXT DEFAULT '',
                    whatsapp_phone_number_id TEXT DEFAULT '',
                    whatsapp_access_token TEXT DEFAULT '',
                    whatsapp_verify_token TEXT DEFAULT 'guestscale-verify',
                    google_review_link TEXT DEFAULT '',
                    settings JSONB DEFAULT '{}'::jsonb,
                    floor_tables JSONB DEFAULT '[]'::jsonb,
                    status TEXT DEFAULT 'trial',
                    trial_ends_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    first_name TEXT DEFAULT '',
                    last_name TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    restaurant_id UUID REFERENCES restaurants(id),
                    role TEXT DEFAULT 'owner',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS mt_bookings (
                    id TEXT NOT NULL,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    booking_date TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (id, restaurant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mt_bookings_rid ON mt_bookings(restaurant_id);
                CREATE INDEX IF NOT EXISTS idx_mt_bookings_date ON mt_bookings(restaurant_id, booking_date);

                CREATE TABLE IF NOT EXISTS mt_contacts (
                    phone TEXT NOT NULL,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (phone, restaurant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mt_contacts_rid ON mt_contacts(restaurant_id);

                CREATE TABLE IF NOT EXISTS mt_conversations (
                    conv_key TEXT NOT NULL,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (conv_key, restaurant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mt_conversations_rid ON mt_conversations(restaurant_id);

                CREATE TABLE IF NOT EXISTS mt_review_queue (
                    id SERIAL PRIMARY KEY,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_mt_reviews_rid ON mt_review_queue(restaurant_id);

                CREATE TABLE IF NOT EXISTS mt_restaurant_status (
                    restaurant_id UUID PRIMARY KEY REFERENCES restaurants(id),
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS mt_daily_stats (
                    id SERIAL PRIMARY KEY,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    stat_date TEXT NOT NULL,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_mt_daily_stats_rid ON mt_daily_stats(restaurant_id, stat_date);

                CREATE TABLE IF NOT EXISTS mt_waitlist (
                    id SERIAL PRIMARY KEY,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    wait_date TEXT DEFAULT '',
                    status TEXT DEFAULT 'waiting',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_mt_waitlist_rid ON mt_waitlist(restaurant_id, wait_date, status);
            """)
        logger.info("Database connected, multi-tenant tables ready")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        db_pool = None


# ==============================================================
# DB HELPERS (multi-tenant)
# ==============================================================

async def db_save_booking(restaurant_id: str, booking: dict):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_bookings (id, restaurant_id, data, booking_date, created_at)
                VALUES ($1, $2::uuid, $3::jsonb, $4, NOW())
                ON CONFLICT (id, restaurant_id) DO UPDATE SET data = $3::jsonb
            """, booking["id"], restaurant_id, json.dumps(booking, default=str), booking.get("date", ""))
    except Exception as e:
        logger.error(f"DB save booking error: {e}")


async def db_save_contact(restaurant_id: str, phone: str, data: dict):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_contacts (phone, restaurant_id, data, updated_at)
                VALUES ($1, $2::uuid, $3::jsonb, NOW())
                ON CONFLICT (phone, restaurant_id) DO UPDATE SET data = $3::jsonb, updated_at = NOW()
            """, phone, restaurant_id, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save contact error: {e}")


async def db_save_conversation(restaurant_id: str, conv_key: str, messages: list):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_conversations (conv_key, restaurant_id, messages, updated_at)
                VALUES ($1, $2::uuid, $3::jsonb, NOW())
                ON CONFLICT (conv_key, restaurant_id) DO UPDATE SET messages = $3::jsonb, updated_at = NOW()
            """, conv_key, restaurant_id, json.dumps(messages, default=str))
    except Exception as e:
        logger.error(f"DB save conversation error: {e}")


async def db_save_review(restaurant_id: str, review: dict):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_review_queue (restaurant_id, data, created_at)
                VALUES ($1::uuid, $2::jsonb, NOW())
            """, restaurant_id, json.dumps(review, default=str))
    except Exception as e:
        logger.error(f"DB save review error: {e}")


async def db_mark_review_sent(restaurant_id: str, phone: str):
    """Mark all reviews for a phone as sent in the DB to prevent re-sending on restart."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            # Update the JSON data to set sent=true for matching phone
            await conn.execute("""
                UPDATE mt_review_queue 
                SET data = jsonb_set(data, '{sent}', 'true')
                WHERE restaurant_id = $1::uuid 
                AND data->>'phone' = $2
                AND (data->>'sent')::text != 'true'
            """, restaurant_id, phone)
    except Exception as e:
        logger.error(f"DB mark review sent error: {e}")


async def db_save_restaurant_status(restaurant_id: str, data: dict):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_restaurant_status (restaurant_id, data, updated_at)
                VALUES ($1::uuid, $2::jsonb, NOW())
                ON CONFLICT (restaurant_id) DO UPDATE SET data = $2::jsonb, updated_at = NOW()
            """, restaurant_id, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save restaurant status error: {e}")


async def db_save_daily_stats(restaurant_id: str, stat_date: str, data: dict):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_daily_stats (restaurant_id, stat_date, data, created_at)
                VALUES ($1::uuid, $2, $3::jsonb, NOW())
            """, restaurant_id, stat_date, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save daily stats error: {e}")


async def db_save_waitlist_entry(restaurant_id: str, entry: dict):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_waitlist (restaurant_id, data, wait_date, status, created_at)
                VALUES ($1::uuid, $2::jsonb, $3, $4, NOW())
            """, restaurant_id, json.dumps(entry, default=str), entry.get("date", ""), entry.get("status", "waiting"))
    except Exception as e:
        logger.error(f"DB save waitlist error: {e}")


async def db_update_waitlist_status(restaurant_id: str, entry_id: str, status: str):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE mt_waitlist SET status = $3, data = jsonb_set(data, '{status}', to_jsonb($3::text))
                WHERE restaurant_id = $1::uuid AND data->>'id' = $2
            """, restaurant_id, entry_id, status)
    except Exception as e:
        logger.error(f"DB update waitlist error: {e}")


async def db_save_restaurant(restaurant_id: str, rest: dict):
    """Persist restaurant settings + floor_tables back to DB."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE restaurants SET
                    name = $2, owner_phone = $3, settings = $4::jsonb,
                    floor_tables = $5::jsonb, google_review_link = $6,
                    updated_at = NOW()
                WHERE id = $1::uuid
            """, restaurant_id, rest.get("name", ""),
                rest.get("owner_phone", ""),
                json.dumps(rest.get("settings", {})),
                json.dumps(rest.get("floor_tables", [])),
                rest.get("google_review_link", ""))
    except Exception as e:
        logger.error(f"DB save restaurant error: {e}")


# ==============================================================
# LOAD ALL DATA FROM DB
# ==============================================================

async def load_all_restaurants():
    """Load all restaurants and their data from DB into memory."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            # Load restaurants
            rows = await conn.fetch("SELECT * FROM restaurants")
            for row in rows:
                rid = str(row["id"])
                rest = {
                    "id": rid,
                    "slug": row["slug"],
                    "name": row["name"],
                    "owner_phone": row["owner_phone"] or "",
                    "whatsapp_phone_number_id": row["whatsapp_phone_number_id"] or "",
                    "whatsapp_access_token": row["whatsapp_access_token"] or "",
                    "whatsapp_verify_token": row["whatsapp_verify_token"] or "guestscale-verify",
                    "google_review_link": row["google_review_link"] or "",
                    "settings": json.loads(row["settings"]) if row["settings"] else {},
                    "floor_tables": json.loads(row["floor_tables"]) if row["floor_tables"] else [],
                    "status": row["status"] or "trial",
                    "trial_ends_at": row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                restaurants_cache[rid] = rest

                # Map phone_number_id -> restaurant_id for webhook routing
                if rest["whatsapp_phone_number_id"]:
                    pid_to_restaurant[rest["whatsapp_phone_number_id"]] = rid

                # Map restaurant phone number -> restaurant_id for Twilio missed call routing
                rest_phone = rest.get("settings", {}).get("phone", "") or rest.get("phone", "")
                if rest_phone:
                    normalized = normalize_phone(rest_phone)
                    if normalized:
                        phone_to_restaurant[normalized] = rid
                # Also map Twilio number if configured
                twilio_num = rest.get("settings", {}).get("twilio_number", "")
                if twilio_num:
                    normalized_twilio = normalize_phone(twilio_num)
                    if normalized_twilio:
                        phone_to_restaurant[normalized_twilio] = rid

                # Init in-memory structures
                bookings[rid] = []
                floor_tables[rid] = rest["floor_tables"]
                review_queue[rid] = []
                contacts[rid] = {}
                stats[rid] = {"messages_today": 0, "bookings_today": 0, "languages": {}, "last_reset": today_paris().isoformat()}
                daily_stats_history[rid] = []
                waitlist[rid] = []
                data_versions[rid] = 0

                # Init restaurant status
                restaurant_status[rid] = {
                    "status": "open", "message": "", "closed_dates": [],
                    "full_dates": {}, "temp_message": "",
                    "updated_at": datetime.utcnow().isoformat(),
                }

                logger.info(f"Loaded restaurant: {rest['name']} ({rid[:8]}...)")

            # Load bookings
            rows = await conn.fetch("SELECT restaurant_id, data FROM mt_bookings ORDER BY created_at DESC LIMIT 5000")
            for row in rows:
                rid = str(row["restaurant_id"])
                if rid in bookings:
                    bookings[rid].append(json.loads(row["data"]))
            logger.info(f"Loaded {len(rows)} bookings")

            # Load contacts
            rows = await conn.fetch("SELECT restaurant_id, phone, data FROM mt_contacts ORDER BY updated_at DESC LIMIT 50000")
            for row in rows:
                rid = str(row["restaurant_id"])
                if rid in contacts:
                    contacts[rid][row["phone"]] = json.loads(row["data"])
            logger.info(f"Loaded {len(rows)} contacts")

            # Load conversations
            rows = await conn.fetch("SELECT restaurant_id, conv_key, messages FROM mt_conversations ORDER BY updated_at DESC LIMIT 10000")
            for row in rows:
                rid = str(row["restaurant_id"])
                conv_key = row["conv_key"]
                conversations[f"{rid}:{conv_key}"] = json.loads(row["messages"])
            logger.info(f"Loaded {len(rows)} conversations")

            # Load review queues - only recent and not yet sent
            rows = await conn.fetch("""
                SELECT restaurant_id, data FROM mt_review_queue 
                WHERE created_at > NOW() - INTERVAL '48 hours'
                AND (data->>'sent')::text != 'true'
                ORDER BY created_at DESC LIMIT 500
            """)
            for row in rows:
                rid = str(row["restaurant_id"])
                if rid in review_queue:
                    review_queue[rid].append(json.loads(row["data"]))
            logger.info(f"Loaded {len(rows)} review queue items")

            # Load restaurant statuses
            rows = await conn.fetch("SELECT restaurant_id, data FROM mt_restaurant_status")
            for row in rows:
                rid = str(row["restaurant_id"])
                if rid in restaurant_status:
                    restaurant_status[rid] = json.loads(row["data"])
                    # Propagate reminders_enabled into settings cache
                    if "reminders_enabled" in restaurant_status[rid] and rid in restaurants_cache:
                        restaurants_cache[rid].setdefault("settings", {})["reminders_enabled"] = restaurant_status[rid]["reminders_enabled"]
            logger.info("Loaded restaurant statuses")

            # Load daily stats history
            rows = await conn.fetch("SELECT restaurant_id, data FROM mt_daily_stats ORDER BY stat_date DESC LIMIT 3000")
            for row in rows:
                rid = str(row["restaurant_id"])
                if rid in daily_stats_history:
                    daily_stats_history[rid].append(json.loads(row["data"]))
            logger.info(f"Loaded {len(rows)} daily stats")

            # Load waitlist
            rows = await conn.fetch("SELECT restaurant_id, data FROM mt_waitlist WHERE status IN ('waiting','notified') ORDER BY created_at ASC LIMIT 5000")
            for row in rows:
                rid = str(row["restaurant_id"])
                if rid in waitlist:
                    waitlist[rid].append(json.loads(row["data"]))
            logger.info(f"Loaded {len(rows)} waitlist entries")

            # Init floor plan slots for all restaurants
            for rid in restaurants_cache:
                table_slots[rid] = {}
                init_daily_slots(rid)

            # Re-assign tables for today's bookings (restore slot occupancy after restart)
            today_str = today_paris().isoformat()
            for rid in restaurants_cache:
                rid_bookings = bookings.get(rid, [])
                for b in rid_bookings:
                    if not (b.get("date", "")).startswith(today_str):
                        continue
                    bt = b.get("booking_time") or b.get("time", "")
                    tbl = b.get("table")
                    if bt and tbl:
                        assign_table(rid, bt, tbl, b.get("id", ""))
                logger.info(f"Restored {sum(1 for b in rid_bookings if (b.get('date','').startswith(today_str) and b.get('table')))} table assignments for {restaurants_cache[rid]['name']}")

    except Exception as e:
        logger.error(f"DB load error: {e}")


# ==============================================================
# FLOOR PLAN & TABLE MANAGEMENT
# ==============================================================

MIDI_SLOTS = ["12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45","14:00","14:15"]
SOIR_SLOTS = ["19:00","19:15","19:30","19:45","20:00","20:15","20:30","20:45","21:00","21:15","21:30","21:45","22:00","22:15","22:30"]
ALL_SLOTS = MIDI_SLOTS + SOIR_SLOTS


def init_daily_slots(rid: str):
    tables = floor_tables.get(rid, [])
    slots = {}
    for slot_time in ALL_SLOTS:
        slots[slot_time] = {}
        for t in tables:
            slots[slot_time][t["id"]] = "available"
    table_slots[rid] = slots


def find_best_table(rid: str, slot_time: str, covers: int, zone_pref: str = None) -> str | None:
    tables = floor_tables.get(rid, [])
    slots = table_slots.get(rid, {}).get(slot_time, {})
    # Collect all available tables
    available = []
    for t in tables:
        if slots.get(t["id"]) == "available":
            available.append(t)

    # Try single table (prefer zone, then any)
    for try_zone in ([zone_pref, None] if zone_pref else [None]):
        candidates = []
        for t in available:
            if t["seats"] < covers:
                continue
            if try_zone and t["zone"] != try_zone:
                continue
            candidates.append(t)
        if candidates:
            candidates.sort(key=lambda t: t["seats"])
            return candidates[0]["id"]

    # No single table fits — find smallest combination that covers the group
    # Strategy: take the biggest available table, then find the smallest complement
    pool = sorted(available, key=lambda t: t["seats"], reverse=True)
    if zone_pref:
        pool = sorted(pool, key=lambda t: (0 if t["zone"] == zone_pref else 1, -t["seats"]))
    if not pool:
        return None
    # Start with the largest table, then add the smallest table that fills the gap
    best_combo = None
    best_waste = 999
    for i, big in enumerate(pool):
        remaining = covers - big["seats"]
        if remaining <= 0:
            continue  # single table would have matched above
        combo = [big]
        total = big["seats"]
        # Sort remaining tables by seats ascending to pick the smallest that fills the gap
        rest = sorted([t for j, t in enumerate(pool) if j != i], key=lambda t: t["seats"])
        for t in rest:
            combo.append(t)
            total += t["seats"]
            if total >= covers:
                break
        if total >= covers:
            waste = total - covers
            if waste < best_waste:
                best_waste = waste
                best_combo = list(combo)
    if best_combo:
        best_combo.sort(key=lambda t: t["seats"], reverse=True)
        return "+".join(t["id"] for t in best_combo)
    return None


MEAL_DURATION_SLOTS = 8  # 8 x 15min = 2h meal duration

def _split_table_ids(table_id: str) -> list:
    """Split a possibly combined table id like 'T5+T3' into ['T5','T3']."""
    return [t.strip() for t in table_id.split("+") if t.strip()]

def assign_table(rid: str, slot_time: str, table_id: str, booking_id: str):
    """Block a table (or multi-table combo) for 2h starting from the booking slot."""
    if rid not in table_slots:
        return
    ids = _split_table_ids(table_id)
    try:
        start_idx = ALL_SLOTS.index(slot_time)
    except ValueError:
        if slot_time in table_slots[rid]:
            for tid in ids:
                table_slots[rid][slot_time][tid] = f"booked:{booking_id}"
        return
    for i in range(MEAL_DURATION_SLOTS):
        idx = start_idx + i
        if idx >= len(ALL_SLOTS):
            break
        s = ALL_SLOTS[idx]
        if s in table_slots[rid]:
            for tid in ids:
                table_slots[rid][s][tid] = f"booked:{booking_id}"


def release_table(rid: str, slot_time: str, table_id: str):
    """Release a table (or multi-table combo) for 2h starting from the slot."""
    if rid not in table_slots:
        return
    ids = _split_table_ids(table_id)
    try:
        start_idx = ALL_SLOTS.index(slot_time)
    except ValueError:
        if slot_time in table_slots.get(rid, {}):
            for tid in ids:
                table_slots[rid][slot_time][tid] = "available"
        return
    for i in range(MEAL_DURATION_SLOTS):
        idx = start_idx + i
        if idx >= len(ALL_SLOTS):
            break
        s = ALL_SLOTS[idx]
        if s in table_slots[rid]:
            for tid in ids:
                table_slots[rid][s][tid] = "available"


def get_available_slots(rid: str, covers: int, service: str = None) -> list:
    slots_to_check = ALL_SLOTS
    if service == "midi":
        slots_to_check = MIDI_SLOTS
    elif service == "soir":
        slots_to_check = SOIR_SLOTS
    available = []
    for slot_time in slots_to_check:
        if find_best_table(rid, slot_time, covers):
            available.append(slot_time)
    return available


def get_slot_summary(rid: str) -> dict:
    tables = floor_tables.get(rid, [])
    slots = table_slots.get(rid, {})
    summary = {}
    for slot_time in ALL_SLOTS:
        slot_data = slots.get(slot_time, {})
        total = len(tables)
        avail = sum(1 for t in tables if slot_data.get(t["id"]) == "available")
        summary[slot_time] = {"total": total, "available": avail, "booked": total - avail}
    return summary


def build_availability_context(rid: str) -> str:
    summary = get_slot_summary(rid)
    tables = floor_tables.get(rid, [])
    total_tables = len(tables)
    midi_avail = [t for t in MIDI_SLOTS if summary[t]["available"] > 0]
    soir_avail = [t for t in SOIR_SLOTS if summary[t]["available"] > 0]
    lines = [f"\n📅 DISPONIBILITÉS POUR AUJOURD'HUI ({today_paris().strftime('%A %d %B').lower()}) :"]
    if not midi_avail:
        lines.append("MIDI : COMPLET (aucune table disponible)")
    else:
        lines.append(f"MIDI : {len(midi_avail)} créneaux disponibles ({', '.join(midi_avail[:5])}{'...' if len(midi_avail) > 5 else ''})")
    if not soir_avail:
        lines.append("SOIR : COMPLET (aucune table disponible)")
    else:
        lines.append(f"SOIR : {len(soir_avail)} créneaux disponibles ({', '.join(soir_avail[:5])}{'...' if len(soir_avail) > 5 else ''})")

    # Per-zone availability for key evening slots
    lines.append("")
    lines.append("DÉTAIL PAR ZONE ET CRÉNEAU (ce soir) :")
    key_slots = ["19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]
    zones_list = list(set(t.get("zone", "salle") for t in tables))
    for z in sorted(zones_list):
        zone_tables = [t for t in tables if t.get("zone", "salle") == z]
        slot_info = []
        for sl in key_slots:
            slot_data = table_slots.get(rid, {}).get(sl, {})
            free = sum(1 for t in zone_tables if slot_data.get(t["id"]) == "available")
            if free == 0:
                slot_info.append(f"{sl} COMPLET")
            else:
                slot_info.append(f"{sl} ({free} libre{'s' if free > 1 else ''})")
        lines.append(f"  {z.upper()} ({len(zone_tables)} tables) : {' | '.join(slot_info)}")

    max_seats = max(t["seats"] for t in tables) if tables else 0
    lines.append(f"\nCapacité max par table : {max_seats} personnes")
    lines.append("")
    lines.append("INSTRUCTIONS RÉSERVATION :")
    lines.append("- Quand un client veut réserver, collecte : nombre de personnes, DATE, heure souhaitée, nom, et préférence zone (salle/terrasse) si demandée.")
    lines.append("- Si le client ne précise pas de date, DEMANDE-LUI pour quelle date.")
    lines.append("- Le client peut réserver pour auj., demain, ou n'importe quel jour futur.")
    lines.append("- Les disponibilités en temps réel ci-dessus sont pour AUJOURD'HUI uniquement. Pour les autres jours, accepte la réservation et le restaurant validera.")
    lines.append("- RÈGLE STRICTE : Si une ZONE est COMPLET (ex: salle complet) et le client veut cette zone, tu DOIS lui dire que cette zone est complète. Propose une autre zone disponible ou un autre créneau.")
    lines.append("- RÈGLE STRICTE : Si MIDI ou SOIR est entièrement COMPLET, tu DOIS dire que c'est complet. NE CONFIRME PAS de réservation. Propose la liste d&#39;attente ou un autre jour.")
    lines.append("- Si le créneau demandé est complet, propose les créneaux les plus proches disponibles OU la liste d&#39;attente OU un autre jour.")
    lines.append("- Si un créneau est dispo dans la zone souhaitée, confirme la réservation en précisant le créneau et la date.")
    lines.append("- NE JAMAIS mentionner les numéros de table au client. Dis simplement que la réservation est confirmée.")
    lines.append("- NE JAMAIS confirmer une réservation si aucun créneau n'est disponible pour la zone et la période demandées.")
    return "\n".join(lines)


def extract_booking_date(message: str) -> str:
    import re
    msg = message.lower().strip()
    today = today_paris()

    # "ce soir", "auj.", "tonight", "today", "this evening"
    if any(k in msg for k in ("ce soir", "ce midi", "aujourd", "tonight", "today", "this evening", "stasera", "oggi", "heute")):
        return today.isoformat()

    months_fr = {"janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
                 "juillet": 7, "aout": 8, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12}
    m = re.search(r'(\d{1,2})\s+(janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre)(?:\s+(\d{4}))?', msg)
    if m:
        day = int(m.group(1))
        month = months_fr.get(m.group(2), 1)
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, month, day)
            if d < today:
                d = date(year + 1, month, day)
            return d.isoformat()
        except ValueError:
            pass
    m = re.search(r'(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?', msg)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            d = date(year, month, day)
            if d < today:
                d = date(year + 1, month, day)
            return d.isoformat()
        except ValueError:
            pass
    if "demain" in msg or "tomorrow" in msg or "domani" in msg or "morgen" in msg:
        return (today + timedelta(days=1)).isoformat()
    if "après-demain" in msg or "après demain" in msg:
        return (today + timedelta(days=2)).isoformat()
    days_fr = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
    for day_name, weekday in days_fr.items():
        if day_name in msg:
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).isoformat()
    return today.isoformat()


# ==============================================================
# REVIEW FOLLOWUP
# ==============================================================

async def schedule_review_followup(rid: str, customer_phone: str, customer_name: str, booking_time: str):
    rq = review_queue.get(rid, [])
    rq.append({
        "phone": customer_phone,
        "name": customer_name,
        "booking_time": booking_time,
        "restaurant_id": rid,
        "scheduled_at": datetime.utcnow().isoformat(),
        "sent": False,
    })
    review_queue[rid] = rq
    logger.info(f"Review followup scheduled for {customer_name} ({customer_phone})")
    await db_save_review(rid, rq[-1])


async def send_review_request(rid: str, customer_phone: str, customer_name: str):
    rest = restaurants_cache.get(rid)
    if not rest:
        return
    name = customer_name.split()[0] if customer_name else ""
    greeting = f"Bonjour {name} ! " if name else "Bonjour ! "
    message = (
        f"{greeting}Merci d'avoir choisi {rest['name']} ! 😊\n\n"
        f"Comment s'est passé votre repas ? Votre avis nous intéresse !"
    )
    await send_whatsapp_message(
        rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], customer_phone, message
    )
    await db_mark_review_sent(rid, customer_phone)
    logger.info(f"Review request sent to {customer_phone}")
    await increment_message_count(rid, "review")


async def handle_review_response(rid: str, customer_phone: str, message_text: str) -> str | None:
    rq = review_queue.get(rid, [])
    pending = [r for r in rq if r["phone"] == customer_phone and r["sent"] and not r.get("responded")]
    if not pending:
        return None
    # Don't intercept greetings or new conversation starters — let them go to the AI
    greeting_patterns = ["bonjour", "bonsoir", "salut", "hello", "hi", "hey", "coucou", "bonne soirée",
                         "good morning", "good evening", "buongiorno", "buonasera"]
    question_patterns = ["réserver", "reservation", "réservation", "menu", "carte", "horaire", "ouvert",
                         "table", "disponible", "prix", "adresse", "fermé", "ferme", "heure", "place",
                         "combien", "est-ce que", "est ce que", "vous êtes", "vous etes", "c'est",
                         "quel", "quelle", "comment", "où", "quand", "?"]
    msg_lower = message_text.strip().lower()
    is_greeting = any(msg_lower.startswith(g) or msg_lower == g for g in greeting_patterns)
    is_question = any(kw in msg_lower for kw in question_patterns)
    if is_greeting or is_question:
        # Mark reviews as responded to avoid future interception
        for r in pending:
            r["responded"] = True
        return None
    rest = restaurants_cache.get(rid)
    if not rest:
        return None
    client = get_claude()
    try:
        resp = await client.messages.create(
            model=CLAUDE_MODEL, max_tokens=10,
            system="Analyze the following restaurant review response. Reply with ONLY one word: POSITIVE, NEGATIVE, or NEUTRAL. The response is: ",
            messages=[{"role": "user", "content": message_text}],
            temperature=0,
        )
        sentiment = resp.content[0].text.strip().upper()
    except Exception:
        sentiment = "NEUTRAL"
    for r in pending:
        r["responded"] = True
        r["sentiment"] = sentiment
        r["response"] = message_text[:200]
    google_link = rest.get("google_review_link", "")
    if "POSITIVE" in sentiment:
        if google_link:
            return (f"Merci beaucoup, c'est adorable ! 🥰\n\nVotre avis compte énormément pour nous et notre équipe. "
                    f"Si vous avez 30 secondes, un petit mot sur Google nous aiderait beaucoup :\n\n⭐ {google_link}\n\nMerci et à très bientôt !")
        return "Merci beaucoup pour votre retour ! 🥰 Nous sommes ravis que vous ayez passé un bon moment. À très bientôt !"
    elif "NEGATIVE" in sentiment:
        return (f"Merci pour votre retour, nous sommes désolés que l'expérience n'ait pas été à la hauteur. 😔\n\n"
                f"Votre avis est précieux et nous allons le transmettre directement à notre équipe. "
                f"Nous ferons tout pour nous améliorer.\n\nN'hésitez pas à nous donner plus de détails, nous prenons chaque retour très au sérieux. 🙏")
    else:
        if google_link:
            return (f"Merci pour votre retour ! 😊\n\nSi vous souhaitez partager votre expérience, votre avis sur Google nous aiderait beaucoup :\n\n⭐ {google_link}\n\nÀ très bientôt !")
        return "Merci pour votre retour ! 😊 À très bientôt !"


async def process_review_queue():
    now = now_paris()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    for rid, rq in review_queue.items():
        rest = restaurants_cache.get(rid)
        if not rest:
            continue
        for r in rq:
            if r["sent"] or r.get("responded"):
                continue
            scheduled_at = r.get("scheduled_at", "")
            if not scheduled_at:
                r["sent"] = True
                continue
            scheduled_date = scheduled_at[:10]
            # Skip reviews older than yesterday
            if scheduled_date < yesterday:
                r["sent"] = True
                continue

            booking_time_str = r.get("booking_time", "")
            if booking_time_str and ":" in booking_time_str:
                try:
                    bh, bm = booking_time_str.split(":")
                    booking_hour = int(bh)
                    is_dinner = booking_hour >= 18

                    if is_dinner:
                        # Dinner: send next day at 10:30
                        booking_date = date.fromisoformat(scheduled_date)
                        next_day = booking_date + timedelta(days=1)
                        send_after = datetime(next_day.year, next_day.month, next_day.day, 10, 30)
                        # Make timezone-aware for comparison
                        try:
                            import zoneinfo
                            send_after = send_after.replace(tzinfo=zoneinfo.ZoneInfo("Europe/Paris"))
                        except Exception:
                            pass
                        if now.replace(tzinfo=None) >= send_after.replace(tzinfo=None):
                            await send_review_request(rid, r["phone"], r["name"])
                            r["sent"] = True
                    else:
                        # Lunch: send 5h after booking time (same day)
                        booking_date = date.fromisoformat(scheduled_date)
                        meal_dt = datetime(booking_date.year, booking_date.month, booking_date.day, booking_hour, int(bm))
                        send_after = meal_dt + timedelta(hours=5)
                        if now.replace(tzinfo=None) >= send_after:
                            await send_review_request(rid, r["phone"], r["name"])
                            r["sent"] = True
                except Exception as e:
                    logger.warning(f"Review timing error: {e}")
                    r["sent"] = True
            else:
                # No booking time: send 3h after scheduling
                try:
                    scheduled = datetime.fromisoformat(scheduled_at)
                    if (now.replace(tzinfo=None) - scheduled).total_seconds() > 10800:
                        await send_review_request(rid, r["phone"], r["name"])
                        r["sent"] = True
                except Exception:
                    r["sent"] = True


# ==============================================================
# WAITLIST SYSTEM
# ==============================================================

def get_waitlist_timeout(booking_time_str: str, service: str) -> int:
    """Calculate timeout in minutes before cascading to next person.
    Dynamic: more time if reservation is far away, less if close."""
    now = datetime.utcnow()
    if booking_time_str and ":" in booking_time_str:
        try:
            bh, bm = booking_time_str.split(":")
            target = now.replace(hour=int(bh), minute=int(bm), second=0)
            minutes_until = (target - now).total_seconds() / 60
            if minutes_until > 180:  # >3h away
                return 45
            elif minutes_until > 60:  # 1-3h away
                return 30
            else:  # <1h away
                return 20
        except Exception:
            pass
    # Default by service
    if service == "midi":
        return 30
    return 45


async def add_to_waitlist(rid: str, phone: str, name: str, covers: int, service: str, booking_date: str, booking_time: str = ""):
    """Add a customer to the waitlist for a specific date/service."""
    wl = waitlist.setdefault(rid, [])
    # Check if already on waitlist
    for w in wl:
        if w["phone"] == phone and w["date"] == booking_date and w["service"] == service and w["status"] == "waiting":
            return None  # Already on waitlist
    position = len([w for w in wl if w["date"] == booking_date and w["service"] == service and w["status"] in ("waiting", "notified")]) + 1
    entry = {
        "id": f"W{len(wl)+1}_{rid[:8]}",
        "phone": phone,
        "name": name,
        "covers": covers,
        "service": service,
        "date": booking_date,
        "preferred_time": booking_time,
        "added_at": datetime.utcnow().isoformat(),
        "status": "waiting",  # waiting, notified, accepted, declined, expired
        "notified_at": None,
        "position": position,
    }
    wl.append(entry)
    await db_save_waitlist_entry(rid, entry)
    bump_version(rid)
    logger.info(f"Waitlist: {name} added at position {position} for {booking_date} {service}")
    return entry


async def notify_next_on_waitlist(rid: str, booking_date: str, service: str, freed_time: str = "", freed_covers: int = 0):
    """When a table becomes available, notify the next person on the waitlist."""
    rest = restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id"):
        return
    wl = waitlist.get(rid, [])
    # Find next waiting person for this date/service who fits the table size
    candidates = [w for w in wl if w["date"] == booking_date and w["service"] == service and w["status"] == "waiting"]
    if freed_covers > 0:
        # Prioritize people whose party size fits
        fitting = [w for w in candidates if w["covers"] <= freed_covers]
        if fitting:
            candidates = fitting
    candidates.sort(key=lambda w: w["position"])
    if not candidates:
        return
    next_person = candidates[0]
    next_person["status"] = "notified"
    next_person["notified_at"] = datetime.utcnow().isoformat()
    await db_update_waitlist_status(rid, next_person["id"], "notified")
    bump_version(rid)
    # Send WhatsApp message
    service_label = "ce midi" if service == "midi" else "ce soir"
    time_label = f" vers {freed_time}" if freed_time else ""
    msg = (
        f"Bonjour {next_person['name'].split()[0] if next_person['name'] else ''} ! 🎉\n\n"
        f"Bonne nouvelle ! Une table s'est libérée {service_label}{time_label} chez {rest['name']}.\n\n"
        f"Souhaitez-vous la réserver pour {next_person['covers']} personnes ?\n\n"
        f"Répondez *OUI* pour confirmer ou *NON* si vous n'êtes plus disponible."
    )
    await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], next_person["phone"], msg)
    logger.info(f"Waitlist: notified {next_person['name']} ({next_person['phone']}) for {booking_date} {service}")


async def handle_waitlist_response(rid: str, customer_phone: str, message_text: str) -> str | None:
    """Check if customer is responding to a waitlist notification."""
    wl = waitlist.get(rid, [])
    notified = [w for w in wl if w["phone"] == customer_phone and w["status"] == "notified"]
    if not notified:
        return None
    rest = restaurants_cache.get(rid)
    if not rest:
        return None
    entry = notified[0]
    msg_upper = message_text.strip().upper()
    if msg_upper in ("OUI", "YES", "SI", "OK", "D'ACCORD", "DACCORD", "PARFAIT", "JE CONFIRME", "CONFIRME"):
        entry["status"] = "accepted"
        await db_update_waitlist_status(rid, entry["id"], "accepted")
        # Create the booking
        rid_bookings = bookings.setdefault(rid, [])
        booking_id = f"R{len(rid_bookings)+1}"
        booking_time = entry.get("preferred_time", "")
        assigned_table = None
        if booking_time and booking_time in ALL_SLOTS:
            assigned_table = find_best_table(rid, booking_time, entry["covers"])
            if assigned_table:
                assign_table(rid, booking_time, assigned_table, booking_id)
        new_booking = {
            "id": booking_id, "phone": customer_phone, "name": entry["name"],
            "message": "Via liste d&#39;attente", "timestamp": datetime.utcnow().isoformat(),
            "date": entry["date"], "status": "confirmed" if assigned_table else "pending",
            "time": booking_time, "booking_time": booking_time,
            "covers": entry["covers"], "table": assigned_table, "zone": "", "source": "waitlist",
        }
        rid_bookings.append(new_booking)
        await db_save_booking(rid, new_booking)
        bump_version(rid)
        logger.info(f"Waitlist: {entry['name']} accepted, booking {booking_id} created")
        return f"Parfait, c'est confirmé ! 🎉 Votre table est réservée pour {entry['covers']} personnes. À tout à l'heure !"
    elif msg_upper in ("NON", "NO", "PAS POSSIBLE", "ANNULER", "ANNULE", "CANCEL"):
        entry["status"] = "declined"
        await db_update_waitlist_status(rid, entry["id"], "declined")
        bump_version(rid)
        logger.info(f"Waitlist: {entry['name']} declined")
        # Notify the next person
        await notify_next_on_waitlist(rid, entry["date"], entry["service"], entry.get("preferred_time", ""), entry["covers"])
        return "Pas de souci, merci de nous avoir répondu ! On vous garde en tête pour une prochaine fois. 😊"
    return None  # Not a clear yes/no, let the AI handle it


async def process_waitlist_timeouts():
    """Check for waitlist entries that haven't responded in time and cascade to next."""
    now = datetime.utcnow()
    for rid, wl in waitlist.items():
        for entry in wl:
            if entry["status"] != "notified" or not entry.get("notified_at"):
                continue
            notified_at = datetime.fromisoformat(entry["notified_at"])
            timeout = get_waitlist_timeout(entry.get("preferred_time", ""), entry.get("service", "soir"))
            if (now - notified_at).total_seconds() > timeout * 60:
                entry["status"] = "expired"
                await db_update_waitlist_status(rid, entry["id"], "expired")
                logger.info(f"Waitlist: {entry['name']} expired after {timeout}min, cascading")
                # Send expiry message
                rest = restaurants_cache.get(rid)
                if rest and rest.get("whatsapp_phone_number_id"):
                    await send_whatsapp_message(
                        rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], entry["phone"],
                        f"Le temps de réponse est écoulé, la table a été proposée au client suivant. N'hésitez pas à nous recontacter ! 🙏"
                    )
                # Cascade to next
                await notify_next_on_waitlist(rid, entry["date"], entry["service"], entry.get("preferred_time", ""), entry["covers"])


# ==============================================================
# OWNER COMMANDS
# ==============================================================

OWNER_COMMANDS_HELP = """ *Commandes GuestScale :*

📊 *STATUS* — Voir le statut actuel
📈 *STATS* — Statistiques du jour

🔴 *COMPLET CE SOIR* — Marquer complet ce soir
🔴 *COMPLET MIDI* — Marquer complet ce midi
🔴 *COMPLET* [date] — Marquer complet (ex: COMPLET 28/02)
🟡 *FERMÉ AUJOURD'HUI* — Fermeture exceptionnelle auj.
🟡 *FERMÉ* [date] — Fermeture exceptionnelle (ex: FERMÉ 01/03)
🟡 *FERMÉ DU* [date] *AU* [date] — Fermeture période
🟢 *OUVERT* — Retour à la normale

💬 *MESSAGE* [texte] — Ajouter un message temporaire pour les clients
💬 *MESSAGE OFF* — Supprimer le message temporaire

❓ *AIDE* — Afficher cette aide"""


async def handle_owner_command(rid: str, message: str) -> str:
    msg = message.strip().upper()
    status = restaurant_status.get(rid, {})
    today = today_paris()

    if msg in ("AIDE", "HELP", "?"):
        return OWNER_COMMANDS_HELP

    if msg == "STATUS":
        s = status.get("status", "open")
        status_map = {"open": "🟢 Ouvert", "full_tonight": "🔴 Complet ce soir", "full_lunch": "🔴 Complet ce midi", "closed_today": "🟡 Fermé auj."}
        text = f"📊 *Statut actuel :* {status_map.get(s, s)}\n"
        if status.get("temp_message"):
            text += f"💬 Message actif : \"{status['temp_message']}\"\n"
        if status.get("closed_dates"):
            text += f"📅 Fermetures prévues : {', '.join(status['closed_dates'])}\n"
        if status.get("full_dates"):
            text += f"📅 Complet : {', '.join(f'{d} ({p})' for d, p in status['full_dates'].items())}\n"
        return text

    if msg == "STATS":
        st = stats.get(rid, {})
        if st.get("last_reset") != today.isoformat():
            st["messages_today"] = 0
            st["bookings_today"] = 0
            st["last_reset"] = today.isoformat()
        rid_convs = sum(1 for k in conversations if k.startswith(rid))
        return (f"📈 *Statistiques du jour :*\n\n"
                f"💬 Messages traités : {st.get('messages_today', 0)}\n"
                f"🍽️ Réservations : {st.get('bookings_today', 0)}\n"
                f"🌍 Langues : {', '.join(f'{l}: {c}' for l, c in st.get('languages', {}).items())}\n"
                f"👥 Conversations actives : {rid_convs}")

    if msg in ("COMPLET CE SOIR", "COMPLET SOIR", "FULL TONIGHT"):
        status["status"] = "full_tonight"
        status.setdefault("full_dates", {})[today.isoformat()] = "soir"
        status["updated_at"] = datetime.utcnow().isoformat()
        await db_save_restaurant_status(rid, status)
        return "🔴 C'est noté ! L'agent informe les clients que vous êtes complet ce soir. Envoyez *OUVERT* pour revenir à la normale."

    if msg in ("COMPLET MIDI", "COMPLET CE MIDI", "FULL LUNCH"):
        status["status"] = "full_lunch"
        status.setdefault("full_dates", {})[today.isoformat()] = "midi"
        status["updated_at"] = datetime.utcnow().isoformat()
        await db_save_restaurant_status(rid, status)
        return "🔴 C'est noté ! L'agent informe les clients que vous êtes complet ce midi. Envoyez *OUVERT* pour revenir à la normale."

    if msg.startswith("COMPLET "):
        date_str = msg.replace("COMPLET ", "").strip()
        try:
            d = datetime.strptime(date_str, "%d/%m").replace(year=today.year).date()
            status.setdefault("full_dates", {})[d.isoformat()] = "journée"
            status["updated_at"] = datetime.utcnow().isoformat()
            await db_save_restaurant_status(rid, status)
            return f"🔴 Noté : complet le {d.strftime('%d/%m/%Y')}."
        except ValueError:
            return "❌ Format de date non reconnu. Utilisez : COMPLET 28/02"

    if msg in ("FERMÉ AUJOURD'HUI", "FERME AUJOURD'HUI", "FERMÉ", "FERME", "CLOSED TODAY"):
        status["status"] = "closed_today"
        status.setdefault("closed_dates", []).append(today.isoformat())
        status["updated_at"] = datetime.utcnow().isoformat()
        await db_save_restaurant_status(rid, status)
        return "🟡 Fermeture exceptionnelle enregistrée pour auj.. L'agent prévient les clients. Envoyez *OUVERT* demain."

    if msg.startswith("FERMÉ ") or msg.startswith("FERME "):
        date_str = msg.replace("FERMÉ ", "").replace("FERME ", "").strip()
        if "AU" in date_str:
            parts = date_str.split("AU")
            try:
                start = datetime.strptime(parts[0].replace("DU", "").strip(), "%d/%m").replace(year=today.year).date()
                end = datetime.strptime(parts[1].strip(), "%d/%m").replace(year=today.year).date()
                current = start
                while current <= end:
                    status.setdefault("closed_dates", []).append(current.isoformat())
                    current += timedelta(days=1)
                status["updated_at"] = datetime.utcnow().isoformat()
                await db_save_restaurant_status(rid, status)
                return f"🟡 Fermeture enregistrée du {start.strftime('%d/%m')} au {end.strftime('%d/%m')}."
            except ValueError:
                return "❌ Format non reconnu. Utilisez : FERMÉ DU 01/03 AU 15/03"
        else:
            try:
                d = datetime.strptime(date_str, "%d/%m").replace(year=today.year).date()
                status.setdefault("closed_dates", []).append(d.isoformat())
                status["updated_at"] = datetime.utcnow().isoformat()
                await db_save_restaurant_status(rid, status)
                return f"🟡 Fermeture enregistrée le {d.strftime('%d/%m/%Y')}."
            except ValueError:
                return "❌ Format non reconnu. Utilisez : FERMÉ 01/03"

    if msg in ("OUVERT", "OPEN", "NORMAL"):
        status["status"] = "open"
        status["updated_at"] = datetime.utcnow().isoformat()
        await db_save_restaurant_status(rid, status)
        return "🟢 Statut remis à *ouvert*. L'agent reprend normalement."

    if msg.startswith("MESSAGE "):
        text = message[8:].strip()
        if text.upper() == "OFF":
            status["temp_message"] = ""
            status["updated_at"] = datetime.utcnow().isoformat()
            await db_save_restaurant_status(rid, status)
            return "💬 Message temporaire supprimé."
        else:
            status["temp_message"] = text
            status["updated_at"] = datetime.utcnow().isoformat()
            await db_save_restaurant_status(rid, status)
            return f"💬 Message temporaire activé :\n\"{text}\"\n\nLes clients verront ce message. Envoyez *MESSAGE OFF* pour le retirer."

    return None


# ==============================================================
# CLAUDE AI
# ==============================================================

claude_client = None


def get_claude():
    global claude_client
    if claude_client is None:
        claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return claude_client


def build_system_prompt(rest: dict, rid: str, customer_phone: str = None) -> str:
    ctx = rest.get("settings", {})
    status = restaurant_status.get(rid, {})

    status_context = ""
    current_status = status.get("status", "open")
    today_str = today_paris().isoformat()

    if current_status == "full_tonight":
        status_context = "\n⚠️ IMPORTANT : Le restaurant est COMPLET CE SOIR. Informe poliment le client et propose de réserver pour un autre soir."
    elif current_status == "full_lunch":
        status_context = "\n⚠️ IMPORTANT : Le restaurant est COMPLET CE MIDI. Informe poliment le client et propose de réserver pour un autre créneau."
    elif current_status == "closed_today":
        status_context = "\n⚠️ IMPORTANT : Le restaurant est FERMÉ AUJOURD'HUI (fermeture exceptionnelle). Informe poliment le client et propose de réserver pour un autre jour."

    if today_str in status.get("closed_dates", []):
        status_context = "\n⚠️ IMPORTANT : Le restaurant est FERMÉ AUJOURD'HUI. Informe poliment et propose un autre jour."

    if today_str in status.get("full_dates", {}):
        period = status["full_dates"][today_str]
        status_context = f"\n⚠️ IMPORTANT : Le restaurant est COMPLET ({period}) auj.. Informe poliment et propose un autre créneau."

    future_closed = [d for d in status.get("closed_dates", []) if d > today_str]
    if future_closed:
        status_context += f"\nFermetures prévues : {', '.join(future_closed)}. Si le client veut réserver à ces dates, informe-le que c'est fermé."

    temp_msg = ""
    if status.get("temp_message"):
        temp_msg = f"\n📢 MESSAGE DU RESTAURANT : {status['temp_message']}. Mentionne cette info si c'est pertinent pour le client."

    booking_section = ""
    if ctx.get("booking_link"):
        booking_section = f"\nRÉSERVATION : Si le client veut réserver, envoie-lui ce lien : {ctx['booking_link']}"
    else:
        booking_section = "\nRÉSERVATION : Si le client veut réserver, collecte : nombre de personnes, date, heure, nom. Une fois toutes les infos obtenues, confirme la réservation de manière claire et définitive (ex: 'Votre réservation est confirmée !'). Ne dis PAS que le restaurant doit encore valider — la réservation est automatiquement enregistrée."

    availability_context = build_availability_context(rid)

    # CRM customer profile
    customer_context = ""
    rid_contacts = contacts.get(rid, {})
    rid_bookings = bookings.get(rid, [])
    if customer_phone and customer_phone in rid_contacts:
        ct = rid_contacts[customer_phone]
        customer_context = f"\n\n👤 PROFIL CLIENT (confidentiel, ne mentionne pas que tu as ces infos — utilise-les naturellement) :"
        customer_context += f"\n- Nom : {ct.get('name', 'Inconnu')}"
        customer_context += f"\n- Visites : {ct.get('visits', 0)} fois"
        if ct.get("language"):
            customer_context += f"\n- Langue preferee : {ct['language']}"
        if ct.get("tags"):
            customer_context += f"\n- Tags : {', '.join(ct['tags'])}"
        if ct.get("notes"):
            customer_context += f"\n- Notes du restaurateur : {ct['notes']}"
        if ct.get("preferences"):
            customer_context += f"\n- Preferences : {ct['preferences']}"
        client_bookings = [b for b in rid_bookings if b.get("phone") == customer_phone]
        if client_bookings:
            recent = client_bookings[-3:]
            bk_lines = []
            for b in recent:
                bk_lines.append(f"  - {b.get('covers', '?')}p, {b.get('booking_time') or b.get('time', '?')}, table {b.get('table', '?')}")
            customer_context += f"\n- Dernieres reservations :\n" + "\n".join(bk_lines)
            tables_used = [b.get("table", "") for b in client_bookings if b.get("table")]
            zones_used = [b.get("zone", "") for b in client_bookings if b.get("zone")]
            avg_covers = sum(b.get("covers", 0) for b in client_bookings) / len(client_bookings) if client_bookings else 0
            if tables_used:
                fav_table = Counter(tables_used).most_common(1)[0][0]
                customer_context += f"\n- Table favorite : {fav_table}"
            if zones_used:
                fav_zone = Counter(zones_used).most_common(1)[0][0]
                customer_context += f"\n- Zone preferee : {fav_zone}"
            if avg_covers > 0:
                customer_context += f"\n- Taille groupe habituelle : {round(avg_covers)}p"
        if ct.get("visits", 0) >= 3:
            customer_context += "\n- ⭐ CLIENT FIDELE — traite-le avec une attention particuliere, mentionne que tu es content de le/la revoir."
        elif ct.get("visits", 0) == 0:
            customer_context += "\n- 🆕 NOUVEAU CLIENT — sois particulierement accueillant et propose de l'aider a choisir."

    return f"""Tu es l&#39;assistant virtuel du restaurant "{rest['name']}".

RÔLE : Tu réponds aux clients sur WhatsApp de manière naturelle et chaleureuse.
Tu parles comme un membre de l'équipe, pas comme un robot.

📆 NOUS SOMMES LE : {format_date_fr(today_paris())}
🕐 IL EST ACTUELLEMENT : {now_paris().strftime('%H:%M')} (heure de Paris)

RÈGLES HORAIRES STRICTES :
- Compare TOUJOURS l'heure actuelle ({now_paris().strftime('%H:%M')}) avec les horaires du restaurant avant de proposer une disponibilité.
- Si l'heure actuelle dépasse l'heure de DERNIER SERVICE (ex: 22h30 pour le dîner), le restaurant est FERMÉ pour ce service. Ne propose PAS de créneau ce soir.
- L'heure de DERNIER SERVICE est l'heure ultime à laquelle un client peut être attablé : tu peux accepter une réservation jusqu'à cette heure incluse (ex: si dernier service 22h30, tu confirmes sans hésiter une résa à 22h00, 22h15 ou 22h30). Ne refuse JAMAIS un créneau encore disponible avant l'heure de dernier service.
- Si le client demande "ce midi" et qu'il est après 14h, le service du midi est terminé. Propose le soir ou un autre jour.
- Ne propose JAMAIS un créneau dans le passé (ex: ne pas proposer 19h si il est déjà 21h).

{TONE_PROMPTS.get(ctx.get('tone_preset', ''), '')}
TON : {ctx.get('tone', 'Professionnel mais chaleureux')}
LANGUES : Réponds dans la langue du client. Tu parles {ctx.get('languages', 'français')}.
{status_context}
{temp_msg}

INFORMATIONS DU RESTAURANT :
- Description : {ctx.get('description', '')}
- Adresse : {ctx.get('address', '')}
- Téléphone : {ctx.get('phone', '')}
- Horaires : {ctx.get('hours', '')}
- Infos pratiques : {ctx.get('special_info', '')}

MENU :
{ctx.get('menu', 'Non renseigné')}
{('LIEN MENU : ' + ctx.get('menu_url', '')) if ctx.get('menu_url') else ''}
Si le client demande la carte ou le menu et qu'un lien menu est disponible, envoie-le.

ALLERGÈNES : {ctx.get('allergens_policy', 'Demander au restaurant')}
{booking_section}
{availability_context}
{customer_context}

RÈGLES STRICTES :
- **RÈGLE CRITIQUE : Ne JAMAIS créer une réservation tant que tu n'as pas confirmé les 3 informations obligatoires : la date, l'heure, et le nombre de couverts. Si une information manque, pose la question AVANT de réserver. Ne mets JAMAIS un nombre de couverts par défaut.**
- Ne JAMAIS inventer d'information. Si tu ne sais pas, dis-le et propose d'appeler le restaurant.
- Sur les allergènes/santé : TOUJOURS recommander de confirmer directement avec le restaurant.
- Reste dans ton rôle : tu ne parles QUE du restaurant et de sujets liés.
- Si le message n'a rien à voir, redirige poliment.
- Sois concis : 2-4 phrases max par réponse, sauf si le client pose plusieurs questions.
- Si une demande est complexe ou urgente, propose de transférer au restaurant.
- N'explicite JAMAIS que tu as acces a un profil CRM ou a des donnees personnelles. Utilise les infos naturellement.
- Si tu ne peux PAS traiter la demande (allergie grave mettant en danger la vie, plainte serieuse, demande d'evenement prive, groupe >12 personnes, client demande explicitement un humain, ou 3 echanges sans resolution), reponds UNIQUEMENT avec ce JSON exact sur une seule ligne : {{"action":"escalate","reason":"...","summary":"..."}}
"""


async def ask_claude(system_prompt: str, messages: list) -> str:
    try:
        client = get_claude()
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=messages,
            temperature=0.7,
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Désolé, je rencontre un petit souci technique. Le restaurant va vous répondre directement. 🙏"


# ==============================================================
# WHATSAPP API
# ==============================================================

async def send_whatsapp_message(phone_number_id: str, access_token: str, to: str, text: str):
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    max_length = 4096
    chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]
    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in chunks:
            payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": chunk}}
            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.error(f"WhatsApp API error: {resp.status_code} {resp.text}")
            except Exception as e:
                logger.error(f"WhatsApp send error: {e}")


async def mark_as_read(phone_number_id: str, access_token: str, message_id: str):
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, headers=headers, json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
        except Exception:
            pass


def parse_webhook(body: dict) -> dict | None:
    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return None
        msg = messages[0]
        contacts_data = value.get("contacts", [{}])
        name = contacts_data[0].get("profile", {}).get("name", "") if contacts_data else ""
        phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
        return {
            "phone_number_id": phone_number_id,
            "from": msg.get("from", ""),
            "name": name,
            "text": msg.get("text", {}).get("body", "") if msg.get("type") == "text" else "[media]",
            "message_id": msg.get("id", ""),
        }
    except Exception:
        return None


# ==============================================================
# CONVERSATION & CRM
# ==============================================================

def get_conversation(rid: str, customer_phone: str) -> list:
    key = f"{rid}:{customer_phone}"
    return conversations.get(key, [])


def save_message(rid: str, customer_phone: str, role: str, content: str, sender_type=None):
    key = f"{rid}:{customer_phone}"
    if key not in conversations:
        conversations[key] = []
    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if sender_type:
        msg["sender_type"] = sender_type
    conversations[key].append(msg)
    conversations[key] = conversations[key][-30:]
    bump_version(rid)
    # Persist async
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_conversation(rid, customer_phone, conversations[key]))
    except Exception:
        pass


def detect_preferences(rid: str, customer_phone: str, message: str):
    import re
    rid_contacts = contacts.get(rid, {})
    ct = rid_contacts.get(customer_phone, {})
    prefs = ct.get("preferences", "")
    pref_list = [p.strip() for p in prefs.split(",") if p.strip()] if prefs else []
    patterns = {
        "vegetarien": r"(v[ée]g[ée]tari|vegetarian|no meat)",
        "vegan": r"(v[ée]gan|plant.based)",
        "sans gluten": r"(sans gluten|gluten.free|c[oe]liac)",
        "halal": r"(halal)",
        "casher": r"(casher|kosher)",
        "allergie noix": r"(nut.allerg|allergi.*noix|allergi.*cacahu)",
        "allergie lactose": r"(lact|dairy.free|sans lait)",
        "terrasse": r"(terrasse|dehors|outside|ext[ée]rieur|outdoor)",
        "intérieur": r"(int[ée]rieur|inside|salle|indoor)",
        "famille": r"(enfant|kid|child|b[ée]b[ée]|baby|famille|family|chaise haute|highchair)",
        "anniversaire": r"(anniversaire|birthday)",
        "business": r"(business|professionnel|r[ée]union|meeting|d[ée]jeuner d.affaire)",
    }
    for pref_name, pattern in patterns.items():
        if re.search(pattern, message.lower()) and pref_name not in pref_list:
            pref_list.append(pref_name)
    if pref_list:
        ct["preferences"] = ", ".join(pref_list)
        rid_contacts[customer_phone] = ct


def track_stats(rid: str, is_booking: bool = False, language: str = "fr"):
    st = stats.get(rid, {"messages_today": 0, "bookings_today": 0, "languages": {}, "last_reset": today_paris().isoformat()})
    today = today_paris().isoformat()
    if st.get("last_reset") != today:
        if st.get("last_reset"):
            save_daily_stats_snapshot(rid, st)
        st["messages_today"] = 0
        st["bookings_today"] = 0
        st["languages"] = {}
        st["last_reset"] = today
    st["messages_today"] = st.get("messages_today", 0) + 1
    if is_booking:
        st["bookings_today"] = st.get("bookings_today", 0) + 1
    langs = st.get("languages", {})
    langs[language] = langs.get(language, 0) + 1
    st["languages"] = langs
    stats[rid] = st


def save_daily_stats_snapshot(rid: str, st: dict):
    snapshot_date = st.get("last_reset", today_paris().isoformat())
    rid_bookings = bookings.get(rid, [])
    day_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(snapshot_date)]
    sources = {}
    total_covers = 0
    for b in day_bookings:
        s = b.get("source", "autre")
        sources[s] = sources.get(s, 0) + 1
        total_covers += b.get("covers", 0)
    snapshot = {
        "date": snapshot_date,
        "bookings": len(day_bookings),
        "covers": total_covers,
        "messages": st.get("messages_today", 0),
        "cancelled": 0,
        "sources": sources,
    }
    dsh = daily_stats_history.get(rid, [])
    dsh.append(snapshot)
    if len(dsh) > 90:
        dsh.pop(0)
    daily_stats_history[rid] = dsh
    logger.info(f"Daily stats saved for {snapshot_date}: {len(day_bookings)} bookings, {total_covers} covers")
    # Persist async
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_daily_stats(rid, snapshot_date, snapshot))
    except Exception:
        pass


def build_daily_recap(rid: str) -> str:
    rest = restaurants_cache.get(rid)
    rid_bookings = bookings.get(rid, [])
    rid_contacts = contacts.get(rid, {})
    today_str = today_paris().isoformat()
    tomorrow_str = (today_paris() + timedelta(days=1)).isoformat()
    today_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(today_str)]
    tomorrow_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(tomorrow_str)]
    total_covers_today = sum(b.get("covers", 0) for b in today_bookings)
    total_covers_tomorrow = sum(b.get("covers", 0) for b in tomorrow_bookings)
    total_tables = len(floor_tables.get(rid, []))
    occupied = len([b for b in today_bookings if b.get("table")])
    occ_rate = round(occupied / total_tables * 100) if total_tables else 0
    new_contacts_today = sum(1 for p, c in rid_contacts.items() if (c.get("first_seen") or "").startswith(today_str))
    rid_convs = sum(1 for k in conversations if k.startswith(rid))
    st = stats.get(rid, {})
    msgs_today = st.get("messages_today", 0)
    rq = review_queue.get(rid, [])
    pending_reviews = sum(1 for r in rq if not r.get("sent"))
    lines = [
        f"📊 *Recap du {today_paris().strftime('%A %d %B')}*",
        "",
        f"🍽 *{len(today_bookings)} reservations* · {total_covers_today} couverts",
        f"📈 Taux occupation : {occ_rate}% ({occupied}/{total_tables} tables)",
        f"💬 {msgs_today} messages · {rid_convs} conversations",
    ]
    if new_contacts_today:
        lines.append(f"👤 {new_contacts_today} nouveaux contacts")
    if pending_reviews:
        lines.append(f"⭐ {pending_reviews} avis en attente")
    if tomorrow_bookings:
        lines.append("")
        lines.append(f"📅 *Demain : {len(tomorrow_bookings)} reservations* · {total_covers_tomorrow} couverts")
        for b in tomorrow_bookings[:5]:
            lines.append(f"  · {b.get('name','?')} — {b.get('covers',0)}p @ {b.get('booking_time') or b.get('time','?')}")
        if len(tomorrow_bookings) > 5:
            lines.append(f"  ... et {len(tomorrow_bookings)-5} autres")
    else:
        lines.append("")
        lines.append("📅 *Demain : aucune reservation pour le moment*")
    sources = {}
    for b in today_bookings:
        s = b.get("source", "autre")
        sources[s] = sources.get(s, 0) + 1
    if sources:
        src_labels = {"whatsapp": "WhatsApp", "web": "Web", "phone": "Tel", "walk-in": "Walk-in", "zenchef": "Zenchef"}
        lines.append("")
        lines.append("📊 *Par canal :*")
        for s, cnt in sorted(sources.items(), key=lambda x: -x[1]):
            lines.append(f"  · {src_labels.get(s, s)} : {cnt}")
    lines.append("")
    lines.append("_GuestScale — Bonne soiree !_")
    return "\n".join(lines)


# ==============================================================
# BOOKING REMINDERS
# ==============================================================

async def send_booking_reminders():
    """
    Send WhatsApp reminders for upcoming bookings:
    - Lunch (time < 15:00): reminder sent the day before at 19:00 Paris time
    - Dinner (time >= 15:00): reminder sent the same day at 11:00 Paris time
    """
    np = now_paris()
    current_hour = np.hour
    today_str = np.strftime("%Y-%m-%d")
    tomorrow = np + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")

    for rid, rest in restaurants_cache.items():
        if not rest.get("whatsapp_phone_number_id") or not rest.get("whatsapp_access_token"):
            continue
        # Check if reminders are enabled (default: True)
        settings = rest.get("settings") or {}
        if settings.get("reminders_enabled") is False:
            continue

        rid_bookings = bookings.get(rid, [])
        daily_msg = restaurant_status.get(rid, {}).get("daily_message", "")
        rest_name = rest.get("name", "le restaurant")

        for b in rid_bookings:
            if b.get("reminder_sent") or b.get("status") in ("cancelled", "no-show"):
                continue
            bdate = b.get("date", "")
            btime = b.get("booking_time") or b.get("time", "")
            phone = b.get("phone", "")
            name = b.get("name", "")
            covers = b.get("covers", 1)
            if not bdate or not btime or not phone:
                continue

            # Determine if lunch or dinner
            try:
                hour = int(btime.split(":")[0])
            except (ValueError, IndexError):
                continue
            is_lunch = hour < 15

            should_send = False
            if is_lunch and bdate == tomorrow_str and current_hour == 19:
                # Lunch tomorrow -> send tonight at 19h
                should_send = True
            elif not is_lunch and bdate == today_str and current_hour == 11:
                # Dinner today -> send this morning at 11h
                should_send = True

            if not should_send:
                continue

            # Build the reminder message
            first_name = name.split()[0] if name and name != phone else ""
            greeting = f"Bonjour {first_name}" if first_name else "Bonjour"
            service_label = "déjeuner" if is_lunch else "dîner"

            if is_lunch:
                date_label = "demain" if bdate == tomorrow_str else bdate
            else:
                date_label = "ce soir"

            msg = (
                f"{greeting} 👋\n\n"
                f"On vous rappelle votre réservation pour le {service_label} "
                f"{date_label} à {btime} ({covers} personne{'s' if covers > 1 else ''}) "
                f"chez {rest_name}.\n"
            )
            if daily_msg:
                msg += f"\n📋 Le mot du chef : {daily_msg}\n"
            msg += "\nÀ très vite ! 🍽️"
            msg += "\n\n_Si vous souhaitez modifier ou annuler, répondez simplement à ce message._"

            try:
                await send_whatsapp_message(
                    rest["whatsapp_phone_number_id"],
                    rest["whatsapp_access_token"],
                    phone, msg
                )
                b["reminder_sent"] = True
                await db_save_booking(rid, b)
                logger.info(f"Reminder sent: {name} ({phone}) for {bdate} {btime} @ {rest_name}")
                await increment_message_count(rid, "reminder")
            except Exception as e:
                logger.error(f"Reminder send error for {name}: {e}")


async def send_daily_recap():
    for rid, rest in restaurants_cache.items():
        owner = rest.get("owner_phone")
        if not owner or not rest.get("whatsapp_phone_number_id"):
            continue
        try:
            recap = build_daily_recap(rid)
            await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], owner, recap)
            st = stats.get(rid, {})
            save_daily_stats_snapshot(rid, st)
            logger.info(f"Daily recap sent to owner of {rest['name']}")

            # Also send recap via email if configured
            user_email = None
            if db_pool:
                try:
                    async with db_pool.acquire() as conn:
                        user_email = await conn.fetchval(
                            "SELECT email FROM users WHERE restaurant_id = $1::uuid LIMIT 1", rid)
                except Exception:
                    pass
            if user_email and BREVO_API_KEY:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        await client.post("https://api.brevo.com/v3/smtp/email",
                            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                            json={
                                "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                                "to": [{"email": user_email}],
                                "subject": f"Recap {rest['name']} — {today_paris().strftime('%d/%m')}",
                                "htmlContent": f"<div style='font-family:sans-serif;max-width:560px;margin:0 auto;padding:24px'><pre style='white-space:pre-wrap;font-family:inherit'>{recap}</pre></div>",
                            })
                except Exception as e:
                    logger.error(f"Recap email error for {rid}: {e}")
        except Exception as e:
            logger.error(f"Daily recap error for {rest['name']}: {e}")


def track_contact(rid: str, customer_phone: str, customer_name: str = "", language: str = "fr"):
    rid_contacts = contacts.setdefault(rid, {})
    now = datetime.utcnow().isoformat()
    if customer_phone not in rid_contacts:
        rid_contacts[customer_phone] = {
            "name": customer_name or customer_phone,
            "phone": customer_phone,
            "first_seen": now,
            "last_seen": now,
            "visits": 1,
            "bookings": [],
            "tags": [],
            "language": language,
            "notes": "",
            "source": "whatsapp",
        }
    else:
        c = rid_contacts[customer_phone]
        c["last_seen"] = now
        c["visits"] = c.get("visits", 0) + 1
        if customer_name and customer_name != customer_phone:
            c["name"] = customer_name
        if language:
            c["language"] = language
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_contact(rid, customer_phone, rid_contacts[customer_phone]))
    except Exception:
        pass


# ==============================================================
# NOTIFICATION & BOOKING CREATION
# ==============================================================

async def notify_owner(rid: str, rest: dict, customer_phone: str, customer_name: str, message: str, ai_response: str = ""):
    confirm_keywords = ["confirmé", "confirme", "noté", "note", "réservé", "reserve", "booked", "enregistré"]
    refusal_keywords = ["désolé", "desole", "impossible", "trop tard", "fermé", "ferme", "ne peux pas", "ne pourrons pas", "ne pourrai pas", "complet", "plus de place", "plus aucune", "malheureusement", "service terminé", "service termine", "déjà fermé", "deja ferme"]
    ai_response_lower = ai_response.lower()
    ai_confirmed_booking = any(kw in ai_response_lower for kw in confirm_keywords)
    ai_refused = any(kw in ai_response_lower for kw in refusal_keywords)
    # Only create a booking if the AI explicitly confirmed AND did not refuse.
    # Previously this used `client_wants_booking or ai_confirmed_booking`, which created
    # bookings even when the AI refused (e.g. slot too late) — see issue with 22h cutoff.
    is_booking = ai_confirmed_booking and not ai_refused
    is_duplicate = False
    if is_booking:
        import re
        # Combine client message and AI response for better extraction
        combined = message + " " + ai_response
        time_match = re.search(r'(\d{1,2})[h:](\d{2})?', combined)
        booking_time = None
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2) or 0)
            m = (m // 15) * 15
            booking_time = f"{h:02d}:{m:02d}"
        covers = 2
        covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax|invit)', combined.lower())
        if covers_match:
            covers = int(covers_match.group(1))
        else:
            covers_match2 = re.search(r'(?:pour|for|de|table)\s+(\d+)', combined.lower())
            if covers_match2:
                covers = int(covers_match2.group(1))
            else:
                covers_match3 = re.search(r'(?:serons|sera|sommes|seront|being)\s+(\d+)', combined.lower())
                if covers_match3:
                    covers = int(covers_match3.group(1))
        if covers < 1 or covers > 30:
            covers = 2
        zone_pref = None
        if "terrasse" in combined.lower():
            zone_pref = "terrasse"
        elif "bar" in combined.lower():
            zone_pref = "bar"

        # Detect special occasion
        occasion = None
        combined_lower = combined.lower()
        if any(k in combined_lower for k in ("anniversaire", "birthday", "compleanno", "geburtstag")):
            occasion = "anniversaire"
        elif any(k in combined_lower for k in ("demande en mariage", "proposal", "proposta")):
            occasion = "mariage"
        elif any(k in combined_lower for k in ("fiançailles", "fiancailles", "engagement")):
            occasion = "fiancailles"
        elif any(k in combined_lower for k in ("saint-valentin", "saint valentin", "valentine")):
            occasion = "saint-valentin"
        elif any(k in combined_lower for k in ("fête", "fete", "célébration", "celebration", "party", "festa")):
            occasion = "fete"

        rid_bookings = bookings.setdefault(rid, [])
        booking_date = extract_booking_date(combined)

        # Anti-duplicate: same phone + same name + same date + exact same time
        # Different name = not a duplicate (client booking for someone else)
        # Same name but different time = not a duplicate (midi + soir)
        is_duplicate = False
        candidate_name = (customer_name or customer_phone).strip().lower()
        candidate_time = booking_time or ""
        for existing in rid_bookings:
            if existing.get("status") in ("cancelled",):
                continue
            if existing.get("date", "") != booking_date:
                continue
            existing_name = (existing.get("name") or "").strip().lower()
            # Different name = different person, never a duplicate
            if existing_name != candidate_name:
                continue
            existing_time = existing.get("booking_time") or existing.get("time", "")
            # Both have a time: only duplicate if exact same time
            if existing_time and candidate_time:
                if existing_time == candidate_time:
                    is_duplicate = True
                    break
            # Both have no time: same name + same date = duplicate
            elif not existing_time and not candidate_time:
                is_duplicate = True
                break
        if is_duplicate:
            logger.warning(f"Duplicate booking blocked: {customer_name} {booking_date} {booking_time}")
        else:

            booking_id = f"R{len(rid_bookings)+1}"
            assigned_table = None
            if booking_time:
                if booking_time in ALL_SLOTS:
                    assigned_table = find_best_table(rid, booking_time, covers, zone_pref)
                else:
                    for slot in ALL_SLOTS:
                        if abs(int(slot.split(':')[0])*60+int(slot.split(':')[1]) - int(booking_time.split(':')[0])*60-int(booking_time.split(':')[1])) <= 15:
                            assigned_table = find_best_table(rid, slot, covers, zone_pref)
                            if assigned_table:
                                booking_time = slot
                                break
                if assigned_table:
                    assign_table(rid, booking_time, assigned_table, booking_id)

            new_booking = {
                "id": booking_id, "phone": customer_phone, "name": customer_name or customer_phone,
                "message": message[:200], "timestamp": datetime.utcnow().isoformat(), "date": booking_date,
                "status": "confirmed" if assigned_table else "pending", "time": booking_time or "",
                "booking_time": booking_time or "", "covers": covers, "table": assigned_table,
                "zone": zone_pref, "source": "whatsapp", "occasion": occasion,
            }
            rid_bookings.append(new_booking)
            track_stats(rid, is_booking=True)
            await db_save_booking(rid, new_booking)
            bump_version(rid)
            await schedule_review_followup(rid, customer_phone, customer_name, booking_time or "")
            logger.info(f"Booking {booking_id}: {customer_name} {covers}p @ {booking_date} {booking_time} -> {assigned_table or 'unassigned'}")

    if not rest.get("owner_phone") or not rest.get("whatsapp_phone_number_id"):
        return
    if is_booking and not is_duplicate:
        date_label = booking_date
        try:
            bd = date.fromisoformat(booking_date)
            if bd == today_paris():
                date_label = "auj."
            elif bd == today_paris() + timedelta(days=1):
                date_label = "demain"
            else:
                date_label = bd.strftime("%A %d/%m")
        except Exception:
            pass
        notif = (
            f"🍽️ Nouvelle réservation !\n\n"
            f"👤 {customer_name or customer_phone}\n"
            f"📱 {customer_phone}\n"
            f"📅 {date_label}"
            f"{(' · ' + booking_time) if booking_time else ''}"
            f"{(' · ' + str(covers) + 'p') if covers else ''}\n"
            f"💬 \"{message[:150]}\"\n\n"
            f"{'✅ Table ' + assigned_table if assigned_table else '⏳ En attente de table'}"
        )
        await send_whatsapp_message(
            rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], rest["owner_phone"], notif
        )

    # Detect waitlist request (client or AI mentions liste d&#39;attente)
    if not is_booking:
        waitlist_keywords = ["liste d&#39;attente", "liste attente", "waiting list", "waitlist", "inscrire sur", "inscrit sur"]
        wants_waitlist = any(kw in message.lower() for kw in waitlist_keywords) or any(kw in ai_response.lower() for kw in waitlist_keywords)
        if wants_waitlist:
            import re
            combined = message + " " + ai_response
            # Extract covers
            covers = 2
            covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax)', combined.lower())
            if covers_match:
                covers = int(covers_match.group(1))
            else:
                covers_match2 = re.search(r'(?:pour|for|de|table)\s+(\d+)', combined.lower())
                if covers_match2:
                    covers = int(covers_match2.group(1))
            if covers < 1 or covers > 30:
                covers = 2
            # Extract time
            time_match = re.search(r'(\d{1,2})[h:](\d{2})?', combined)
            booking_time = ""
            if time_match:
                h = int(time_match.group(1))
                m = int(time_match.group(2) or 0)
                booking_time = f"{h:02d}:{m:02d}"
            # Determine service
            booking_date = extract_booking_date(combined)
            service = "soir"
            if booking_time:
                hour = int(booking_time.split(":")[0])
                service = "midi" if hour < 15 else "soir"
            elif "midi" in combined.lower():
                service = "midi"
            # Add to waitlist
            entry = await add_to_waitlist(rid, customer_phone, customer_name or customer_phone, covers, service, booking_date, booking_time)
            if entry:
                logger.info(f"Waitlist auto-add: {customer_name} {covers}p {service} {booking_date}")


# ==============================================================
# MAIN MESSAGE PROCESSING
# ==============================================================

async def process_and_reply(rid: str, phone_number_id: str, customer_phone: str, customer_name: str, message_text: str):
    rest = restaurants_cache.get(rid)
    if not rest:
        logger.warning(f"No restaurant for rid: {rid}")
        return

    owner_phone = rest.get("owner_phone", "")
    access_token = rest.get("whatsapp_access_token", "")

    if owner_phone and customer_phone == owner_phone:
        response = await handle_owner_command(rid, message_text)
        if response is not None:
            await send_whatsapp_message(phone_number_id, access_token, customer_phone, response)
            logger.info(f"Owner command [{rest['name']}]: {message_text[:50]}")
            return

    # Check if AI is paused for this restaurant or conversation
    rest_settings = rest.get("settings", {})
    if not rest_settings.get("ai_enabled", True):
        return
    paused_until = rest.get("ai_paused_until", "")
    if paused_until and now_paris().isoformat() < paused_until:
        return
    conv_pauses = ai_paused_conversations.get(rid, {})
    conv_pause = conv_pauses.get(customer_phone, "")
    if conv_pause and now_paris().isoformat() < conv_pause:
        return

    # Check waitlist response first
    waitlist_response = await handle_waitlist_response(rid, customer_phone, message_text)
    if waitlist_response:
        await send_whatsapp_message(phone_number_id, access_token, customer_phone, waitlist_response)
        save_message(rid, customer_phone, "user", message_text)
        save_message(rid, customer_phone, "assistant", waitlist_response)
        return

    review_response = await handle_review_response(rid, customer_phone, message_text)
    if review_response:
        await send_whatsapp_message(phone_number_id, access_token, customer_phone, review_response)
        save_message(rid, customer_phone, "user", message_text)
        save_message(rid, customer_phone, "assistant", review_response)
        return

    system_prompt = build_system_prompt(rest, rid, customer_phone)
    history = get_conversation(rid, customer_phone)
    claude_messages = []
    for msg in history[-10:]:
        claude_messages.append({"role": msg["role"], "content": msg["content"]})
    claude_messages.append({"role": "user", "content": message_text})

    response = await ask_claude(system_prompt, claude_messages)

    # Check for escalation request
    if '{"action":"escalate"' in response:
        try:
            import json as json_mod
            esc_data = json_mod.loads(response.strip())
            if esc_data.get("action") == "escalate":
                escalations.setdefault(rid, []).append({
                    "phone": customer_phone, "name": customer_name,
                    "reason": esc_data.get("reason", ""), "summary": esc_data.get("summary", ""),
                    "status": "open", "created_at": now_paris().isoformat(),
                })
                # Pause AI on this conversation
                ai_paused_conversations.setdefault(rid, {})[customer_phone] = (now_paris() + timedelta(hours=4)).isoformat()
                response = "Je transfère votre demande à l'équipe du restaurant. Vous serez recontacté rapidement. 🙏"
                # Notify owner
                if owner_phone:
                    owner_msg = f"⚠️ ESCALADE\n{customer_name or customer_phone}\nRaison: {esc_data.get('reason', '?')}\nResume: {esc_data.get('summary', '?')}"
                    await send_whatsapp_message(phone_number_id, access_token, owner_phone, owner_msg)
                bump_version(rid)
        except Exception:
            pass  # Not valid JSON, treat as normal response

    save_message(rid, customer_phone, "user", message_text)
    save_message(rid, customer_phone, "assistant", response)
    track_stats(rid, language="fr")
    track_contact(rid, customer_phone, customer_name)
    detect_preferences(rid, customer_phone, message_text)

    await send_whatsapp_message(phone_number_id, access_token, customer_phone, response)
    await notify_owner(rid, rest, customer_phone, customer_name, message_text, response)

    logger.info(f"[{rest['name']}] {customer_name or customer_phone}: {message_text[:80]}")


# ==============================================================
# DASHBOARD HTML
# ==============================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuestScale — Dashboard</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' fill='none'%3E%3Ccircle cx='10' cy='10' r='4' fill='%232D7DD2'/%3E%3Ccircle cx='22' cy='10' r='4' fill='%234ECDC4'/%3E%3Ccircle cx='16' cy='22' r='4' fill='%234ECDC4'/%3E%3Cline x1='13' y1='11' x2='19' y2='11' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='11' y1='13' x2='15' y2='19' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='21' y1='13' x2='17' y2='19' stroke='%234ECDC4' stroke-width='2'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#F4F5F9;--card:#FFF;--sb:#0F1117;--sbh:#1A1D27;--sba:#252836;--sbt:#6B7280;
  --t:#111827;--ts:#6B7280;--tm:#9CA3AF;--b:#E5E7EB;--bl:#F3F4F6;
  --ac:#2D7DD2;--ac2:#4ECDC4;--acg:linear-gradient(135deg,#2D7DD2,#4ECDC4);
  --al:#EBF4FF;--ok:#4ECDC4;--okb:#E6FAF8;--wa:#F59E0B;--wab:#FFFBEB;
  --da:#EF4444;--bl2:#2D7DD2;--blb:#EBF4FF;
  --f:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 6px rgba(0,0,0,.05),0 2px 4px rgba(0,0,0,.04);
  --shadow-lg:0 10px 25px rgba(0,0,0,.08),0 4px 10px rgba(0,0,0,.04);
  --radius:12px;
}
body{font-family:var(--f);background:var(--bg);color:var(--t);min-height:100vh;-webkit-font-smoothing:antialiased}

/* === LOGIN === */
.lo{position:fixed;inset:0;background:#0F1117;display:flex;align-items:center;justify-content:center;z-index:100}
.lbox{text-align:center;width:360px}
.l-logo{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:8px}
.l-icon{width:40px;height:40px;background:#1A1D27;border-radius:10px;display:flex;align-items:center;justify-content:center}
.l-icon svg{width:28px;height:28px}
.lwm{font-size:28px;font-weight:800;color:#fff;letter-spacing:-.03em}
.lsub{font-size:11px;color:#6B7280;letter-spacing:.12em;margin-bottom:36px;text-transform:uppercase}
.lcd{background:#1A1D27;border-radius:16px;padding:28px 24px;border:1px solid #252836}
.linp{width:100%;padding:13px 16px;border-radius:10px;background:#0F1117;border:1.5px solid #374151;font-size:14px;color:#F9FAFB;outline:none;font-family:var(--f);transition:border .2s}
.linp::placeholder{color:#6B7280}
.linp:focus{border-color:var(--ac)}
.lbtn{width:100%;padding:13px;border-radius:10px;border:none;background:var(--acg);color:#fff;font-size:14px;font-weight:700;cursor:pointer;font-family:var(--f);margin-top:12px;transition:opacity .2s}
.lbtn:hover{opacity:.9}
.lerr{color:var(--da);font-size:13px;margin-bottom:14px;display:none;background:#FEF2F220;padding:10px 14px;border-radius:10px;border:1px solid #EF444430}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-6px)}40%,80%{transform:translateX(6px)}}
.shake{animation:shake .4s ease}

/* === APP LAYOUT === */
.app{display:none}.app.v{display:flex}
.sidebar{width:240px;background:var(--sb);position:fixed;height:100vh;display:flex;flex-direction:column;z-index:40;border-right:1px solid #1F2937}
.sb-b{padding:24px 20px 28px;border-bottom:1px solid #1F2937}
.sb-logo{display:flex;align-items:center;gap:10px}
.sb-icon{width:32px;height:32px;background:#1A1D27;border-radius:8px;display:flex;align-items:center;justify-content:center}
.sb-icon svg{width:22px;height:22px}
.sb-wm{font-size:17px;font-weight:800;color:#F9FAFB;letter-spacing:-.02em}
.sb-s{font-size:9px;color:#4B5563;letter-spacing:.15em;text-transform:uppercase;margin-top:1px}
.sb-n{padding:16px 12px;flex:1;overflow-y:auto}
.sb-l{font-size:10px;font-weight:700;color:#4B5563;letter-spacing:.1em;padding:0 8px;margin-bottom:8px;margin-top:16px;text-transform:uppercase}
.sb-l:first-child{margin-top:0}
.nb{width:100%;display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;border:none;background:transparent;color:var(--sbt);font-size:13px;font-weight:500;text-align:left;font-family:var(--f);cursor:pointer;margin-bottom:1px;transition:all .15s}
.nb:hover{background:var(--sbh);color:#D1D5DB}
.nb.on{background:var(--sba);color:#F9FAFB;font-weight:600}
.nb .ic{font-size:14px;width:20px;text-align:center;opacity:.5}.nb.on .ic{opacity:1}
.nb-badge{margin-left:auto;min-width:18px;height:18px;border-radius:9px;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;padding:0 5px}
.sb-u{padding:16px 20px;border-top:1px solid #1F2937;display:flex;align-items:center;gap:10px}
.uav{width:32px;height:32px;border-radius:8px;background:var(--acg);display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700}

/* === MAIN CONTENT === */
.main{flex:1;margin-left:240px}
.topbar{padding:16px 32px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:30;background:rgba(244,245,249,.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--b)}
.topbar h1{font-size:18px;font-weight:700;letter-spacing:-.02em;color:var(--t)}
.sp{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600}
.sd2{width:7px;height:7px;border-radius:50%;box-shadow:0 0 6px rgba(16,185,129,.5)}
.content{padding:24px 32px;max-width:1120px}

/* === STAT GRID === */
.sg{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.sc{background:var(--card);border-radius:var(--radius);padding:20px 18px;border:1px solid var(--b);transition:all .2s;cursor:default;box-shadow:var(--shadow)}
.sc:hover{box-shadow:var(--shadow-md);transform:translateY(-1px)}
.sl{font-size:11px;font-weight:600;color:var(--tm);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px}
.sv{font-size:30px;font-weight:800;letter-spacing:-.03em;line-height:1}
.ss2{font-size:12px;color:var(--ts);margin-top:6px;font-weight:500}

/* === CARDS === */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.card{background:var(--card);border-radius:var(--radius);border:1px solid var(--b);overflow:hidden;box-shadow:var(--shadow)}
.card-h{padding:14px 18px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center}
.card-t{font-size:14px;font-weight:700;color:var(--t)}.card-s{font-size:12px;color:var(--tm);margin-top:1px;font-weight:500}

/* === BUTTONS === */
.ba{padding:6px 14px;border-radius:8px;border:none;background:var(--acg);color:#fff;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);transition:opacity .2s;box-shadow:0 1px 3px rgba(99,102,241,.3)}
.ba:hover{opacity:.85}

/* === ROWS & BADGES === */
.rw{padding:11px 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bl);transition:background .1s}
.rw:last-child{border-bottom:none}
.rw:hover{background:var(--bl)}
.rl{display:flex;align-items:center;gap:10px}
.dot{width:6px;height:6px;border-radius:50%}
.badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:6px}
.src-badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px}

/* === DAILY BANNER === */
.db{background:linear-gradient(135deg,#EEF2FF,#E0E7FF);border:1px solid #C7D2FE;border-radius:var(--radius);padding:16px 18px;margin-bottom:18px;box-shadow:var(--shadow)}
.db-top{display:flex;align-items:flex-start;gap:14px}
.di{width:38px;height:38px;border-radius:10px;background:var(--acg);display:flex;align-items:center;justify-content:center;color:#fff;font-size:16px;flex-shrink:0}
.dlb{font-size:10px;font-weight:700;color:var(--ac);letter-spacing:.08em;text-transform:uppercase}
.dtx{font-size:14px;font-weight:600;color:var(--t);margin-top:4px;cursor:pointer;padding:4px 8px;border-radius:8px;border:1.5px solid transparent;transition:border .2s}
.dtx:hover{border-color:#C7D2FE}
.dtx-edit{font-size:14px;font-weight:600;color:var(--t);margin-top:4px;padding:8px 10px;border-radius:10px;border:1.5px solid var(--ac);background:#fff;width:100%;outline:none;font-family:var(--f);resize:none;min-height:44px}
.dme{font-size:11px;color:var(--ts);margin-top:4px}
.db-act{display:flex;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid #C7D2FE40}
.dbb{padding:7px 14px;border-radius:8px;border:none;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;align-items:center;gap:5px;transition:opacity .2s}
.dbb-s{background:var(--acg);color:#fff}.dbb-s:hover{opacity:.85}
.dbb-b{background:var(--bl2);color:#fff}.dbb-b:hover{opacity:.85}
.dbb-c{background:#fff;color:var(--ts);border:1px solid var(--b)}.dbb-c:hover{background:var(--bl)}

/* === FLOORPLAN === */
.fm{background:var(--card);border-radius:var(--radius);border:1px solid var(--b);padding:18px;margin-bottom:14px;cursor:pointer;transition:all .2s;box-shadow:var(--shadow)}
.fm:hover{box-shadow:var(--shadow-md);transform:translateY(-1px)}
.fc{position:relative;height:180px;background:var(--bg);border-radius:10px;border:1px solid var(--bl);overflow:hidden;margin-top:10px}
.ftbl{position:absolute;display:flex;flex-direction:column;align-items:center;justify-content:center;border:2px solid;font-size:10px;font-weight:700}

/* === CONTACTS, CONVERSATIONS === */
.cr{padding:12px 18px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--bl)}
.cr:last-child{border-bottom:none}
.cav{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.cmsg{font-size:12px;color:var(--ts);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cg3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.cc{padding:14px;border-radius:10px;background:var(--bg);border:1px solid var(--bl)}
.conv-list-item{padding:12px 14px;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.conv-list-item.selected{background:var(--al);border-left:3px solid var(--ac)}

/* === CHAT BUBBLES === */
.bubble{padding:10px 14px;border-radius:14px;max-width:80%;font-size:13px;line-height:1.5;margin-bottom:8px}
.bubble-user{background:var(--acg);color:#fff;margin-left:auto;border-bottom-right-radius:4px}
.bubble-bot{background:var(--bl);color:var(--t);margin-right:auto;border-bottom-left-radius:4px}

/* === MENU === */
.ms{margin-bottom:20px}
.mc{font-size:12px;font-weight:700;color:var(--ac);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bl)}
.mi-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bl)}
.mi-row:last-child{border-bottom:none}
.mi-n{font-size:14px;font-weight:600}.mi-d{font-size:12px;color:var(--ts);margin-top:2px}.mi-p{font-size:14px;font-weight:700;color:var(--ac);white-space:nowrap}
.menu-sec{margin-bottom:24px;border:1px solid var(--b);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow)}

/* === CONFIG === */
.cfs{margin-bottom:28px}
.cft{font-size:15px;font-weight:700;margin-bottom:4px}
.cfsb{font-size:12px;color:var(--ts);margin-bottom:16px}
.cfr{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--bl)}
.cfr:last-child{border-bottom:none}
.cfl{font-size:14px;font-weight:500}.cfd{font-size:12px;color:var(--tm)}
.tog{position:relative;width:44px;height:24px;background:var(--b);border-radius:12px;cursor:pointer;transition:background .2s;flex-shrink:0}
.tog.on{background:var(--ac)}
.togd{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:transform .2s;box-shadow:0 1px 3px rgba(0,0,0,.1)}
.tog.on .togd{transform:translateX(20px)}

/* === MODALS === */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:150}
.modal-bg.show{display:flex}
.modal{background:var(--card);border-radius:16px;padding:28px;width:420px;max-width:90vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg)}
.modal h2{font-size:17px;font-weight:700;margin-bottom:4px}
.finp{width:100%;padding:11px 14px;border-radius:8px;background:var(--bg);border:1.5px solid var(--b);font-size:13px;color:var(--t);outline:none;font-family:var(--f);margin-bottom:10px;transition:border .2s}
.finp:focus{border-color:var(--ac)}
.finp-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.finp-label{font-size:10px;font-weight:700;color:var(--tm);letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px}
.finp-group{margin-bottom:4px}
.modal-act{display:flex;gap:8px;margin-top:16px}
.mbtn{flex:1;padding:11px;border-radius:10px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:var(--f);transition:opacity .2s}
.mbtn-p{background:var(--acg);color:#fff;box-shadow:0 1px 3px rgba(99,102,241,.3)}.mbtn-p:hover{opacity:.85}
.mbtn-s{background:var(--bg);color:var(--ts);border:1px solid var(--b)}

/* === MISC === */
.toast{position:fixed;bottom:24px;right:24px;background:var(--sb);color:#fff;padding:12px 24px;border-radius:10px;font-weight:600;font-size:13px;box-shadow:var(--shadow-lg);z-index:200;display:none;animation:su .3s ease;max-width:90vw}
@keyframes su{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.at-box{background:var(--okb);border:1px solid #BBF7D0;border-radius:10px;padding:12px 14px;margin-top:8px;display:none}
.at-l{font-size:11px;font-weight:600;color:var(--ok);letter-spacing:.06em;text-transform:uppercase}
.at-v{font-size:20px;font-weight:700;color:var(--ok);margin-top:4px}
.at-c{font-size:12px;color:var(--ac);cursor:pointer;font-weight:600;margin-top:4px}
.tsel{display:none;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:8px}
.tsb{padding:8px;border-radius:8px;border:1.5px solid var(--b);background:var(--card);font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);text-align:center;transition:all .15s}
.tsb:hover{border-color:var(--ac);background:var(--al)}
.tsb.sel{border-color:var(--ok);background:var(--okb);color:var(--ok)}
.tsb.taken{opacity:.3;cursor:not-allowed}
.dinp{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:13px;color:var(--t);outline:none;font-family:var(--f);resize:none;min-height:60px;transition:border .2s}
.dinp:focus{border-color:var(--ac)}
.msg-input{flex:1;padding:10px 14px;border-radius:8px;background:var(--bg);border:1.5px solid var(--b);color:var(--t);font-size:13px;outline:none;font-family:var(--f);transition:border .2s}
.msg-input:focus{border-color:var(--ac)}
.msg-btn{padding:10px 18px;border-radius:8px;border:none;background:var(--acg);color:#fff;font-weight:700;font-size:13px;cursor:pointer;font-family:var(--f);white-space:nowrap;transition:opacity .2s}
.msg-btn:hover{opacity:.85}
.star{color:var(--wa)}
.review-card{padding:14px 18px;border-bottom:1px solid var(--bl)}
.review-card:last-child{border-bottom:none}
.ph{background:var(--card);border-radius:var(--radius);padding:60px;border:1px solid var(--b);text-align:center;box-shadow:var(--shadow)}
.phi{font-size:36px;opacity:.2;margin-bottom:12px}

/* === MONTHLY CALENDAR === */
.cal-wrap{background:var(--card);border-radius:var(--radius);border:1px solid var(--b);box-shadow:var(--shadow);padding:16px;margin-bottom:14px}
.cal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.cal-nav{display:flex;align-items:center;gap:4px}
.cal-arrow{width:28px;height:28px;border-radius:6px;border:1.5px solid var(--b);background:var(--card);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:13px;color:var(--ts);transition:all .15s}
.cal-arrow:hover{border-color:var(--ac);color:var(--ac);background:var(--al)}
.cal-title{font-size:14px;font-weight:700;color:var(--t);cursor:pointer;padding:4px 10px;border-radius:6px;transition:all .15s}
.cal-title:hover{background:var(--al);color:var(--ac)}
.cal-today-btn{padding:4px 10px;border-radius:6px;border:1.5px solid var(--b);background:var(--card);font-size:11px;font-weight:700;color:var(--ts);cursor:pointer;font-family:var(--f);transition:all .15s}
.cal-today-btn:hover{border-color:var(--ac);color:var(--ac);background:var(--al)}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.cal-dow{font-size:9px;font-weight:700;color:var(--tm);text-transform:uppercase;text-align:center;padding:4px 0;letter-spacing:.04em}
.cal-cell{position:relative;aspect-ratio:1;display:flex;flex-direction:column;align-items:center;justify-content:center;border-radius:8px;cursor:pointer;font-family:var(--f);transition:all .12s}
.cal-cell:hover{background:var(--bl)}
.cal-cell.other{opacity:.3}
.cal-cell.today{border:1.5px solid var(--b)}
.cal-cell.sel{background:var(--ac);border-color:var(--ac)}
.cal-cell.sel .cal-num{color:#fff}
.cal-cell.sel .cal-dot{background:#fff}
.cal-num{font-size:12px;font-weight:700;color:var(--t);line-height:1}
.cal-dot{width:4px;height:4px;border-radius:50%;background:var(--ac2);margin-top:2px;opacity:0}
.cal-dot.has{opacity:1}
.cal-picker{position:absolute;top:100%;left:0;right:0;background:var(--card);border:1px solid var(--b);border-radius:10px;box-shadow:var(--shadow-lg);padding:12px;z-index:20;display:none}
.cal-picker.show{display:block}
.cal-picker-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.cal-picker-item{padding:8px;border-radius:6px;text-align:center;font-size:12px;font-weight:600;color:var(--t);cursor:pointer;transition:all .12s}
.cal-picker-item:hover{background:var(--al);color:var(--ac)}
.cal-picker-item.sel{background:var(--ac);color:#fff}

/* === FLOORPLAN WITH SIDEBAR === */
.fp-layout{display:flex;gap:14px;align-items:flex-start}
.fp-main{flex:1;min-width:0}
.fp-sidebar{width:300px;flex-shrink:0;background:var(--card);border-radius:var(--radius);border:1px solid var(--b);box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column}
.fp-sidebar .cal-wrap{border:none;box-shadow:none;border-radius:0;border-bottom:1px solid var(--bl);margin-bottom:0;padding:12px 16px}
.fp-sb-header{padding:14px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center}
.fp-sb-title{font-size:13px;font-weight:700;color:var(--t)}
.fp-sb-count{font-size:11px;font-weight:600;color:var(--tm)}
.fp-sb-list{flex:1;overflow-y:auto;scrollbar-width:thin;max-height:280px}
.fp-sb-item{padding:10px 16px;border-bottom:1px solid var(--bl);cursor:pointer;transition:background .1s}
.fp-sb-item:last-child{border-bottom:none}
.fp-sb-item:hover{background:var(--bl)}
.fp-sb-item.active{background:var(--al);border-left:3px solid var(--ac)}
.fp-sb-name{font-size:13px;font-weight:600;color:var(--t)}
.fp-sb-meta{font-size:11px;color:var(--tm);margin-top:2px;display:flex;align-items:center;gap:6px}
.fp-sb-table{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:var(--okb);color:var(--ok)}
.fp-sb-no-table{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:var(--wab);color:var(--wa)}
.fp-sb-empty{padding:30px 16px;text-align:center;color:var(--tm);font-size:12px}

/* === MOBILE === */
/* === ONBOARDING WIZARD === */
.ob-overlay{position:fixed;inset:0;background:rgba(15,17,23,.92);backdrop-filter:blur(8px);z-index:200;display:flex;align-items:center;justify-content:center}
.ob-card{background:var(--card);border-radius:20px;width:520px;max-width:94vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);padding:32px}
.ob-steps{display:flex;gap:4px;margin-bottom:24px}
.ob-step{flex:1;height:4px;border-radius:2px;background:var(--bl);transition:background .3s}
.ob-step.done{background:var(--ac)}
.ob-step.active{background:var(--acg)}
.ob-title{font-size:16px;font-weight:700;color:var(--t);margin-bottom:4px}
.ob-desc{font-size:13px;color:var(--ts);margin-bottom:18px}
.ob-field{margin-bottom:14px}
.ob-label{font-size:10px;font-weight:700;color:var(--tm);letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px}
.ob-input{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:14px;color:var(--t);outline:none;font-family:var(--f);transition:border .2s}
.ob-input:focus{border-color:var(--ac)}
.ob-textarea{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:13px;color:var(--t);outline:none;font-family:var(--f);resize:none;min-height:80px;transition:border .2s}
.ob-textarea:focus{border-color:var(--ac)}
.ob-actions{display:flex;gap:8px;margin-top:20px}
.ob-btn{flex:1;padding:12px;border-radius:10px;border:none;font-size:14px;font-weight:700;cursor:pointer;font-family:var(--f);transition:opacity .2s}
.ob-btn-p{background:var(--acg);color:#fff}.ob-btn-p:hover{opacity:.85}
.ob-btn-s{background:var(--bg);color:var(--ts);border:1px solid var(--b)}.ob-btn-s:hover{background:var(--bl)}
.ob-skip{font-size:12px;color:var(--tm);text-align:center;margin-top:12px;cursor:pointer}
.ob-skip:hover{color:var(--ac)}

.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--sb);padding:6px 0 calc(env(safe-area-inset-bottom,0px) + 8px);z-index:50;border-top:1px solid #1F2937}
.mobile-nav-items{display:flex;justify-content:space-around}
.mobile-nav-btn{background:none;border:none;color:#6B7280;font-size:9px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 8px;transition:color .15s;min-width:52px}
.mobile-nav-btn.active{color:var(--ac)}
.mobile-nav-btn span{font-size:18px;line-height:1.2}
.mobile-more-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:55;opacity:0;transition:opacity .2s}
.mobile-more-overlay.show{display:block;opacity:1}
.mobile-more-drawer{position:fixed;bottom:0;left:0;right:0;background:var(--sb);z-index:56;border-radius:16px 16px 0 0;padding:8px 0 calc(env(safe-area-inset-bottom,0px) + 16px);transform:translateY(100%);transition:transform .25s cubic-bezier(.4,0,.2,1)}
.mobile-more-drawer.show{transform:translateY(0)}
.mobile-more-handle{width:36px;height:4px;background:#374151;border-radius:2px;margin:4px auto 12px}
.mobile-more-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;padding:0 12px}
.mobile-more-item{background:none;border:none;color:#9CA3AF;font-size:10px;font-weight:600;font-family:var(--f);display:flex;flex-direction:column;align-items:center;gap:4px;padding:12px 4px;border-radius:12px;cursor:pointer;transition:all .15s}
.mobile-more-item:active,.mobile-more-item:hover{background:#1F2937;color:#E5E7EB}
.mobile-more-item.active{color:var(--ac)}
.mobile-more-item span{font-size:22px;line-height:1}
.mobile-more-item .mmi-badge{position:absolute;top:6px;right:50%;transform:translateX(140%);background:var(--ac);color:#fff;font-size:8px;font-weight:700;padding:1px 5px;border-radius:8px;min-width:14px;text-align:center}
.mobile-more-item{position:relative}
@media(max-width:768px){
  .sidebar{display:none}
  .main{margin-left:0}
  .mobile-nav{display:block}
  .content{padding:14px;padding-bottom:80px}
  .topbar{padding:12px 14px}
  .topbar h1{font-size:16px}
  .topbar .sp{padding:3px 8px;font-size:11px}
  /* Hide secondary topbar info on mobile */
  .topbar>div:last-child>div:first-child{display:none}

  /* Stats grid: 2 cols, compact */
  .sg{grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px}
  .sc{padding:14px 12px}
  .sv{font-size:24px}
  .sl{font-size:9px;margin-bottom:6px}
  .ss2{font-size:11px;margin-top:4px}

  /* Cards */
  .g2{grid-template-columns:1fr;gap:10px}
  .cg3{grid-template-columns:1fr 1fr}
  .card-h{padding:12px 14px}
  .rw{padding:10px 14px}

  /* Overview layout: stack vertically */
  .ov-layout{flex-direction:column!important}
  .ov-layout>div:last-child{width:100%!important}

  /* Calendar compact */
  .cal-wrap{padding:12px}
  .cal-num{font-size:11px}
  .cal-header{margin-bottom:8px}
  .cal-title{font-size:13px;padding:3px 8px}

  /* Floorplan */
  .fp-layout{flex-direction:column}
  .fp-sidebar{width:100%}
  .fp-sb-list{max-height:200px}
  #fpCanvas{height:320px!important}

  /* Conversations stacked */
  .conv-split{flex-direction:column!important}
  .conv-split>div:first-child{width:100%!important;max-height:200px;overflow-y:auto;border-right:none!important;border-bottom:1px solid var(--bl)}
  .conv-split>div:last-child{width:100%!important}

  /* Modals: wider on mobile */
  .modal{width:95vw;padding:20px;border-radius:12px}
  .finp-row{grid-template-columns:1fr}

  /* Daily banner compact */
  .db{padding:12px 14px}
  .di{width:32px;height:32px;font-size:14px}

  /* Floor mini on overview */
  .fc{height:140px}

  /* Touch: larger targets */
  .nb,.mobile-nav-btn{min-height:44px}
  .ba{min-height:36px;padding:8px 14px}
  .cal-cell{min-height:32px}

  /* Toast above mobile nav */
  .toast{bottom:80px;right:50%;transform:translateX(50%);text-align:center}
}

/* Extra small screens */
@media(max-width:380px){
  .content{padding:10px;padding-bottom:80px}
  .sg{grid-template-columns:1fr 1fr;gap:8px}
  .sv{font-size:20px}
  .sc{padding:12px 10px}
  .cal-num{font-size:10px}
  .cal-dow{font-size:8px}
  #fpCanvas{height:260px!important}
  .fp-sb-list{max-height:160px}
  .modal{padding:16px}
}
</style>
</style>
</head>
<body>

<div class="lo" id="loginOverlay">
<div class="lbox">
  <div class="l-logo"><div class="l-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div class="lwm">Guest<span style="color:#4ECDC4">Scale</span></div></div>
  <div class="lsub">Plateforme IA pour restaurants</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Identifiants incorrects. Veuillez reessayer.</div>
    <input class="linp" type="email" id="loginEmail" placeholder="Email" autocomplete="email" style="margin-bottom:10px" oninput="document.getElementById('loginError').style.display='none'">
    <div style="position:relative">
      <input class="linp" type="password" id="loginPwd" placeholder="Mot de passe" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()" oninput="document.getElementById('loginError').style.display='none';this.style.borderColor='#374151'">
      <button data-togglePwd onclick="togglePwdVis()" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;color:#6B7280;padding:4px" id="pwdToggle" type="button" title="Afficher le mot de passe">&#128065;</button>
    </div>
    <button class="lbtn" type="button" onclick="doLogin()" data-doLogin>Se connecter</button>
    <div style="text-align:center;margin-top:12px">
      <a href="#" onclick="showForgotPwd();return false" style="font-size:12px;color:#6B7280;text-decoration:none">Mot de passe oublié ?</a>
    </div>
    <div style="text-align:center;margin-top:16px">
      <span style="font-size:12px;color:#6B7280">Pas encore de compte ?</span>
      <a href="https://guestscale.com#inscription" style="font-size:12px;color:#4ECDC4;text-decoration:none;font-weight:600;margin-left:4px">Essai gratuit 30 jours</a>
    </div>
  </div>
  <!-- Forgot password form (hidden by default) -->
  <div id="forgotPwdForm" style="display:none">
    <div class="lerr" id="forgotError" style="display:none"></div>
    <div class="lerr" id="forgotSuccess" style="display:none;color:#10B981;border-color:#6EE7B7;background:#ECFDF520"></div>
    <div id="forgotStep1">
      <p style="font-size:13px;color:#9CA3AF;margin-bottom:12px">Entrez votre email pour recevoir un code de reinitialisation.</p>
      <input class="linp" type="email" id="forgotEmail" placeholder="Email" style="margin-bottom:8px">
      <button class="lbtn" type="button" onclick="sendResetCode()">Envoyer le code</button>
    </div>
    <div id="forgotStep2" style="display:none">
      <p style="font-size:13px;color:#9CA3AF;margin-bottom:12px">Entrez le code recu par email et votre nouveau mot de passe.</p>
      <input class="linp" type="text" id="resetCode" placeholder="Code a 6 chiffres" style="margin-bottom:8px">
      <input class="linp" type="password" id="newPwd" placeholder="Nouveau mot de passe (min. 12 car.)" style="margin-bottom:8px">
      <button class="lbtn" type="button" onclick="doResetPwd()">Changer le mot de passe</button>
    </div>
    <div style="text-align:center;margin-top:12px">
      <a href="#" onclick="hideForgotPwd();return false" style="font-size:12px;color:#6B7280;text-decoration:none">Retour a la connexion</a>
    </div>
  </div>
</div>
</div>

<div class="app" id="app">
<div class="sidebar">
  <div class="sb-b"><div class="sb-logo"><div class="sb-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div><div class="sb-wm">Guest<span style="color:#4ECDC4">Scale</span></div><div class="sb-s">Restaurant AI</div></div></div></div>
  <div class="sb-n">
    <div class="sb-l">PRINCIPAL</div>
    <button class="nb on" data-pg="overview"><span class="ic">&#9672;</span> Vue d&#39;ensemble</button>
    <button class="nb" data-pg="floorplan"><span class="ic">&#8862;</span> Plan de salle</button>
    <button class="nb" data-pg="bookings"><span class="ic">&#9673;</span> Réservations <span class="nb-badge" id="bookBadge" style="background:var(--wa);color:#fff">0</span></button>
    <button class="nb" data-pg="menu"><span class="ic">&#9680;</span> Menu</button>
    <div class="sb-l">CLIENTS</div>
    <button class="nb" data-pg="conversations"><span class="ic">&#9672;</span> Conversations <span class="nb-badge" id="convBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="reviews"><span class="ic">&#9733;</span> Avis <span class="nb-badge" id="reviewBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="contacts"><span class="ic">&#9671;</span> Contacts</button>
    <button class="nb" data-pg="waitlist"><span class="ic">&#9201;</span> Liste d'attente <span class="nb-badge" id="waitBadge" style="background:var(--wa);color:#fff">0</span></button>
    <div class="sb-l">PARAMÈTRES</div>
    <button class="nb" data-pg="config"><span class="ic">&#9881;</span> Configuration</button>
    <button class="nb" data-pg="stats"><span class="ic">&#9899;</span> Statistiques</button>
    <button class="nb" data-pg="account"><span class="ic">&#128100;</span> Mon compte</button>
  </div>
  <div class="sb-u" id="sidebarUser">
    <div class="uav">GS</div>
    <div><div style="color:#E5E7EB;font-size:13px;font-weight:600" id="sbRestName">Restaurant</div><div style="color:#6B7280;font-size:11px" id="sbUserEmail">Admin</div></div>
  </div>
</div>

<div class="main">
  <div class="topbar">
    <div><h1 id="pageTitle">Vue d&#39;ensemble</h1><span style="font-size:12px;color:var(--tm);font-weight:500" id="currentDate"></span></div>
    <div style="display:flex;align-items:center;gap:14px">
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--tm);font-weight:500">
        <span style="display:flex;align-items:center;gap:3px"><span class="dot" style="background:#25D366"></span> WhatsApp</span>
        <span style="display:flex;align-items:center;gap:3px"><span class="dot" style="background:#F59E0B"></span> Zenchef</span>
      </div>
      <div class="sp" id="statusPill" style="background:var(--okb)"><div class="sd2" id="statusDot" style="background:var(--ok)"></div> <span id="statusLabel" style="color:var(--ok);font-size:12px;font-weight:600">En ligne</span></div>
      <span style="font-size:13px;color:var(--tm);font-weight:500" id="currentTime"></span>
    </div>
  </div>

  <div class="content" id="mainContent">
  </div>
</div>
</div>

<!-- RESERVATION MODAL -->
<div class="modal-bg" id="resaModal" onclick="if(event.target===this)closeResaModal()">
<div class="modal">
  <h2>Nouvelle reservation</h2>
  <div class="card-s" style="margin-bottom:20px">Remplissez les informations du client <span id="resaDateLabel" style="font-weight:600;color:var(--ac)"></span></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Prenom</div><input class="finp" id="resaFirst" placeholder="Marie"></div><div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="resaLast" placeholder="Laurent"></div></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="resaCovers" type="number" min="1" max="20" value="2" onchange="resaAutoAssign()"></div><div class="finp-group"><div class="finp-label">Heure</div><input class="finp" id="resaTime" type="time" value="20:00" onchange="resaAutoAssign()"></div></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Telephone</div><input class="finp" id="resaPhone" placeholder="+33 6 ..."></div><div class="finp-group"><div class="finp-label">Email</div><input class="finp" id="resaEmail" placeholder="marie@email.com"></div></div>
  <div class="finp-group"><div class="finp-label">Source</div><select class="finp" id="resaSource" style="cursor:pointer"><option value="phone">Telephone</option><option value="walk-in">Walk-in</option><option value="whatsapp">WhatsApp</option><option value="web">Chat web</option><option value="zenchef">Zenchef</option></select></div>
  <div class="at-box" id="resaTableBox"><div class="at-l">Table assignee automatiquement</div><div class="at-v" id="resaTableVal"></div><div class="at-c" onclick="showResaTableSelect()">Changer de table</div></div>
  <div class="tsel" id="resaTableSel"></div>
  <div class="modal-act"><button class="mbtn mbtn-s" onclick="closeResaModal()">Annuler</button><button class="mbtn mbtn-p" onclick="submitResa()">Confirmer</button></div>
</div>
</div>

<!-- EDIT RESERVATION MODAL -->
<div class="modal-bg" id="editResaModal" onclick="if(event.target===this)closeEditResa()">
<div class="modal">
  <h2>Modifier la reservation</h2>
  <div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="editResaName"></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="editResaCovers" type="number" min="1" max="20"></div><div class="finp-group"><div class="finp-label">Heure</div><input class="finp" id="editResaTime" type="time"></div></div>
  <div class="finp-group"><div class="finp-label">Telephone</div><input class="finp" id="editResaPhone"></div>
  <div class="finp-group"><div class="finp-label">Table</div><select class="finp" id="editResaTable" style="cursor:pointer"></select></div>
  <div class="modal-act"><button class="mbtn mbtn-s" onclick="deleteResa()" style="color:#EF4444">Supprimer</button><button class="mbtn mbtn-s" onclick="closeEditResa()">Annuler</button><button class="mbtn mbtn-p" onclick="saveEditResa()">Enregistrer</button></div>
</div>
</div>

<div class="mobile-nav" id="mobileNav">
  <div class="mobile-nav-items">
    <button class="mobile-nav-btn active" data-pg="overview"><span>&#9673;</span>Accueil</button>
    <button class="mobile-nav-btn" data-pg="bookings"><span>&#128197;</span>Resas</button>
    <button class="mobile-nav-btn" data-pg="conversations"><span>&#128172;</span>Chat</button>
    <button class="mobile-nav-btn" data-pg="contacts"><span>&#128101;</span>Contacts</button>
    <button class="mobile-nav-btn" id="mobileMoreBtn" onclick="toggleMobileMore()"><span>&#8943;</span>Plus</button>
  </div>
</div>
<div class="mobile-more-overlay" id="mobileMoreOverlay" onclick="closeMobileMore()"></div>
<div class="mobile-more-drawer" id="mobileMoreDrawer">
  <div class="mobile-more-handle"></div>
  <div class="mobile-more-grid">
    <button class="mobile-more-item" data-pg="floorplan"><span>&#8862;</span>Plan</button>
    <button class="mobile-more-item" data-pg="menu"><span>&#9680;</span>Menu</button>
    <button class="mobile-more-item" data-pg="reviews"><span>&#9733;</span>Avis</button>
    <button class="mobile-more-item" data-pg="waitlist"><span>&#9201;</span>Attente</button>
    <button class="mobile-more-item" data-pg="stats"><span>&#9899;</span>Stats</button>
    <button class="mobile-more-item" data-pg="config"><span>&#9881;</span>Config</button>
    <button class="mobile-more-item" data-pg="account"><span>&#128100;</span>Compte</button>
  </div>
</div>

<div class="toast" id="toast"></div>
<div id="onboardingOverlay" style="display:none"></div>

<!-- HELP ASSISTANT -->
<style>
.help-btn{position:fixed;bottom:20px;right:20px;z-index:600;width:48px;height:48px;border-radius:50%;background:var(--acg);color:white;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 4px 16px rgba(45,125,210,.3);transition:all .2s}
@media(max-width:768px){.help-btn{bottom:76px}.help-panel{bottom:134px}}
.help-btn:hover{transform:scale(1.08)}
.help-btn.open{transform:rotate(45deg)}
.help-panel{position:fixed;bottom:78px;right:20px;z-index:600;width:340px;max-height:460px;border-radius:14px;background:var(--c);border:1.5px solid var(--b);box-shadow:0 16px 48px rgba(0,0,0,.25);display:none;flex-direction:column;overflow:hidden}
.help-panel.show{display:flex}
.help-hd{padding:14px 18px;background:var(--acg);display:flex;align-items:center;gap:10px}
.help-hd-title{font-size:14px;font-weight:700;color:white}
.help-hd-sub{font-size:10px;color:rgba(255,255,255,.7)}
.help-msgs{flex:1;padding:14px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;min-height:180px;max-height:300px}
.help-msg{max-width:85%;padding:8px 12px;border-radius:10px;font-size:12px;line-height:1.5}
.help-msg.bot{background:var(--bg);color:var(--t);align-self:flex-start;border-bottom-left-radius:3px}
.help-msg.user{background:var(--acg);color:white;align-self:flex-end;border-bottom-right-radius:3px}
.help-inp{padding:10px 14px;border-top:1px solid var(--b);display:flex;gap:6px}
.help-inp input{flex:1;padding:8px 12px;border-radius:16px;border:1px solid var(--b);background:var(--bg);color:var(--t);font-size:12px;font-family:var(--f);outline:none}
.help-inp input:focus{border-color:var(--ac)}
.help-inp button{width:32px;height:32px;border-radius:50%;background:var(--acg);color:white;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.help-quick{padding:6px 14px;display:flex;flex-wrap:wrap;gap:4px}
.help-quick button{padding:4px 10px;border-radius:12px;border:1px solid var(--b);background:transparent;color:var(--ts);font-size:10px;font-family:var(--f);cursor:pointer}
.help-quick button:hover{border-color:var(--ac);color:var(--ac)}
</style>

<button class="help-btn" id="helpBtn" onclick="toggleHelp()">?</button>
<div class="help-panel" id="helpPanel">
<div class="help-hd"><div><div class="help-hd-title">Assistant GuestScale</div><div class="help-hd-sub">Je peux vous aider</div></div></div>
<div class="help-msgs" id="helpMsgs"></div>
<div class="help-quick" id="helpQuick">
<button onclick="helpSend('Ajouter une table')">Ajouter une table</button>
<button onclick="helpSend('Modifier les horaires')">Modifier les horaires</button>
<button onclick="helpSend('Voir les stats')">Voir les stats</button>
<button onclick="helpSend('Gérer la liste d attente')">Liste d'attente</button>
</div>
<div class="help-inp">
<input type="text" id="helpInput" placeholder="Posez une question..." onkeydown="if(event.key==='Enter')helpSendInput()">
<button onclick="helpSendInput()">&#10148;</button>
</div>
</div>

<script>
// === AUTH ===
var TOKEN=null;
var USER_DATA=null;
var dailyMsg='';
var resaSelTable=null;
var selectedDate=fmtDate(new Date());

function fmtDate(d){var y=d.getFullYear();var m=String(d.getMonth()+1).padStart(2,'0');var day=String(d.getDate()).padStart(2,'0');return y+'-'+m+'-'+day}
function parseDateLocal(s){var p=s.split('-');return new Date(parseInt(p[0]),parseInt(p[1])-1,parseInt(p[2]))}
var MONTH_NAMES=["Janvier","Fevrier","Mars","Avril","Mai","Juin","Juillet","Aout","Septembre","Octobre","Novembre","Decembre"];
var MONTH_SHORT=["Jan","Fev","Mar","Avr","Mai","Jun","Jul","Aou","Sep","Oct","Nov","Dec"];
var DOW_NAMES=["L","M","M","J","V","S","D"];
var calPickerMode=null;

function buildCalendar(){
  var sel=parseDateLocal(selectedDate);
  var today=fmtDate(new Date());
  var year=sel.getFullYear();
  var month=sel.getMonth();
  var firstDay=new Date(year,month,1).getDay();
  var startIdx=(firstDay+6)%7;
  var daysInMonth=new Date(year,month+1,0).getDate();
  var daysInPrev=new Date(year,month,0).getDate();
  var h='<div class="cal-wrap" id="calWidget">';
  h+='<div class="cal-header">';
  h+='<div class="cal-nav"><div class="cal-arrow" data-calShift="-1">&#8249;</div>';
  h+='<div class="cal-title" data-calTogglePicker>'+MONTH_NAMES[month]+' '+year+'</div>';
  h+='<div class="cal-arrow" data-calShift="1">&#8250;</div></div>';
  h+='<div class="cal-today-btn" data-calToday>Aujourd&#39;hui</div>';
  h+='</div>';
  h+='<div class="cal-picker" id="calPicker"></div>';
  h+='<div class="cal-grid">';
  DOW_NAMES.forEach(function(d){h+='<div class="cal-dow">'+d+'</div>'});
  for(var i=startIdx-1;i>=0;i--){
    var day=daysInPrev-i;
    var pm=month===0?11:month-1;var py=month===0?year-1:year;
    var ds=fmtDate(new Date(py,pm,day));
    var cnt=bookings.filter(function(b){return(b.date||"").startsWith(ds)}).length;
    h+='<div class="cal-cell other" data-calDate="'+ds+'"><span class="cal-num">'+day+'</span><span class="cal-dot'+(cnt>0?" has":"")+'"></span></div>';
  }
  for(var d=1;d<=daysInMonth;d++){
    var ds=fmtDate(new Date(year,month,d));
    var isToday=ds===today;var isSel=ds===selectedDate;
    var cnt=bookings.filter(function(b){return(b.date||"").startsWith(ds)}).length;
    h+='<div class="cal-cell'+(isSel?" sel":"")+(isToday&&!isSel?" today":"")+'\" data-calDate="'+ds+'"><span class="cal-num">'+d+'</span><span class="cal-dot'+(cnt>0?" has":"")+'"></span></div>';
  }
  var total=startIdx+daysInMonth;var remaining=(7-total%7)%7;
  for(var i=1;i<=remaining;i++){
    var nm=month===11?0:month+1;var ny=month===11?year+1:year;
    var ds=fmtDate(new Date(ny,nm,i));
    var cnt=bookings.filter(function(b){return(b.date||"").startsWith(ds)}).length;
    h+='<div class="cal-cell other" data-calDate="'+ds+'"><span class="cal-num">'+i+'</span><span class="cal-dot'+(cnt>0?" has":"")+'"></span></div>';
  }
  h+='</div></div>';
  return h;
}

function showCalPicker(mode){
  var el=document.getElementById("calPicker");
  if(!el)return;
  calPickerMode=mode;
  var sel=parseDateLocal(selectedDate);
  var h='<div class="cal-picker-grid">';
  if(mode==="month"){
    MONTH_SHORT.forEach(function(m,i){
      h+='<div class="cal-picker-item'+(i===sel.getMonth()?" sel":"")+'\" data-calPickMonth="'+i+'">'+m+'</div>';
    });
  }else{
    var cy=sel.getFullYear();
    for(var y=cy-4;y<=cy+4;y++){
      h+='<div class="cal-picker-item'+(y===cy?" sel":"")+'\" data-calPickYear="'+y+'">'+y+'</div>';
    }
  }
  h+='</div>';
  el.innerHTML=h;
  el.classList.add("show");
}

function getBookingsForDate(dateStr){
  return bookings.filter(function(b){return(b.date||"").startsWith(dateStr)});
}

function getToken(){
  if(TOKEN)return TOKEN;
  try{TOKEN=sessionStorage.getItem('gs_token')}catch(e){}
  return TOKEN;
}

function apiFetch(url,opts){
  opts=opts||{};
  opts.headers=opts.headers||{};
  var t=getToken();
  if(t)opts.headers['Authorization']='Bearer '+t;
  if(!opts.headers['Content-Type']&&opts.body)opts.headers['Content-Type']='application/json';
  return fetch(url,opts);
}

function doLogin(){
  var email=document.getElementById('loginEmail').value.trim();
  var pwd=document.getElementById('loginPwd').value;
  var err=document.getElementById('loginError');
  if(!email||!pwd){err.style.display='block';err.textContent='Veuillez remplir email et mot de passe.';return}
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email,password:pwd})})
  .then(function(r){return r.json()})
  .then(function(d){
    if(d.token){
      TOKEN=d.token;
      USER_DATA=d.user;
      try{sessionStorage.setItem('gs_token',d.token)}catch(e){}
      document.getElementById('loginOverlay').style.display='none';
      document.getElementById('app').classList.add('v');
      if(USER_DATA){
        document.getElementById('sbRestName').textContent=USER_DATA.restaurant_name||'Restaurant';
        document.getElementById('sbUserEmail').textContent=USER_DATA.email||'';
      }
      loadAll();
    }else{
      err.style.display='block';
      err.textContent=d.error||'Identifiants incorrects.';
      document.getElementById('loginPwd').style.borderColor='var(--da)';
      document.getElementById('loginPwd').classList.remove('shake');
      void document.getElementById('loginPwd').offsetWidth;
      document.getElementById('loginPwd').classList.add('shake');
    }
  })
  .catch(function(){
    err.style.display='block';
    err.textContent='Erreur de connexion au serveur.';
  });
}

// Auto-login if token exists
(function(){
  var t=null;
  try{t=sessionStorage.getItem('gs_token')}catch(e){}
  if(t){
    TOKEN=t;
    apiFetch('/api/me').then(function(r){return r.json()}).then(function(d){
      if(d.user){
        USER_DATA=d.user;
        document.getElementById('loginOverlay').style.display='none';
        document.getElementById('app').classList.add('v');
        document.getElementById('sbRestName').textContent=d.user.restaurant_name||'Restaurant';
        document.getElementById('sbUserEmail').textContent=d.user.email||'';
        loadAll();
      }else{
        TOKEN=null;
        try{sessionStorage.removeItem('gs_token')}catch(e){}
      }
    }).catch(function(){TOKEN=null;try{sessionStorage.removeItem('gs_token')}catch(e){}});
  }
})();

function togglePwdVis(){
  var inp=document.getElementById('loginPwd');
  var btn=document.getElementById('pwdToggle');
  if(inp.type==='password'){inp.type='text';btn.textContent='🔒'}
  else{inp.type='password';btn.textContent='👁'}
}

function showForgotPwd(){
  document.querySelector('.lcd').style.display='none';
  document.getElementById('forgotPwdForm').style.display='block';
}
function hideForgotPwd(){
  document.getElementById('forgotPwdForm').style.display='none';
  document.querySelector('.lcd').style.display='block';
  document.getElementById('forgotStep1').style.display='block';
  document.getElementById('forgotStep2').style.display='none';
  document.getElementById('forgotError').style.display='none';
  document.getElementById('forgotSuccess').style.display='none';
}
function sendResetCode(){
  var email=document.getElementById('forgotEmail').value.trim();
  if(!email){document.getElementById('forgotError').textContent='Entrez votre email';document.getElementById('forgotError').style.display='block';return}
  document.getElementById('forgotError').style.display='none';
  fetch('/api/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.error){document.getElementById('forgotError').textContent=d.error;document.getElementById('forgotError').style.display='block';return}
    document.getElementById('forgotSuccess').textContent='Code envoye ! Verifiez votre email.';
    document.getElementById('forgotSuccess').style.display='block';
    document.getElementById('forgotStep1').style.display='none';
    document.getElementById('forgotStep2').style.display='block';
  });
}
function doResetPwd(){
  var code=document.getElementById('resetCode').value.trim();
  var pwd=document.getElementById('newPwd').value;
  if(!code||!pwd){document.getElementById('forgotError').textContent='Code et mot de passe requis';document.getElementById('forgotError').style.display='block';return}
  document.getElementById('forgotError').style.display='none';
  fetch('/api/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:code,new_password:pwd})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.error){document.getElementById('forgotError').textContent=d.error;document.getElementById('forgotError').style.display='block';return}
    document.getElementById('forgotSuccess').textContent='Mot de passe modifie ! Vous pouvez vous connecter.';
    document.getElementById('forgotSuccess').style.display='block';
    document.getElementById('forgotStep2').style.display='none';
    setTimeout(hideForgotPwd,3000);
  });
}

function doLogout(){
  TOKEN=null;USER_DATA=null;
  try{sessionStorage.removeItem('gs_token')}catch(e){}
  location.reload();
}

/* Auto-logout after 2h of inactivity */
var _idleTimer=null;
function resetIdleTimer(){
  if(_idleTimer)clearTimeout(_idleTimer);
  _idleTimer=setTimeout(function(){
    if(TOKEN){showToast('Session expirée — reconnexion nécessaire');doLogout()}
  },7200000); /* 2 hours */
}
['mousemove','keydown','click','scroll','touchstart'].forEach(function(ev){
  document.addEventListener(ev,resetIdleTimer,{passive:true});
});
resetIdleTimer();

/* Global error handler */
window.addEventListener('unhandledrejection',function(e){
  if(e.reason&&e.reason.message&&e.reason.message.indexOf('401')!==-1){doLogout()}
});

/* HTML escape — sanitize user data before innerHTML injection */
function esc(s){if(!s)return '';var d=document.createElement('div');d.textContent=String(s);return d.innerHTML}

var pageTitles={overview:"Vue d'ensemble",floorplan:"Plan de salle",bookings:"Réservations",menu:"Menu",conversations:"Conversations",reviews:"Avis",contacts:"Contacts",config:"Configuration",stats:"Statistiques",account:"Mon compte",waitlist:"Liste d'attente"};

function toggleMobileMore(){
  var ov=document.getElementById('mobileMoreOverlay');
  var dr=document.getElementById('mobileMoreDrawer');
  var isOpen=dr.classList.contains('show');
  if(isOpen){closeMobileMore()}
  else{ov.classList.add('show');dr.classList.add('show')}
}
function closeMobileMore(){
  document.getElementById('mobileMoreOverlay').classList.remove('show');
  document.getElementById('mobileMoreDrawer').classList.remove('show');
}

var morePages=['floorplan','menu','reviews','waitlist','stats','config','account'];

function switchPage(id,btn){
  currentPage=id;
  document.getElementById('pageTitle').textContent=pageTitles[id]||id;
  document.querySelectorAll('.nb').forEach(function(b){b.classList.remove('on')});
  if(btn&&btn.classList&&!btn.classList.contains('mobile-nav-btn')&&!btn.classList.contains('mobile-more-item'))btn.classList.add('on');
  else{var b=document.querySelector('.sidebar [data-pg="'+id+'"]');if(b)b.classList.add('on')}
  /* Mobile bottom nav */
  document.querySelectorAll('.mobile-nav-btn').forEach(function(b){b.classList.remove('active')});
  var mb=document.querySelector('.mobile-nav-btn[data-pg="'+id+'"]');
  if(mb){mb.classList.add('active')}
  else if(morePages.indexOf(id)!==-1){
    document.getElementById('mobileMoreBtn').classList.add('active');
  }
  /* Mobile more drawer items */
  document.querySelectorAll('.mobile-more-item').forEach(function(b){b.classList.remove('active')});
  var mi=document.querySelector('.mobile-more-item[data-pg="'+id+'"]');
  if(mi)mi.classList.add('active');
  closeMobileMore();
  renderPage(id);
  window.scrollTo({top:0,behavior:'smooth'});
}

function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(function(){t.style.display='none'},2500)}

function updateTime(){var n=new Date();document.getElementById('currentDate').textContent=n.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});document.getElementById('currentTime').textContent=n.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}

// ===== DATA =====
var bookings=[],contacts={},conversations={},floorplan=[],reviewQueue=[],waitlistEntries=[];
var floorSlots={};
var cancelledCount=0;
var restaurantConfig={};
var overviewBlocks={daily:true,stats:true,floor:true,bookings:true,contacts:true};

function mergeBookingsIntoFloor(){
  var tableBookings={};
  bookings.forEach(function(b){
    if(b.table && (b.date||'').startsWith(selectedDate)){
      b.table.split('+').forEach(function(tid){tableBookings[tid.trim()]=b.name});
    }
  });
  floorplan.forEach(function(t){
    t.booking_name=tableBookings[t.id]||null;
  });
}

var currentPage='overview';
var lastVersion=0;

function loadAll(){
  updateTime();setInterval(updateTime,30000);
  fetchData();
  setInterval(checkUpdates,3000);
}

function checkUpdates(){
  apiFetch('/api/version').then(function(r){return r.json()}).then(function(d){
    if(d.v&&d.v!==lastVersion){
      lastVersion=d.v;
      fetchDataSilent();
    }
  }).catch(function(){});
}

function fetchDataSilent(){
  var ok=function(r){return r.ok?r.json():Promise.resolve(null)};
  Promise.all([
    apiFetch('/api/bookings').then(ok).catch(function(){return null}),
    apiFetch('/api/contacts').then(ok).catch(function(){return null}),
    apiFetch('/api/conversations').then(ok).catch(function(){return null}),
    apiFetch('/api/floorplan').then(ok).catch(function(){return null}),
    apiFetch('/api/reviews').then(ok).catch(function(){return null}),
    apiFetch('/api/daily').then(ok).catch(function(){return null}),
    apiFetch('/api/menu').then(ok).catch(function(){return null}),
    apiFetch('/api/waitlist').then(ok).catch(function(){return null})
  ]).then(function(res){
    if(res[0])bookings=(res[0].bookings)||[];
    if(res[1]){contacts={};(res[1].contacts||[]).forEach(function(c){if(c.phone)contacts[c.phone]=c})}
    if(res[2]){conversations={};(res[2].conversations||[]).forEach(function(cv){conversations[cv.phone||cv.id]=cv})}
    if(res[3]){floorplan=(res[3].tables||[]);floorSlots=(res[3].slots||{});mergeBookingsIntoFloor()}
    if(res[4])reviewQueue=(res[4].queue||[]);
    if(res[5])dailyMsg=(res[5].message)||'';
    if(res[6])menuSections=(res[6].sections)||[];
    if(res[7])waitlistEntries=(res[7].waitlist)||[];
    updateBadges();
    if(currentPage==='overview'||currentPage==='bookings'||currentPage==='conversations'||currentPage==='floorplan'||currentPage==='waitlist')renderPage(currentPage);
  }).catch(function(){});
}

function fetchData(){
  var ok=function(r){return r.ok?r.json():Promise.resolve(null)};
  Promise.all([
    apiFetch('/api/bookings').then(ok).catch(function(){return []}),
    apiFetch('/api/contacts').then(ok).catch(function(){return {}}),
    apiFetch('/api/conversations').then(ok).catch(function(){return {}}),
    apiFetch('/api/floorplan').then(ok).catch(function(){return []}),
    apiFetch('/api/reviews').then(ok).catch(function(){return []}),
    apiFetch('/api/config').then(ok).catch(function(){return {}}),
    apiFetch('/api/daily').then(ok).catch(function(){return {message:''}}),
    apiFetch('/api/menu').then(ok).catch(function(){return {sections:[]}}),
    apiFetch('/api/waitlist').then(ok).catch(function(){return {waitlist:[]}})
  ]).then(function(res){
    bookings=(res[0]&&res[0].bookings)||[];
    var ctData=res[1]||{};
    contacts={};
    (ctData.contacts||[]).forEach(function(c){if(c.phone)contacts[c.phone]=c});
    var convData=res[2]||{};
    conversations={};
    (convData.conversations||[]).forEach(function(cv){conversations[cv.phone||cv.id]=cv});
    var fpData=res[3]||{};
    floorplan=(fpData.tables||[]);
    floorSlots=(fpData.slots||{});
    mergeBookingsIntoFloor();
    var rvData=res[4]||{};
    reviewQueue=(rvData.queue||[]);
    restaurantConfig=res[5]||{};
    // Load reminders setting
    apiFetch('/api/settings').then(function(r){return r.ok?r.json():null}).then(function(s){
      if(s&&typeof s.reminders_enabled!=='undefined')restaurantConfig._reminders_enabled=s.reminders_enabled;
    }).catch(function(){});
    dailyMsg=(res[6]&&res[6].message)||'';
    menuSections=(res[7]&&res[7].sections)||[];
    waitlistEntries=(res[8]&&res[8].waitlist)||[];
    updateBadges();
    renderPage(currentPage||'overview');
    checkOnboarding();
  }).catch(function(err){
    console.error('Load error:',err);
    renderPage(currentPage||'overview');
  });
}

function updateBadges(){
  var today=fmtDate(new Date());
  var todayBookings=bookings.filter(function(b){return(b.date||'').startsWith(today)});
  document.getElementById('bookBadge').textContent=todayBookings.length;
  var convCount=Object.keys(conversations).length;
  document.getElementById('convBadge').textContent=convCount;
  var pendingReviews=reviewQueue.filter(function(r){return!r.sent}).length;
  document.getElementById('reviewBadge').textContent=pendingReviews;
  var waitingCount=waitlistEntries.filter(function(w){return w.status==='waiting'||w.status==='notified'}).length;
  var wb=document.getElementById('waitBadge');if(wb)wb.textContent=waitingCount;
}

// ===== PAGE RENDERER =====
function renderPage(id){
  var c=document.getElementById('mainContent');
  if(id==='overview') renderOverview(c);
  else if(id==='floorplan') renderFloorplan(c);
  else if(id==='bookings') renderBookings(c);
  else if(id==='menu') renderMenu(c);
  else if(id==='conversations') renderConversations(c);
  else if(id==='reviews') renderReviews(c);
  else if(id==='contacts') renderContacts(c);
  else if(id==='config') renderConfig(c);
  else if(id==='stats') renderStats(c);
  else if(id==='account') renderAccount(c);
  else if(id==='waitlist') renderWaitlist(c);
}

// ===== ACCOUNT PAGE =====
function renderAccount(c){
  var u=USER_DATA||{};
  var h='';
  h+='<div class="card" style="padding:24px;margin-bottom:16px"><div class="cfs"><div class="cft">Informations du compte</div><div class="cfsb">Gerez votre compte et votre abonnement</div>';
  h+='<div class="cfr"><div><div class="cfl">Email</div><div class="cfd">'+(u.email||'—')+'</div></div></div>';
  h+='<div class="cfr"><div><div class="cfl">Nom</div><div class="cfd">'+(u.first_name||'')+' '+(u.last_name||'')+'</div></div></div>';
  h+='<div class="cfr"><div><div class="cfl">Restaurant</div><div class="cfd">'+(u.restaurant_name||'—')+'</div></div></div>';
  h+='<div class="cfr"><div><div class="cfl">Statut</div><div class="cfd"><span class="badge" style="background:'+(u.restaurant_status==='active'?'var(--okb)':'var(--wab)')+';color:'+(u.restaurant_status==='active'?'var(--ok)':'var(--wa)')+'">'+(u.restaurant_status==='active'?'Actif':'Essai gratuit')+'</span></div></div></div>';
  if(u.trial_ends_at){
    var te=new Date(u.trial_ends_at);
    var now=new Date();
    var days=Math.ceil((te-now)/(1000*60*60*24));
    if(days>0){
      h+='<div class="cfr"><div><div class="cfl">Fin de l essai</div><div class="cfd">'+te.toLocaleDateString('fr-FR')+' ('+days+' jours restants)</div></div></div>';
    }
  }
  h+='</div></div>';
  // Change password
  h+='<div class="card" style="padding:24px;margin-bottom:16px"><div class="cfs"><div class="cft">Changer le mot de passe</div>';
  h+='<div class="finp-group" style="margin-top:12px"><div class="finp-label">Mot de passe actuel</div><input class="finp" type="password" id="accCurPwd"></div>';
  h+='<div class="finp-group"><div class="finp-label">Nouveau mot de passe</div><input class="finp" type="password" id="accNewPwd"></div>';
  h+='<div class="finp-group"><div class="finp-label">Confirmer</div><input class="finp" type="password" id="accNewPwd2"></div>';
  h+='<button class="ba" style="margin-top:8px" onclick="changePassword()">Modifier le mot de passe</button>';
  h+='</div></div>';
  // Logout
  h+='<div class="card" style="padding:24px"><button style="padding:10px 20px;border-radius:8px;border:1px solid var(--da);background:transparent;color:var(--da);font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" onclick="doLogout()">Se deconnecter</button></div>';
  c.innerHTML=h;
}

function changePassword(){
  var cur=document.getElementById('accCurPwd').value;
  var np=document.getElementById('accNewPwd').value;
  var np2=document.getElementById('accNewPwd2').value;
  if(!cur||!np){showToast('Remplissez tous les champs');return}
  if(np!==np2){showToast('Les mots de passe ne correspondent pas');return}
  if(np.length<12){showToast('Minimum 12 caractères');return}
  apiFetch('/api/change-password',{method:'POST',body:JSON.stringify({current_password:cur,new_password:np})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok')showToast('Mot de passe modifie');
    else showToast(d.error||'Erreur');
  }).catch(function(){showToast('Erreur')});
}

// ===== WAITLIST =====
function renderWaitlist(c){
  var today=fmtDate(new Date());
  var active=waitlistEntries.filter(function(w){return w.status==='waiting'||w.status==='notified'});
  var past=waitlistEntries.filter(function(w){return w.status==='accepted'||w.status==='declined'||w.status==='expired'});
  var h='';
  // Add to waitlist form
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="card-t" style="margin-bottom:14px">Ajouter a la liste d&#39;attente</div>';
  h+='<div class="finp-row"><div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="wlName" placeholder="Marie Laurent"></div><div class="finp-group"><div class="finp-label">Telephone</div><input class="finp" id="wlPhone" placeholder="+33 6 ..."></div></div>';
  h+='<div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="wlCovers" type="number" min="1" max="20" value="2"></div><div class="finp-group"><div class="finp-label">Service</div><select class="finp" id="wlService"><option value="midi">Midi</option><option value="soir" selected>Soir</option></select></div></div>';
  h+='<div class="finp-row"><div class="finp-group"><div class="finp-label">Date</div><input class="finp" id="wlDate" type="date" value="'+today+'"></div><div class="finp-group"><div class="finp-label">Heure souhaitee</div><input class="finp" id="wlTime" type="time" value="20:00"></div></div>';
  h+='<button class="ba" style="margin-top:8px" onclick="addToWaitlist()">Ajouter</button></div>';
  // Active waitlist
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><div><div class="card-t">En attente</div><div class="card-s">'+active.length+' personnes</div></div></div>';
  if(!active.length){
    h+='<div style="padding:24px;text-align:center;color:var(--tm);font-size:13px">Aucune personne en liste d&#39;attente</div>';
  }else{
    active.forEach(function(w,i){
      var statusBg=w.status==='notified'?'var(--al)':'var(--wab)';
      var statusCol=w.status==='notified'?'var(--ac)':'var(--wa)';
      var statusLabel=w.status==='notified'?'Notifie':'En attente';
      h+='<div style="padding:14px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center">';
      h+='<div><div style="font-size:14px;font-weight:600">'+w.name+'</div>';
      h+='<div style="font-size:12px;color:var(--tm);margin-top:2px">'+w.covers+'p · '+(w.service==='midi'?'Midi':'Soir')+' · '+w.date+(w.preferred_time?' · '+w.preferred_time:'')+'</div>';
      if(w.phone)h+='<div style="font-size:11px;color:var(--ts);margin-top:2px">'+w.phone+'</div>';
      h+='</div>';
      h+='<div style="display:flex;align-items:center;gap:8px">';
      h+='<span class="badge" style="background:'+statusBg+';color:'+statusCol+'">'+statusLabel+'</span>';
      if(w.status==='waiting')h+='<button style="padding:4px 10px;border-radius:6px;border:1px solid var(--ac);background:var(--al);color:var(--ac);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-wlNotify="'+w.id+'|'+w.date+'|'+w.service+'|'+w.covers+'">Notifier</button>';
      h+='<button style="padding:4px 10px;border-radius:6px;border:1px solid var(--b);background:var(--card);color:var(--da);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-wlRemove="'+w.id+'">Retirer</button>';
      h+='</div></div>';
    });
  }
  h+='</div>';
  // History
  if(past.length){
    h+='<div class="card"><div class="card-h"><div><div class="card-t">Historique</div><div class="card-s">'+past.length+' entries</div></div></div>';
    past.slice(-20).reverse().forEach(function(w){
      var sCol=w.status==='accepted'?'var(--ok)':w.status==='declined'?'var(--da)':'var(--tm)';
      var sLabel=w.status==='accepted'?'Accepte':w.status==='declined'?'Decline':'Expire';
      h+='<div style="padding:10px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center;opacity:.7">';
      h+='<div><span style="font-weight:600">'+w.name+'</span> <span style="color:var(--tm);font-size:12px">'+w.covers+'p · '+w.date+'</span></div>';
      h+='<span style="font-size:12px;font-weight:600;color:'+sCol+'">'+sLabel+'</span></div>';
    });
    h+='</div>';
  }
  c.innerHTML=h;
}

function addToWaitlist(){
  var name=document.getElementById('wlName').value.trim();
  var phone=document.getElementById('wlPhone').value.trim();
  var covers=document.getElementById('wlCovers').value;
  var service=document.getElementById('wlService').value;
  var wdate=document.getElementById('wlDate').value;
  var wtime=document.getElementById('wlTime').value;
  if(!name){showToast('Nom requis');return}
  apiFetch('/api/waitlist/add',{method:'POST',body:JSON.stringify({name:name,phone:phone,covers:parseInt(covers),service:service,date:wdate,time:wtime})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast(name+' ajoute a la liste');fetchData();}
    else showToast(d.error||'Erreur');
  });
}

function removeWaitlist(id){
  apiFetch('/api/waitlist/remove',{method:'POST',body:JSON.stringify({id:id})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='removed'){showToast('Retire de la liste');fetchData();}
  });
}

function notifyWaitlist(id,wdate,service,covers){
  apiFetch('/api/waitlist/notify',{method:'POST',body:JSON.stringify({date:wdate,service:service,covers:parseInt(covers)})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Notification envoyee');fetchData();}
  });
}

// ===== OVERVIEW =====
function renderOverview(c){
  var tb=getBookingsForDate(selectedDate);
  var convArr=Object.entries(conversations);
  var ctArr=Object.entries(contacts);
  var totalSeats=floorplan.reduce(function(a,t){return a+t.seats},0);
  var today=fmtDate(new Date());
  var isToday=selectedDate===today;
  var dateLabel=isToday?"auj.":parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});
  
  var h='';
  
  // Top layout: stats left + calendar right
  h+='<div class="ov-layout" style="display:flex;gap:14px;align-items:flex-start;margin-bottom:14px">';
  h+='<div style="flex:1;min-width:0">';
  
  // Daily message
  if(overviewBlocks.daily&&isToday){
    h+='<div class="db" id="ov-daily"><div class="db-top"><div class="di">📢</div><div style="flex:1"><div class="dlb">Message du jour <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--ts)">— cliquez pour modifier</span></div>';
    h+='<div class="dtx" id="dailyView" onclick="editDaily()">'+(dailyMsg||'Aucun message — cliquez pour ajouter')+'</div>';
    h+='<textarea class="dtx-edit" id="dailyEdit" style="display:none"></textarea>';
    h+='<div class="dme" id="dailyMeta">Transmis automatiquement par l agent IA aux clients</div></div></div>';
    h+='<div class="db-act" id="dailyActions" style="display:none"><button class="dbb dbb-s" onclick="saveDaily()">💾 Enregistrer</button><button class="dbb dbb-b" onclick="broadcastDaily()">📤 Envoyer aux contacts</button><button class="dbb dbb-c" onclick="cancelDaily()">Annuler</button></div></div>';
  }
  
  // Stats
  if(overviewBlocks.stats){
    h+='<div class="sg" id="ov-stats">';
    h+='<div class="sc" data-nav="conversations" style="cursor:pointer"><div class="sl">Messages</div><div class="sv" style="color:var(--ac)">'+convArr.reduce(function(a,e){var d=e[1];return a+((d.messages&&d.messages.length)||d.count||0)},0)+'</div><div class="ss2">total</div></div>';
    h+='<div class="sc" data-nav="bookings" style="cursor:pointer"><div class="sl">Réservations</div><div class="sv" style="color:var(--ok)">'+tb.length+'</div><div class="ss2">'+dateLabel+'</div></div>';
    h+='<div class="sc" data-nav="conversations" style="cursor:pointer"><div class="sl">Conversations</div><div class="sv" style="color:var(--bl2)">'+convArr.length+'</div><div class="ss2">clients actifs</div></div>';
    h+='<div class="sc" data-nav="contacts" style="cursor:pointer"><div class="sl">Contacts</div><div class="sv" style="color:var(--wa)">'+ctArr.length+'</div><div class="ss2">en base</div></div>';
    h+='</div>';
  }
  h+='</div>'; // close left column
  
  // Calendar right column
  h+='<div style="width:280px;flex-shrink:0">';
  h+=buildCalendar();
  h+='</div>';
  h+='</div>'; // close ov-layout
  
  // Floor plan mini
  if(overviewBlocks.floor){
    h+='<div class="fm" id="ov-floor" data-nav="floorplan"><div style="display:flex;justify-content:space-between;align-items:center"><div><div class="card-t">Plan de salle</div><div class="card-s">'+floorplan.length+' tables · '+totalSeats+' places</div></div><span style="font-size:12px;color:var(--ac);font-weight:600">Modifier →</span></div><div class="fc" id="floorMiniCanvas"></div></div>';
  }
  
  // Bookings + Conversations
  if(overviewBlocks.bookings){
    var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
    h+='<div class="g2" id="ov-book"><div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+tb.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle réservation</button></div>';
    tb.slice(0,5).forEach(function(b){
      h+='<div class="rw"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+'</div></div></div><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div>';
    });
    if(tb.length===0) h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucune réservation '+dateLabel+'</div>';
    h+='</div>';
    
    // Conversations
    h+='<div class="card"><div class="card-h"><div><div class="card-t">Conversations</div><div class="card-s">'+convArr.length+' actives</div></div></div>';
    convArr.slice(0,4).forEach(function(e,i){
      var phone=e[0],data=e[1];
      var name=(contacts[phone]&&contacts[phone].name)||phone;
      var lastMsg=data.last_message||((data.messages&&data.messages.length)?data.messages[data.messages.length-1].content:'...');
      var colors=['var(--al)','var(--blb)','var(--okb)','var(--wab)'];
      var tcolors=['var(--ac)','var(--bl2)','var(--ok)','var(--wa)'];
      h+='<div class="cr"><div class="cav" style="background:'+colors[i%4]+';color:'+tcolors[i%4]+'">'+name.charAt(0).toUpperCase()+'</div><div style="flex:1;min-width:0"><div style="display:flex;justify-content:space-between"><span style="font-size:14px;font-weight:600">'+name+'</span></div><div class="cmsg">'+lastMsg+'</div></div></div>';
    });
    if(convArr.length===0) h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucune conversation</div>';
    h+='</div></div>';
  }
  
  // Contacts
  if(overviewBlocks.contacts){
    h+='<div class="card" style="padding:20px" id="ov-contacts"><div class="card-t" style="margin-bottom:4px">Base de contacts</div><div class="card-s" style="margin-bottom:16px">'+ctArr.length+' clients</div><div class="cg3">';
    ctArr.slice(0,6).forEach(function(e){
      var phone=e[0],ct=e[1];
      var srcColors2={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
      var srcLabels={whatsapp:'WhatsApp',web:'Web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
      var src=ct.source||'phone';
      h+='<div class="cc"><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm);margin-top:4px">'+phone+'</div><div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px"><span style="font-size:11px;color:var(--ts)">'+(ct.visits||0)+' visite'+((ct.visits||0)>1?'s':'')+'</span><span class="src-badge" style="color:'+(srcColors2[src]||'#A8A29E')+';background:'+(srcColors2[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
    });
    h+='</div></div>';
  }
  
  c.innerHTML=h;
  if(overviewBlocks.floor&&floorplan.length>0) drawFloorMini();
}

// Daily message inline edit
function editDaily(){
  document.getElementById('dailyView').style.display='none';
  var ed=document.getElementById('dailyEdit');ed.style.display='block';ed.value=dailyMsg;ed.focus();
  ed.onkeydown=function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();saveDaily()}};
  document.getElementById('dailyActions').style.display='flex';
  document.getElementById('dailyMeta').style.display='none';
}
function saveDaily(){
  dailyMsg=document.getElementById('dailyEdit').value.trim();
  // Save to backend
  apiFetch('/api/daily',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:dailyMsg})});
  document.getElementById('dailyView').textContent=dailyMsg||'Aucun message — cliquez pour ajouter';
  cancelDaily();
  showToast('💾 Message du jour enregistré');
}
function cancelDaily(){
  document.getElementById('dailyView').style.display='block';
  document.getElementById('dailyEdit').style.display='none';
  document.getElementById('dailyActions').style.display='none';
  document.getElementById('dailyMeta').style.display='block';
}
function broadcastDaily(){
  saveDaily();
  apiFetch('/api/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:dailyMsg})});
  showToast('📤 Message envoyé aux contacts');
}

// Floor plan mini drawing
function drawFloorMini(){
  var el=document.getElementById('floorMiniCanvas');
  if(!el||!floorplan.length)return;
  el.querySelectorAll('.ftbl').forEach(function(e){e.remove()});
  var zoneColors={salle:'#2563EB',terrasse:'#16A34A',bar:'#D97706'};
  floorplan.forEach(function(t){
    var d=document.createElement('div');d.className='ftbl';
    var w=(t.shape==='round'?(t.seats<=2?34:t.seats<=4?40:48):(t.seats<=2?34:t.seats<=4?44:t.seats<=6?52:60))*.85;
    var h2=(t.shape==='round'?w:(t.seats<=4?34:38))*.85;
    var c=zoneColors[t.zone]||'#2563EB';
    var bk=t.booking_name;
    d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'8px')+';border-color:'+(bk?'#DC262660':c+'50')+';background:'+(bk?'#DC262610':c+'08')+';color:'+(bk?'#DC2626':c);
    d.innerHTML='<div style="font-size:8px;font-weight:800">'+t.id+'</div><div style="font-size:7px;color:'+(bk?'#DC2626':'var(--tm)')+'">'+( bk||t.seats+'p')+'</div>';
    el.appendChild(d);
  });
}

// ===== FLOORPLAN PAGE =====
// ===== FLOORPLAN - DUAL MODE =====
var fpSelected=null;
var fpDragging=null;
var fpMode='resa';
var fpService='midi';
var fpSlot='all';
var fpZones=[{id:'salle',label:'Salle',color:'#6366F1'},{id:'terrasse',label:'Terrasse',color:'#10B981'},{id:'bar',label:'Bar',color:'#F59E0B'}];

function fpMergeForService(){
  var filtered=bookings.filter(function(b){
    // Filter by selected date first
    if(!(b.date||'').startsWith(selectedDate))return false;
    var bt=b.booking_time||b.time||'';if(!bt||!b.table)return false;
    var bh=parseInt(bt.split(':')[0])||0;
    if(fpService==='midi'&&bh>=17)return false;
    if(fpService==='soir'&&bh<17)return false;
    if(fpSlot!=='all'){var sh=parseInt(fpSlot.split(':')[0])||0;var sm=parseInt(fpSlot.split(':')[1])||0;var bm=parseInt(bt.split(':')[1])||0;if(Math.abs((bh*60+bm)-(sh*60+sm))>90)return false}
    return true;
  });
  var tb={};filtered.forEach(function(b){if(b.table){b.table.split('+').forEach(function(tid){tb[tid.trim()]=b.name})}});
  floorplan.forEach(function(t){t.booking_name=tb[t.id]||null});
}

var fpServiceInitDone=false;

function renderFloorplan(c){
  var nowH=new Date().getHours();
  if(!fpServiceInitDone&&fpSlot==='all'&&nowH>=17){fpService='soir';fpServiceInitDone=true;}
  fpMergeForService();
  var totalSeats=floorplan.reduce(function(a,t){return a+(t.seats||0)},0);
  var booked=floorplan.filter(function(t){return t.booking_name}).length;
  var free=floorplan.length-booked;

  // Get bookings for selected date + service for sidebar
  var sidebarBookings=bookings.filter(function(b){
    if(!(b.date||'').startsWith(selectedDate))return false;
    var bt=b.booking_time||b.time||'';
    if(!bt)return true; // show unassigned too
    var bh=parseInt(bt.split(':')[0])||0;
    if(fpService==='midi'&&bh>=17)return false;
    if(fpService==='soir'&&bh<17)return false;
    return true;
  }).sort(function(a,b){return(a.booking_time||a.time||'').localeCompare(b.booking_time||b.time||'')});
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};

  var h='';

  h+='<div class="card" style="padding:20px;margin-bottom:14px">';
  h+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px"><div><div class="card-t">Plan de salle</div><div class="card-s" id="fpSummary">'+floorplan.length+' tables \u00b7 '+totalSeats+' places \u00b7 <span style="color:var(--ok)">'+free+' libres</span> \u00b7 <span style="color:var(--da)">'+booked+' occupees</span></div></div>';
  h+='<div style="display:flex;gap:6px"><button style="padding:8px 16px;border-radius:8px;border:1.5px solid '+(fpMode==='resa'?'var(--ac)':'var(--b)')+';background:'+(fpMode==='resa'?'var(--al)':'var(--card)')+';color:'+(fpMode==='resa'?'var(--ac)':'var(--ts)')+';font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpModeResa>Réservations</button>';
  h+='<button style="padding:8px 16px;border-radius:8px;border:1.5px solid '+(fpMode==='edit'?'var(--ac)':'var(--b)')+';background:'+(fpMode==='edit'?'var(--al)':'var(--card)')+';color:'+(fpMode==='edit'?'var(--ac)':'var(--ts)')+';font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpModeEdit>Modifier plan</button></div></div>';
  if(fpMode==='edit'){
    h+='<div style="display:flex;gap:5px;margin-bottom:10px;padding:8px 12px;background:var(--bg);border-radius:10px;overflow-x:auto;align-items:center"><span style="font-size:11px;font-weight:700;color:var(--tm);white-space:nowrap;margin-right:4px">Ajouter :</span>';
    [{s:'round',n:2},{s:'round',n:4},{s:'round',n:6},{s:'rect',n:2},{s:'rect',n:4},{s:'rect',n:6},{s:'rect',n:8}].forEach(function(p){h+='<button style="padding:5px 10px;border-radius:7px;border:1.5px solid var(--b);background:var(--card);color:var(--t);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f);white-space:nowrap;display:flex;align-items:center;gap:3px" data-fpAdd="'+p.s+'-'+p.n+'"><span style="width:'+(p.s==='round'?12:16)+'px;height:12px;border-radius:'+(p.s==='round'?'50%':'2px')+';border:2px solid var(--ac);display:inline-block"></span>'+p.n+'p</button>'});
    h+='<div style="margin-left:auto"><button class="ba" data-fpSave>Enregistrer le plan</button></div></div>';
  }
  if(fpMode==='resa'){
    h+='<div style="display:flex;gap:0;margin-bottom:10px"><button style="flex:1;padding:10px;border:1.5px solid '+(fpService==='midi'?'var(--ac)':'var(--b)')+';border-right:none;border-radius:8px 0 0 8px;background:'+(fpService==='midi'?'var(--al)':'var(--card)')+';color:'+(fpService==='midi'?'var(--ac)':'var(--ts)')+';font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSvc="midi">&#9728; Midi</button>';
    h+='<button style="flex:1;padding:10px;border:1.5px solid '+(fpService==='soir'?'var(--ac)':'var(--b)')+';border-radius:0 8px 8px 0;background:'+(fpService==='soir'?'var(--al)':'var(--card)')+';color:'+(fpService==='soir'?'var(--ac)':'var(--ts)')+';font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSvc="soir">&#9790; Soir</button></div>';
    var slots=fpService==='midi'?["all","12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45","14:00","14:15","14:30"]:["all","19:00","19:15","19:30","19:45","20:00","20:15","20:30","20:45","21:00","21:15","21:30","21:45","22:00","22:15","22:30"];
    h+='<div style="display:flex;gap:4px;margin-bottom:10px;overflow-x:auto;padding-bottom:4px">';
    slots.forEach(function(s){var label=s==='all'?'Tous':s;var active=fpSlot===s;var cnt=0;if(s!=='all'){var sh=parseInt(s.split(':')[0]);var sm=parseInt(s.split(':')[1]);bookings.forEach(function(b){if(!(b.date||'').startsWith(selectedDate))return;var bt=b.booking_time||b.time||'';if(!bt||!b.table)return;var bh=parseInt(bt.split(':')[0])||0;var bm=parseInt(bt.split(':')[1])||0;if(Math.abs((bh*60+bm)-(sh*60+sm))<=15)cnt++})}h+='<button style="padding:6px 12px;border-radius:20px;border:1.5px solid '+(active?'var(--ac)':'var(--b)')+';background:'+(active?'var(--ac)':'var(--card)')+';color:'+(active?'#fff':'var(--ts)')+';font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f);white-space:nowrap" data-fpSlot="'+s+'">'+label+(s!=='all'&&cnt>0?'<span style="margin-left:4px;padding:1px 5px;border-radius:10px;background:'+(active?'#fff3':'var(--da)')+';color:#fff;font-size:9px;font-weight:800">'+cnt+'</span>':'')+'</button>'});
    h+='</div>';
  }

  // LAYOUT: canvas + sidebar (only in resa mode)
  if(fpMode==='resa'){
    h+='<div class="fp-layout">';
    h+='<div class="fp-main">';
  }

  h+='<div style="position:relative;height:440px;background:var(--bg);border-radius:12px;border:2px solid var(--b);overflow:hidden;touch-action:none;user-select:none" id="fpCanvas">';
  fpZones.forEach(function(z,i){var xMin=i===0?0:i===1?46:84;var xMax=i===0?46:i===1?84:100;h+='<div style="position:absolute;left:'+xMin+'%;top:0;width:'+(xMax-xMin)+'%;height:100%;pointer-events:none"><div style="position:absolute;top:10px;left:50%;transform:translateX(-50%);font-size:10px;color:var(--tm);font-weight:700;letter-spacing:.06em;white-space:nowrap">'+z.label.toUpperCase()+'</div>';if(i<fpZones.length-1)h+='<div style="position:absolute;right:0;top:0;bottom:0;width:1px;border-right:1px dashed var(--b)"></div>';h+='</div>'});
  h+='</div>';
  if(fpMode==='edit'){h+='<div id="fpEditor" style="display:none;margin-top:12px;padding:16px;background:var(--card);border:1px solid var(--b);border-radius:12px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><div style="font-size:15px;font-weight:700" id="fpEdTitle">Table</div><button style="padding:4px 10px;border-radius:6px;border:none;background:#EF444415;color:#EF4444;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpDel>Supprimer</button></div><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px"><div><div class="finp-label">Nom</div><input class="finp" id="fpEdName" style="margin-bottom:0" placeholder="T1"></div><div><div class="finp-label">Places</div><select class="finp" id="fpEdSeats" style="margin-bottom:0;cursor:pointer"><option>2</option><option>4</option><option>6</option><option>8</option><option>10</option><option>12</option></select></div><div><div class="finp-label">Forme</div><select class="finp" id="fpEdShape" style="margin-bottom:0;cursor:pointer"><option value="round">Ronde</option><option value="rect">Rectangle</option></select></div></div><div style="margin-top:10px"><div class="finp-label">Zone</div><div style="display:flex;gap:4px" id="fpEdZones"></div></div></div>'}

  if(fpMode==='resa'){
    // Close fp-main, open sidebar
    h+='<div id="fpResaPopup" style="display:none;margin-top:12px;padding:16px;background:var(--card);border:1px solid var(--b);border-radius:12px"><div id="fpResaContent"></div></div>';
    h+='</div>'; // close fp-main

    // Sidebar with calendar + reservation list
    h+='<div class="fp-sidebar">';
    h+=buildCalendar();
    h+='<div class="fp-sb-header"><div class="fp-sb-title">Réservations</div><div class="fp-sb-count">'+sidebarBookings.length+' '+(fpService==='midi'?'midi':'soir')+'</div></div>';
    h+='<div class="fp-sb-list" id="fpSbList">';
    if(sidebarBookings.length===0){
      h+='<div class="fp-sb-empty">Aucune réservation pour ce service</div>';
    } else {
      sidebarBookings.forEach(function(b,i){
        var srcCol=srcColors[b.source]||'#A8A29E';
        h+='<div class="fp-sb-item'+(fpSelected!==null&&floorplan[fpSelected]&&floorplan[fpSelected].id===b.table?' active':'')+'" data-fpSbResa="'+i+'" data-fpSbTable="'+(b.table||'')+'" data-fpSbId="'+b.id+'">';
        h+='<div style="display:flex;justify-content:space-between;align-items:flex-start">';
        h+='<div class="fp-sb-name"><span class="dot" style="background:'+srcCol+';display:inline-block;vertical-align:middle;margin-right:6px"></span>'+b.name+'</div>';
        if(b.table){h+='<span class="fp-sb-table">'+b.table+'</span>'}
        else{h+='<span class="fp-sb-no-table">Sans table</span>'}
        h+='</div>';
        h+='<div class="fp-sb-meta">';
        h+='<span>'+(b.booking_time||b.time||'—')+'</span>';
        h+='<span>'+b.covers+'p</span>';
        if(b.phone)h+='<span>'+b.phone+'</span>';
        h+='</div>';
        h+='</div>';
      });
    }
    h+='</div>';
    // Add new resa button at bottom of sidebar
    h+='<div style="padding:10px 16px;border-top:1px solid var(--bl)"><button class="ba" style="width:100%;padding:10px;font-size:12px" onclick="openResaModal()">+ Nouvelle réservation</button></div>';
    h+='</div>'; // close fp-sidebar
    h+='</div>'; // close fp-layout
  }

  h+='</div>';c.innerHTML=h;fpSelected=null;fpDrawTables();
  if(fpMode==='edit')fpInitDrag();else fpInitResaMode();
  fpInitSidebarClicks();
}

function fpDrawTables(){
  var el=document.getElementById('fpCanvas');
  if(!el)return;
  el.querySelectorAll('.ftbl').forEach(function(e){e.remove()});
  var zc={salle:'#6366F1',terrasse:'#10B981',bar:'#F59E0B'};
  floorplan.forEach(function(t,i){
    var d=document.createElement('div');
    d.className='ftbl';
    d.setAttribute('data-fpTbl',i);
    var w=t.shape==='round'?(t.seats<=2?44:t.seats<=4?52:60):(t.seats<=2?44:t.seats<=4?56:t.seats<=6?66:76);
    var h2=t.shape==='round'?w:(t.seats<=4?44:48);
    var co=zc[t.zone]||'#6366F1';
    var sel=fpSelected===i;
    var bk=t.booking_name;

    if(fpMode==='resa'){
      // Resa mode: green=free, red=occupied
      var bg=bk?'#EF444418':'#10B98112';
      var bc=bk?'#EF4444':'#10B981';
      var tc=bk?'#EF4444':co;
      d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'10px')+';border:2px solid '+(sel?co:bc)+';background:'+(sel?co+'25':bg)+';color:'+tc+';cursor:pointer;box-shadow:'+(sel?'0 4px 14px '+co+'40':'0 1px 3px rgba(0,0,0,.08)')+';z-index:'+(sel?10:1)+';transition:all .15s';
      d.innerHTML='<div style="font-size:11px;font-weight:800">'+t.id+'</div><div style="font-size:9px;font-weight:600;color:'+(bk?'#EF4444':'#10B981')+'">'+(bk?bk.split(' ')[0]:t.seats+'p')+'</div>';
    } else {
      // Edit mode: zone colors
      d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'10px')+';border:2px solid '+(sel?co:co+'50')+';background:'+(sel?co+'20':co+'08')+';color:'+co+';cursor:grab;box-shadow:'+(sel?'0 4px 14px '+co+'40':'none')+';z-index:'+(sel?10:1)+';transition:all .15s';
      d.innerHTML='<div style="font-size:11px;font-weight:800">'+t.id+'</div><div style="font-size:9px;color:var(--tm)">'+t.seats+'p</div>';
    }
    el.appendChild(d);
  });
}

// === EDIT MODE: drag & drop ===
function fpInitDrag(){
  var canvas=document.getElementById('fpCanvas');
  if(!canvas)return;
  function getPos(e){
    var r=canvas.getBoundingClientRect();
    var cx=e.clientX!==undefined?e.clientX:(e.touches?e.touches[0].clientX:0);
    var cy=e.clientY!==undefined?e.clientY:(e.touches?e.touches[0].clientY:0);
    return{x:Math.max(3,Math.min(97,(cx-r.left)/r.width*100)),y:Math.max(5,Math.min(95,(cy-r.top)/r.height*100))};
  }
  function detectZone(x){if(x<46)return 'salle';if(x<84)return 'terrasse';return 'bar'}
  canvas.addEventListener('mousedown',function(e){
    var tbl=e.target.closest('[data-fpTbl]');
    if(tbl){e.preventDefault();fpDragging=parseInt(tbl.getAttribute('data-fpTbl'));fpSelected=fpDragging;fpShowEditor();fpDrawTables()}
  });
  canvas.addEventListener('touchstart',function(e){
    var tbl=e.target.closest('[data-fpTbl]');
    if(tbl){e.preventDefault();fpDragging=parseInt(tbl.getAttribute('data-fpTbl'));fpSelected=fpDragging;fpShowEditor();fpDrawTables()}
  },{passive:false});
  function onMove(e){if(fpDragging===null)return;e.preventDefault();var p=getPos(e.touches?e.touches[0]:e);var t=floorplan[fpDragging];if(t){t.x=p.x;t.y=p.y;t.zone=detectZone(p.x);fpDrawTables()}}
  function onUp(){if(fpDragging!==null){fpShowEditor();fpDragging=null}}
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
  document.addEventListener('touchmove',onMove,{passive:false});
  document.addEventListener('touchend',onUp);
}

// === RESA MODE: click to book/view ===
function fpInitResaMode(){
  var canvas=document.getElementById('fpCanvas');
  if(!canvas)return;
  canvas.addEventListener('click',function(e){
    var tbl=e.target.closest('[data-fpTbl]');
    if(!tbl)return;
    var idx=parseInt(tbl.getAttribute('data-fpTbl'));
    var t=floorplan[idx];
    if(!t)return;
    fpSelected=idx;
    fpDrawTables();
    fpHighlightSidebarItem(t.id);
    if(t.booking_name){
      fpShowResaInfo(idx);
    } else {
      fpBookTable(idx);
    }
  });
}

function fpInitSidebarClicks(){
  var list=document.getElementById('fpSbList');
  if(!list)return;
  list.addEventListener('click',function(e){
    var item=e.target.closest('[data-fpSbTable]');
    if(!item)return;
    var tableId=item.getAttribute('data-fpSbTable');
    if(!tableId)return;
    // Find the table index
    var idx=-1;
    floorplan.forEach(function(t,i){if(t.id===tableId)idx=i});
    if(idx===-1)return;
    fpSelected=idx;
    fpDrawTables();
    // Highlight sidebar item
    list.querySelectorAll('.fp-sb-item').forEach(function(el){el.classList.remove('active')});
    item.classList.add('active');
    // Show resa info popup
    if(floorplan[idx].booking_name){
      fpShowResaInfo(idx);
    }
  });
}

function fpHighlightSidebarItem(tableId){
  var list=document.getElementById('fpSbList');
  if(!list)return;
  list.querySelectorAll('.fp-sb-item').forEach(function(el){
    el.classList.toggle('active',el.getAttribute('data-fpSbTable')===tableId);
  });
}

function fpShowResaInfo(idx){
  var t=floorplan[idx];
  var popup=document.getElementById('fpResaPopup');
  if(!popup)return;
  var bk=null;
  // Find booking matching this table AND current service
  bookings.forEach(function(b){
    if(b.table!==t.id)return;
    var bt=b.booking_time||b.time||'';
    var bh=parseInt((bt||'0').split(':')[0])||0;
    if(fpService==='midi'&&bh>=17)return;
    if(fpService==='soir'&&bh<17)return;
    bk=b;
  });
  if(!bk){popup.style.display='none';return}
  popup.style.display='block';
  var h='<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px">';
  h+='<div><div style="font-size:16px;font-weight:700">'+bk.name+'</div>';
  if(bk.phone)h+='<div style="font-size:12px;color:var(--ts);margin-top:2px">'+bk.phone+'</div>';
  h+='</div>';
  h+='<div style="display:flex;gap:6px"><button style="padding:4px 10px;border-radius:6px;border:none;background:#EF444412;color:#EF4444;font-size:11px;font-weight:600;cursor:pointer;font-family:var(--f)" data-fpCancelResa="'+bk.id+'">Annuler</button><button style="padding:4px 10px;border-radius:6px;border:none;background:var(--bg);color:var(--ts);font-size:11px;font-weight:600;cursor:pointer;font-family:var(--f)" data-fpClosePopup>Fermer</button></div>';
  h+='</div>';
  // Inline edit: time + covers + table info
  h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px">';
  h+='<div><div class="finp-label">Heure</div><input type="time" class="finp" style="margin-bottom:0" id="fpResaTime" value="'+(bk.booking_time||bk.time||'20:00')+'"></div>';
  h+='<div><div class="finp-label">Couverts</div><input type="number" class="finp" style="margin-bottom:0" id="fpResaCovers" value="'+(bk.covers||2)+'" min="1" max="20"></div>';
  h+='<div><div class="finp-label">Table</div><div style="padding:11px 14px;background:var(--bg);border-radius:8px;font-size:13px;font-weight:600;color:var(--ac)">'+t.id+' ('+t.seats+'p, '+t.zone+')</div></div>';
  h+='</div>';
  h+='<button style="padding:7px 16px;border-radius:8px;border:none;background:var(--ac);color:#fff;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);margin-bottom:14px" data-fpSaveResa="'+bk.id+'">Enregistrer les modifications</button>';
  // Move table
  h+='<div class="finp-label" style="margin-bottom:6px">Deplacer vers une autre table</div>';
  h+='<div style="display:flex;gap:4px;flex-wrap:wrap">';
  floorplan.forEach(function(ot,oi){
    if(oi===idx)return;
    var otBk=null;bookings.forEach(function(b2){if(b2.table===ot.id){var bt2=b2.booking_time||b2.time||'';var bh2=parseInt((bt2||'0').split(':')[0])||0;if(fpService==='midi'&&bh2<17)otBk=b2;if(fpService==='soir'&&bh2>=17)otBk=b2}});
    var color=otBk?'#F59E0B':'#10B981';
    h+='<button style="padding:5px 10px;border-radius:6px;border:1.5px solid '+color+'40;background:'+color+'08;color:'+color+';font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSwap="'+bk.id+'-'+ot.id+'" title="'+(otBk?'Swap avec '+otBk.name:'Libre')+'">'+ot.id+' ('+ot.seats+'p)</button>';
  });
  h+='</div>';
  document.getElementById('fpResaContent').innerHTML=h;
}

function fpSaveResaInline(bookingId){
  var timeEl=document.getElementById('fpResaTime');
  var coversEl=document.getElementById('fpResaCovers');
  if(!timeEl||!coversEl)return;
  var data={booking_id:bookingId,time:timeEl.value,covers:parseInt(coversEl.value)||2};
  apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
    // Update locally
    bookings.forEach(function(b){if(b.id===bookingId){b.booking_time=data.time;b.time=data.time;b.covers=data.covers}});
    fpMergeForService();fpDrawTables();
    showToast('Reservation modifiee');
  });
}

function fpBookTable(idx){
  // Open reservation modal pre-filled with this table
  var t=floorplan[idx];
  resaSelTable=t.id;
  var el=document.getElementById('resaFirst');if(el)el.value='';
  el=document.getElementById('resaLast');if(el)el.value='';
  document.getElementById('resaCovers').value=String(Math.min(t.seats,4));
  document.getElementById('resaTime').value='20:00';
  document.getElementById('resaPhone').value='';
  document.getElementById('resaEmail').value='';
  document.getElementById('resaSource').value='phone';
  document.getElementById('resaTableBox').style.display='block';
  document.getElementById('resaTableVal').textContent=t.id+' ('+t.seats+'p, '+t.zone+')';
  document.getElementById('resaTableSel').style.display='none';
  document.getElementById('resaModal').classList.add('show');
}

function fpSwapTable(bookingId,newTableId){
  var oldBooking=null;
  bookings.forEach(function(b){if(b.id===bookingId)oldBooking=b});
  if(!oldBooking)return;
  var newTableBooking=null;
  bookings.forEach(function(b){if(b.table===newTableId)newTableBooking=b});
  var oldTable=oldBooking.table;
  if(newTableBooking){
    apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:newTableBooking.id,table:oldTable})});
    newTableBooking.table=oldTable;
  }
  apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bookingId,table:newTableId})});
  oldBooking.table=newTableId;
  mergeBookingsIntoFloor();
  fpDrawTables();
  var newIdx=floorplan.findIndex(function(t){return t.id===newTableId});
  fpSelected=newIdx;
  fpDrawTables();
  fpShowResaInfo(newIdx);
  showToast('Table changee'+(newTableBooking?' (swap avec '+newTableBooking.name+')':''));
}

function fpCancelResa(bookingId){
  if(!confirm('Annuler cette reservation ?'))return;
  apiFetch('/api/bookings/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bookingId})}).then(function(){
    // Remove locally
    for(var i=0;i<bookings.length;i++){if(bookings[i].id===bookingId){bookings.splice(i,1);break}}
    // Track cancellation
    cancelledCount=(cancelledCount||0)+1;
    mergeBookingsIntoFloor();
    fpSelected=null;
    fpDrawTables();
    var pp=document.getElementById('fpResaPopup');if(pp)pp.style.display='none';
    fpUpdateSummary();
    showToast('Reservation annulee');
  });
}

function fpShowEditor(){
  var ed=document.getElementById('fpEditor');
  if(!ed)return;
  if(fpSelected===null||!floorplan[fpSelected]){ed.style.display='none';return}
  var t=floorplan[fpSelected];
  ed.style.display='block';
  document.getElementById('fpEdTitle').textContent='Table '+t.id;
  document.getElementById('fpEdName').value=t.id;
  document.getElementById('fpEdSeats').value=String(t.seats);
  document.getElementById('fpEdShape').value=t.shape||'rect';
  var zhtml='';
  fpZones.forEach(function(z){
    zhtml+='<button style="flex:1;padding:7px;border-radius:7px;border:2px solid '+(t.zone===z.id?z.color:'var(--b)')+';background:'+(t.zone===z.id?z.color+'10':'var(--card)')+';color:'+(t.zone===z.id?z.color:'var(--ts)')+';font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSetZone="'+z.id+'">'+z.label+'</button>';
  });
  document.getElementById('fpEdZones').innerHTML=zhtml;
}

function fpAddTable(shape,seats){
  var id='T'+(floorplan.length+1);
  floorplan.push({id:id,seats:seats,shape:shape,zone:'salle',x:20+Math.random()*20,y:30+Math.random()*30});
  fpSelected=floorplan.length-1;
  fpDrawTables();fpShowEditor();fpUpdateSummary();
}
function fpDeleteSelected(){
  if(fpSelected===null)return;
  floorplan.splice(fpSelected,1);fpSelected=null;
  fpDrawTables();fpShowEditor();fpUpdateSummary();
  showToast('Table supprimee');
}
function fpUpdateSelected(key,val){
  if(fpSelected===null)return;
  var t=floorplan[fpSelected];
  if(key==='seats')t.seats=parseInt(val)||2;
  else if(key==='shape')t.shape=val;
  else if(key==='zone')t.zone=val;
  else if(key==='id')t.id=val;
  fpDrawTables();fpShowEditor();fpUpdateSummary();
}
function fpUpdateSummary(){
  var el=document.getElementById('fpSummary');
  if(!el)return;
  var booked=floorplan.filter(function(t){return t.booking_name}).length;
  var free=floorplan.length-booked;
  el.innerHTML=floorplan.length+' tables · '+floorplan.reduce(function(a,t){return a+(t.seats||0)},0)+' places · <span style="color:var(--ok)">'+free+' libres</span> · <span style="color:var(--da)">'+booked+' occupees</span>';
}
function fpSave(){
  apiFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tables:floorplan})}).then(function(){
    showToast('Plan de salle enregistre');drawFloorMini();
  });
}
function drawFloorFull(){fpDrawTables()}

// ===== BOOKINGS =====
var bookingsView='day'; // 'day' or 'week'

function renderBookings(c){
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
  var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
  var today=fmtDate(new Date());
  var isToday=selectedDate===today;
  var dateLabel=isToday?"auj.":parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});

  var h='<div style="display:flex;gap:8px;margin-bottom:14px">';
  h+='<button class="ba'+(bookingsView==='day'?' on':'')+'" style="padding:6px 16px;font-size:12px;'+(bookingsView==='day'?'background:var(--acg);color:white;border:none;':'')+'" data-bkView="day">Jour</button>';
  h+='<button class="ba'+(bookingsView==='week'?' on':'')+'" style="padding:6px 16px;font-size:12px;'+(bookingsView==='week'?'background:var(--acg);color:white;border:none;':'')+'" data-bkView="week">Semaine</button>';
  h+='</div>';

  if(bookingsView==='week'){
    h+=renderWeekView(srcColors, srcLabels);
  } else {
    h+='<div class="ov-layout" style="display:flex;gap:14px;align-items:flex-start">';
    h+='<div style="flex:1;min-width:0">';
    var filtered=getBookingsForDate(selectedDate);
    h+='<div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+filtered.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle réservation</button></div>';
    filtered.forEach(function(b){
      var globalIdx=bookings.indexOf(b);
      h+='<div class="rw" data-editResa="'+globalIdx+'" style="cursor:pointer"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+(b.phone?' · '+b.phone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div></div>';
    });
    if(!filtered.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucune réservation '+dateLabel+'</div>';
    h+='</div>';
    h+='</div>';
    h+='<div style="width:280px;flex-shrink:0">';
    h+=buildCalendar();
    h+='</div>';
    h+='</div>';
  }
  c.innerHTML=h;
}

function renderWeekView(srcColors, srcLabels){
  var sel=parseDateLocal(selectedDate);
  var dow=sel.getDay();
  var mondayOffset=dow===0?-6:1-dow;
  var monday=new Date(sel);
  monday.setDate(sel.getDate()+mondayOffset);

  var days=[];
  for(var i=0;i<7;i++){
    var d=new Date(monday);
    d.setDate(monday.getDate()+i);
    days.push(fmtDate(d));
  }

  var dayNames=['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'];
  var today=fmtDate(new Date());

  var h='<div class="card" style="padding:0;overflow:hidden">';

  // Header row
  h+='<div style="display:grid;grid-template-columns:repeat(7,1fr);border-bottom:2px solid var(--b)">';
  days.forEach(function(ds,i){
    var d=parseDateLocal(ds);
    var isToday=ds===today;
    var isSel=ds===selectedDate;
    var dayBookings=bookings.filter(function(b){return(b.date||'').startsWith(ds)});
    var totalCovers=0;dayBookings.forEach(function(b){totalCovers+=(b.covers||0)});
    var midiCount=dayBookings.filter(function(b){var t=b.booking_time||b.time||'';return t&&parseInt(t.split(':')[0])<15}).length;
    var soirCount=dayBookings.length-midiCount;

    h+='<div style="padding:12px 8px;text-align:center;cursor:pointer;border-right:'+(i<6?'1px solid var(--b)':'none')+';background:'+(isToday?'linear-gradient(135deg,#EBF4FF,#E6FAF8)':isSel?'var(--bg)':'white')+'" data-calDate="'+ds+'">';
    h+='<div style="font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase">'+dayNames[i]+'</div>';
    h+='<div style="font-size:20px;font-weight:800;color:'+(isToday?'var(--ac)':'var(--t)')+';margin:2px 0">'+d.getDate()+'</div>';
    h+='<div style="font-size:10px;color:var(--tm)">'+d.toLocaleDateString('fr-FR',{month:'short'})+'</div>';
    if(dayBookings.length){
      h+='<div style="margin-top:6px;padding:4px 6px;background:'+(isToday?'var(--ac)':'var(--ok)')+';color:white;border-radius:6px;font-size:11px;font-weight:700">'+dayBookings.length+' résa'+(dayBookings.length>1?'s':'')+'</div>';
      h+='<div style="font-size:10px;color:var(--ts);margin-top:2px">'+totalCovers+' couverts</div>';
    } else {
      h+='<div style="margin-top:6px;font-size:11px;color:var(--tm);font-style:italic">Aucune</div>';
    }
    h+='</div>';
  });
  h+='</div>';

  // Detail rows per day
  h+='<div style="max-height:500px;overflow-y:auto">';
  days.forEach(function(ds,i){
    var d=parseDateLocal(ds);
    var isToday=ds===today;
    var dayBookings=bookings.filter(function(b){return(b.date||'').startsWith(ds)});
    if(!dayBookings.length) return;

    // Sort by time
    dayBookings.sort(function(a,b){return(a.booking_time||a.time||'').localeCompare(b.booking_time||b.time||'')});

    var dayLabel=dayNames[i]+' '+d.getDate()+' '+d.toLocaleDateString('fr-FR',{month:'short'});
    h+='<div style="padding:10px 16px;background:'+(isToday?'#F0F9FF':'var(--bg)')+';font-size:12px;font-weight:700;color:'+(isToday?'var(--ac)':'var(--ts)')+';border-bottom:1px solid var(--b);display:flex;justify-content:space-between">';
    h+='<span>'+dayLabel+'</span>';
    var totalCov=0;dayBookings.forEach(function(b){totalCov+=(b.covers||0)});
    h+='<span>'+dayBookings.length+' résa'+(dayBookings.length>1?'s':'')+' · '+totalCov+' couverts</span>';
    h+='</div>';

    dayBookings.forEach(function(b){
      var globalIdx=bookings.indexOf(b);
      h+='<div class="rw" data-editResa="'+globalIdx+'" style="cursor:pointer;padding:8px 16px"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:13px;font-weight:600">'+b.name+'</div><div style="font-size:11px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+(b.zone?' · '+b.zone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="font-size:10px;color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok);font-size:10px">'+(b.table||'—')+'</span></div></div>';
    });
  });

  // Empty week message
  var weekTotal=0;days.forEach(function(ds){weekTotal+=bookings.filter(function(b){return(b.date||'').startsWith(ds)}).length});
  if(!weekTotal){
    h+='<div style="padding:40px;text-align:center;color:var(--tm)">Aucune réservation cette semaine</div>';
  }

  h+='</div></div>';

  // Week navigation
  h+='<div style="display:flex;justify-content:center;gap:12px;margin-top:12px">';
  h+='<button class="ba" style="font-size:12px;padding:6px 14px" data-weekShift="-1">&#8249; Semaine préc.</button>';
  h+='<button class="ba" style="font-size:12px;padding:6px 14px" data-weekToday>Cette semaine</button>';
  h+='<button class="ba" style="font-size:12px;padding:6px 14px" data-weekShift="1">Semaine suiv. &#8250;</button>';
  h+='</div>';

  return h;
}

var editResaIdx=null;
function openEditResa(idx){
  editResaIdx=idx;
  var b=bookings[idx];
  if(!b)return;
  document.getElementById('editResaName').value=b.name||'';
  document.getElementById('editResaCovers').value=b.covers||2;
  document.getElementById('editResaTime').value=(b.booking_time||b.time||'20:00');
  document.getElementById('editResaPhone').value=b.phone||'';
  document.getElementById('editResaTable').value=b.table||'';
  // Build table options
  var sel=document.getElementById('editResaTable');
  sel.innerHTML='<option value="">— Aucune —</option>';
  floorplan.forEach(function(t){
    var opt=document.createElement('option');
    opt.value=t.id;opt.textContent=t.id+' ('+t.seats+'p, '+t.zone+')';
    if(t.id===b.table)opt.selected=true;
    sel.appendChild(opt);
  });
  document.getElementById('editResaModal').classList.add('show');
}
function closeEditResa(){document.getElementById('editResaModal').classList.remove('show');editResaIdx=null}
function saveEditResa(){
  if(editResaIdx===null)return;
  var b=bookings[editResaIdx];
  var data={
    booking_id:b.id,
    name:document.getElementById('editResaName').value.trim(),
    covers:parseInt(document.getElementById('editResaCovers').value)||2,
    time:document.getElementById('editResaTime').value,
    phone:document.getElementById('editResaPhone').value.trim(),
    table:document.getElementById('editResaTable').value
  };
  apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
    fetchData();
    closeEditResa();
    showToast('Reservation modifiee');
  });
}
function deleteResa(){
  if(editResaIdx===null)return;
  var b=bookings[editResaIdx];
  if(!confirm('Supprimer la reservation de '+b.name+' ?'))return;
  apiFetch('/api/bookings/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:b.id})}).then(function(){
    fetchData();
    closeEditResa();
    showToast('Reservation supprimee');
  });
}

// ===== MENU =====
// ===== MENU EDITOR =====
var menuSections=[];

function loadMenu(){
  apiFetch('/api/menu').then(function(r){return r.json()}).then(function(d){
    menuSections=d.sections||[];
  }).catch(function(){menuSections=[]});
}

function renderMenu(c){
  var h='';
  h+='<div class="db"><div class="db-top"><div class="di">📢</div><div style="flex:1"><div class="dlb">Message du jour</div><div style="font-size:15px;font-weight:600;color:var(--t);margin-top:4px">'+(dailyMsg||'Aucun message')+'</div><div class="dme">Transmis automatiquement par l&#39;agent IA</div></div></div></div>';

  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><div><div class="card-t">La Carte</div><div class="card-s">'+(restaurantConfig.name||'Restaurant')+'</div></div><div style="display:flex;gap:8px"><label style="padding:6px 14px;border-radius:8px;border:none;background:var(--bg);color:var(--t);font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f);display:flex;align-items:center;gap:4px" id="menuScanBtn">📸 Scanner<input type="file" accept="image/*" style="display:none" id="menuScanInput" multiple></label><button class="ba" data-addSection>+ Section</button></div></div>';

  if(!menuSections.length){
    h+='<div style="text-align:center;padding:40px;color:var(--tm)"><div style="font-size:32px;margin-bottom:8px">📋</div><div style="font-size:14px">Aucune section. Cliquez "+ Section" pour commencer.</div></div>';
  }

  menuSections.forEach(function(sec,si){
    h+='<div class="menu-sec" style="margin-bottom:24px;border:1px solid var(--bl);border-radius:12px;overflow:hidden">';
    h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:var(--bg)">';
    h+='<div style="display:flex;align-items:center;gap:8px"><input style="font-size:14px;font-weight:700;color:var(--ac);border:none;background:transparent;outline:none;font-family:var(--f);text-transform:uppercase;letter-spacing:.04em;width:200px" value="'+sec.title+'" data-secTitle="'+si+'" placeholder="Nom de la section"></div>';
    h+='<div style="display:flex;gap:6px"><button class="ba" style="font-size:11px;padding:4px 10px" data-addItem="'+si+'">+ Plat</button><button style="font-size:11px;padding:4px 10px;border-radius:6px;border:1px solid var(--b);background:var(--card);color:var(--da);cursor:pointer;font-family:var(--f);font-weight:600" data-delSection="'+si+'">Supprimer</button></div>';
    h+='</div>';

    if(!sec.items||!sec.items.length){
      h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucun plat dans cette section</div>';
    } else {
      sec.items.forEach(function(item,ii){
        h+='<div style="display:flex;align-items:center;gap:10px;padding:10px 16px;border-top:1px solid var(--bl)">';
        h+='<div style="flex:1"><input style="font-size:14px;font-weight:600;color:var(--t);border:none;background:transparent;outline:none;font-family:var(--f);width:100%" value="'+(item.name||'')+'" data-itemName="'+si+'-'+ii+'" placeholder="Nom du plat">';
        h+='<input style="font-size:12px;color:var(--ts);border:none;background:transparent;outline:none;font-family:var(--f);width:100%;margin-top:2px" value="'+(item.description||'')+'" data-itemDesc="'+si+'-'+ii+'" placeholder="Description (optionnel)"></div>';
        h+='<input style="font-size:14px;font-weight:700;color:var(--ac);border:none;background:transparent;outline:none;font-family:var(--f);width:60px;text-align:right" value="'+(item.price||'')+'" data-itemPrice="'+si+'-'+ii+'" placeholder="Prix">';
        h+='<button style="background:none;border:none;cursor:pointer;font-size:14px;color:var(--tm);padding:4px" data-delItem="'+si+'-'+ii+'">✕</button>';
        h+='</div>';
      });
    }
    h+='</div>';
  });

  h+='</div>';

  if(menuSections.length){
    h+='<div style="display:flex;gap:8px"><button class="ba" style="padding:10px 20px" data-saveMenu>Enregistrer le menu</button></div>';
  }

  c.innerHTML=h;
  menuScanInit();
}

function menuCollectData(){
  menuSections.forEach(function(sec,si){
    var titleEl=document.querySelector('[data-secTitle="'+si+'"]');
    if(titleEl) sec.title=titleEl.value.trim();
    (sec.items||[]).forEach(function(item,ii){
      var nEl=document.querySelector('[data-itemName="'+si+'-'+ii+'"]');
      var dEl=document.querySelector('[data-itemDesc="'+si+'-'+ii+'"]');
      var pEl=document.querySelector('[data-itemPrice="'+si+'-'+ii+'"]');
      if(nEl) item.name=nEl.value.trim();
      if(dEl) item.description=dEl.value.trim();
      if(pEl) item.price=pEl.value.trim();
    });
  });
}

function menuAddSection(){
  menuCollectData();
  menuSections.push({title:'Nouvelle section',items:[]});
  renderMenu(document.getElementById('mainContent'));
}

function menuDelSection(si){
  menuCollectData();
  menuSections.splice(si,1);
  renderMenu(document.getElementById('mainContent'));
}

function menuAddItem(si){
  menuCollectData();
  if(!menuSections[si].items) menuSections[si].items=[];
  menuSections[si].items.push({name:'',description:'',price:''});
  renderMenu(document.getElementById('mainContent'));
}

function menuDelItem(si,ii){
  menuCollectData();
  menuSections[si].items.splice(ii,1);
  renderMenu(document.getElementById('mainContent'));
}

function menuSave(){
  menuCollectData();
  apiFetch('/api/menu',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sections:menuSections})}).then(function(){
    showToast('Menu enregistre');
  });
}

function menuScanInit(){
  var input=document.getElementById('menuScanInput');
  if(!input)return;
  input.addEventListener('change',function(){
    var files=input.files;
    if(!files.length)return;
    var scanBtn=document.getElementById('menuScanBtn');
    scanBtn.innerHTML='⏳ Analyse en cours...';
    scanBtn.style.opacity='0.6';
    var pending=files.length;
    var allSections=[];
    Array.from(files).forEach(function(file){
      var reader=new FileReader();
      reader.onload=function(e){
        var b64=e.target.result;
        var mt=file.type||'image/jpeg';
        apiFetch('/api/menu/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:b64,media_type:mt})}).then(function(r){return r.json()}).then(function(d){
          if(d.sections&&d.sections.length){
            d.sections.forEach(function(s){allSections.push(s)});
          }
          pending--;
          if(pending<=0){
            scanBtn.innerHTML='📸 Scanner';
            scanBtn.style.opacity='1';
            if(allSections.length){
              menuCollectData();
              allSections.forEach(function(s){menuSections.push(s)});
              renderMenu(document.getElementById('mainContent'));
              showToast(allSections.length+' sections ajoutees depuis image');
            }else{
              showToast('Aucun plat detecte');
            }
          }
        }).catch(function(){
          pending--;
          if(pending<=0){scanBtn.innerHTML='📸 Scanner';scanBtn.style.opacity='1';showToast('Erreur de scan')}
        });
      };
      reader.readAsDataURL(file);
    });
  });
}

// ===== CONVERSATIONS =====
function renderConversations(c){
  var entries=Object.entries(conversations);
  if(!entries.length){c.innerHTML='<div class="ph"><div class="phi">◈</div><div style="font-size:18px;font-weight:600;margin-bottom:4px">Conversations</div><div style="font-size:14px;color:var(--tm)">Aucune conversation pour le moment</div></div>';return}
  var h='<div class="card" style="display:grid;grid-template-columns:280px 1fr;height:500px"><div style="border-right:1px solid var(--bl);overflow-y:auto">';
  entries.forEach(function(e,i){
    var phone=e[0],data=e[1];
    var name=(contacts[phone]&&contacts[phone].name)||phone;
    var lastMsg=data.last_message||'';
    h+='<div class="conv-list-item'+(i===0?' selected':'')+'" data-conv="'+phone+'"><div style="font-size:13px;font-weight:600">'+name+'</div><div style="font-size:11px;color:var(--tm);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+lastMsg+'</div></div>';
  });
  h+='</div><div id="convMessages" style="padding:20px;overflow-y:auto;display:flex;flex-direction:column"></div></div>';
  c.innerHTML=h;
  if(entries.length)selectConv(entries[0][0]);
}
function selectConv(phone,el){
  if(el){document.querySelectorAll('.conv-list-item').forEach(function(e){e.classList.remove('selected')});el.classList.add('selected')}
  var data=conversations[phone];if(!data)return;
  var mc=document.getElementById('convMessages');
  var h='';
  (data.messages||[]).forEach(function(m){
    h+='<div class="bubble '+(m.role==='user'?'bubble-user':'bubble-bot')+'">'+esc(m.content||m.text||'')+'</div>';
  });
  mc.innerHTML=h;
  mc.scrollTop=mc.scrollHeight;
}

// ===== REVIEWS =====
function renderReviews(c){
  if(!reviewQueue.length){c.innerHTML='<div class="ph"><div class="phi">★</div><div style="font-size:18px;font-weight:600;margin-bottom:4px">Avis</div><div style="font-size:14px;color:var(--tm)">Aucun avis en attente</div></div>';return}
  var stats={total:reviewQueue.length,sent:0,responded:0,positive:0,negative:0,neutral:0};
  reviewQueue.forEach(function(r){if(r.sent)stats.sent++;if(r.responded){stats.responded++;if(r.sentiment==='POSITIVE')stats.positive++;else if(r.sentiment==='NEGATIVE')stats.negative++;else stats.neutral++;}});
  var h='';
  // Stats bar
  h+='<div class="sg" style="margin-bottom:16px">';
  h+='<div class="sc"><div class="sl">Total</div><div class="sv" style="color:var(--ac)">'+stats.total+'</div><div class="ss2">demandes</div></div>';
  h+='<div class="sc"><div class="sl">Envoyés</div><div class="sv" style="color:var(--ok)">'+stats.sent+'</div><div class="ss2">messages</div></div>';
  h+='<div class="sc"><div class="sl">Réponses</div><div class="sv" style="color:var(--bl2)">'+stats.responded+'</div><div class="ss2">clients</div></div>';
  h+='<div class="sc"><div class="sl">Positifs</div><div class="sv" style="color:#10B981">'+stats.positive+'</div><div class="ss2">😊</div></div>';
  h+='</div>';
  // Review list
  h+='<div class="card">';
  reviewQueue.slice().reverse().forEach(function(r){
    var sentimentColor=r.sentiment==='POSITIVE'?'#10B981':r.sentiment==='NEGATIVE'?'#EF4444':'#F59E0B';
    var sentimentLabel=r.sentiment==='POSITIVE'?'😊 Positif':r.sentiment==='NEGATIVE'?'😔 Négatif':r.sentiment?'😐 Neutre':'';
    var statusLabel=r.responded?sentimentLabel:r.sent?'Envoyé':'En attente';
    var statusBg=r.responded?(r.sentiment==='POSITIVE'?'#E6FAF8':r.sentiment==='NEGATIVE'?'#FEF2F2':'#FFFBEB'):r.sent?'var(--okb)':'var(--wab)';
    var statusCol=r.responded?sentimentColor:r.sent?'var(--ok)':'var(--wa)';
    h+='<div style="padding:16px;border-bottom:1px solid var(--bl)">';
    h+='<div style="display:flex;justify-content:space-between;align-items:center">';
    h+='<div><div style="font-size:14px;font-weight:600">'+(r.name||r.phone)+'</div>';
    h+='<div style="font-size:12px;color:var(--tm);margin-top:2px">'+(r.booking_time||'')+'</div></div>';
    h+='<span class="badge" style="background:'+statusBg+';color:'+statusCol+'">'+statusLabel+'</span></div>';
    if(r.response){
      h+='<div style="margin-top:10px;padding:10px 14px;background:var(--bg);border-radius:10px;border-left:3px solid '+sentimentColor+'">';
      h+='<div style="font-size:11px;font-weight:700;color:var(--tm);margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em">Réponse du client</div>';
      h+='<div style="font-size:13px;color:var(--t)">'+r.response+'</div>';
      h+='</div>';
    }
    h+='</div>';
  });
  h+='</div>';
  c.innerHTML=h;
}

// ===== CONTACTS =====
function renderContacts(c){
  var entries=Object.entries(contacts);
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
  var srcLabels={whatsapp:'WhatsApp',web:'Web',phone:'Tel','walk-in':'Walk-in',zenchef:'Zenchef'};
  var h='<div class="card"><div class="card-h"><div><div class="card-t">Tous les contacts</div><div class="card-s">'+entries.length+' clients</div></div></div>';
  entries.forEach(function(e){
    var phone=e[0],ct=e[1];
    var src=ct.source||'phone';
    h+='<div class="rw" data-contact="'+phone+'" style="cursor:pointer"><div class="rl"><div style="width:36px;height:36px;border-radius:50%;background:var(--al);display:flex;align-items:center;justify-content:center;color:var(--ac);font-size:13px;font-weight:700">'+(ct.name||'?').charAt(0).toUpperCase()+'</div><div><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm)">'+phone+(ct.email?' · '+ct.email:'')+'</div></div></div><div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:var(--ts)">'+(ct.visits||0)+' visite'+((ct.visits||0)>1?'s':'')+'</span><span class="src-badge" style="color:'+(srcColors[src]||'#A8A29E')+';background:'+(srcColors[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
  });
  if(!entries.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucun contact</div>';
  h+='</div>';
  c.innerHTML=h;
}

function openContactCard(phone){
  var ct=contacts[phone];
  if(!ct)return;
  var conv=conversations[phone];
  var msgs=(conv&&conv.messages)?conv.messages:(conv||[]);
  var resas=bookings.filter(function(b){return b.phone===phone});
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};

  var h='<div style="margin-bottom:16px"><button class="ba" data-nav="contacts" style="font-size:12px;padding:4px 12px">← Retour</button></div>';
  // Client header
  h+='<div class="card" style="padding:24px;margin-bottom:16px">';
  h+='<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px"><div style="width:56px;height:56px;border-radius:50%;background:var(--al);display:flex;align-items:center;justify-content:center;color:var(--ac);font-size:22px;font-weight:700">'+(ct.name||'?').charAt(0).toUpperCase()+'</div>';
  h+='<div><div style="font-size:20px;font-weight:700;color:var(--t)">'+(ct.name||phone)+'</div>';
  h+='<div style="font-size:13px;color:var(--tm)">'+phone+(ct.email?' · '+ct.email:'')+'</div></div></div>';
  // Stats row
  h+='<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">';
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--ac)">'+(ct.visits||0)+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Visites</div></div>';
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--ok)">'+resas.length+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Réservations</div></div>';
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--bl2)">'+msgs.length+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Messages</div></div>';
  h+='</div></div>';

  // Preferences, tags, notes — always show section with edit buttons
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><div class="card-t" style="margin:0">Profil client</div><div style="display:flex;gap:6px"><button class="ba" style="font-size:11px;padding:3px 10px" data-editprefs="'+phone+'">Modifier</button></div></div>';
  if(ct.preferences){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Préférences</span><div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">';
    ct.preferences.split(',').forEach(function(p){if(p.trim())h+='<span style="padding:3px 8px;border-radius:6px;background:var(--al);color:var(--ac);font-size:11px;font-weight:600">'+p.trim()+'</span>'});
    h+='</div></div>'}
  if(ct.tags&&ct.tags.length){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Tags</span><div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">';
    ct.tags.forEach(function(t){h+='<span style="padding:3px 8px;border-radius:6px;background:var(--okb);color:var(--ok);font-size:11px;font-weight:600">'+t+'</span>'});
    h+='</div></div>'}
  if(ct.notes){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Notes</span><div style="margin-top:4px;font-size:13px;color:var(--ts);background:var(--bg);padding:10px;border-radius:8px">'+ct.notes+'</div></div>'}
  if(!ct.notes){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Notes</span><div style="margin-top:4px;font-size:12px;color:var(--tm);font-style:italic">Aucune note. Cliquez Modifier pour ajouter.</div></div>'}
  if(ct.language){h+='<div><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Langue</span><span style="margin-left:8px;font-size:13px;color:var(--ts)">'+ct.language+'</span></div>'}
  h+='</div>';

  // Reservations with dates
  if(resas.length){
    h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="card-t" style="margin-bottom:12px">Historique réservations</div>';
    resas.forEach(function(b){
      var dateLabel=b.date||'';
      if(dateLabel){
        try{
          var parts=dateLabel.split('-');
          var d=new Date(parseInt(parts[0]),parseInt(parts[1])-1,parseInt(parts[2]));
          var days=['Dim','Lun','Mar','Mer','Jeu','Ven','Sam'];
          var months=['jan','fév','mar','avr','mai','jun','jul','aoû','sep','oct','nov','déc'];
          dateLabel=days[d.getDay()]+' '+d.getDate()+' '+months[d.getMonth()]+' '+d.getFullYear();
        }catch(e){}
      }
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bl)">';
      h+='<div><div style="font-weight:600;font-size:14px">'+b.covers+'p · '+(b.booking_time||b.time||'')+'</div>';
      h+='<div style="font-size:11px;color:var(--tm);margin-top:2px">'+(dateLabel||'Date non renseignée')+(b.source?' · '+b.source:'')+'</div></div>';
      h+='<div style="display:flex;gap:6px;align-items:center">';
      if(b.zone)h+='<span style="font-size:10px;color:var(--tm)">'+b.zone+'</span>';
      h+='<span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div>';
      h+='</div>';
    });
    h+='</div>';
  }

  // Conversation history
  if(msgs.length){
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:12px">Conversation</div>';
    msgs.slice(-15).forEach(function(m){
      var isBot=m.role==='assistant';
      h+='<div style="display:flex;flex-direction:column;align-items:'+(isBot?'flex-start':'flex-end')+';margin-bottom:8px">';
      h+='<div style="max-width:80%;padding:8px 12px;border-radius:12px;background:'+(isBot?'var(--bg)':'var(--ac)')+';color:'+(isBot?'var(--t)':'white')+';font-size:13px">'+esc((m.content||m.text||'').substring(0,200))+'</div>';
      h+='<div style="font-size:10px;color:var(--tm);margin-top:2px">'+(m.time||'')+'</div>';
      h+='</div>';
    });
    h+='</div>';
  }

  document.getElementById('mainContent').innerHTML=h;
}

function editContactPrefs(phone){
  var ct=contacts[phone];
  if(!ct)return;
  var h='<div style="margin-bottom:16px"><button class="ba" style="font-size:12px;padding:4px 12px" data-backcontact="'+phone+'">&#8592; Retour</button></div>';
  h+='<div class="card" style="padding:24px;margin-bottom:16px">';
  h+='<div class="card-t" style="margin-bottom:16px">Modifier le profil client</div>';
  h+='<div style="font-size:14px;font-weight:600;color:var(--t);margin-bottom:16px">'+(ct.name||phone)+'</div>';

  h+='<div style="margin-bottom:14px"><label style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;display:block;margin-bottom:4px">Préférences</label>';
  h+='<input id="editPrefs" type="text" value="'+(ct.preferences||'')+'" placeholder="ex: terrasse, viande, Chateau Miraval rosé, table 2" style="width:100%;padding:10px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);outline:none">';
  h+='<div style="font-size:10px;color:var(--tm);margin-top:3px">Séparez par des virgules</div></div>';

  h+='<div style="margin-bottom:14px"><label style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;display:block;margin-bottom:4px">Notes</label>';
  h+='<textarea id="editNotes" rows="4" placeholder="ex: Anniversaire en juin, aime les desserts, vient souvent le vendredi soir" style="width:100%;padding:10px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);outline:none;resize:vertical">'+(ct.notes||'')+'</textarea></div>';

  h+='<div style="margin-bottom:14px"><label style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;display:block;margin-bottom:4px">Tags</label>';
  h+='<input id="editTags" type="text" value="'+((ct.tags||[]).join(', '))+'" placeholder="ex: VIP, fidèle, allergique gluten" style="width:100%;padding:10px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);outline:none">';
  h+='<div style="font-size:10px;color:var(--tm);margin-top:3px">Séparez par des virgules</div></div>';

  h+='<div style="display:flex;gap:10px;margin-top:20px">';
  h+='<button class="ba" style="background:var(--acg);color:white;border:none;padding:10px 24px;font-weight:700" data-saveprefs="'+phone+'">Enregistrer</button>';
  h+='<button class="ba" style="padding:10px 24px" data-backcontact="'+phone+'">Annuler</button>';
  h+='</div></div>';

  document.getElementById('mainContent').innerHTML=h;
}

function saveContactPrefs(phone){
  var prefs=document.getElementById('editPrefs').value.trim();
  var notes=document.getElementById('editNotes').value.trim();
  var tags=document.getElementById('editTags').value.trim();
  var tagsArr=tags?tags.split(',').map(function(t){return t.trim()}).filter(function(t){return t}):[];

  // Update locally
  if(contacts[phone]){
    contacts[phone].preferences=prefs;
    contacts[phone].notes=notes;
    contacts[phone].tags=tagsArr;
  }

  // Save to server
  apiFetch('/api/contacts/note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone,note:notes})});
  apiFetch('/api/contacts/tag',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone,tags:tagsArr})});
  // Save preferences via a new endpoint or reuse note
  apiFetch('/api/contacts/preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone,preferences:prefs})});

  showToast('Profil mis à jour');
  openContactCard(phone);
}

// ===== CONFIG =====
function renderConfig(c){
  var h='';
  // Overview toggles
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="cfs"><div class="cft">Personnaliser la vue d&#39;ensemble</div><div class="cfsb">Cochez les blocs à afficher sur votre page d&#39;accueil</div>';
  var blocks=[{k:'daily',l:'📢 Message du jour'},{k:'stats',l:'📊 Statistiques'},{k:'floor',l:'⊞ Plan de salle'},{k:'bookings',l:'◉ Réservations & Conversations'},{k:'contacts',l:'◇ Contacts'}];
  blocks.forEach(function(b){
    h+='<div class="cfr"><div><div class="cfl">'+b.l+'</div></div><div class="tog'+(overviewBlocks[b.k]?' on':'')+'" data-blk="'+b.k+'" onclick="toggleOverviewBlock(this)"><div class="togd"></div></div></div>';
  });
  h+='</div></div>';

  // Automations section
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="cfs"><div class="cft">Automatisations</div><div class="cfsb">Configurez les messages automatiques envoyés à vos clients</div>';
  var remOn=(restaurantConfig._reminders_enabled!==false);
  h+='<div class="cfr"><div><div class="cfl">🔔 Rappels de réservation</div><div class="cfd">Déjeuner : rappel la veille à 19h · Dîner : rappel le jour même à 11h</div></div><div class="tog'+(remOn?' on':'')+'" onclick="toggleReminders(this)"><div class="togd"></div></div></div>';
  h+='</div></div>';
  
  // Restaurant config
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="cfs"><div class="cft">Informations du restaurant</div><div class="cfsb">Utilisées par l&#39;agent IA pour répondre aux clients</div>';
  var fields=[{k:'name',l:'Nom'},{k:'address',l:'Adresse'},{k:'phone',l:'Téléphone'},{k:'hours',l:'Horaires'},{k:'description',l:'Description'},{k:'tone',l:'Ton de l agent IA'}];
  fields.forEach(function(f){
    h+='<div class="cfr"><div><div class="cfl">'+f.l+'</div><div class="cfd">'+(restaurantConfig[f.k]||'Non configure')+'</div></div><span style="font-size:12px;color:var(--ac);font-weight:600;cursor:pointer" data-cfgkey="'+f.k+'" data-cfglabel="'+f.l+'">Modifier</span></div>';
  });
  h+='</div></div>';
  
  c.innerHTML=h;
}

function toggleOverviewBlock(el){
  el.classList.toggle('on');
  var k=el.getAttribute('data-blk');
  overviewBlocks[k]=el.classList.contains('on');
  showToast(el.classList.contains('on')?'Bloc active':'Bloc masque');
}

function toggleReminders(el){
  el.classList.toggle('on');
  var enabled=el.classList.contains('on');
  restaurantConfig._reminders_enabled=enabled;
  apiFetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reminders_enabled:enabled})});
  showToast(enabled?'Rappels activés':'Rappels désactivés');
}

function editConfigField(key,label){
  var val=prompt(label+' :',restaurantConfig[key]||'');
  if(val!==null){
    restaurantConfig[key]=val;
    apiFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(restaurantConfig)});
    renderConfig(document.getElementById('mainContent'));
    showToast(label+' mis a jour');
  }
}

// ===== ONBOARDING WIZARD =====
var obStep=0;
var obData={};
var OB_STEPS=[
  {id:'welcome',title:'Bienvenue sur GuestScale',desc:'Configurons votre restaurant en quelques etapes.',fields:[]},
  {id:'info',title:'Informations du restaurant',desc:'Ces infos seront utilisees par votre agent IA.',fields:[
    {k:'name',l:'Nom du restaurant',type:'input',placeholder:'Le Cosi Nice'},
    {k:'address',l:'Adresse',type:'input',placeholder:'12 rue de la Paix, 06000 Nice'},
    {k:'phone',l:'Telephone',type:'input',placeholder:'+33 4 93 XX XX XX'}
  ]},
  {id:'hours',title:'Horaires et description',desc:'Aidez votre agent IA a renseigner les clients.',fields:[
    {k:'hours',l:'Horaires d ouverture',type:'textarea',placeholder:'Lundi-Vendredi 12h-14h30, 19h-22h30\\nSamedi 19h-23h\\nFerme le dimanche'},
    {k:'description',l:'Description courte',type:'textarea',placeholder:'Restaurant italien au coeur du Vieux-Nice, cuisine traditionnelle et produits frais.'}
  ]},
  {id:'tone',title:'Personnalite de votre agent IA',desc:'Definissez comment votre assistant parle aux clients.',fields:[
    {k:'tone',l:'Ton de communication',type:'textarea',placeholder:'Chaleureux et professionnel, tutoie les clients reguliers, utilise des emojis avec parcimonie.'},
    {k:'languages',l:'Langues parlees',type:'input',placeholder:'francais, anglais, italien'}
  ]},
  {id:'done',title:'Votre restaurant est configure !',desc:'Vous pouvez maintenant recevoir des reservations et configurer votre plan de salle.',fields:[]}
];

function checkOnboarding(){
  // Check if onboarding was already completed (session)
  try{if(sessionStorage.getItem('ob_done')==='1')return}catch(e){}
  // Check if restaurant has minimal config (name + address set and not default)
  var name=restaurantConfig.name||'';
  var addr=restaurantConfig.address||'';
  if(name&&name!=='Le Cosi Nice'&&addr){
    try{sessionStorage.setItem('ob_done','1')}catch(e){}
    return;
  }
  // Also check server-side flag
  apiFetch('/api/settings').then(function(r){return r.json()}).then(function(d){
    if(d.onboarding_done==='1'){
      try{sessionStorage.setItem('ob_done','1')}catch(e){}
      return;
    }
    // Show onboarding
    obStep=0;
    obData={
      name:restaurantConfig.name||'',
      address:restaurantConfig.address||'',
      phone:restaurantConfig.phone||'',
      hours:restaurantConfig.hours||'',
      description:restaurantConfig.description||'',
      tone:restaurantConfig.tone||'',
      languages:restaurantConfig.languages||'francais, anglais, italien'
    };
    renderOnboarding();
  }).catch(function(){});
}

function renderOnboarding(){
  var el=document.getElementById('onboardingOverlay');
  var step=OB_STEPS[obStep];
  var total=OB_STEPS.length;

  var h='<div class="ob-overlay"><div class="ob-card">';
  // Logo
  h+='<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><div style="width:36px;height:36px;background:#0F1117;border-radius:9px;display:flex;align-items:center;justify-content:center"><svg viewBox="0 0 32 32" fill="none" style="width:22px;height:22px"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div style="font-size:18px;font-weight:800;color:var(--t);letter-spacing:-.02em">Guest<span style="color:#4ECDC4">Scale</span></div></div>';
  h+='<div style="font-size:12px;color:var(--tm);margin-bottom:24px">Configuration de votre restaurant</div>';

  // Progress steps
  h+='<div class="ob-steps">';
  for(var i=0;i<total;i++){
    h+='<div class="ob-step'+(i<obStep?' done':'')+(i===obStep?' active':'')+'"></div>';
  }
  h+='</div>';

  // Content
  h+='<div class="ob-title">'+step.title+'</div>';
  h+='<div class="ob-desc">'+step.desc+'</div>';

  if(step.id==='welcome'){
    h+='<div style="padding:20px;background:var(--bg);border-radius:12px;margin-bottom:10px">';
    h+='<div style="font-size:13px;color:var(--ts);line-height:1.6">';
    h+='&#10003; Agent IA WhatsApp pour vos clients<br>';
    h+='&#10003; Gestion des reservations et plan de salle<br>';
    h+='&#10003; CRM et suivi des contacts<br>';
    h+='&#10003; Statistiques et recap quotidien';
    h+='</div></div>';
  }

  if(step.id==='done'){
    h+='<div style="padding:24px;background:var(--bg);border-radius:12px;text-align:center;margin-bottom:10px">';
    h+='<div style="font-size:40px;margin-bottom:8px">&#127881;</div>';
    h+='<div style="font-size:14px;color:var(--t);font-weight:600">Prochaines etapes :</div>';
    h+='<div style="font-size:13px;color:var(--ts);margin-top:8px;line-height:1.6">';
    h+='1. Configurez votre plan de salle<br>';
    h+='2. Ajoutez votre menu<br>';
    h+='3. Testez l agent IA sur WhatsApp';
    h+='</div></div>';
  }

  // Fields
  step.fields.forEach(function(f){
    h+='<div class="ob-field"><div class="ob-label">'+f.l+'</div>';
    if(f.type==='textarea'){
      h+='<textarea class="ob-textarea" id="ob_'+f.k+'" placeholder="'+f.placeholder+'">'+(obData[f.k]||'')+'</textarea>';
    }else{
      h+='<input class="ob-input" id="ob_'+f.k+'" placeholder="'+f.placeholder+'" value="'+(obData[f.k]||'')+'">';
    }
    h+='</div>';
  });

  // Actions
  h+='<div class="ob-actions">';
  if(obStep>0&&step.id!=='done'){
    h+='<button class="ob-btn ob-btn-s" data-obPrev>Retour</button>';
  }
  if(step.id==='done'){
    h+='<button class="ob-btn ob-btn-p" data-obFinish>Commencer</button>';
  }else if(step.id==='welcome'){
    h+='<button class="ob-btn ob-btn-p" data-obNext>Configurer mon restaurant</button>';
  }else{
    h+='<button class="ob-btn ob-btn-p" data-obNext>Continuer</button>';
  }
  h+='</div>';

  if(step.id!=='done'&&step.id!=='welcome'){
    h+='<div class="ob-skip" data-obSkipAll>Passer la configuration</div>';
  }

  h+='</div></div>';
  el.innerHTML=h;
  el.style.display='block';
}

function obSaveStepData(){
  var step=OB_STEPS[obStep];
  step.fields.forEach(function(f){
    var el=document.getElementById('ob_'+f.k);
    if(el)obData[f.k]=el.value.trim();
  });
}

function obNext(){
  obSaveStepData();
  obStep++;
  if(obStep>=OB_STEPS.length)obStep=OB_STEPS.length-1;
  renderOnboarding();
}

function obPrev(){
  obSaveStepData();
  obStep--;
  if(obStep<0)obStep=0;
  renderOnboarding();
}

function obFinish(){
  // Save all config
  var cfg={};
  for(var k in obData){if(obData[k])cfg[k]=obData[k]}
  apiFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}).then(function(){
    // Update local config
    for(var k in cfg){restaurantConfig[k]=cfg[k]}
    // Mark onboarding done
    try{sessionStorage.setItem('ob_done','1')}catch(e){}
    apiFetch('/api/settings?set=onboarding_done&value=1');
    // Hide overlay
    document.getElementById('onboardingOverlay').style.display='none';
    fetchData();
    showToast('Restaurant configure avec succes !');
  });
}

function obSkipAll(){
  try{sessionStorage.setItem('ob_done','1')}catch(e){}
  apiFetch('/api/settings?set=onboarding_done&value=1');
  document.getElementById('onboardingOverlay').style.display='none';
}

// ===== STATS =====
function renderStats(c){
  c.innerHTML='<div style="text-align:center;padding:40px;color:var(--tm)">Chargement des statistiques...</div>';
  apiFetch('/api/stats/history').then(function(r){return r.json()}).then(function(data){
    var t=data.today||{};
    var history=data.history||[];
    var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Telephone','walk-in':'Walk-in',zenchef:'Zenchef'};
    var srcColors={whatsapp:'#25D366',web:'#2D7DD2',phone:'#9CA3AF','walk-in':'#6B7280',zenchef:'#F59E0B'};

    var h='';

    // Today's recap card
    h+='<div class="card" style="padding:20px;margin-bottom:14px;background:linear-gradient(135deg,#EBF4FF,#E6FAF8);border-color:#B8D8F8">';
    h+='<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px"><div><div style="font-size:16px;font-weight:800;color:var(--t)">Recap du jour</div><div style="font-size:12px;color:var(--ts);margin-top:2px">'+new Date().toLocaleDateString("fr-FR",{weekday:"long",day:"numeric",month:"long"})+'</div></div>';
    if(t.tomorrow_bookings>0){h+='<div style="padding:8px 14px;background:var(--card);border-radius:8px;border:1px solid var(--b)"><div style="font-size:18px;font-weight:800;color:var(--ac)">'+t.tomorrow_bookings+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">DEMAIN</div></div>'}
    h+='</div>';

    // KPI row inside recap
    h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--ok)">'+t.bookings+'</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Resas</div></div>';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--ac)">'+t.covers+'</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Couverts</div></div>';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--bl2)">'+t.occ_rate+'%</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Occupation</div></div>';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--wa)">'+t.messages+'</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Messages</div></div>';
    h+='</div>';

    // Extra info row
    h+='<div style="display:flex;gap:12px;margin-top:12px;font-size:12px;color:var(--ts)">';
    if(t.new_contacts)h+='<span>👤 '+t.new_contacts+' nouveaux contacts</span>';
    if(t.pending_reviews)h+='<span>⭐ '+t.pending_reviews+' avis en attente</span>';
    h+='<span>'+t.tables_occupied+'/'+t.tables_total+' tables occupees</span>';
    h+='</div>';
    h+='</div>';

    // History chart (bar chart with bookings per day)
    if(history.length>0){
      h+='<div class="card" style="padding:20px;margin-bottom:14px"><div class="card-t" style="margin-bottom:16px">Historique des reservations</div>';
      h+='<div style="display:flex;align-items:flex-end;gap:3px;height:140px;padding-bottom:24px;position:relative">';
      var maxB=Math.max.apply(null,history.map(function(d){return d.bookings||0}).concat([t.bookings||1]));
      // Show history + today
      var allDays=history.concat([{date:t.date,bookings:t.bookings,covers:t.covers}]);
      var last14=allDays.slice(-14);
      last14.forEach(function(d,i){
        var pct=maxB?Math.round((d.bookings||0)/maxB*100):0;
        var isToday=d.date===t.date;
        var dayLabel=d.date?d.date.slice(8,10):"";
        var dow="";try{var dt=new Date(d.date+"T12:00:00");dow=["D","L","M","M","J","V","S"][dt.getDay()]}catch(e){}
        h+='<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">';
        h+='<div style="font-size:9px;font-weight:700;color:'+(isToday?'var(--ac)':'var(--tm)')+'">'+((d.bookings||0)||"")+'</div>';
        h+='<div style="width:100%;height:'+Math.max(pct,4)+'%;background:'+(isToday?'var(--acg)':'var(--ac)30')+';border-radius:4px 4px 0 0;min-height:4px;transition:height .3s"></div>';
        h+='<div style="font-size:8px;color:'+(isToday?'var(--ac)':'var(--tm)')+';font-weight:'+(isToday?'800':'600')+'">'+dow+'</div>';
        h+='<div style="font-size:8px;color:'+(isToday?'var(--ac)':'var(--tm)')+';font-weight:'+(isToday?'800':'500')+'">'+dayLabel+'</div>';
        h+='</div>';
      });
      h+='</div></div>';
    }

    // Source breakdown + Communication (2 cols)
    h+='<div class="g2" style="margin-bottom:14px">';

    // Sources
    var sources=t.sources||{};
    var totalSrc=Object.values(sources).reduce(function(a,v){return a+v},0)||1;
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Réservations par canal</div>';
    var srcEntries=Object.entries(sources).sort(function(a,b){return b[1]-a[1]});
    if(srcEntries.length){
      srcEntries.forEach(function(e){
        var pct=Math.round(e[1]/totalSrc*100);
        var col=srcColors[e[0]]||"#9CA3AF";
        h+='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">';
        h+='<div style="width:80px;font-size:12px;font-weight:600;color:var(--ts)">'+(srcLabels[e[0]]||e[0])+'</div>';
        h+='<div style="flex:1;height:28px;background:var(--bg);border-radius:6px;overflow:hidden;position:relative"><div style="width:'+Math.max(pct,2)+'%;height:100%;background:'+col+';border-radius:6px;transition:width .3s"></div><span style="position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:11px;font-weight:700;color:var(--t)">'+e[1]+' ('+pct+'%)</span></div>';
        h+='</div>';
      });
    }else{
      h+='<div style="text-align:center;color:var(--tm);padding:20px;font-size:13px">Aucune donnee</div>';
    }
    h+='</div>';

    // Communication
    var convArr=Object.entries(conversations);
    var totalMsgs=0;convArr.forEach(function(e){var d=e[1];totalMsgs+=((d.messages&&d.messages.length)||d.count||0)});
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Communication</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--ac)">'+totalMsgs+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Messages total</div></div>';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--ok)">'+convArr.length+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Conversations</div></div>';
    h+='</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--wa)">'+Object.keys(contacts).length+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Contacts CRM</div></div>';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--bl2)">'+(convArr.length?Math.round(totalMsgs/convArr.length):0)+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Msg/client</div></div>';
    h+='</div>';
    h+='</div>';

    h+='</div>';

    c.innerHTML=h;
  }).catch(function(err){
    console.error('Stats error:',err);
    c.innerHTML='<div style="text-align:center;padding:40px;color:var(--tm)">Erreur de chargement des statistiques</div>';
  });
}

// ===== RESERVATION MODAL =====
function openResaModal(){
  resaSelTable=null;
  ['resaFirst','resaLast','resaPhone','resaEmail'].forEach(function(id){document.getElementById(id).value=''});
  document.getElementById('resaCovers').value='2';
  document.getElementById('resaTime').value='20:00';
  document.getElementById('resaSource').value='phone';
  document.getElementById('resaTableBox').style.display='none';
  document.getElementById('resaTableSel').style.display='none';
  // Show selected date in modal
  var today=fmtDate(new Date());
  var dl=document.getElementById('resaDateLabel');
  if(dl){
    if(selectedDate===today)dl.textContent='';
    else dl.textContent='— '+parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});
  }
  document.getElementById('resaModal').classList.add('show');
  resaAutoAssign();
}
function closeResaModal(){document.getElementById('resaModal').classList.remove('show')}

function resaAutoAssign(){
  var covers=parseInt(document.getElementById('resaCovers').value)||2;
  var time=document.getElementById('resaTime').value||'20:00';
  var best=null;
  // Only block tables that are booked at the SAME time (within 2h window)
  var th=parseInt(time.split(':')[0]);
  var tm=parseInt(time.split(':')[1]);
  var tMin=th*60+tm;
  var bookedTables=[];
  bookings.forEach(function(b){
    if(!(b.date||'').startsWith(selectedDate))return;
    if(!b.table)return;
    var bt=(b.booking_time||b.time||'');
    if(!bt)return;
    var bh=parseInt(bt.split(':')[0])||0;
    var bm=parseInt(bt.split(':')[1])||0;
    var bMin=bh*60+bm;
    if(Math.abs(bMin-tMin)<120)bookedTables.push(b.table);
  });
  floorplan.forEach(function(t){
    if(bookedTables.indexOf(t.id)===-1&&t.seats>=covers){if(!best||t.seats<best.seats)best=t}
  });
  if(best){
    resaSelTable=best.id;
    document.getElementById('resaTableBox').style.display='block';
    document.getElementById('resaTableVal').textContent=best.id+' ('+best.seats+'p, '+best.zone+')';
  }else{
    resaSelTable=null;
    document.getElementById('resaTableBox').style.display='block';
    document.getElementById('resaTableVal').textContent='Aucune table disponible';
  }
  document.getElementById('resaTableSel').style.display='none';
}

function showResaTableSelect(){
  var covers=parseInt(document.getElementById('resaCovers').value)||2;
  var time=document.getElementById('resaTime').value||'20:00';
  var th=parseInt(time.split(':')[0]);
  var tm=parseInt(time.split(':')[1]);
  var tMin=th*60+tm;
  var bookedTables=[];
  bookings.forEach(function(b){
    if(!(b.date||'').startsWith(selectedDate))return;
    if(!b.table)return;
    var bt=(b.booking_time||b.time||'');
    if(!bt)return;
    var bh=parseInt(bt.split(':')[0])||0;
    var bm=parseInt(bt.split(':')[1])||0;
    if(Math.abs(bh*60+bm-tMin)<120)bookedTables.push(b.table);
  });
  var h='';
  floorplan.forEach(function(t){
    var taken=bookedTables.indexOf(t.id)!==-1;
    h+='<div class="tsb'+(taken?' taken':t.id===resaSelTable?' sel':'')+'" '+(taken?'':'data-pick="'+t.id+'"')+'>'+t.id+'<br><span style="font-size:10px;color:var(--tm)">'+t.seats+'p</span></div>';
  });
  document.getElementById('resaTableSel').innerHTML=h;
  document.getElementById('resaTableSel').style.display='grid';
}

function pickResaTable(id){
  resaSelTable=id;
  var t=floorplan.find(function(x){return x.id===id});
  document.getElementById('resaTableVal').textContent=t.id+' ('+t.seats+'p, '+t.zone+')';
  document.getElementById('resaTableSel').style.display='none';
}

function submitResa(){
  var first=document.getElementById('resaFirst').value.trim();
  var last=document.getElementById('resaLast').value.trim();
  if(!first||!last){showToast('Veuillez remplir le nom et prenom');return}
  var data={
    name:first+' '+last,
    covers:parseInt(document.getElementById('resaCovers').value)||2,
    time:document.getElementById('resaTime').value,
    phone:document.getElementById('resaPhone').value.trim(),
    email:document.getElementById('resaEmail').value.trim(),
    source:document.getElementById('resaSource').value,
    table:resaSelTable||'',
    date:selectedDate
  };
  apiFetch('/api/bookings/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(d){
    fetchData();
    closeResaModal();
    showToast(data.name+' — '+(d.table?'Table '+d.table:'Sans table'));
  }).catch(function(){
    showToast('Erreur lors de la creation');
  });
}

// === EVENT DELEGATION ===
document.addEventListener('click',function(e){
  // Login buttons
  if(e.target.closest('[data-doLogin]')){doLogin();return}
  if(e.target.closest('[data-togglePwd]')){togglePwdVis();return}

  // Onboarding wizard
  if(e.target.closest('[data-obNext]')){obNext();return}
  if(e.target.closest('[data-obPrev]')){obPrev();return}
  if(e.target.closest('[data-obFinish]')){obFinish();return}
  if(e.target.closest('[data-obSkipAll]')){obSkipAll();return}


  // Bookings view toggle
  var bkt=e.target.closest("[data-bkView]");
  if(bkt){bookingsView=bkt.getAttribute("data-bkView");renderPage(currentPage);return}
  var wst=e.target.closest("[data-weekShift]");
  if(wst){var shift=parseInt(wst.getAttribute("data-weekShift"));var d=parseDateLocal(selectedDate);d.setDate(d.getDate()+shift*7);selectedDate=fmtDate(d);renderPage(currentPage);return}
  var wtt=e.target.closest("[data-weekToday]");
  if(wtt){selectedDate=fmtDate(new Date());renderPage(currentPage);return}
  // Calendar navigation
  var t=e.target.closest('[data-calDate]');
  if(t){selectedDate=t.getAttribute('data-calDate');mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calShift]');
  if(t){var shift=parseInt(t.getAttribute('data-calShift'));var d=parseDateLocal(selectedDate);d.setMonth(d.getMonth()+shift);selectedDate=fmtDate(d);mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calToday]');
  if(t){selectedDate=fmtDate(new Date());mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calTogglePicker]');
  if(t){var pk=document.getElementById('calPicker');if(pk&&pk.classList.contains('show')){pk.classList.remove('show');calPickerMode=null}else if(calPickerMode==='month'){showCalPicker('year')}else{showCalPicker('month')}return}
  t=e.target.closest('[data-calPickMonth]');
  if(t){var m=parseInt(t.getAttribute('data-calPickMonth'));var d=parseDateLocal(selectedDate);d.setMonth(m);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calPickYear]');
  if(t){var y=parseInt(t.getAttribute('data-calPickYear'));var d=parseDateLocal(selectedDate);d.setFullYear(y);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}

  t=e.target.closest('[data-pg]');
  if(t){switchPage(t.getAttribute('data-pg'),t);return}
  t=e.target.closest('[data-nav]');
  if(t){switchPage(t.getAttribute('data-nav'));return}
  t=e.target.closest('[data-conv]');
  if(t){selectConv(t.getAttribute('data-conv'),t);return}
  t=e.target.closest('[data-pick]');
  if(t&&!t.classList.contains('taken')){pickResaTable(t.getAttribute('data-pick'));return}
  t=e.target.closest('[data-cfgkey]');
  if(t){editConfigField(t.getAttribute('data-cfgkey'),t.getAttribute('data-cfglabel'));return}
  t=e.target.closest('[data-blk]');
  if(t&&t.classList.contains('tog')){toggleOverviewBlock(t);return}
  // Menu events
  if(e.target.closest('[data-addSection]')){menuAddSection();return}
  t=e.target.closest('[data-delSection]');
  if(t){menuDelSection(parseInt(t.getAttribute('data-delSection')));return}
  t=e.target.closest('[data-addItem]');
  if(t){menuAddItem(parseInt(t.getAttribute('data-addItem')));return}
  t=e.target.closest('[data-delItem]');
  if(t){var p=t.getAttribute('data-delItem').split('-');menuDelItem(parseInt(p[0]),parseInt(p[1]));return}
  if(e.target.closest('[data-saveMenu]')){menuSave();return}
  // Floor plan events
  if(e.target.closest('[data-fpSave]')){fpSave();return}
  if(e.target.closest('[data-fpDel]')){fpDeleteSelected();return}
  if(e.target.closest('[data-fpModeEdit]')){fpMode='edit';fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  if(e.target.closest('[data-fpModeResa]')){fpMode='resa';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSvc]');
  if(t){fpService=t.getAttribute('data-fpSvc');fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSlot]');
  if(t){fpSlot=t.getAttribute('data-fpSlot');fpMergeForService();fpDrawTables();
    // Re-render slot pills
    renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSaveResa]');
  if(t){fpSaveResaInline(t.getAttribute('data-fpSaveResa'));return}
  if(e.target.closest('[data-fpClosePopup]')){var pp=document.getElementById('fpResaPopup');if(pp)pp.style.display='none';fpSelected=null;fpDrawTables();return}
  t=e.target.closest('[data-fpCancelResa]');
  if(t){fpCancelResa(t.getAttribute('data-fpCancelResa'));return}
  t=e.target.closest('[data-fpSwap]');
  if(t){var parts=t.getAttribute('data-fpSwap').split('-');fpSwapTable(parts[0],parts[1]);return}
  t=e.target.closest('[data-fpAdd]');
  if(t){var ps=t.getAttribute('data-fpAdd').split('-');fpAddTable(ps[0],parseInt(ps[1]));return}
  t=e.target.closest('[data-fpSetZone]');
  if(t){fpUpdateSelected('zone',t.getAttribute('data-fpSetZone'));return}
  // Booking edit
  t=e.target.closest('[data-editResa]');
  if(t){openEditResa(parseInt(t.getAttribute('data-editResa')));return}
  // Contact click
  t=e.target.closest('[data-contact]');
  if(t){openContactCard(t.getAttribute('data-contact'));return}
  // Contact edit prefs
  t=e.target.closest('[data-editprefs]');
  if(t){editContactPrefs(t.getAttribute('data-editprefs'));return}
  // Save prefs
  t=e.target.closest('[data-saveprefs]');
  if(t){saveContactPrefs(t.getAttribute('data-saveprefs'));return}
  // Back to contact
  t=e.target.closest('[data-backcontact]');
  if(t){openContactCard(t.getAttribute('data-backcontact'));return}
});
function submitResa(){
  var first=document.getElementById('resaFirst').value.trim();
  var last=document.getElementById('resaLast').value.trim();
  if(!first||!last){showToast('Veuillez remplir le nom et prenom');return}
  var data={
    name:first+' '+last,
    covers:parseInt(document.getElementById('resaCovers').value)||2,
    time:document.getElementById('resaTime').value,
    phone:document.getElementById('resaPhone').value.trim(),
    email:document.getElementById('resaEmail').value.trim(),
    source:document.getElementById('resaSource').value,
    table:resaSelTable||'',
    date:selectedDate
  };
  apiFetch('/api/bookings/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(d){
    fetchData();
    closeResaModal();
    showToast(data.name+' — '+(d.table?'Table '+d.table:'Sans table'));
  }).catch(function(){
    showToast('Erreur lors de la creation');
  });
}

// === EVENT DELEGATION ===
document.addEventListener('click',function(e){
  // Login buttons
  if(e.target.closest('[data-doLogin]')){doLogin();return}
  if(e.target.closest('[data-togglePwd]')){togglePwdVis();return}

  // Onboarding wizard
  if(e.target.closest('[data-obNext]')){obNext();return}
  if(e.target.closest('[data-obPrev]')){obPrev();return}
  if(e.target.closest('[data-obFinish]')){obFinish();return}
  if(e.target.closest('[data-obSkipAll]')){obSkipAll();return}


  // Bookings view toggle
  var bkt2=e.target.closest("[data-bkView]");
  if(bkt2){bookingsView=bkt2.getAttribute("data-bkView");renderPage(currentPage);return}
  var wst2=e.target.closest("[data-weekShift]");
  if(wst2){var shift=parseInt(wst2.getAttribute("data-weekShift"));var d=parseDateLocal(selectedDate);d.setDate(d.getDate()+shift*7);selectedDate=fmtDate(d);renderPage(currentPage);return}
  var wtt2=e.target.closest("[data-weekToday]");
  if(wtt2){selectedDate=fmtDate(new Date());renderPage(currentPage);return}
  // Calendar navigation
  var t=e.target.closest('[data-calDate]');
  if(t){selectedDate=t.getAttribute('data-calDate');mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calShift]');
  if(t){var shift=parseInt(t.getAttribute('data-calShift'));var d=parseDateLocal(selectedDate);d.setMonth(d.getMonth()+shift);selectedDate=fmtDate(d);mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calToday]');
  if(t){selectedDate=fmtDate(new Date());mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calTogglePicker]');
  if(t){var pk=document.getElementById('calPicker');if(pk&&pk.classList.contains('show')){pk.classList.remove('show');calPickerMode=null}else if(calPickerMode==='month'){showCalPicker('year')}else{showCalPicker('month')}return}
  t=e.target.closest('[data-calPickMonth]');
  if(t){var m=parseInt(t.getAttribute('data-calPickMonth'));var d=parseDateLocal(selectedDate);d.setMonth(m);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calPickYear]');
  if(t){var y=parseInt(t.getAttribute('data-calPickYear'));var d=parseDateLocal(selectedDate);d.setFullYear(y);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}

  t=e.target.closest('[data-pg]');
  if(t){switchPage(t.getAttribute('data-pg'),t);return}
  t=e.target.closest('[data-nav]');
  if(t){switchPage(t.getAttribute('data-nav'));return}
  t=e.target.closest('[data-conv]');
  if(t){selectConv(t.getAttribute('data-conv'),t);return}
  t=e.target.closest('[data-pick]');
  if(t&&!t.classList.contains('taken')){pickResaTable(t.getAttribute('data-pick'));return}
  t=e.target.closest('[data-cfgkey]');
  if(t){editConfigField(t.getAttribute('data-cfgkey'),t.getAttribute('data-cfglabel'));return}
  t=e.target.closest('[data-blk]');
  if(t&&t.classList.contains('tog')){toggleOverviewBlock(t);return}
  // Menu events
  if(e.target.closest('[data-addSection]')){menuAddSection();return}
  t=e.target.closest('[data-delSection]');
  if(t){menuDelSection(parseInt(t.getAttribute('data-delSection')));return}
  t=e.target.closest('[data-addItem]');
  if(t){menuAddItem(parseInt(t.getAttribute('data-addItem')));return}
  t=e.target.closest('[data-delItem]');
  if(t){var p=t.getAttribute('data-delItem').split('-');menuDelItem(parseInt(p[0]),parseInt(p[1]));return}
  if(e.target.closest('[data-saveMenu]')){menuSave();return}
  // Floor plan events
  if(e.target.closest('[data-fpSave]')){fpSave();return}
  if(e.target.closest('[data-fpDel]')){fpDeleteSelected();return}
  if(e.target.closest('[data-fpModeEdit]')){fpMode='edit';fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  if(e.target.closest('[data-fpModeResa]')){fpMode='resa';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSvc]');
  if(t){fpService=t.getAttribute('data-fpSvc');fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSlot]');
  if(t){fpSlot=t.getAttribute('data-fpSlot');fpMergeForService();fpDrawTables();
    // Re-render slot pills
    renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSaveResa]');
  if(t){fpSaveResaInline(t.getAttribute('data-fpSaveResa'));return}
  if(e.target.closest('[data-fpClosePopup]')){var pp=document.getElementById('fpResaPopup');if(pp)pp.style.display='none';fpSelected=null;fpDrawTables();return}
  t=e.target.closest('[data-fpCancelResa]');
  if(t){fpCancelResa(t.getAttribute('data-fpCancelResa'));return}
  t=e.target.closest('[data-fpSwap]');
  if(t){var parts=t.getAttribute('data-fpSwap').split('-');fpSwapTable(parts[0],parts[1]);return}
  t=e.target.closest('[data-fpAdd]');
  if(t){var ps=t.getAttribute('data-fpAdd').split('-');fpAddTable(ps[0],parseInt(ps[1]));return}
  t=e.target.closest('[data-fpSetZone]');
  if(t){fpUpdateSelected('zone',t.getAttribute('data-fpSetZone'));return}
  // Booking edit
  t=e.target.closest('[data-editResa]');
  if(t){openEditResa(parseInt(t.getAttribute('data-editResa')));return}
  // Contact click
  t=e.target.closest('[data-contact]');
  if(t){openContactCard(t.getAttribute('data-contact'));return}
  // Contact edit prefs
  t=e.target.closest('[data-editprefs]');
  if(t){editContactPrefs(t.getAttribute('data-editprefs'));return}
  // Save prefs
  t=e.target.closest('[data-saveprefs]');
  if(t){saveContactPrefs(t.getAttribute('data-saveprefs'));return}
  // Back to contact
  t=e.target.closest('[data-backcontact]');
  if(t){openContactCard(t.getAttribute('data-backcontact'));return}
  // Waitlist buttons
  t=e.target.closest('[data-wlNotify]');
  if(t){var p=t.getAttribute('data-wlNotify').split('|');notifyWaitlist(p[0],p[1],p[2],p[3]);return}
  t=e.target.closest('[data-wlRemove]');
  if(t){removeWaitlist(t.getAttribute('data-wlRemove'));return}
});

// Login Enter key
document.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&e.target&&e.target.id==='loginPwd'){doLogin()}
});
// Floor editor input listeners
document.addEventListener('change',function(e){
  if(e.target.id==='fpEdName')fpUpdateSelected('id',e.target.value);
  if(e.target.id==='fpEdSeats')fpUpdateSelected('seats',e.target.value);
  if(e.target.id==='fpEdShape')fpUpdateSelected('shape',e.target.value);
});

// === HELP ASSISTANT ===
var helpOpen=false;
var helpGreeted=false;

function toggleHelp(){
  helpOpen=!helpOpen;
  document.getElementById('helpPanel').classList.toggle('show',helpOpen);
  document.getElementById('helpBtn').classList.toggle('open',helpOpen);
  document.getElementById('helpBtn').textContent=helpOpen?'+':'?';
  if(!helpGreeted){
    helpGreeted=true;
    setTimeout(function(){helpAddBot("Bonjour ! Je suis l&#39;assistant GuestScale. Comment puis-je vous aider ?")},400);
  }
}

function helpAddBot(text){
  var d=document.createElement('div');d.className='help-msg bot';
  d.innerHTML=text;document.getElementById('helpMsgs').appendChild(d);
  document.getElementById('helpMsgs').scrollTop=99999;
}
function helpAddUser(text){
  var d=document.createElement('div');d.className='help-msg user';
  d.textContent=text;document.getElementById('helpMsgs').appendChild(d);
  document.getElementById('helpMsgs').scrollTop=99999;
}

function helpMatch(text){
  var t=text.toLowerCase();
  if(t.match(/table|plan.*salle|ajouter.*table/))return "Pour g\u00e9rer vos tables, allez dans <b>Plan de salle</b> (menu gauche). Cliquez <b>Modifier plan</b> puis <b>+ Ajouter</b> pour cr\u00e9er une table. Vous pouvez d\u00e9finir la zone (salle, terrasse, bar), la capacit\u00e9 et la forme.";
  if(t.match(/horaire|heure|ouvert/))return "Allez dans <b>Configuration</b> (menu gauche). Modifiez le champ <b>Horaires</b>. L'agent IA utilisera ces horaires pour informer les clients.";
  if(t.match(/stat|statistiq|chiffre/))return "Cliquez sur <b>Statistiques</b> dans le menu gauche. Vous verrez l'historique jour par jour : r\u00e9servations, couverts, messages, langues des clients.";
  if(t.match(/menu|carte|plat|scan/))return "Allez dans <b>Menu</b> (menu gauche). Vous pouvez ajouter des sections et des plats manuellement, ou cliquer <b>Scanner</b> pour photographier votre carte et l'importer automatiquement.";
  if(t.match(/reserv|resa|book/))return "Les r\u00e9servations sont dans l'onglet <b>R\u00e9servations</b>. Vous avez une vue <b>Jour</b> et <b>Semaine</b>. Cliquez <b>+ Nouvelle</b> pour ajouter une r\u00e9sa manuellement. Les r\u00e9sas WhatsApp arrivent automatiquement.";
  if(t.match(/contact|crm|client|fiche/))return "L'onglet <b>Contacts</b> liste tous vos clients. Cliquez sur un contact pour voir sa fiche : visites, r\u00e9servations, pr\u00e9f\u00e9rences. Vous pouvez modifier les pr\u00e9f\u00e9rences et ajouter des notes manuellement.";
  if(t.match(/avis|review|google/))return "Les demandes d'avis Google sont envoy\u00e9es automatiquement 2h apr\u00e8s chaque repas. Configurez votre lien Google dans <b>Configuration</b>. Les r\u00e9ponses apparaissent dans l'onglet <b>Avis</b>.";
  if(t.match(/attente|waitlist|liste/))return "La <b>Liste d'attente</b> est dans le menu gauche. Quand l'IA d\u00e9tecte que c'est complet, elle propose automatiquement la liste d&#39;attente au client. Vous pouvez aussi ajouter manuellement des entr\u00e9es et notifier les clients quand une place se lib\u00e8re.";
  if(t.match(/whatsapp|message|conversation/))return "Les conversations WhatsApp sont dans l'onglet <b>Conversations</b>. Vous voyez tous les \u00e9changes entre l'IA et vos clients en temps r\u00e9el.";
  if(t.match(/config|param|person/))return "Tout se configure dans <b>Configuration</b> : nom, adresse, t\u00e9l\u00e9phone, horaires, description, ton de l'agent IA. L'IA utilise ces infos pour r\u00e9pondre aux clients.";
  if(t.match(/mot.*passe|password|connexion|login/))return "Pour changer votre mot de passe, allez dans <b>Mon compte</b> (en bas du menu gauche).";
  return "Je ne suis pas s\u00fbr de comprendre votre question. Essayez de me demander comment g\u00e9rer les <b>tables</b>, les <b>r\u00e9servations</b>, le <b>menu</b>, les <b>contacts</b>, ou les <b>param\u00e8tres</b>.";
}

function helpSend(text){
  helpAddUser(text);
  document.getElementById('helpQuick').style.display='none';
  setTimeout(function(){helpAddBot(helpMatch(text))},800);
}
function helpSendInput(){
  var inp=document.getElementById('helpInput');
  var text=inp.value.trim();if(!text)return;
  inp.value='';helpSend(text);
}
</script>
</body>
</html>
"""


# ==============================================================
# WEB CHAT SESSIONS
# ==============================================================

web_sessions = {}


# ==============================================================
# FASTAPI APP
# ==============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await load_all_restaurants()
    logger.info(f"GuestScale v5.0 started — {len(restaurants_cache)} restaurants loaded")

    import asyncio

    async def review_loop():
        while True:
            try:
                await process_review_queue()
            except Exception as e:
                logger.error(f"Review queue error: {e}")
            await asyncio.sleep(300)

    async def recap_loop():
        while True:
            try:
                now = datetime.utcnow()
                if now.hour == 21 and now.minute < 6:
                    await send_daily_recap()
                    await asyncio.sleep(3600)
                else:
                    await asyncio.sleep(300)
            except Exception as e:
                logger.error(f"Recap loop error: {e}")
                await asyncio.sleep(300)

    async def slot_reset_loop():
        """Reset table slots at midnight for all restaurants."""
        while True:
            try:
                await asyncio.sleep(60)
                now = datetime.utcnow()
                if now.hour == 0 and now.minute < 2:
                    for rid in restaurants_cache:
                        init_daily_slots(rid)
                    logger.info("Daily slots reset for all restaurants")
                    await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Slot reset error: {e}")
                await asyncio.sleep(300)

    async def waitlist_loop():
        """Check waitlist timeouts every 2 minutes."""
        while True:
            try:
                await process_waitlist_timeouts()
            except Exception as e:
                logger.error(f"Waitlist loop error: {e}")
            await asyncio.sleep(120)

    async def reminder_loop():
        """Send booking reminders — check every 5 minutes."""
        while True:
            try:
                await send_booking_reminders()
            except Exception as e:
                logger.error(f"Reminder loop error: {e}")
            await asyncio.sleep(300)

    task1 = asyncio.create_task(review_loop())
    task2 = asyncio.create_task(recap_loop())
    task3 = asyncio.create_task(slot_reset_loop())
    task4 = asyncio.create_task(waitlist_loop())
    task5 = asyncio.create_task(reminder_loop())
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()
    if db_pool:
        await db_pool.close()
    logger.info("GuestScale stopped")


SHOW_DOCS = os.getenv("SHOW_DOCS", "false").lower() == "true"

app = FastAPI(
    title="GuestScale API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if SHOW_DOCS else None,
    redoc_url="/redoc" if SHOW_DOCS else None,
    openapi_url="/openapi.json" if SHOW_DOCS else None,
)
app.add_middleware(CORSMiddleware, allow_origins=["https://app.guestscale.com", "https://guestscale.com", "https://www.guestscale.com", "http://localhost:3000", "http://localhost:8000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)


# ==============================================================
# SECURITY MIDDLEWARE
# ==============================================================

ALLOWED_HOSTS = {"app.guestscale.com", "guestscale.com", "www.guestscale.com", "localhost", "127.0.0.1"}

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Block direct Railway URL access (except health checks and webhooks)
    host = request.headers.get("host", "").split(":")[0]
    path = request.url.path
    if host not in ALLOWED_HOSTS and not path.startswith("/webhook") and path != "/health":
        if "railway.app" in host:
            return Response(status_code=404, content="Not found")

    response = await call_next(request)

    # Security headers
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    # Prevent caching of sensitive API responses
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, private, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://api.anthropic.com https://graph.facebook.com https://api.brevo.com; "
        "frame-ancestors 'none'"
    )
    return response


# ==============================================================
# EXCEPTION HANDLERS — prevent 500 crashes on malformed input
# ==============================================================

from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail or "Erreur"})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Catch JSON decode errors, validation errors, and anything else
    error_type = type(exc).__name__
    if "JSON" in error_type or "Decode" in error_type or "Value" in str(exc)[:50]:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    logger.error(f"Unhandled error on {request.url.path}: {error_type}: {exc}")
    return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


# Helper: safely parse JSON body with validation
async def safe_json(request: Request) -> dict | None:
    """Parse JSON body safely. Returns None if invalid or contains NoSQL operators."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return None
        # Reject NoSQL injection operators
        for key, val in data.items():
            if isinstance(key, str) and key.startswith("$"):
                return None
            if isinstance(val, dict):
                for k in val:
                    if isinstance(k, str) and k.startswith("$"):
                        return None
            if isinstance(val, str) and len(val) > 10000:
                return None  # Reject oversized values
        return data
    except Exception:
        return None


def is_valid_email(email: str) -> bool:
    """Strict email validation — rejects CRLF injection, null bytes, and invalid formats."""
    if not email or "\r" in email or "\n" in email or "\x00" in email or " " in email:
        return False
    return bool(re_mod.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email))


# ==============================================================
# BREVO EMAIL
# ==============================================================

async def send_brevo_welcome(email: str, first_name: str, restaurant_name: str, password: str = ""):
    """Send welcome email via Brevo API and add contact to trial list."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping welcome email")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Create/update contact and add to list
            await client.post(
                "https://api.brevo.com/v3/contacts",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "email": email,
                    "attributes": {"PRENOM": first_name, "NOM_RESTAURANT": restaurant_name},
                    "listIds": [BREVO_LIST_ID],
                    "updateEnabled": True,
                }
            )
            logger.info(f"Brevo: contact {email} added to list {BREVO_LIST_ID}")

            # 2. Send transactional welcome email with credentials
            pwd_display = password if password else "(celui choisi lors de votre inscription)"
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": email, "name": first_name}],
                    "subject": f"Bienvenue sur GuestScale, {first_name} !",
                    "htmlContent": f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<div style="text-align:center;margin-bottom:24px">
<svg viewBox="0 0 32 32" fill="none" width="40" height="40" xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
<h1 style="font-size:24px;font-weight:800;color:#111827;margin:12px 0 4px">Bienvenue sur GuestScale !</h1>
<p style="font-size:14px;color:#6B7280">Votre essai gratuit de 30 jours est active.</p>
</div>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:20px;margin-bottom:20px">
<p style="font-size:14px;color:#374151;margin:0 0 8px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="font-size:14px;color:#374151;margin:0 0 8px"><strong>Votre identifiant :</strong> {email}</p>
<p style="font-size:14px;color:#374151;margin:0"><strong>Votre mot de passe :</strong> {pwd_display}</p>
</div>
<div style="background:#FEF3C7;border:1px solid #FCD34D;border-radius:12px;padding:14px;margin-bottom:20px">
<p style="font-size:12px;color:#92400E;margin:0">Nous vous recommandons de conserver cet email. Vous pouvez modifier votre mot de passe depuis votre tableau de bord.</p>
</div>
<div style="text-align:center;margin-bottom:20px">
<a href="https://app.guestscale.com/login" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#2D7DD2,#4ECDC4);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px">Acceder a mon dashboard</a>
</div>
<p style="font-size:13px;color:#6B7280;text-align:center">Des questions ? Repondez directement a cet email.</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Restaurant AI Platform<br>Nice, France</p>
</div>""",
                }
            )
            if resp.status_code < 300:
                logger.info(f"Brevo: welcome email sent to {email}")
            else:
                logger.error(f"Brevo email error: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Brevo error: {e}")


async def send_admin_notification_email(user_email: str, first_name: str, last_name: str, restaurant_name: str, phone: str):
    """Notify contact@guestscale.com when a new restaurant registers."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping admin notification")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": "contact@guestscale.com", "name": "GuestScale Admin"}],
                    "subject": f"Nouvelle inscription : {restaurant_name}",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:24px">
<h2 style="color:#111827;margin:0 0 16px">Nouvelle inscription GuestScale</h2>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:16px;margin-bottom:16px">
<p style="margin:4px 0;font-size:14px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Nom :</strong> {first_name} {last_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Email :</strong> {user_email}</p>
<p style="margin:4px 0;font-size:14px"><strong>Tel :</strong> {phone or 'Non renseigne'}</p>
<p style="margin:4px 0;font-size:14px"><strong>Date :</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
</div>
<p style="font-size:13px;color:#6B7280">Total restaurants : {len(restaurants_cache)}</p>
</div>""",
                }
            )
            if resp.status_code < 300:
                logger.info(f"Admin notification sent for {restaurant_name}")
            else:
                logger.error(f"Admin notif error: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Admin notification error: {e}")


# ==============================================================
# AUTH ENDPOINTS
# ==============================================================

@app.post("/api/register")
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

    if not db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de données non disponible"})

    # Generate slug from restaurant name
    slug = re_mod.sub(r'[^a-z0-9]+', '', restaurant_name.lower().replace(" ", ""))[:30] or "restaurant"

    try:
        async with db_pool.acquire() as conn:
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
        restaurants_cache[rid_str] = {
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
        bookings[rid_str] = []
        floor_tables[rid_str] = []
        table_slots[rid_str] = {}
        review_queue[rid_str] = []
        contacts[rid_str] = {}
        stats[rid_str] = {"messages_today": 0, "bookings_today": 0, "languages": {}, "last_reset": today_paris().isoformat()}
        daily_stats_history[rid_str] = []
        waitlist[rid_str] = []
        data_versions[rid_str] = 0
        restaurant_status[rid_str] = {"status": "open", "message": "", "closed_dates": [], "full_dates": {}, "temp_message": "", "updated_at": datetime.utcnow().isoformat()}
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


@app.post("/api/login")
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
    if not db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de données non disponible"})
    try:
        async with db_pool.acquire() as conn:
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
password_reset_tokens = {}


@app.post("/api/forgot-password")
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
    if not db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de donnees non disponible"})

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, first_name FROM users WHERE email = $1", email)
            if not row:
                # Don't reveal if email exists or not
                return {"status": "ok", "message": "Si un compte existe avec cet email, un lien de reinitialisation a ete envoye."}

        # Generate a 6-digit code
        import random
        code = f"{random.randint(100000, 999999)}"
        password_reset_tokens[code] = {
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


@app.post("/api/reset-password")
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

    token_data = password_reset_tokens.get(code)
    if not token_data:
        return JSONResponse(status_code=401, content={"error": "Code invalide ou expire"})
    if time_mod.time() > token_data["expires"]:
        password_reset_tokens.pop(code, None)
        return JSONResponse(status_code=401, content={"error": "Code expire. Veuillez en demander un nouveau."})

    email = token_data["email"]
    password_reset_tokens.pop(code, None)

    if not db_pool:
        return JSONResponse(status_code=503, content={"error": "Base de donnees non disponible"})

    try:
        async with db_pool.acquire() as conn:
            pwd_hash = hash_password(new_password)
            result = await conn.execute("UPDATE users SET password_hash = $1 WHERE email = $2", pwd_hash, email)
            if "UPDATE 0" in result:
                return JSONResponse(status_code=404, content={"error": "Utilisateur non trouve"})
        logger.info(f"Password reset for {email}")
        return {"status": "ok", "message": "Mot de passe modifie avec succes. Vous pouvez vous connecter."}
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


@app.post("/api/logout")
async def api_logout():
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie("gs_token", path="/")
    return response


@app.get("/api/me")
async def api_me(request: Request):
    auth = get_auth(request)
    if not auth:
        return JSONResponse(status_code=401, content={"error": "Non authentifié"})
    rid = auth.get("restaurant_id", "")
    rest = restaurants_cache.get(rid, {})
    return {
        "user": {
            "email": auth.get("email", ""),
            "user_id": auth.get("user_id", ""),
            "restaurant_id": rid,
            "restaurant_name": rest.get("name", ""),
            "restaurant_status": rest.get("status", "trial"),
            "trial_ends_at": rest.get("trial_ends_at"),
            "role": auth.get("role", "owner"),
        }
    }


@app.post("/api/change-password")
async def api_change_password(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    data = await safe_json(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    current = data.get("current_password", "")
    new_pwd = data.get("new_password", "")
    if not current or not new_pwd:
        return JSONResponse(status_code=400, content={"error": "Champs requis"})
    if len(new_pwd) < 12:
        return JSONResponse(status_code=400, content={"error": "Minimum 12 caractères"})
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1::uuid", auth["user_id"])
            if not row or not verify_password(current, row["password_hash"]):
                return JSONResponse(status_code=401, content={"error": "Mot de passe actuel incorrect"})
            await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2::uuid", hash_password(new_pwd), auth["user_id"])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Change password error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


@app.delete("/api/account/delete")
async def api_delete_account(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    user_id = auth.get("user_id", "")
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_contacts WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_conversations WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_review_queue WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM users WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM restaurants WHERE id = $1::uuid", rid)
        # Clear in-memory
        for store in [bookings, contacts, conversations, floor_tables, table_slots, review_queue, stats, daily_stats_history, waitlist, data_versions, restaurant_status, table_statuses, table_groups, escalations, missed_call_tracker, campaigns_store]:
            store.pop(rid, None)
        restaurants_cache.pop(rid, None)
        logger.info(f"Account deleted: restaurant {rid[:8]}... by user {user_id}")
        response = JSONResponse(content={"status": "ok", "message": "Compte et donnees supprimes"})
        response.delete_cookie("gs_token", path="/")
        return response
    except Exception as e:
        logger.error(f"Account deletion error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur lors de la suppression"})


@app.delete("/api/contacts/{phone}/gdpr")
async def api_gdpr_delete_contact(request: Request, phone: str):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    # Remove contact
    rid_contacts = contacts.get(rid, {})
    rid_contacts.pop(phone, None)
    # Remove bookings
    rid_bookings = bookings.get(rid, [])
    bookings[rid] = [b for b in rid_bookings if b.get("phone") != phone]
    # Remove conversations
    conv_key = f"{rid}:{phone}"
    conversations.pop(conv_key, None)
    # Save to DB
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_contacts WHERE restaurant_id = $1::uuid AND data->>'phone' = $2", rid, phone)
                await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid AND data->>'phone' = $2", rid, phone)
                await conn.execute("DELETE FROM mt_conversations WHERE restaurant_id = $1::uuid AND phone = $2", rid, phone)
        except Exception as e:
            logger.error(f"GDPR deletion error: {e}")
    bump_version(rid)
    logger.info(f"GDPR deletion: contact {phone} from restaurant {rid[:8]}...")
    return {"status": "ok", "message": "Donnees du contact supprimees"}


# ==============================================================
# TWILIO — MISSED CALL DETECTION
# ==============================================================

async def send_whatsapp_template(phone_number_id: str, access_token: str, to: str, template_name: str, restaurant_name: str):
    """Send a WhatsApp template message (required for business-initiated conversations)."""
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "fr"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": restaurant_name}
                    ]
                }
            ]
        }
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                logger.info(f"WhatsApp template sent to {to} for {restaurant_name}")
                return True
            else:
                logger.error(f"WhatsApp template error: {resp.status_code} {resp.text}")
                # Fallback: try sending as regular text (works if client has messaged within 24h)
                fallback_text = f"Bonjour ! 👋 Vous avez essayé de joindre {restaurant_name} et nous n&#39;avons pas pu prendre votre appel. Je suis l&#39;assistant du restaurant, comment puis-je vous aider ? Réservation, menu, horaires... je suis là pour vous ! 😊"
                await send_whatsapp_message(phone_number_id, access_token, to, fallback_text)
                return True
        except Exception as e:
            logger.error(f"WhatsApp template send error: {e}")
            return False


async def handle_missed_call(caller_phone: str, restaurant_phone: str):
    """Handle a missed call: find restaurant and send WhatsApp to caller."""
    normalized_resto = normalize_phone(restaurant_phone)
    rid = phone_to_restaurant.get(normalized_resto)

    if not rid:
        # Try matching with all restaurant phone numbers
        for r_rid, rest in restaurants_cache.items():
            rest_phone = rest.get("settings", {}).get("phone", "") or ""
            if normalize_phone(rest_phone) == normalized_resto:
                rid = r_rid
                break

    if not rid:
        logger.warning(f"Missed call: no restaurant found for {restaurant_phone}")
        return

    rest = restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id") or not rest.get("whatsapp_access_token"):
        logger.warning(f"Missed call: restaurant {rid} not configured for WhatsApp")
        return

    caller_normalized = normalize_phone(caller_phone)

    # Anti-spam: max 1 per phone per day
    today = today_paris().isoformat()
    tracker = missed_call_tracker.setdefault(rid, {})
    if caller_normalized in tracker and tracker[caller_normalized].get("date") == today:
        logger.info(f"Missed call skipped (already handled today): {caller_phone}")
        return
    tracker[caller_normalized] = {"wa_sent_at": now_paris().isoformat(), "date": today}

    restaurant_name = rest.get("name", "notre restaurant")

    # Try template first, fallback to regular message
    success = await send_whatsapp_template(
        rest["whatsapp_phone_number_id"],
        rest["whatsapp_access_token"],
        caller_normalized,
        "missed_call_followup",
        restaurant_name
    )

    if success:
        # Track the missed call in the contact
        rid_contacts = contacts.setdefault(rid, {})
        if caller_normalized not in rid_contacts:
            rid_contacts[caller_normalized] = {
                "name": caller_phone,
                "phone": caller_normalized,
                "source": "missed_call",
                "visits": 0,
                "first_seen": datetime.utcnow().isoformat(),
                "tags": [],
                "preferences": "",
                "notes": "",
            }
        rid_contacts[caller_normalized].setdefault("tags", [])
        if "appel manque" not in rid_contacts[caller_normalized]["tags"]:
            rid_contacts[caller_normalized]["tags"].append("appel manque")
        await db_save_contact(rid, caller_normalized, rid_contacts[caller_normalized])

        # Save as conversation start
        save_message(rid, caller_normalized, "assistant",
            f"[Appel manqué] Message automatique envoyé suite à un appel sans réponse du restaurant {restaurant_name}.")

        bump_version(rid)
        logger.info(f"Missed call handled: {caller_phone} -> {restaurant_name}")
        await increment_message_count(rid, "missed_call")


@app.post("/twilio/voice")
async def twilio_voice_webhook(request: Request, background_tasks: BackgroundTasks):
    """Twilio webhook: receives forwarded calls when restaurant doesn't answer.
    The restaurant's phone forwards unanswered calls to the shared Twilio number.
    Twilio calls this endpoint. We detect the caller and the original restaurant number."""
    form = await request.form()
    caller = form.get("From", "")        # The client who called
    forwarded_from = form.get("ForwardedFrom", "")  # The restaurant number that forwarded
    called = form.get("Called", "")       # The Twilio number
    call_status = form.get("CallStatus", "")
    dial_status = form.get("DialCallStatus", "")

    logger.info(f"Twilio voice: caller={caller} forwarded_from={forwarded_from} called={called} status={call_status}")

    # Determine the restaurant phone (ForwardedFrom is the restaurant's number)
    restaurant_phone = forwarded_from or called

    if caller and restaurant_phone:
        background_tasks.add_task(handle_missed_call, caller, restaurant_phone)

    # Return TwiML: play a brief message then hang up
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="fr-FR" voice="alice">Bonjour, le restaurant ne peut pas prendre votre appel pour le moment. Vous allez recevoir un message WhatsApp pour vous aider. A bientot.</Say>
    <Hangup/>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/status")
async def twilio_status_callback(request: Request):
    """Twilio status callback for call completion tracking."""
    form = await request.form()
    logger.info(f"Twilio status: {dict(form)}")
    return {"status": "ok"}

@app.post("/twilio/confirm-gather")
async def twilio_confirm_gather(request: Request):
    """Handle DTMF response from reservation confirmation call."""
    form = await request.form()
    digits = form.get("Digits", "")
    caller = form.get("From", "")
    logger.info(f"Twilio DTMF: caller={caller} digits={digits}")

    caller_norm = normalize_phone(caller)

    # Find the restaurant and booking
    for rid, rid_bookings in bookings.items():
        for b in rid_bookings:
            if b.get("phone") == caller_norm and not b.get("reminder_confirmed"):
                if digits == "1":
                    b["reminder_confirmed"] = True
                    logger.info(f"Booking confirmed via DTMF: {b.get('name')} {b.get('date')}")
                    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="fr-FR" voice="Polly.Lea">Merci, votre réservation est confirmée. À bientôt !</Say></Response>'
                elif digits == "2":
                    b["reminder_confirmed"] = False
                    b["status"] = "cancelled"
                    if b.get("table") and b.get("booking_time"):
                        release_table(rid, b["booking_time"], b["table"])
                    logger.info(f"Booking cancelled via DTMF: {b.get('name')} {b.get('date')}")
                    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="fr-FR" voice="Polly.Lea">Votre reservation a ete annulee. Merci et a bientot.</Say></Response>'
                    bump_version(rid)
                else:
                    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="fr-FR" voice="Polly.Lea">Appuyez 1 pour confirmer ou 2 pour annuler.</Say><Gather numDigits="1" action="/twilio/confirm-gather"/></Response>'
                return Response(content=twiml, media_type="application/xml")

    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="fr-FR" voice="Polly.Lea">Merci de votre appel. A bientot.</Say></Response>'
    return Response(content=twiml, media_type="application/xml")


# ==============================================================
# AI PAUSE / ESCALATION / MISSED CALLS ENDPOINTS
# ==============================================================

@app.post("/api/toggle-ai")
async def api_toggle_ai(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    rest = restaurants_cache.get(rid)
    if rest:
        rest.setdefault("settings", {})["ai_enabled"] = data.get("enabled", True)
        await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "ok"}

@app.post("/api/pause-ai")
async def api_pause_ai(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    minutes = int(data.get("minutes", 60))
    rest = restaurants_cache.get(rid)
    if rest:
        rest["ai_paused_until"] = (now_paris() + timedelta(minutes=minutes)).isoformat()
    return {"status": "ok", "paused_until": rest.get("ai_paused_until") if rest else None}

@app.post("/api/conversation/pause")
async def api_pause_conversation(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone", "")
    paused = data.get("paused", True)
    minutes = int(data.get("minutes", 120))
    if paused:
        ai_paused_conversations.setdefault(rid, {})[phone] = (now_paris() + timedelta(minutes=minutes)).isoformat()
    else:
        ai_paused_conversations.get(rid, {}).pop(phone, None)
    return {"status": "ok"}

@app.post("/api/conversations/send")
async def api_send_manual_message(request: Request):
    """Send a manual WhatsApp message from the restaurateur to a client."""
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone", "")
    message = sanitize_input(data.get("message", ""), 2000)
    if not phone or not message:
        return JSONResponse(status_code=400, content={"error": "Telephone et message requis"})
    rest = restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id") or not rest.get("whatsapp_access_token"):
        return JSONResponse(status_code=400, content={"error": "WhatsApp non configure"})
    # Send via WhatsApp
    await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], phone, message)
    # Save in conversation history with human flag
    save_message(rid, phone, "assistant", message, sender_type="human")
    bump_version(rid)
    return {"status": "ok"}

@app.get("/api/escalations")
async def api_escalations(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"escalations": escalations.get(rid, [])}

@app.post("/api/escalations/resolve")
async def api_resolve_escalation(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone", "")
    for e in escalations.get(rid, []):
        if e["phone"] == phone and e["status"] == "open":
            e["status"] = "resolved"
            e["resolved_at"] = now_paris().isoformat()
    # Unpause conversation
    ai_paused_conversations.get(rid, {}).pop(phone, None)
    return {"status": "ok"}

@app.get("/api/missed-calls")
async def api_missed_calls(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"calls": [{"phone": p, **v} for p, v in missed_call_tracker.get(rid, {}).items()]}


# ==============================================================
# WEBHOOK (routes by phone_number_id)
# ==============================================================

@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe":
        # Check token against any restaurant's verify token
        for rid, rest in restaurants_cache.items():
            if rest.get("whatsapp_verify_token") == token:
                return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    parsed = parse_webhook(body)
    if not parsed:
        return {"status": "ignored"}
    phone_number_id = parsed["phone_number_id"]
    rid = pid_to_restaurant.get(phone_number_id)
    if not rid:
        logger.warning(f"No restaurant for phone_number_id: {phone_number_id}")
        return {"status": "unknown_restaurant"}
    rest = restaurants_cache.get(rid)
    if rest:
        background_tasks.add_task(mark_as_read, phone_number_id, rest["whatsapp_access_token"], parsed["message_id"])
    background_tasks.add_task(process_and_reply, rid, phone_number_id, parsed["from"], parsed["name"], parsed["text"])
    return {"status": "ok"}


# ==============================================================
# DASHBOARD
# ==============================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    # Don't serve admin HTML without at least a hint of authorization
    # The actual auth happens client-side with ADMIN_SECRET, but we can
    # prevent scanners from detecting this route by checking a query param
    if not request.query_params.get("k"):
        return Response(status_code=404, content="Not found")
    return HTMLResponse(ADMIN_DASHBOARD_HTML)


@app.get("/")
async def root():
    return RedirectResponse(url="/login")


DASHBOARD_DIR = Path(__file__).parent / "guestscale-dashboard" / "dist"

@app.get("/login", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/{slug}", response_class=HTMLResponse)
async def dashboard_page(request: Request, slug: str = ""):
    index_file = DASHBOARD_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file, media_type="text/html")
    return HTMLResponse(DASHBOARD_HTML)


# ==============================================================
# API ENDPOINTS (JWT-authenticated, multi-tenant)
# ==============================================================

@app.get("/api/version")
async def api_version(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"v": data_versions.get(rid, 0)}


@app.get("/api/dashboard")
async def api_dashboard_data(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    st = stats.get(rid, {})
    today_str = today_paris().isoformat()
    if st.get("last_reset") != today_str:
        st["messages_today"] = 0
        st["bookings_today"] = 0
        st["languages"] = {}
        st["last_reset"] = today_str
    status = restaurant_status.get(rid, {})
    recent = []
    for k, msgs in sorted(conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True)[:20]:
        if not k.startswith(rid) or not msgs:
            continue
        phone = k.split(":")[1] if ":" in k else k
        last = msgs[-1]
        recent.append({"phone": phone, "last_message": last["content"][:200], "time": last.get("timestamp", "")[:16].replace("T", " ")})
    return {"stats": st, "status": status, "conversations_count": sum(1 for k in conversations if k.startswith(rid)), "recent_conversations": recent}


@app.post("/api/status")
async def api_update_status(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = restaurant_status.get(rid, {})
    status["status"] = data.get("status", "open")
    status["updated_at"] = datetime.utcnow().isoformat()
    restaurant_status[rid] = status
    await db_save_restaurant_status(rid, status)
    return {"status": "updated"}


@app.post("/api/message")
async def api_update_message(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = restaurant_status.get(rid, {})
    status["temp_message"] = data.get("message", "")
    restaurant_status[rid] = status
    await db_save_restaurant_status(rid, status)
    return {"status": "updated"}


@app.get("/api/conversations")
async def api_list_conversations(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    result = []
    for k, msgs in sorted(conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True):
        if not k.startswith(rid + ":") or not msgs:
            continue
        phone = k.split(":")[1] if ":" in k else k
        result.append({
            "phone": phone,
            "messages": [{"role": m["role"], "content": m["content"], "time": m.get("timestamp", "")[:16].replace("T", " ")} for m in msgs],
            "last_message": msgs[-1]["content"][:200],
            "last_time": msgs[-1].get("timestamp", "")[:16].replace("T", " "),
            "count": len(msgs),
        })
    return {"conversations": result}


@app.get("/api/bookings")
async def api_list_bookings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"bookings": bookings.get(rid, [])[-100:]}


@app.get("/api/floorplan")
async def api_get_floorplan(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {
        "tables": floor_tables.get(rid, []),
        "slots": table_slots.get(rid, {}),
        "bookings": bookings.get(rid, [])[-100:],
        "slot_summary": get_slot_summary(rid),
        "statuses": table_statuses.get(rid, {}),
        "groups": table_groups.get(rid, []),
    }


@app.post("/api/floorplan/assign")
async def api_assign_table(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    booking_id = data.get("booking_id")
    table_id = data.get("table_id")
    slot_time = data.get("slot_time")
    if not all([booking_id, table_id, slot_time]):
        return {"error": "Missing fields"}
    for b in bookings.get(rid, []):
        if b.get("id") == booking_id:
            if b.get("table") and b.get("time"):
                release_table(rid, b["time"], b["table"])
            b["table"] = table_id
            b["status"] = "confirmed"
            break
    assign_table(rid, slot_time, table_id, booking_id)
    bump_version(rid)
    return {"status": "assigned"}


@app.post("/api/floorplan/release")
async def api_release_table(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    booking_id = data.get("booking_id")
    for b in bookings.get(rid, []):
        if b.get("id") == booking_id and b.get("table") and b.get("time"):
            release_table(rid, b["time"], b["table"])
            b["table"] = None
            b["status"] = "pending"
            break
    bump_version(rid)
    return {"status": "released"}


@app.post("/api/floorplan/status")
async def api_table_status(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    table_id = data.get("table_id", "")
    status = data.get("status", "available")
    date = data.get("date", "")
    service = data.get("service", "soir")
    if status not in ("available", "reserved", "seated", "dessert", "done", "noshow"):
        return {"error": "Invalid status"}
    key = f"{date}:{service}:{table_id}"
    table_statuses.setdefault(rid, {})[key] = status
    bump_version(rid)
    return {"status": "ok"}


@app.post("/api/floorplan/group")
async def api_table_group(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    tables = data.get("tables", [])
    name = data.get("name", "+".join(tables))
    if len(tables) < 2:
        return {"error": "Need at least 2 tables"}
    groups = table_groups.setdefault(rid, [])
    groups.append({"tables": tables, "name": name})
    bump_version(rid)
    return {"status": "ok", "groups": groups}


@app.post("/api/floorplan/ungroup")
async def api_table_ungroup(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    name = data.get("name", "")
    groups = table_groups.get(rid, [])
    table_groups[rid] = [g for g in groups if g.get("name") != name]
    bump_version(rid)
    return {"status": "ok", "groups": table_groups[rid]}


@app.post("/api/floorplan/setup")
async def api_floorplan_setup(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    tables_data = data.get("tables", [])
    zones = data.get("zones", ["Salle"])
    groups_data = data.get("groups", [])
    services = data.get("services", {})

    # Auto-position tables by zone
    zone_list = list(set(t.get("zone", "Salle") for t in tables_data)) or zones
    new_tables = []
    for zi, zone in enumerate(zone_list):
        zone_tables = [t for t in tables_data if t.get("zone") == zone]
        cols = max(2, int(len(zone_tables) ** 0.5) + 1)
        x_start = (zi / len(zone_list)) * 80 + 10
        x_range = 80 / len(zone_list) - 5
        for ti, t in enumerate(zone_tables):
            row = ti // cols
            col = ti % cols
            new_tables.append({
                "id": t.get("id", f"T{len(new_tables)+1}"),
                "seats": int(t.get("capacity", t.get("seats", 4))),
                "shape": "round" if int(t.get("capacity", t.get("seats", 4))) <= 4 else "rect",
                "zone": zone.lower(),
                "x": round(x_start + (col / max(cols-1, 1)) * x_range, 1),
                "y": round(15 + (row / max(3, 1)) * 65, 1),
            })

    floor_tables[rid] = new_tables
    rest = restaurants_cache.get(rid)
    if rest:
        rest["floor_tables"] = new_tables
        if services:
            rest.setdefault("settings", {})["services"] = services
    table_groups[rid] = groups_data
    init_daily_slots(rid)
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "ok", "tables": new_tables}


@app.get("/api/widget/{slug}/config")
async def api_widget_config(slug: str):
    rid = None
    for r_id, r in restaurants_cache.items():
        if r.get("slug") == slug:
            rid = r_id
            break
    if not rid:
        return JSONResponse(status_code=404, content={"error": "Restaurant not found"})
    rest = restaurants_cache[rid]
    ctx = rest.get("settings", {})
    # Available slots for next 14 days
    available = {}
    for i in range(14):
        d = (today_paris() + timedelta(days=i)).isoformat()
        slots = get_available_slots(rid, 2)
        available[d] = [s for s in slots if s.get("available", 0) > 0]
    zones = list(set(t.get("zone", "salle") for t in floor_tables.get(rid, [])))
    return {
        "name": rest["name"],
        "hours": ctx.get("hours", ""),
        "zones": zones,
        "max_covers": 12,
        "available": available,
    }

@app.post("/api/widget/{slug}/book")
async def api_widget_book(request: Request, slug: str):
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    rl_ok, *_ = check_rate_limit(client_ip, "default")
    if not rl_ok:
        return JSONResponse(status_code=429, content={"error": "Trop de tentatives"})
    rid = None
    for r_id, r in restaurants_cache.items():
        if r.get("slug") == slug:
            rid = r_id
            break
    if not rid:
        return JSONResponse(status_code=404, content={"error": "Restaurant not found"})
    data = await request.json()
    sanitize_dict(data, ["name", "phone", "email", "notes", "zone"], 500)
    name = data.get("name", "")
    phone = data.get("phone", "")
    email = data.get("email", "")
    if not name or not phone:
        return JSONResponse(status_code=400, content={"error": "Nom et telephone requis"})
    booking_date = data.get("date", today_paris().isoformat())
    booking_time = data.get("time", "")
    covers = int(data.get("covers", 2))
    zone = data.get("zone", "")
    notes = data.get("notes", "")
    rid_bookings = bookings.setdefault(rid, [])
    booking_id = f"R{len(rid_bookings)+1}"
    assigned_table = None
    if booking_time:
        assigned_table = find_best_table(rid, booking_time, covers, zone or None)
        if assigned_table:
            assign_table(rid, booking_time, assigned_table, booking_id)
    new_booking = {
        "id": booking_id, "phone": phone, "name": name,
        "date": booking_date, "time": booking_time, "booking_time": booking_time,
        "covers": covers, "table": assigned_table, "zone": zone,
        "source": "widget", "status": "confirmed" if assigned_table else "pending",
        "email": email, "notes": notes, "timestamp": datetime.utcnow().isoformat(),
    }
    rid_bookings.append(new_booking)
    await db_save_booking(rid, new_booking)
    bump_version(rid)
    # Create/update contact
    rid_contacts = contacts.setdefault(rid, {})
    if phone not in rid_contacts:
        rid_contacts[phone] = {"phone": phone, "name": name, "email": email, "source": "widget", "visits": 0, "first_seen": datetime.utcnow().isoformat()}
    rid_contacts[phone]["visits"] = rid_contacts[phone].get("visits", 0) + 1
    rid_contacts[phone]["last_seen"] = datetime.utcnow().isoformat()
    if email:
        rid_contacts[phone]["email"] = email
    await db_save_contact(rid, phone, rid_contacts[phone])
    return {"status": "ok", "booking_id": booking_id, "table": assigned_table}


@app.get("/api/campaigns")
async def api_list_campaigns(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"campaigns": campaigns_store.get(rid, [])}

@app.post("/api/campaigns/preview")
async def api_campaign_preview(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    filters = data.get("filters", {})
    matched = _filter_contacts(rid, filters)
    return {"count": len(matched)}

WHATSAPP_BROADCAST_COST_CENTS = 15  # 0,15 € HT par message WhatsApp campagne

def get_wallet_cents(rid: str) -> int:
    rest = restaurants_cache.get(rid, {})
    return int(rest.get("settings", {}).get("wallet_balance_cents", 0) or 0)

async def debit_wallet(rid: str, amount_cents: int) -> bool:
    rest = restaurants_cache.get(rid)
    if not rest:
        return False
    settings = rest.setdefault("settings", {})
    current = int(settings.get("wallet_balance_cents", 0) or 0)
    if current < amount_cents:
        return False
    settings["wallet_balance_cents"] = current - amount_cents
    await db_save_restaurant(rid, rest)
    return True


@app.get("/api/wallet")
async def api_get_wallet(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    cents = get_wallet_cents(rid)
    return {"balance_cents": cents, "balance_eur": round(cents / 100, 2),
            "wa_msg_cost_cents": WHATSAPP_BROADCAST_COST_CENTS}


@app.post("/api/campaigns/send")
async def api_campaign_send(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    subject = sanitize_input(data.get("subject", ""), 200)
    body = sanitize_input(data.get("body", ""), 5000)
    filters = data.get("filters", {})
    channels = data.get("channels") or ["email"]
    if not isinstance(channels, list):
        channels = ["email"]
    channels = [c for c in channels if c in ("email", "whatsapp")]
    if not channels:
        return JSONResponse(status_code=400, content={"error": "Au moins un canal requis (email ou whatsapp)"})
    template_label = sanitize_input(data.get("template", ""), 80)
    if "email" in channels and not subject:
        return JSONResponse(status_code=400, content={"error": "Objet requis pour l'envoi email"})
    if not body:
        return JSONResponse(status_code=400, content={"error": "Message requis"})
    rest = restaurants_cache.get(rid, {})
    rest_name = rest.get("name", "Restaurant")
    matched = _filter_contacts(rid, filters)

    # Pre-flight wallet check for WhatsApp broadcast
    cost_cents = 0
    if "whatsapp" in channels:
        wa_recipients = sum(1 for c in matched if c.get("phone"))
        cost_cents = wa_recipients * WHATSAPP_BROADCAST_COST_CENTS
        wallet = get_wallet_cents(rid)
        if cost_cents > wallet:
            return JSONResponse(status_code=402, content={
                "error": f"Wallet insuffisant : {wa_recipients} messages WhatsApp = {cost_cents/100:.2f} € HT, solde {wallet/100:.2f} €",
                "needed_cents": cost_cents, "balance_cents": wallet,
            })

    sent_email = 0
    sent_wa = 0
    wa_phone_id = rest.get("whatsapp_phone_number_id", "")
    wa_token = rest.get("whatsapp_access_token", "")
    for ct in matched:
        ct_name = (ct.get("name") or "").split()[0] if ct.get("name") else ""
        text_body = body.replace("{prenom}", ct_name).replace("{restaurant}", rest_name)
        # Email
        if "email" in channels:
            ct_email = ct.get("email")
            if ct_email:
                subj = subject.replace("{prenom}", ct_name).replace("{restaurant}", rest_name)
                try:
                    if BREVO_API_KEY:
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post("https://api.brevo.com/v3/smtp/email",
                                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                                json={"sender": {"name": rest_name, "email": "contact@guestscale.com"},
                                      "to": [{"email": ct_email, "name": ct.get("name", "")}],
                                      "subject": subj, "htmlContent": f"<div style='font-family:sans-serif;max-width:560px;margin:0 auto;padding:24px'>{text_body}</div>"})
                        sent_email += 1
                except Exception as e:
                    logger.error(f"Campaign email error: {e}")
        # WhatsApp
        if "whatsapp" in channels:
            ct_phone = ct.get("phone")
            if ct_phone and wa_phone_id and wa_token:
                try:
                    await send_whatsapp_message(wa_phone_id, wa_token, ct_phone, text_body)
                    debited = await debit_wallet(rid, WHATSAPP_BROADCAST_COST_CENTS)
                    if debited:
                        sent_wa += 1
                        await increment_message_count(rid, "broadcast")
                    else:
                        logger.warning(f"Wallet drained mid-campaign: {rid}")
                        break
                except Exception as e:
                    logger.error(f"Campaign WhatsApp error: {e}")

    sent_count = sent_email + sent_wa
    campaign = {
        "id": f"C{len(campaigns_store.get(rid, []))+1}",
        "subject": subject,
        "template": template_label,
        "channels": channels,
        "sent": sent_count,
        "sent_email": sent_email,
        "sent_whatsapp": sent_wa,
        "total": len(matched),
        "cost_cents": sent_wa * WHATSAPP_BROADCAST_COST_CENTS,
        "date": now_paris().isoformat(),
        "filters": filters,
    }
    campaigns_store.setdefault(rid, []).append(campaign)
    return {"status": "ok", "sent": sent_count, "sent_email": sent_email,
            "sent_whatsapp": sent_wa, "cost_cents": campaign["cost_cents"],
            "wallet_balance_cents": get_wallet_cents(rid)}

def _filter_contacts(rid: str, filters: dict) -> list:
    rid_contacts = contacts.get(rid, {})
    matched = list(rid_contacts.values())
    tags = filters.get("tags", [])
    if tags:
        matched = [c for c in matched if any(t in (c.get("tags") or []) for t in tags)]
    not_seen_days = filters.get("not_seen_days")
    if not_seen_days:
        cutoff = (today_paris() - timedelta(days=int(not_seen_days))).isoformat()
        matched = [c for c in matched if (c.get("last_seen") or "") < cutoff]
    return matched


@app.get("/api/reviews")
async def api_get_reviews(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rq = review_queue.get(rid, [])
    return {
        "queue": rq[-50:],
        "stats": {
            "total": len(rq),
            "sent": sum(1 for r in rq if r.get("sent")),
            "responded": sum(1 for r in rq if r.get("responded")),
            "positive": sum(1 for r in rq if r.get("sentiment") == "POSITIVE"),
            "negative": sum(1 for r in rq if r.get("sentiment") == "NEGATIVE"),
        }
    }


@app.get("/api/contacts/export")
async def api_export_contacts(request: Request):
    import csv, io
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_contacts = contacts.get(rid, {})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Nom", "Telephone", "Email", "Source", "Visites", "Tags", "Preferences", "Notes", "Langue"])
    for c in sorted(rid_contacts.values(), key=lambda c: c.get("name", "")):
        writer.writerow([
            c.get("name", ""), c.get("phone", ""), c.get("email", ""),
            c.get("source", ""), c.get("visits", 0),
            ", ".join(c.get("tags", [])), c.get("preferences", ""),
            c.get("notes", ""), c.get("language", ""),
        ])
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=contacts_export.csv"})


@app.get("/api/bookings/export")
async def api_export_bookings(request: Request):
    import csv, io
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    date_from = request.query_params.get("from", "")
    date_to = request.query_params.get("to", "9999")
    rid_bookings = bookings.get(rid, [])
    filtered = [b for b in rid_bookings if date_from <= (b.get("date") or "") <= date_to]
    filtered.sort(key=lambda b: (b.get("date", ""), b.get("booking_time", "")))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Heure", "Nom", "Telephone", "Couverts", "Table", "Zone", "Source", "Statut", "Occasion"])
    for b in filtered:
        writer.writerow([
            b.get("date", ""), b.get("booking_time", b.get("time", "")),
            b.get("name", ""), b.get("phone", ""),
            b.get("covers", ""), b.get("table", ""), b.get("zone", ""),
            b.get("source", ""), b.get("status", ""), b.get("occasion", ""),
        ])
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=reservations_export.csv"})


@app.get("/api/contacts/search")
async def api_search_contacts(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    q = (request.query_params.get("q") or "").strip().lower()
    if len(q) < 2:
        return {"results": []}
    rid_contacts = contacts.get(rid, {})
    results = []
    for phone, ct in rid_contacts.items():
        name = (ct.get("name") or "").lower()
        email = (ct.get("email") or "").lower()
        if q in name or q in email or q in phone:
            results.append(ct)
        if len(results) >= 5:
            break
    return {"results": results}


@app.get("/api/contacts")
async def api_get_contacts(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_contacts = contacts.get(rid, {})
    contact_list = sorted(rid_contacts.values(), key=lambda c: c.get("last_seen", ""), reverse=True)
    return {"contacts": contact_list[:200], "total": len(rid_contacts)}


@app.post("/api/contacts/tag")
async def api_tag_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone")
    tag = sanitize_input(data.get("tag", ""), 100)
    tags_list = [sanitize_input(t, 100) for t in data.get("tags", []) if isinstance(t, str)]
    rid_contacts = contacts.get(rid, {})
    if phone in rid_contacts:
        if tags_list:
            rid_contacts[phone]["tags"] = tags_list
        elif tag:
            if tag not in rid_contacts[phone].get("tags", []):
                rid_contacts[phone].setdefault("tags", []).append(tag)
        await db_save_contact(rid, phone, rid_contacts[phone])
    return {"status": "ok"}


@app.post("/api/contacts/note")
async def api_note_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone")
    note_text = sanitize_input(data.get("note", ""), 2000)
    rid_contacts = contacts.get(rid, {})
    if phone in rid_contacts:
        ct = rid_contacts[phone]
        # Migrate old string notes to list format
        existing = ct.get("notes", "")
        if isinstance(existing, str):
            ct["notes"] = [{"text": existing, "date": now_paris().isoformat()}] if existing else []
        if note_text:
            ct["notes"].append({"text": note_text, "date": now_paris().isoformat()})
        await db_save_contact(rid, phone, ct)
        bump_version(rid)
    return {"status": "ok"}


@app.post("/api/contacts/preferences")
async def api_preferences_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone")
    preferences = sanitize_input(data.get("preferences", ""), 1000)
    rid_contacts = contacts.get(rid, {})
    if phone in rid_contacts:
        rid_contacts[phone]["preferences"] = preferences
        await db_save_contact(rid, phone, rid_contacts[phone])
    return {"status": "ok"}


@app.get("/api/config")
async def api_get_config(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "No restaurant"}
    ctx = rest.get("settings", {})
    return {
        "name": rest.get("name", ""),
        "description": ctx.get("description", ""),
        "menu": ctx.get("menu", ""),
        "hours": ctx.get("hours", ""),
        "address": ctx.get("address", ""),
        "phone": ctx.get("phone", ""),
        "tone": ctx.get("tone", ""),
        "languages": ctx.get("languages", ""),
        "special_info": ctx.get("special_info", ""),
        "booking_link": ctx.get("booking_link", ""),
        "allergens_policy": ctx.get("allergens_policy", ""),
        "google_review_link": rest.get("google_review_link", ctx.get("google_review_link", "")),
        "tables": floor_tables.get(rid, []),
    }


@app.post("/api/config")
async def api_update_config(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "No restaurant"}
    ctx = rest.setdefault("settings", {})
    text_fields = ["description", "menu", "hours", "address", "phone", "tone", "languages", "special_info", "booking_link", "allergens_policy", "google_review_link", "avg_ticket"]
    sanitize_dict(data, text_fields + ["name"], 2000)
    for field in text_fields:
        if field in data:
            ctx[field] = data[field]
    if "name" in data:
        rest["name"] = data["name"]
    if "tables" in data:
        floor_tables[rid] = data["tables"]
        rest["floor_tables"] = data["tables"]
        init_daily_slots(rid)
    logger.info(f"Config updated for {rest['name']}: {list(data.keys())}")
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "updated"}


@app.get("/api/menu")
async def api_get_menu(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"sections": []}
    ctx = rest.get("settings", {})
    return {"sections": ctx.get("menu_sections", [])}


@app.post("/api/menu")
async def api_save_menu(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "No restaurant"}
    sections = data.get("sections", [])
    for sec in sections:
        if isinstance(sec.get("title"), str):
            sec["title"] = sanitize_input(sec["title"], 200)
        for item in sec.get("items", []):
            for k in ("name", "description", "price"):
                if isinstance(item.get(k), str):
                    item[k] = sanitize_input(item[k], 500)
    ctx = rest.setdefault("settings", {})
    ctx["menu_sections"] = sections
    text_lines = []
    for sec in sections:
        text_lines.append(f"\n--- {sec.get('title', '')} ---")
        for item in sec.get("items", []):
            line = f"- {item.get('name', '')}"
            if item.get("description"):
                line += f" : {item['description']}"
            if item.get("price"):
                line += f" ({item['price']})"
            text_lines.append(line)
    ctx["menu"] = "\n".join(text_lines)
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "ok"}


@app.post("/api/menu/scan")
async def api_scan_menu(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    data = await request.json()
    image_b64 = data.get("image", "")
    media_type = data.get("media_type", "image/jpeg")
    if not image_b64:
        return {"error": "No image provided"}
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,")[1]
    if not ANTHROPIC_API_KEY:
        return {"error": "No API key"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514", "max_tokens": 4000,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                        {"type": "text", "text": 'Transcris ce menu de restaurant en JSON. Retourne UNIQUEMENT du JSON valide sans backticks. Format: {"sections": [{"title": "Entrees", "items": [{"name": "Salade Cesar", "description": "Romaine, parmesan", "price": "12"}]}]}. Identifie les sections (Entrees, Plats, Desserts, Boissons, Vins etc). Pour chaque plat: nom, description si visible, prix sans symbole euro. Garde l orthographe exacte.'}
                    ]}]
                }
            )
            result = resp.json()
            text = result.get("content", [{}])[0].get("text", "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            sections = json.loads(text).get("sections", [])
            return {"sections": sections}
    except Exception as e:
        logger.error(f"Menu scan error: {e}")
        return {"error": str(e), "sections": []}


@app.get("/api/daily")
async def api_get_daily(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    status = restaurant_status.get(rid, {})
    return {"message": status.get("daily_message", "")}


@app.post("/api/daily")
async def api_set_daily(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = restaurant_status.setdefault(rid, {})
    status["daily_message"] = sanitize_input(data.get("message", ""), 1000)
    await db_save_restaurant_status(rid, status)
    rest = restaurants_cache.get(rid)
    if rest:
        rest.setdefault("settings", {})["special_info"] = data.get("message", "")
    bump_version(rid)
    return {"status": "ok"}


@app.post("/api/broadcast")
async def api_broadcast(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    msg = sanitize_input(data.get("message", ""), 1000)
    if not msg:
        return {"error": "No message"}
    rest = restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id"):
        return {"error": "WhatsApp not configured"}
    rid_contacts = contacts.get(rid, {})
    sent = 0
    for phone, ct in rid_contacts.items():
        if phone and phone.startswith("+"):
            try:
                await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], phone, msg)
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast failed for {phone}: {e}")
    return {"status": "ok", "sent": sent}


@app.get("/api/stats/history")
async def api_stats_history(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_bookings = bookings.get(rid, [])
    today_str = today_paris().isoformat()
    tomorrow_str = (today_paris() + timedelta(days=1)).isoformat()
    today_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(today_str)]
    tomorrow_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(tomorrow_str)]
    total_tables = len(floor_tables.get(rid, []))
    occupied = len([b for b in today_bookings if b.get("table")])
    st = stats.get(rid, {})
    rid_contacts = contacts.get(rid, {})
    rq = review_queue.get(rid, [])
    sources = {}
    for b in today_bookings:
        s = b.get("source", "autre")
        sources[s] = sources.get(s, 0) + 1
    today_data = {
        "date": today_str,
        "bookings": len(today_bookings),
        "covers": sum(b.get("covers", 0) for b in today_bookings),
        "messages": st.get("messages_today", 0),
        "tables_total": total_tables,
        "tables_occupied": occupied,
        "occ_rate": round(occupied / total_tables * 100) if total_tables else 0,
        "sources": sources,
        "tomorrow_bookings": len(tomorrow_bookings),
        "tomorrow_covers": sum(b.get("covers", 0) for b in tomorrow_bookings),
        "new_contacts": sum(1 for c in rid_contacts.values() if (c.get("first_seen") or "").startswith(today_str)),
        "pending_reviews": sum(1 for r in rq if not r.get("sent")),
        "cancelled": 0,
    }
    date_from = request.query_params.get("from", (today_paris() - timedelta(days=30)).isoformat())
    date_to = request.query_params.get("to", today_str)
    # Build history from bookings
    history = []
    d = date.fromisoformat(date_from)
    end_d = date.fromisoformat(date_to)
    total_bk = 0
    total_covers = 0
    all_sources = {}
    all_zones = {}
    client_visits = {}
    while d <= end_d:
        ds = d.isoformat()
        day_bk = [b for b in rid_bookings if (b.get("date") or "").startswith(ds)]
        day_covers = sum(b.get("covers", 0) for b in day_bk)
        src = {}
        for b in day_bk:
            s = b.get("source", "autre")
            src[s] = src.get(s, 0) + 1
            all_sources[s] = all_sources.get(s, 0) + 1
            z = b.get("zone") or "salle"
            all_zones[z] = all_zones.get(z, 0) + 1
            cn = b.get("name", "")
            if cn:
                client_visits[cn] = client_visits.get(cn, 0) + 1
        total_bk += len(day_bk)
        total_covers += day_covers
        if day_bk:
            history.append({"date": ds, "bookings": len(day_bk), "covers": day_covers, "sources": src})
        d += timedelta(days=1)
    noshow_count = sum(1 for b in rid_bookings if b.get("status") == "noshow" and date_from <= (b.get("date") or "") <= date_to)
    top_clients = sorted(client_visits.items(), key=lambda x: -x[1])[:10]
    avg_ticket = float(restaurants_cache.get(rid, {}).get("settings", {}).get("avg_ticket", 25))
    return {
        "history": history[-90:],
        "today": today_data,
        "period": {
            "from": date_from, "to": date_to,
            "total_bookings": total_bk,
            "total_covers": total_covers,
            "avg_covers_per_day": round(total_covers / max(len(history), 1), 1),
            "sources": all_sources,
            "zones": all_zones,
            "noshow_count": noshow_count,
            "noshow_rate": round(noshow_count / max(total_bk, 1) * 100, 1),
            "top_clients": [{"name": n, "visits": v} for n, v in top_clients],
            "estimated_revenue": round(total_covers * avg_ticket),
            "avg_ticket": avg_ticket,
        }
    }


@app.post("/api/bookings/add")
@app.post("/api/bookings/manual")
async def api_add_manual_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    sanitize_dict(data, ["name", "phone", "email", "notes", "zone", "source"], 500)
    rid_bookings = bookings.setdefault(rid, [])
    booking_id = f"R{len(rid_bookings)+1}"
    name = data.get("name", "")
    covers = int(data.get("covers", 2))
    booking_time = data.get("time", "")
    zone = data.get("zone", "")
    source = data.get("source", "phone")
    phone = data.get("phone", "")
    email = data.get("email", "")
    notes = data.get("notes", "")
    requested_table = data.get("table", "")
    booking_date = data.get("date", "") or datetime.utcnow().strftime("%Y-%m-%d")
    assigned_table = None
    if requested_table:
        assigned_table = requested_table
        if booking_time and booking_time in ALL_SLOTS:
            assign_table(rid, booking_time, assigned_table, booking_id)
    elif booking_time and booking_time in ALL_SLOTS:
        assigned_table = find_best_table(rid, booking_time, covers, zone or None)
        if assigned_table:
            assign_table(rid, booking_time, assigned_table, booking_id)
    new_booking = {
        "id": booking_id, "phone": phone, "email": email,
        "name": name or phone or "Client", "message": notes,
        "timestamp": datetime.utcnow().isoformat(), "date": booking_date,
        "status": "confirmed" if assigned_table else "pending",
        "booking_time": booking_time, "time": booking_time,
        "covers": covers, "table": assigned_table, "zone": zone, "source": source,
    }
    rid_bookings.append(new_booking)
    if phone:
        track_contact(rid, phone, name)
        rid_contacts = contacts.get(rid, {})
        if email and phone in rid_contacts:
            rid_contacts[phone]["email"] = email
    track_stats(rid, is_booking=True)
    await db_save_booking(rid, new_booking)
    bump_version(rid)
    return {"status": "created", "booking_id": booking_id, "table": assigned_table}


@app.post("/api/bookings/update")
async def api_update_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    sanitize_dict(data, ["name", "phone"], 500)
    bid = data.get("booking_id", "")
    for b in bookings.get(rid, []):
        if b["id"] == bid:
            if "name" in data:
                b["name"] = data["name"]
            if "covers" in data:
                b["covers"] = int(data["covers"])
            if "time" in data:
                b["booking_time"] = data["time"]
                b["time"] = data["time"]
            if "phone" in data:
                b["phone"] = data["phone"]
            if "date" in data:
                b["date"] = data["date"]
            if "table" in data:
                b["table"] = data["table"] or None
                b["status"] = "confirmed" if data["table"] else "pending"
            if "occasion" in data:
                b["occasion"] = data["occasion"] or None
            await db_save_booking(rid, b)
            bump_version(rid)
            return {"status": "updated"}
    return {"error": "Booking not found"}


@app.post("/api/bookings/delete")
async def api_delete_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    bid = data.get("booking_id", "")
    rid_bookings = bookings.get(rid, [])
    for i, b in enumerate(rid_bookings):
        if b["id"] == bid:
            if b.get("table") and b.get("booking_time"):
                release_table(rid, b["booking_time"], b["table"])
            # Determine service for waitlist notification
            bt = b.get("booking_time") or b.get("time", "")
            bh = int(bt.split(":")[0]) if bt and ":" in bt else 0
            service = "midi" if bh < 17 else "soir"
            booking_date = b.get("date", "")
            covers = b.get("covers", 0)
            rid_bookings.pop(i)
            bump_version(rid)
            # Notify waitlist
            if booking_date:
                import asyncio
                asyncio.create_task(notify_next_on_waitlist(rid, booking_date, service, bt, covers))
            return {"status": "deleted"}
    return {"error": "Booking not found"}


@app.get("/api/settings")
async def api_get_settings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    set_key = request.query_params.get("set", "")
    set_val = request.query_params.get("value", "")
    if set_key:
        status = restaurant_status.setdefault(rid, {})
        status[set_key] = set_val
        await db_save_restaurant_status(rid, status)
        return {"status": "ok"}
    status = restaurant_status.get(rid, {})
    rest = restaurants_cache.get(rid, {})
    settings = rest.get("settings") or {}
    return {
        "pages": status.get("dashboard_pages", {
            "floorplan": True, "bookings": True, "conversations": True,
            "reviews": True, "contacts": True, "dashboard": True,
        }),
        "onboarding_done": status.get("onboarding_done", "0"),
        "reminders_enabled": settings.get("reminders_enabled", True),
    }


@app.post("/api/settings")
async def api_update_settings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = restaurant_status.setdefault(rid, {})
    if "pages" in data:
        status["dashboard_pages"] = data.get("pages", {})
    if "reminders_enabled" in data:
        rest = restaurants_cache.get(rid)
        if rest:
            rest.setdefault("settings", {})["reminders_enabled"] = data["reminders_enabled"]
        status["reminders_enabled"] = data["reminders_enabled"]
    await db_save_restaurant_status(rid, status)
    return {"status": "updated"}


# ==============================================================
# WAITLIST API
# ==============================================================

@app.get("/api/waitlist")
async def api_get_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    wl = waitlist.get(rid, [])
    # Filter by date if provided
    date_filter = request.query_params.get("date", "")
    if date_filter:
        wl = [w for w in wl if w.get("date") == date_filter]
    return {
        "waitlist": wl,
        "total": len(wl),
        "waiting": len([w for w in wl if w["status"] == "waiting"]),
        "notified": len([w for w in wl if w["status"] == "notified"]),
    }


@app.post("/api/waitlist/add")
async def api_add_to_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    sanitize_dict(data, ["phone", "name"], 200)
    phone = data.get("phone", "")
    name = data.get("name", "")
    covers = int(data.get("covers", 2))
    service = data.get("service", "soir")
    booking_date = data.get("date", today_paris().isoformat())
    booking_time = data.get("time", "")
    if not name:
        return {"error": "Nom requis"}
    entry = await add_to_waitlist(rid, phone, name, covers, service, booking_date, booking_time)
    if entry:
        return {"status": "ok", "entry": entry}
    return {"error": "Déjà sur la liste d&#39;attente"}


@app.post("/api/waitlist/remove")
async def api_remove_from_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    entry_id = data.get("id", "")
    wl = waitlist.get(rid, [])
    for w in wl:
        if w["id"] == entry_id:
            w["status"] = "declined"
            await db_update_waitlist_status(rid, entry_id, "declined")
            bump_version(rid)
            return {"status": "removed"}
    return {"error": "Entry not found"}


@app.post("/api/waitlist/notify")
async def api_notify_waitlist(request: Request):
    """Manually trigger notification to next person on waitlist."""
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    booking_date = data.get("date", today_paris().isoformat())
    service = data.get("service", "soir")
    freed_time = data.get("time", "")
    freed_covers = int(data.get("covers", 0))
    await notify_next_on_waitlist(rid, booking_date, service, freed_time, freed_covers)
    return {"status": "ok"}


# ==============================================================
# WEBCHAT (multi-tenant: identifies restaurant by slug in URL)
# ==============================================================

@app.post("/api/webchat/message")
async def api_webchat_message(request: Request):
    data = await request.json()
    session_id = data.get("session_id", "")
    message = sanitize_input(data.get("message", ""), 2000).strip()
    visitor_name = sanitize_input(data.get("name", ""), 100)
    slug = data.get("slug", "")

    if not session_id or not message:
        return {"error": "Missing session_id or message"}

    # Find restaurant by slug or use first available
    rid = None
    if slug:
        for r_id, r in restaurants_cache.items():
            if r.get("slug") == slug:
                rid = r_id
                break
    if not rid:
        # Fallback to first restaurant
        rid = list(restaurants_cache.keys())[0] if restaurants_cache else None
    if not rid:
        return {"error": "No restaurant", "reply": "Service temporairement indisponible."}

    rest = restaurants_cache[rid]

    if session_id not in web_sessions:
        web_sessions[session_id] = {
            "messages": [], "name": visitor_name or "", "phone": "",
            "created": datetime.utcnow().isoformat(), "last_active": datetime.utcnow().isoformat(),
            "restaurant_id": rid,
        }

    session = web_sessions[session_id]
    session["last_active"] = datetime.utcnow().isoformat()
    if visitor_name and not session["name"]:
        session["name"] = visitor_name

    import re
    name_patterns = [r"(?:au nom de|je suis|je m'appelle|my name is|c'est|nom\s*:\s*)[\s]*([A-Z][a-zéèêëàâùûôîïç]+(?:\s+[A-Z][a-zéèêëàâùûôîïç]+)?)"]
    for pat in name_patterns:
        nm = re.search(pat, message, re.IGNORECASE)
        if nm and not session["name"]:
            session["name"] = nm.group(1).strip()

    session["messages"].append({"role": "user", "content": message, "timestamp": datetime.utcnow().isoformat()})

    system_prompt = build_system_prompt(rest, rid)
    system_prompt += "\n\nCONTEXTE : Tu réponds via le chat web du site internet du restaurant (pas WhatsApp). Sois concis et accueillant.\nIMPORTANT CHAT WEB : Pour toute demande de réservation, tu DOIS collecter le numéro de téléphone et l'adresse email du client EN PLUS du nom, nombre de personnes, date et heure."

    claude_messages = [{"role": m["role"], "content": m["content"]} for m in session["messages"][-10:]]
    reply = await ask_claude(system_prompt, claude_messages)

    session["messages"].append({"role": "assistant", "content": reply, "timestamp": datetime.utcnow().isoformat()})

    conv_key = f"web_{session_id[:8]}"
    full_key = f"{rid}:{conv_key}"
    if full_key not in conversations:
        conversations[full_key] = []
    conversations[full_key].append({"role": "user", "content": message, "timestamp": datetime.utcnow().isoformat()})
    conversations[full_key].append({"role": "assistant", "content": reply, "timestamp": datetime.utcnow().isoformat()})
    conversations[full_key] = conversations[full_key][-20:]

    track_stats(rid, language="fr")

    phone_match = re.search(r'(?:0|\+33|33)\s*[1-9](?:[\s.-]*\d{2}){4}', message)
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', message)
    if phone_match:
        session["phone"] = re.sub(r'[\s.-]', '', phone_match.group())
    if email_match:
        session["email"] = email_match.group()

    contact_id = session.get("phone") or f"web_{session_id[:8]}"
    track_contact(rid, contact_id, session.get("name", ""))
    rid_contacts = contacts.get(rid, {})
    if session.get("email") and contact_id in rid_contacts:
        rid_contacts[contact_id]["email"] = session["email"]

    # Check for booking keywords
    booking_keywords = ["réserv", "reserv", "book", "table", "prenot"]
    if any(kw in message.lower() for kw in booking_keywords):
        time_match = re.search(r'(\d{1,2})[h:](\d{2})?', message)
        covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax)', message.lower())
        rid_bookings = bookings.setdefault(rid, [])
        booking_id = f"R{len(rid_bookings)+1}"
        booking_time = None
        if time_match:
            h_val = int(time_match.group(1))
            m_val = int(time_match.group(2) or 0)
            m_val = (m_val // 15) * 15
            booking_time = f"{h_val:02d}:{m_val:02d}"
        covers = int(covers_match.group(1)) if covers_match else 2
        assigned_table = None
        if booking_time and booking_time in ALL_SLOTS:
            assigned_table = find_best_table(rid, booking_time, covers)
            if assigned_table:
                assign_table(rid, booking_time, assigned_table, booking_id)
        rid_bookings.append({
            "id": booking_id, "phone": f"web_{session_id[:8]}", "name": session.get("name", "Web visitor"),
            "message": message[:200], "timestamp": datetime.utcnow().isoformat(),
            "status": "confirmed" if assigned_table else "pending",
            "time": booking_time or "", "covers": covers, "table": assigned_table, "zone": "", "source": "web",
        })
        track_stats(rid, is_booking=True)
        bump_version(rid)

    return {"reply": reply, "session_id": session_id}


@app.get("/api/webchat/history")
async def api_webchat_history(request: Request):
    session_id = request.query_params.get("session_id", "")
    if not session_id or session_id not in web_sessions:
        return {"messages": []}
    return {"messages": web_sessions[session_id]["messages"][-20:]}


# ==============================================================
# WIDGET JS (multi-tenant via slug)
# ==============================================================

WIDGET_JS = """
(function(){
  var BASE='__BASE_URL__';
  var COLOR='__COLOR__';
  var WELCOME='__WELCOME__';
  var RESTAURANT='__RESTAURANT__';
  var SLUG='__SLUG__';
  var SESSION=localStorage.getItem('gs_sid')||('gs_'+Math.random().toString(36).substr(2,12));
  localStorage.setItem('gs_sid',SESSION);
  var open=false,loaded=false;
  var style=document.createElement('style');
  style.textContent=`
    #rb-bubble{position:fixed;bottom:24px;right:24px;width:60px;height:60px;border-radius:50%;background:${COLOR};color:white;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 20px rgba(0,0,0,.2);z-index:99999;font-size:28px;transition:transform .2s;border:none}
    #rb-bubble:hover{transform:scale(1.08)}
    #rb-badge{position:absolute;top:-2px;right:-2px;width:18px;height:18px;border-radius:50%;background:#EF4444;color:white;font-size:10px;font-weight:800;display:none;align-items:center;justify-content:center}
    #rb-window{position:fixed;bottom:96px;right:24px;width:380px;height:520px;background:white;border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,.15);z-index:99999;display:none;flex-direction:column;overflow:hidden;font-family:'Inter',-apple-system,sans-serif}
    #rb-header{background:${COLOR};color:white;padding:16px 20px;display:flex;align-items:center;gap:12px}
    #rb-header-icon{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-size:18px}
    #rb-header-name{font-size:15px;font-weight:700}
    #rb-header-status{font-size:11px;opacity:.8}
    #rb-close{margin-left:auto;background:none;border:none;color:white;font-size:20px;cursor:pointer;opacity:.7}
    #rb-close:hover{opacity:1}
    #rb-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
    .rb-msg{max-width:80%;padding:10px 14px;border-radius:14px;font-size:13px;line-height:1.5;word-wrap:break-word}
    .rb-msg-bot{background:#FAF9F7;color:#1C1917;align-self:flex-start;border-bottom-left-radius:4px}
    .rb-msg-user{background:${COLOR};color:white;align-self:flex-end;border-bottom-right-radius:4px}
    .rb-typing{align-self:flex-start;background:#FAF9F7;padding:10px 16px;border-radius:14px;font-size:13px;color:#A8A29E}
    #rb-input-area{padding:12px;border-top:1px solid #E7E5E4;display:flex;gap:8px}
    #rb-input{flex:1;padding:10px 14px;border-radius:10px;border:1.5px solid #E7E5E4;font-size:13px;outline:none;font-family:inherit}
    #rb-input:focus{border-color:${COLOR}}
    #rb-send{background:${COLOR};color:white;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer;font-size:13px;font-family:inherit}
    @media(max-width:480px){#rb-window{bottom:0;right:0;width:100%;height:100%;border-radius:0}}
  `;
  document.head.appendChild(style);
  var bubble=document.createElement('button');
  bubble.id='rb-bubble';
  bubble.innerHTML='💬<div id="rb-badge">1</div>';
  bubble.onclick=function(){toggleChat()};
  document.body.appendChild(bubble);
  var win=document.createElement('div');
  win.id='rb-window';
  win.innerHTML=`
    <div id="rb-header">
      <div id="rb-header-icon"></div>
      <div><div id="rb-header-name">${RESTAURANT}</div><div id="rb-header-status">En ligne — Reponse instantanee</div></div>
      <button id="rb-close" onclick="document.getElementById('rb-window').style.display='none'">&times;</button>
    </div>
    <div id="rb-messages"></div>
    <div id="rb-input-area">
      <input id="rb-input" type="text" placeholder="Votre message..." onkeydown="if(event.key==='Enter')document.getElementById('rb-send').click()">
      <button id="rb-send">Envoyer</button>
    </div>
  `;
  document.body.appendChild(win);
  function toggleChat(){
    open=!open;
    win.style.display=open?'flex':'none';
    document.getElementById('rb-badge').style.display='none';
    if(!loaded){addBotMsg(WELCOME);loaded=true;}
    if(open)document.getElementById('rb-input').focus();
  }
  function addBotMsg(text){var el=document.getElementById('rb-messages');var d=document.createElement('div');d.className='rb-msg rb-msg-bot';d.textContent=text;el.appendChild(d);el.scrollTop=el.scrollHeight;}
  function addUserMsg(text){var el=document.getElementById('rb-messages');var d=document.createElement('div');d.className='rb-msg rb-msg-user';d.textContent=text;el.appendChild(d);el.scrollTop=el.scrollHeight;}
  function showTyping(){var el=document.getElementById('rb-messages');var d=document.createElement('div');d.className='rb-typing';d.id='rb-typing';d.textContent='En train de taper...';el.appendChild(d);el.scrollTop=el.scrollHeight;}
  function hideTyping(){var t=document.getElementById('rb-typing');if(t)t.remove();}
  document.getElementById('rb-send').onclick=async function(){
    var input=document.getElementById('rb-input');
    var msg=input.value.trim();
    if(!msg)return;
    input.value='';
    addUserMsg(msg);
    showTyping();
    try{
      var r=await fetch(BASE+'/api/webchat/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:SESSION,message:msg,slug:SLUG})});
      if(!r.ok){hideTyping();addBotMsg('Erreur de connexion ('+r.status+').');return;}
      var d=await r.json();
      hideTyping();
      if(d.reply)addBotMsg(d.reply);
    }catch(e){
      hideTyping();
      addBotMsg('Desole, un probleme technique. Reessayez.');
    }
  };
  setTimeout(function(){if(!open)document.getElementById('rb-badge').style.display='flex';},3000);
})();
"""


@app.get("/widget.js")
async def serve_widget(request: Request):
    slug = request.query_params.get("slug", "")
    # Find restaurant by slug
    rid = None
    restaurant_name = "Restaurant"
    if slug:
        for r_id, r in restaurants_cache.items():
            if r.get("slug") == slug:
                rid = r_id
                restaurant_name = r["name"]
                break
    if not rid and restaurants_cache:
        rid = list(restaurants_cache.keys())[0]
        restaurant_name = restaurants_cache[rid]["name"]

    color = request.query_params.get("color", "#C2410C")
    welcome = request.query_params.get("welcome", f"Bonjour ! Bienvenue chez {restaurant_name} 😊 Comment puis-je vous aider ?")

    # Hardcode base_url to prevent host header poisoning
    base_url = f"https://{APP_DOMAIN}"

    js = WIDGET_JS.replace("__BASE_URL__", base_url)
    js = js.replace("__COLOR__", color)
    js = js.replace("__WELCOME__", welcome.replace("'", "\\'"))
    js = js.replace("__RESTAURANT__", restaurant_name.replace("'", "\\'"))
    js = js.replace("__SLUG__", slug)

    return Response(content=js, media_type="application/javascript")


@app.get("/widget-preview", response_class=HTMLResponse)
async def widget_preview(request: Request):
    slug = request.query_params.get("slug", "")
    restaurant_name = "Restaurant"
    if slug:
        for r_id, r in restaurants_cache.items():
            if r.get("slug") == slug:
                restaurant_name = r["name"]
                break
    elif restaurants_cache:
        restaurant_name = list(restaurants_cache.values())[0]["name"]
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{restaurant_name} — Widget Preview</title>
<style>body{{font-family:Inter,-apple-system,sans-serif;background:#FAF9F7;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.preview-box{{text-align:center;color:#78716C}}.preview-box h1{{color:#1C1917;font-size:24px;margin-bottom:8px}}.preview-box p{{font-size:14px}}</style>
</head><body>
<div class="preview-box"><h1>{restaurant_name}</h1><p>Cliquez sur la bulle en bas a droite pour tester le chat</p></div>
<script src="/widget.js?slug={slug}"></script>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    return HTMLResponse("<h1>Politique de confidentialité</h1><p>GuestScale — En cours de rédaction</p>")


@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return HTMLResponse("<h1>Conditions générales</h1><p>GuestScale — En cours de rédaction</p>")


# ==============================================================
# SUPER-ADMIN DASHBOARD
# ==============================================================

def sanitize_restaurant(rest: dict) -> dict:
    """Remove sensitive fields from restaurant data before sending to client."""
    safe = dict(rest)
    token = safe.get("whatsapp_access_token", "")
    if token:
        safe["whatsapp_access_token"] = "***" + token[-8:]
    return safe


def verify_admin(request: Request) -> bool:
    """Verify admin access via ADMIN_SECRET in query param or header."""
    secret = request.query_params.get("secret", "") or request.headers.get("x-admin-secret", "")
    return secret == ADMIN_SECRET and ADMIN_SECRET != ""


@app.get("/api/admin/restaurants")
async def admin_list_restaurants(request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    result = []
    for rid, rest in restaurants_cache.items():
        rid_bookings = bookings.get(rid, [])
        rid_contacts = contacts.get(rid, {})
        rid_convs = sum(1 for k in conversations if k.startswith(rid))
        st = stats.get(rid, {})
        result.append({
            "id": rid, "slug": rest.get("slug", ""), "name": rest.get("name", ""),
            "status": rest.get("status", "trial"),
            "trial_ends_at": rest.get("trial_ends_at"),
            "created_at": rest.get("created_at"),
            "owner_phone": rest.get("owner_phone", ""),
            "whatsapp_connected": bool(rest.get("whatsapp_phone_number_id")),
            "google_review_link": bool(rest.get("google_review_link")),
            "total_bookings": len(rid_bookings),
            "total_contacts": len(rid_contacts),
            "total_conversations": rid_convs,
            "messages_today": st.get("messages_today", 0),
            "bookings_today": st.get("bookings_today", 0),
            "tables_count": len(floor_tables.get(rid, [])),
            "has_menu": bool(rest.get("settings", {}).get("menu")),
            "has_address": bool(rest.get("settings", {}).get("address")),
            "waitlist_count": len([w for w in waitlist.get(rid, []) if w.get("status") == "waiting"]),
            "messages_this_month": usage_counters.get(rid, {}).get(today_paris().strftime("%Y-%m"), {}).get("total", 0),
            "plan_limit": PLAN_LIMITS.get(rest.get("settings", {}).get("subscription_plan", "trial"), 500),
        })
    result.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"restaurants": result, "total": len(result)}


@app.get("/api/admin/stats")
async def admin_global_stats(request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    total_restaurants = len(restaurants_cache)
    total_bookings = sum(len(b) for b in bookings.values())
    total_contacts = sum(len(c) for c in contacts.values())
    total_conversations = len(conversations)
    total_messages = sum(s.get("messages_today", 0) for s in stats.values())
    total_tables = sum(len(ft) for ft in floor_tables.values())
    trial_count = sum(1 for r in restaurants_cache.values() if r.get("status") == "trial")
    active_count = sum(1 for r in restaurants_cache.values() if r.get("status") == "active")
    suspended_count = sum(1 for r in restaurants_cache.values() if r.get("status") == "suspended")
    cancelled_count = sum(1 for r in restaurants_cache.values() if r.get("status") == "cancelled")
    wa_connected = sum(1 for r in restaurants_cache.values() if r.get("whatsapp_phone_number_id"))

    # MRR / ARR
    price_per_month = 149
    mrr = active_count * price_per_month
    arr = mrr * 12
    potential_mrr = (active_count + trial_count) * price_per_month

    # Conversion rate trial -> active
    total_ever = total_restaurants
    conversion_rate = round((active_count / total_ever * 100), 1) if total_ever > 0 else 0
    churn_rate = round((cancelled_count / total_ever * 100), 1) if total_ever > 0 else 0

    # Growth: registrations over time
    registrations_timeline = []
    bookings_timeline = []
    messages_timeline = []
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                # Users count
                users_count = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
                # Registrations per week (last 12 weeks)
                rows = await conn.fetch("""
                    SELECT date_trunc('week', created_at) as week, COUNT(*) as cnt
                    FROM restaurants
                    WHERE created_at > NOW() - INTERVAL '12 weeks'
                    GROUP BY week ORDER BY week
                """)
                for row in rows:
                    registrations_timeline.append({
                        "date": row["week"].strftime("%Y-%m-%d") if row["week"] else "",
                        "count": row["cnt"]
                    })
                # Bookings per day (last 30 days)
                rows = await conn.fetch("""
                    SELECT booking_date, COUNT(*) as cnt
                    FROM mt_bookings
                    WHERE created_at > NOW() - INTERVAL '30 days'
                    GROUP BY booking_date ORDER BY booking_date
                """)
                for row in rows:
                    bookings_timeline.append({
                        "date": row["booking_date"] or "",
                        "count": row["cnt"]
                    })
                # Messages per day from daily_stats (last 30 days)
                rows = await conn.fetch("""
                    SELECT stat_date, SUM((data->>'messages')::int) as msgs, SUM((data->>'bookings')::int) as bks
                    FROM mt_daily_stats
                    WHERE stat_date > (NOW() - INTERVAL '30 days')::date::text
                    GROUP BY stat_date ORDER BY stat_date
                """)
                for row in rows:
                    messages_timeline.append({
                        "date": row["stat_date"] or "",
                        "messages": row["msgs"] or 0,
                        "bookings": row["bks"] or 0,
                    })
        except Exception as e:
            logger.error(f"Admin stats query error: {e}")
            users_count = 0
    else:
        users_count = 0

    # Total messages all time (sum of daily stats)
    total_messages_alltime = sum(
        sum(snap.get("messages", 0) for snap in dsh)
        for dsh in daily_stats_history.values()
    )
    total_bookings_alltime = total_bookings

    # Per-restaurant performance
    restaurant_performance = []
    for rid, rest in restaurants_cache.items():
        rid_bks = bookings.get(rid, [])
        rid_cts = contacts.get(rid, {})
        rid_st = stats.get(rid, {})
        dsh = daily_stats_history.get(rid, [])
        total_msgs_resto = sum(snap.get("messages", 0) for snap in dsh) + rid_st.get("messages_today", 0)
        restaurant_performance.append({
            "name": rest.get("name", ""),
            "status": rest.get("status", ""),
            "bookings": len(rid_bks),
            "contacts": len(rid_cts),
            "messages": total_msgs_resto,
            "bookings_today": rid_st.get("bookings_today", 0),
            "messages_today": rid_st.get("messages_today", 0),
            "whatsapp": bool(rest.get("whatsapp_phone_number_id")),
            "created": rest.get("created_at", ""),
        })

    # Avg bookings per restaurant per month (estimate)
    avg_bookings_per_resto = round(total_bookings / max(active_count, 1), 1)

    return {
        "total_restaurants": total_restaurants, "trial_count": trial_count,
        "active_count": active_count, "suspended_count": suspended_count, "cancelled_count": cancelled_count,
        "total_bookings": total_bookings, "total_contacts": total_contacts,
        "total_conversations": total_conversations, "total_messages_today": total_messages,
        "total_messages_alltime": total_messages_alltime,
        "total_tables": total_tables, "whatsapp_connected": wa_connected, "users_count": users_count,
        "mrr": mrr, "arr": arr, "potential_mrr": potential_mrr, "price_per_month": price_per_month,
        "conversion_rate": conversion_rate, "churn_rate": churn_rate,
        "avg_bookings_per_resto": avg_bookings_per_resto,
        "registrations_timeline": registrations_timeline,
        "bookings_timeline": bookings_timeline,
        "messages_timeline": messages_timeline,
        "restaurant_performance": restaurant_performance,
        "total_messages_month": sum(c.get(today_paris().strftime("%Y-%m"), {}).get("total", 0) for c in usage_counters.values()),
    }


@app.get("/api/admin/restaurant/{rid}")
async def admin_restaurant_detail(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_bookings = bookings.get(rid, [])
    rid_contacts = contacts.get(rid, {})
    st = stats.get(rid, {})
    dsh = daily_stats_history.get(rid, [])
    wl = waitlist.get(rid, [])
    status_data = restaurant_status.get(rid, {})
    # Get user info
    user_info = None
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT email, first_name, last_name, phone, created_at FROM users WHERE restaurant_id = $1::uuid LIMIT 1",
                    rid
                )
                if row:
                    user_info = {
                        "email": row["email"], "first_name": row["first_name"] or "",
                        "last_name": row["last_name"] or "", "phone": row["phone"] or "",
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                    }
        except Exception:
            pass
    return {
        "restaurant": sanitize_restaurant(rest), "user": user_info,
        "bookings_count": len(rid_bookings), "contacts_count": len(rid_contacts),
        "stats_today": st, "daily_history": dsh[-14:],
        "waitlist_active": len([w for w in wl if w.get("status") in ("waiting", "notified")]),
        "status": status_data, "tables": floor_tables.get(rid, []),
    }


@app.post("/api/admin/restaurant/{rid}/status")
async def admin_update_restaurant_status(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    data = await request.json()
    new_status = data.get("status", "")
    if new_status in ("trial", "active", "suspended", "cancelled"):
        rest["status"] = new_status
        if db_pool:
            try:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE restaurants SET status = $1 WHERE id = $2::uuid", new_status, rid)
            except Exception as e:
                logger.error(f"Admin status update error: {e}")
        return {"status": "ok", "new_status": new_status}
    return {"error": "Invalid status"}


@app.delete("/api/admin/restaurant/{rid}")
async def admin_delete_restaurant(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}


@app.put("/api/admin/restaurant/{rid}")
async def admin_update_restaurant(rid: str, request: Request):
    """Update restaurant details: name, slug, owner_phone, settings fields, whatsapp config, google_review_link."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    data = await request.json()
    # Updatable top-level fields
    for field in ("name", "slug", "owner_phone", "whatsapp_phone_number_id", "whatsapp_access_token", "google_review_link"):
        if field in data:
            rest[field] = data[field]
    # Updatable settings fields
    if "settings" in data:
        current_settings = rest.get("settings", {})
        for key in ("description", "menu", "hours", "address", "phone", "tone", "languages", "special_info", "allergens_policy", "booking_link", "twilio_number"):
            if key in data["settings"]:
                current_settings[key] = data["settings"][key]
        rest["settings"] = current_settings
    # Update pid mapping if whatsapp_phone_number_id changed
    if "whatsapp_phone_number_id" in data:
        # Remove old mapping
        old_pids = [k for k, v in pid_to_restaurant.items() if v == rid]
        for k in old_pids:
            pid_to_restaurant.pop(k, None)
        if data["whatsapp_phone_number_id"]:
            pid_to_restaurant[data["whatsapp_phone_number_id"]] = rid
    # Update phone mapping if phone changed
    if "settings" in data and "phone" in data["settings"]:
        new_phone = normalize_phone(data["settings"]["phone"])
        if new_phone:
            phone_to_restaurant[new_phone] = rid
    # Persist to DB
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE restaurants SET name=$1, slug=$2, owner_phone=$3, 
                       whatsapp_phone_number_id=$4, whatsapp_access_token=$5, 
                       google_review_link=$6, settings=$7::jsonb
                       WHERE id=$8::uuid""",
                    rest.get("name", ""), rest.get("slug", ""), rest.get("owner_phone", ""),
                    rest.get("whatsapp_phone_number_id", ""), rest.get("whatsapp_access_token", ""),
                    rest.get("google_review_link", ""), json.dumps(rest.get("settings", {})),
                    rid
                )
        except Exception as e:
            logger.error(f"Admin update restaurant error: {e}")
            return {"error": str(e)}
    bump_version(rid)
    logger.info(f"Admin: updated restaurant {rest.get('name')} ({rid[:8]}...)")
    return {"status": "ok", "restaurant": sanitize_restaurant(rest)}


@app.get("/api/admin/restaurant/{rid}/bookings")
async def admin_restaurant_bookings(rid: str, request: Request):
    """Get all bookings for a restaurant."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_bookings = bookings.get(rid, [])
    return {"bookings": rid_bookings, "total": len(rid_bookings)}


@app.get("/api/admin/restaurant/{rid}/contacts")
async def admin_restaurant_contacts(rid: str, request: Request):
    """Get all contacts for a restaurant."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_contacts = contacts.get(rid, {})
    return {"contacts": list(rid_contacts.values()), "total": len(rid_contacts)}


@app.get("/api/admin/restaurant/{rid}/conversations")
async def admin_restaurant_conversations(rid: str, request: Request):
    """Get all conversations for a restaurant."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_convs = {k.split(":", 1)[1]: v for k, v in conversations.items() if k.startswith(rid + ":")}
    return {"conversations": rid_convs, "total": len(rid_convs)}


@app.delete("/api/admin/restaurant/{rid}/booking/{booking_id}")
async def admin_delete_booking(rid: str, booking_id: str, request: Request):
    """Delete a specific booking."""
    if not verify_admin(request):
        return Response(status_code=401)
    rid_bookings = bookings.get(rid, [])
    booking = next((b for b in rid_bookings if b.get("id") == booking_id), None)
    if not booking:
        return {"error": "Booking not found"}
    rid_bookings.remove(booking)
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_bookings WHERE id = $1 AND restaurant_id = $2::uuid", booking_id, rid)
        except Exception as e:
            logger.error(f"Admin delete booking error: {e}")
    bump_version(rid)
    return {"status": "ok", "deleted": booking_id}


@app.delete("/api/admin/restaurant/{rid}")
async def admin_delete_restaurant(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    # Remove from memory
    restaurants_cache.pop(rid, None)
    bookings.pop(rid, None)
    floor_tables.pop(rid, None)
    table_slots.pop(rid, None)
    review_queue.pop(rid, None)
    contacts.pop(rid, None)
    stats.pop(rid, None)
    daily_stats_history.pop(rid, None)
    waitlist.pop(rid, None)
    restaurant_status.pop(rid, None)
    data_versions.pop(rid, None)
    # Remove conversations
    keys_to_remove = [k for k in conversations if k.startswith(rid)]
    for k in keys_to_remove:
        conversations.pop(k, None)
    # Remove from pid mapping
    pid_keys = [k for k, v in pid_to_restaurant.items() if v == rid]
    for k in pid_keys:
        pid_to_restaurant.pop(k, None)
    # Remove from DB
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_waitlist WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_daily_stats WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_restaurant_status WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_review_queue WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_conversations WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_contacts WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM users WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM restaurants WHERE id = $1::uuid", rid)
            logger.info(f"Admin: deleted restaurant {rest.get('name')} ({rid[:8]}...)")
        except Exception as e:
            logger.error(f"Admin delete error: {e}")
    return {"status": "ok", "deleted": rest.get("name", "")}


ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuestScale — Super Admin</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' fill='none'%3E%3Ccircle cx='10' cy='10' r='4' fill='%232D7DD2'/%3E%3Ccircle cx='22' cy='10' r='4' fill='%234ECDC4'/%3E%3Ccircle cx='16' cy='22' r='4' fill='%234ECDC4'/%3E%3Cline x1='13' y1='11' x2='19' y2='11' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='11' y1='13' x2='15' y2='19' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='21' y1='13' x2='17' y2='19' stroke='%234ECDC4' stroke-width='2'/%3E%3C/svg%3E">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#F4F5F9;--card:#FFF;--t:#111827;--ts:#6B7280;--tm:#9CA3AF;--b:#E5E7EB;--bl:#F3F4F6;
  --ac:#2D7DD2;--ac2:#4ECDC4;--acg:linear-gradient(135deg,#2D7DD2,#4ECDC4);
  --ok:#4ECDC4;--okb:#E6FAF8;--wa:#F59E0B;--wab:#FFFBEB;--da:#EF4444;--dab:#FEF2F2;
  --f:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --shadow:0 1px 3px rgba(0,0,0,.06);--shadow-md:0 4px 6px rgba(0,0,0,.05);
  --radius:12px;
}
body{font-family:var(--f);background:var(--bg);color:var(--t);min-height:100vh;-webkit-font-smoothing:antialiased}

/* Login */
.lo{position:fixed;inset:0;background:#0F1117;display:flex;align-items:center;justify-content:center;z-index:100}
.lbox{text-align:center;width:380px}
.l-logo{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:8px}
.l-icon{width:40px;height:40px;background:#1A1D27;border-radius:10px;display:flex;align-items:center;justify-content:center}
.l-icon svg{width:28px;height:28px}
.lwm{font-size:28px;font-weight:800;color:#fff;letter-spacing:-.03em}
.lsub{font-size:11px;color:#6B7280;letter-spacing:.12em;margin-bottom:36px;text-transform:uppercase}
.lcd{background:#1A1D27;border-radius:16px;padding:28px 24px;border:1px solid #252836}
.linp{width:100%;padding:13px 16px;border-radius:10px;background:#0F1117;border:1.5px solid #374151;font-size:14px;color:#F9FAFB;outline:none;font-family:var(--f);transition:border .2s;margin-bottom:8px}
.linp::placeholder{color:#6B7280}
.linp:focus{border-color:var(--ac)}
.lbtn{width:100%;padding:13px;border-radius:10px;border:none;background:var(--acg);color:#fff;font-size:14px;font-weight:700;cursor:pointer;font-family:var(--f);margin-top:8px;transition:opacity .2s}
.lbtn:hover{opacity:.9}
.lerr{color:var(--da);font-size:13px;margin-bottom:14px;display:none;background:#FEF2F220;padding:10px 14px;border-radius:10px;border:1px solid #EF444430}

/* Layout */
.app{display:none}
.app.v{display:flex;min-height:100vh}
.sidebar{width:240px;background:#0F1117;color:#fff;padding:20px 16px;display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:50}
.sb-logo{display:flex;align-items:center;gap:8px;margin-bottom:24px;padding:0 4px}
.sb-logo svg{width:28px;height:28px}
.sb-logo span{font-size:16px;font-weight:800;letter-spacing:-.02em}
.sb-nav{flex:1}
.sb-item{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;color:#9CA3AF;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;margin-bottom:2px}
.sb-item:hover{background:#1A1D27;color:#fff}
.sb-item.active{background:#1A1D27;color:#fff}
.sb-item svg{width:18px;height:18px;opacity:.7}
.sb-item.active svg{opacity:1}
.sb-footer{font-size:11px;color:#4B5563;padding:8px 4px}
.main{margin-left:240px;flex:1;padding:24px 32px;max-width:1200px}

/* Topbar */
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h1{font-size:20px;font-weight:800;letter-spacing:-.02em}
.topbar-actions{display:flex;gap:8px;align-items:center}

/* Components */
.btn{padding:8px 16px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:var(--f);transition:all .15s}
.btn-sm{padding:5px 10px;font-size:11px;border-radius:6px}
.btn-xs{padding:3px 8px;font-size:10px;border-radius:5px}
.btn-primary{background:var(--acg);color:#fff}
.btn-primary:hover{opacity:.9}
.btn-ghost{background:transparent;color:var(--ts);border:1px solid var(--b)}
.btn-ghost:hover{background:var(--bl);color:var(--t)}
.btn-danger{background:var(--dab);color:var(--da);border:1px solid #FECACA}
.btn-danger:hover{background:#FEE2E2}
.btn-ok{background:var(--okb);color:#0D9488;border:1px solid #99F6E4}
.badge{display:inline-flex;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.badge-ok{background:var(--okb);color:#0D9488}
.badge-wa{background:var(--wab);color:#D97706}
.badge-da{background:var(--dab);color:#DC2626}
.badge-ac{background:#EBF4FF;color:#2563EB}

/* KPIs */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--card);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);border:1px solid var(--b)}
.kpi-val{font-size:26px;font-weight:800;letter-spacing:-.03em}
.kpi-label{font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}

/* Cards & Tables */
.card{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--b);overflow:hidden;margin-bottom:16px}
.card-h{padding:16px 20px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}
.card-h h2{font-size:14px;font-weight:700}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:8px 14px;font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--b);background:#FAFBFC}
td{padding:10px 14px;font-size:12px;border-bottom:1px solid #F3F4F6;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#F9FAFB}

/* Modal */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}
.modal.v{display:flex}
.modal-box{background:var(--card);border-radius:16px;max-width:700px;width:95%;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-md)}
.modal-h{padding:18px 24px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}
.modal-h h2{font-size:16px;font-weight:700}
.modal-close{background:none;border:none;font-size:22px;cursor:pointer;color:var(--tm);padding:4px 8px;border-radius:6px}
.modal-close:hover{background:var(--bl);color:var(--t)}
.modal-body{padding:20px 24px}
.modal-footer{padding:16px 24px;border-top:1px solid var(--b);display:flex;justify-content:flex-end;gap:8px}

/* Form */
.form-group{margin-bottom:14px}
.form-group label{display:block;font-size:11px;font-weight:700;color:var(--ts);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:9px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);color:var(--t);outline:none;transition:border .15s}
.form-group input:focus,.form-group textarea:focus{border-color:var(--ac)}
.form-group textarea{resize:vertical;min-height:60px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}

/* Tabs */
.tabs{display:flex;gap:0;border-bottom:2px solid var(--b);margin-bottom:16px}
.tab{padding:10px 18px;font-size:12px;font-weight:700;color:var(--tm);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}
.tab:hover{color:var(--t)}
.tab.active{color:var(--ac);border-color:var(--ac)}

/* Views */
.view{display:none}
.view.v{display:block}

/* Toast */
.toast{position:fixed;bottom:20px;right:20px;background:#1F2937;color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;z-index:300;opacity:0;transform:translateY(10px);transition:all .3s}
.toast.v{opacity:1;transform:translateY(0)}

/* Responsive */
@media(max-width:768px){
  .sidebar{display:none}
  .main{margin-left:0}
  .form-row{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- LOGIN -->
<div class="lo" id="loginOverlay">
<div class="lbox">
  <div class="l-logo"><div class="l-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><span class="lwm">GuestScale</span></div>
  <div class="lsub">Super Admin</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Clé invalide</div>
    <input class="linp" id="secretInput" type="password" placeholder="Clé admin" autofocus>
    <button class="lbtn" id="loginBtn">Accéder</button>
  </div>
</div>
</div>

<!-- APP -->
<div class="app" id="app">

<!-- Sidebar -->
<div class="sidebar">
  <div class="sb-logo">
    <svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
    <span>GuestScale</span>
  </div>
  <div class="sb-nav">
    <div class="sb-item active" data-nav="dashboard"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>Dashboard</div>
    <div class="sb-item" data-nav="restaurants"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v18H3z"/><path d="M9 3v18M3 9h18"/></svg>Restaurants</div>
    <div class="sb-item" data-nav="bookings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>Réservations</div>
    <div class="sb-item" data-nav="conversations"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>Conversations</div>
  </div>
  <div class="sb-footer">GuestScale Admin v5.0</div>
</div>

<!-- Main content -->
<div class="main">

<!-- Dashboard View -->
<div class="view v" id="view-dashboard">
  <div class="topbar"><h1>Dashboard</h1><div class="topbar-actions"><button class="btn btn-ghost btn-sm" onclick="loadAll()">Actualiser</button></div></div>
  <div class="kpis" id="kpis"></div>
  <div id="chartsArea"></div>
  <div class="card">
    <div class="card-h"><h2>Restaurants</h2></div>
    <table><thead><tr><th>Restaurant</th><th>Statut</th><th>Réservations</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th><th>Actions</th></tr></thead><tbody id="dashTbody"></tbody></table>
  </div>
</div>

<!-- Restaurants View -->
<div class="view" id="view-restaurants">
  <div class="topbar"><h1>Restaurants</h1></div>
  <div class="card">
    <table><thead><tr><th>Restaurant</th><th>Slug</th><th>Statut</th><th>Téléphone</th><th>WhatsApp</th><th>Tables</th><th>Actions</th></tr></thead><tbody id="restoTbody"></tbody></table>
  </div>
</div>

<!-- Bookings View -->
<div class="view" id="view-bookings">
  <div class="topbar"><h1>Réservations</h1><div class="topbar-actions"><select id="bookingRestoFilter" class="linp" style="width:200px;background:#fff;color:var(--t);border-color:var(--b);padding:6px 10px;font-size:12px"></select></div></div>
  <div class="card"><table><thead><tr><th>ID</th><th>Client</th><th>Date</th><th>Heure</th><th>Couverts</th><th>Table</th><th>Statut</th><th>Source</th><th>Actions</th></tr></thead><tbody id="bookingsTbody"></tbody></table></div>
</div>

<!-- Conversations View -->
<div class="view" id="view-conversations">
  <div class="topbar"><h1>Conversations</h1><div class="topbar-actions"><select id="convRestoFilter" class="linp" style="width:200px;background:#fff;color:var(--t);border-color:var(--b);padding:6px 10px;font-size:12px"></select></div></div>
  <div id="convList"></div>
</div>

</div><!-- /main -->
</div><!-- /app -->

<!-- Edit Restaurant Modal -->
<div class="modal" id="editModal">
<div class="modal-box">
  <div class="modal-h"><h2 id="editTitle">Modifier le restaurant</h2><button class="modal-close" onclick="closeEdit()">&times;</button></div>
  <div class="modal-body">
    <div class="tabs" id="editTabs">
      <div class="tab active" data-tab="general">Général</div>
      <div class="tab" data-tab="settings">Infos & Menu</div>
      <div class="tab" data-tab="whatsapp">WhatsApp & Twilio</div>
    </div>
    <div id="editTabContent"></div>
  </div>
  <div class="modal-footer">
    <button class="btn btn-ghost" onclick="closeEdit()">Annuler</button>
    <button class="btn btn-primary" onclick="saveEdit()">Enregistrer</button>
  </div>
</div>
</div>

<!-- Detail Modal -->
<div class="modal" id="detailModal">
<div class="modal-box" style="max-width:800px">
  <div class="modal-h"><h2 id="detailTitle">Détails</h2><button class="modal-close" onclick="closeDetail()">&times;</button></div>
  <div class="modal-body" id="detailContent"></div>
</div>
</div>

<!-- Confirm Dialog -->
<div class="modal" id="confirmDialog">
<div class="modal-box" style="max-width:400px">
  <div class="modal-h"><h2 id="confirmTitle">Confirmer</h2><button class="modal-close" onclick="closeConfirm()">&times;</button></div>
  <div class="modal-body"><p id="confirmText"></p></div>
  <div class="modal-footer"><button class="btn btn-ghost" onclick="closeConfirm()">Annuler</button><button class="btn btn-danger" id="confirmOk">Confirmer</button></div>
</div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
var secret='';
var restaurants=[];
var currentEditRid='';
var currentEditData={};
var currentTab='general';

// ===== AUTH =====
document.getElementById('loginBtn').onclick=doLogin;
document.getElementById('secretInput').onkeydown=function(e){if(e.key==='Enter')doLogin()};
function doLogin(){
  secret=document.getElementById('secretInput').value.trim();
  if(!secret){document.getElementById('loginError').style.display='block';return}
  apiFetch('/api/admin/stats').then(function(r){
    if(r.status===401){document.getElementById('loginError').style.display='block';secret='';return}
    return r.json();
  }).then(function(d){
    if(!d)return;
    document.getElementById('loginOverlay').style.display='none';
    document.getElementById('app').classList.add('v');
    loadAll();setInterval(loadAll,15000);
  }).catch(function(){document.getElementById('loginError').style.display='block';secret=''});
}
function apiFetch(url,opts){
  opts=opts||{};
  var sep=url.indexOf('?')>-1?'&':'?';
  return fetch(url+sep+'secret='+encodeURIComponent(secret),opts);
}

// ===== NAV =====
document.querySelectorAll('.sb-item[data-nav]').forEach(function(el){
  el.onclick=function(){
    document.querySelectorAll('.sb-item').forEach(function(e){e.classList.remove('active')});
    el.classList.add('active');
    var view=el.getAttribute('data-nav');
    document.querySelectorAll('.view').forEach(function(v){v.classList.remove('v')});
    document.getElementById('view-'+view).classList.add('v');
    if(view==='bookings')loadBookings();
    if(view==='conversations')loadConversations();
  }
});

// ===== DATA LOADING =====
function loadAll(){
  apiFetch('/api/admin/stats').then(function(r){return r.json()}).then(renderKPIs);
  apiFetch('/api/admin/restaurants').then(function(r){return r.json()}).then(function(d){
    restaurants=d.restaurants||[];
    renderDashTable();
    renderRestoTable();
    renderFilters();
    // Auto-load bookings and conversations data
    if(document.getElementById('view-bookings').classList.contains('v'))loadBookings();
    if(document.getElementById('view-conversations').classList.contains('v'))loadConversations();
  });
}

function renderKPIs(d){
  var h='';
  h+=kpi(d.mrr+'\u20ac','MRR','var(--ok)','mrr');
  h+=kpi(d.arr+'\u20ac','ARR','var(--ok)','arr');
  h+=kpi(d.active_count,'Actifs','var(--ok)','actifs');
  h+=kpi(d.trial_count,'En essai','var(--wa)','trial');
  h+=kpi(d.conversion_rate+'%','Conversion','var(--ac)','conversion');
  h+=kpi(d.churn_rate+'%','Churn','var(--da)','churn');
  h+=kpi(d.total_bookings,'Resas','var(--ac)','resas');
  h+=kpi(d.whatsapp_connected,'WhatsApp','#25D366','whatsapp');
  document.getElementById('kpis').innerHTML=h;
  lastStatsData=d;
  renderCharts(d);
}

function renderCharts(d){
  var c=document.getElementById('chartsArea');
  if(!c)return;
  var h='';
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Revenue</h2></div><div style="padding:16px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;font-size:13px">';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">MRR</div><div style="font-size:22px;font-weight:800;color:var(--ok)">'+d.mrr+'\u20ac</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">ARR</div><div style="font-size:22px;font-weight:800;color:var(--ok)">'+d.arr+'\u20ac</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">MRR potentiel</div><div style="font-size:22px;font-weight:800;color:var(--wa)">'+d.potential_mrr+'\u20ac</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Prix/mois</div><div style="font-size:22px;font-weight:800;color:var(--ts)">'+d.price_per_month+'\u20ac</div></div>';
  h+='</div></div>';
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Funnel</h2></div><div style="padding:16px;display:grid;grid-template-columns:repeat(5,1fr);gap:8px;text-align:center">';
  h+=fs(d.total_restaurants,'Total','var(--ac)');
  h+=fs(d.trial_count,'Trial','var(--wa)');
  h+=fs(d.active_count,'Actifs','var(--ok)');
  h+=fs(d.suspended_count||0,'Suspendus','var(--da)');
  h+=fs(d.cancelled_count||0,'Churned','#6B7280');
  h+='</div></div>';
  if(d.bookings_timeline&&d.bookings_timeline.length){
    h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Reservations (30j)</h2></div><div style="padding:16px">'+mbc(d.bookings_timeline,'count','var(--ac)')+'</div></div>';
  }
  if(d.messages_timeline&&d.messages_timeline.length){
    h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Messages IA (30j)</h2></div><div style="padding:16px">'+mbc(d.messages_timeline,'messages','var(--ok)')+'</div></div>';
  }
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Usage</h2></div><div style="padding:16px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;font-size:13px">';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Messages total</div><div style="font-size:20px;font-weight:800">'+d.total_messages_alltime+'</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Contacts</div><div style="font-size:20px;font-weight:800">'+d.total_contacts+'</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Conversations</div><div style="font-size:20px;font-weight:800">'+d.total_conversations+'</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Moy. resas/resto</div><div style="font-size:20px;font-weight:800">'+d.avg_bookings_per_resto+'</div></div>';
  h+='</div></div>';
  if(d.restaurant_performance&&d.restaurant_performance.length){
    h+='<div class="card"><div class="card-h"><h2>Performance par restaurant</h2></div>';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    d.restaurant_performance.forEach(function(rp){
      var st=rp.status==='active'?'<span class="badge badge-ok">Actif</span>':rp.status==='trial'?'<span class="badge badge-wa">Essai</span>':'<span class="badge badge-da">'+rp.status+'</span>';
      var wa=rp.whatsapp?'<span style="color:var(--ok)">OK</span>':'--';
      h+='<tr><td style="font-weight:600">'+esc(rp.name)+'</td><td>'+st+'</td><td>'+rp.bookings+'</td><td>'+rp.contacts+'</td><td>'+rp.messages+'</td><td>'+wa+'</td></tr>';
    });
    h+='</tbody></table></div>';
  }
  c.innerHTML=h;
}
function fs(v,l,c){return '<div><div style="font-size:28px;font-weight:800;color:'+c+'">'+v+'</div><div style="font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase">'+l+'</div></div>'}
function mbc(data,field,color){
  if(!data||!data.length)return '';
  var max=Math.max.apply(null,data.map(function(d){return d[field]||0}));
  if(max===0)max=1;
  var bw=Math.max(4,Math.floor(600/data.length)-2);
  var h='<div style="display:flex;align-items:flex-end;gap:2px;height:80px">';
  data.forEach(function(d){var val=d[field]||0;var pct=Math.max(2,Math.round(val/max*100));h+='<div title="'+esc(d.date)+': '+val+'" style="width:'+bw+'px;height:'+pct+'%;background:'+color+';border-radius:3px 3px 0 0;opacity:0.8"></div>'});
  h+='</div><div style="display:flex;justify-content:space-between;font-size:9px;color:var(--tm);margin-top:4px"><span>'+esc(data[0].date||'')+'</span><span>'+esc(data[data.length-1].date||'')+'</span></div>';
  return h;
}

function kpi(v,l,c,key){return '<div class="kpi" data-kpi="'+(key||'')+'" style="cursor:pointer;transition:transform .1s"><div class="kpi-val" style="color:'+c+'">'+v+'</div><div class="kpi-label">'+l+'</div></div>'}

// ===== DASHBOARD TABLE =====
function renderDashTable(){
  var h='';
  restaurants.forEach(function(r){
    var st=statusBadge(r.status);
    var wa=r.whatsapp_connected?'<span style="color:var(--ok)">✓ Connecté</span>':'<span style="color:var(--tm)">—</span>';
    h+='<tr>';
    h+='<td><div style="font-weight:700">'+esc(r.name)+'</div><div style="font-size:11px;color:var(--tm)">/'+esc(r.slug)+'</div></td>';
    h+='<td>'+st+'</td>';
    h+='<td><strong>'+r.total_bookings+'</strong> <span style="font-size:10px;color:var(--tm)">('+r.bookings_today+' auj.)</span></td>';
    h+='<td>'+r.total_contacts+'</td>';
    h+='<td>'+r.messages_today+'</td>';
    h+='<td>'+wa+'</td>';
    h+='<td><div style="display:flex;gap:4px">';
    h+='<button class="btn btn-ghost btn-xs" data-action="detail" data-id="'+r.id+'">Détails</button>';
    h+='<button class="btn btn-ghost btn-xs" data-action="edit" data-id="'+r.id+'">Modifier</button>';
    h+='<button class="btn btn-danger btn-xs" data-action="delete" data-id="'+r.id+'" data-name="'+esc(r.name)+'">Suppr.</button>';
    h+='</div></td>';
    h+='</tr>';
  });
  if(!restaurants.length) h='<tr><td colspan="7" style="text-align:center;padding:30px;color:var(--tm)">Aucun restaurant</td></tr>';
  document.getElementById('dashTbody').innerHTML=h;
}

// ===== RESTAURANTS TABLE =====
function renderRestoTable(){
  var h='';
  restaurants.forEach(function(r){
    var st=statusBadge(r.status);
    var wa=r.whatsapp_connected?'<span style="color:var(--ok)">✓</span>':'<span style="color:var(--tm)">—</span>';
    h+='<tr>';
    h+='<td style="font-weight:700">'+esc(r.name)+'</td>';
    h+='<td style="font-size:11px;color:var(--tm)">/'+esc(r.slug)+'</td>';
    h+='<td>'+st+'</td>';
    h+='<td style="font-size:11px">'+esc(r.owner_phone||'—')+'</td>';
    h+='<td>'+wa+'</td>';
    h+='<td>'+r.tables_count+'</td>';
    h+='<td><div style="display:flex;gap:4px">';
    h+='<button class="btn btn-ghost btn-xs" data-action="edit" data-id="'+r.id+'">Modifier</button>';
    h+='<button class="btn btn-ok btn-xs" data-action="setstatus" data-id="'+r.id+'" data-newstatus="active">Activer</button>';
    h+='<button class="btn btn-danger btn-xs" data-action="setstatus" data-id="'+r.id+'" data-newstatus="suspended">Suspendre</button>';
    h+='</div></td>';
    h+='</tr>';
  });
  document.getElementById('restoTbody').innerHTML=h;
}

function statusBadge(s){
  if(s==='trial') return '<span class="badge badge-wa">Essai</span>';
  if(s==='active') return '<span class="badge badge-ok">Actif</span>';
  if(s==='suspended') return '<span class="badge badge-da">Suspendu</span>';
  return '<span class="badge badge-ac">'+s+'</span>';
}

// ===== FILTERS =====
function renderFilters(){
  var opts='<option value="">Tous les restaurants</option>';
  restaurants.forEach(function(r){opts+='<option value="'+r.id+'">'+esc(r.name)+'</option>'});
  var bf=document.getElementById('bookingRestoFilter');
  var cf=document.getElementById('convRestoFilter');
  if(bf)bf.innerHTML=opts;
  if(cf)cf.innerHTML=opts;
}

// ===== EDIT RESTAURANT =====
function openEdit(rid){
  currentEditRid=rid;
  currentTab='general';
  apiFetch('/api/admin/restaurant/'+rid).then(function(r){return r.json()}).then(function(d){
    if(d.error){showToast('Erreur: '+d.error);return}
    currentEditData=d;
    document.getElementById('editTitle').textContent='Modifier — '+d.restaurant.name;
    renderEditTab();
    document.getElementById('editModal').classList.add('v');
  });
}
function closeEdit(){document.getElementById('editModal').classList.remove('v');currentEditRid=''}

document.getElementById('editTabs').onclick=function(e){
  var tab=e.target.getAttribute('data-tab');
  if(!tab)return;
  currentTab=tab;
  document.querySelectorAll('#editTabs .tab').forEach(function(t){t.classList.remove('active')});
  e.target.classList.add('active');
  renderEditTab();
};

function renderEditTab(){
  var r=currentEditData.restaurant;
  var s=r.settings||{};
  var u=currentEditData.user;
  var h='';
  if(currentTab==='general'){
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Nom</label><input id="ef-name" value="'+esc(r.name||'')+'"></div>';
    h+='<div class="form-group"><label>Slug</label><input id="ef-slug" value="'+esc(r.slug||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Téléphone propriétaire</label><input id="ef-owner_phone" value="'+esc(r.owner_phone||'')+'"></div>';
    h+='<div class="form-group"><label>Google Review Link</label><input id="ef-google_review_link" value="'+esc(r.google_review_link||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Statut</label><select id="ef-status"><option value="trial"'+(r.status==='trial'?' selected':'')+'>Essai</option><option value="active"'+(r.status==='active'?' selected':'')+'>Actif</option><option value="suspended"'+(r.status==='suspended'?' selected':'')+'>Suspendu</option><option value="cancelled"'+(r.status==='cancelled'?' selected':'')+'>Annulé</option></select></div>';
    h+='<div class="form-group"><label>Fin essai</label><input type="text" disabled value="'+(r.trial_ends_at?new Date(r.trial_ends_at).toLocaleDateString('fr-FR'):'—')+'"></div>';
    h+='</div>';
    if(u){
      h+='<div style="margin-top:16px;padding:14px;background:var(--bl);border-radius:8px">';
      h+='<div style="font-size:11px;font-weight:700;color:var(--ts);text-transform:uppercase;margin-bottom:6px">Propriétaire</div>';
      h+='<div style="font-size:13px"><strong>'+esc(u.first_name+' '+u.last_name)+'</strong> · '+esc(u.email)+'</div>';
      h+='</div>';
    }
  } else if(currentTab==='settings'){
    h+='<div class="form-group"><label>Description</label><textarea id="ef-description" rows="3">'+esc(s.description||'')+'</textarea></div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Adresse</label><input id="ef-address" value="'+esc(s.address||'')+'"></div>';
    h+='<div class="form-group"><label>Téléphone restaurant</label><input id="ef-phone" value="'+esc(s.phone||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-group"><label>Horaires</label><textarea id="ef-hours" rows="2">'+esc(s.hours||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Menu</label><textarea id="ef-menu" rows="6">'+esc(s.menu||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Ton IA</label><textarea id="ef-tone" rows="2">'+esc(s.tone||'')+'</textarea></div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Langues</label><input id="ef-languages" value="'+esc(s.languages||'')+'"></div>';
    h+='<div class="form-group"><label>Lien de réservation</label><input id="ef-booking_link" value="'+esc(s.booking_link||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-group"><label>Infos spéciales</label><textarea id="ef-special_info" rows="2">'+esc(s.special_info||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Politique allergènes</label><input id="ef-allergens_policy" value="'+esc(s.allergens_policy||'')+'"></div>';
  } else if(currentTab==='whatsapp'){
    h+='<div class="form-group"><label>WhatsApp Phone Number ID</label><input id="ef-whatsapp_phone_number_id" value="'+esc(r.whatsapp_phone_number_id||'')+'"></div>';
    h+='<div class="form-group"><label>WhatsApp Access Token</label><textarea id="ef-whatsapp_access_token" rows="2" style="font-size:11px;word-break:break-all">'+esc(r.whatsapp_access_token||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Numéro Twilio</label><input id="ef-twilio_number" value="'+esc(s.twilio_number||'')+'"></div>';
    h+='<div style="margin-top:16px;padding:14px;background:var(--bl);border-radius:8px">';
    h+='<div style="font-size:11px;font-weight:700;color:var(--ts);margin-bottom:6px">STATUT WHATSAPP</div>';
    h+='<div style="font-size:13px">'+(r.whatsapp_phone_number_id?'<span style="color:var(--ok)">✓ Connecté</span> — Phone ID: '+esc(r.whatsapp_phone_number_id):'<span style="color:var(--da)">✗ Non connecté</span>')+'</div>';
    h+='</div>';
  }
  document.getElementById('editTabContent').innerHTML=h;
}

function saveEdit(){
  var r=currentEditData.restaurant;
  var payload={};
  if(currentTab==='general'){
    payload.name=gv('ef-name');
    payload.slug=gv('ef-slug');
    payload.owner_phone=gv('ef-owner_phone');
    payload.google_review_link=gv('ef-google_review_link');
    var newStatus=gv('ef-status');
    if(newStatus!==r.status){
      apiFetch('/api/admin/restaurant/'+currentEditRid+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:newStatus})});
    }
  } else if(currentTab==='settings'){
    payload.settings={
      description:gv('ef-description'),address:gv('ef-address'),phone:gv('ef-phone'),
      hours:gv('ef-hours'),menu:gv('ef-menu'),tone:gv('ef-tone'),
      languages:gv('ef-languages'),booking_link:gv('ef-booking_link'),
      special_info:gv('ef-special_info'),allergens_policy:gv('ef-allergens_policy')
    };
  } else if(currentTab==='whatsapp'){
    payload.whatsapp_phone_number_id=gv('ef-whatsapp_phone_number_id');
    payload.whatsapp_access_token=gv('ef-whatsapp_access_token');
    payload.settings={twilio_number:gv('ef-twilio_number')};
  }
  apiFetch('/api/admin/restaurant/'+currentEditRid,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Restaurant mis à jour');closeEdit();loadAll()}
    else showToast('Erreur: '+(d.error||''));
  });
}

// ===== DETAIL =====
function openDetail(rid){
  apiFetch('/api/admin/restaurant/'+rid).then(function(r){return r.json()}).then(function(d){
    if(d.error){showToast(d.error);return}
    var r=d.restaurant;var s=r.settings||{};var u=d.user;
    var trial=r.trial_ends_at?new Date(r.trial_ends_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'}):'—';
    var created=r.created_at?new Date(r.created_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'}):'—';
    var h='';
    // KPIs
    h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px">';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--ac)">'+d.bookings_count+'</div><div class="kpi-label">Réservations</div></div>';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--ok)">'+d.contacts_count+'</div><div class="kpi-label">Contacts</div></div>';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--wa)">'+(d.stats_today.messages_today||0)+'</div><div class="kpi-label">Messages auj.</div></div>';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--ts)">'+(d.tables||[]).length+'</div><div class="kpi-label">Tables</div></div>';
    h+='</div>';
    // Info
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">';
    h+=drow('Nom',r.name);h+=drow('Slug','/'+r.slug);h+=drow('Statut',r.status);h+=drow('Créé le',created);
    h+=drow('Fin essai',trial);h+=drow('Adresse',s.address||'—');h+=drow('Téléphone',s.phone||'—');
    h+=drow('WhatsApp',r.whatsapp_phone_number_id?'Connecté':'Non');h+=drow('Google Review',r.google_review_link?'Oui':'Non');
    h+=drow('Menu',s.menu?'Oui ('+s.menu.length+' car.)':'Non');
    h+='</div>';
    if(u){
      h+='<div style="margin-top:16px;padding:12px;background:var(--bl);border-radius:8px;font-size:12px">';
      h+='<strong>Propriétaire :</strong> '+esc(u.first_name+' '+u.last_name)+' · '+esc(u.email);
      h+='</div>';
    }
    h+='<div style="margin-top:16px;display:flex;gap:8px">';
    h+='<button class="btn btn-primary btn-sm" data-action="editfromdetail" data-id="'+rid+'">Modifier</button>';
    h+='<a href="/dashboard/'+r.slug+'" target="_blank" class="btn btn-ghost btn-sm" style="text-decoration:none">Ouvrir le dashboard</a>';
    h+='</div>';
    document.getElementById('detailTitle').textContent=r.name;
    document.getElementById('detailContent').innerHTML=h;
    document.getElementById('detailModal').classList.add('v');
  });
}
function closeDetail(){document.getElementById('detailModal').classList.remove('v')}
function drow(k,v){return '<div style="padding:6px 0;border-bottom:1px solid var(--b)"><span style="color:var(--tm);font-weight:600">'+k+'</span><br><span>'+esc(String(v||'—'))+'</span></div>'}

// ===== BOOKINGS =====
function loadBookings(){
  var sel=document.getElementById('bookingRestoFilter');
  var rid=sel.value;
  if(!rid && restaurants.length){rid=restaurants[0].id;sel.value=rid}
  if(!rid)return;
  apiFetch('/api/admin/restaurant/'+rid+'/bookings').then(function(r){return r.json()}).then(function(d){
    var bks=(d.bookings||[]).slice().reverse();
    var h='';
    bks.forEach(function(b){
      var stClass=b.status==='confirmed'?'badge-ok':b.status==='cancelled'?'badge-da':'badge-wa';
      h+='<tr>';
      h+='<td style="font-size:11px;color:var(--tm)">'+esc(b.id||'')+'</td>';
      h+='<td><strong>'+esc(b.name||'')+'</strong><div style="font-size:10px;color:var(--tm)">'+esc(b.phone||'')+'</div></td>';
      h+='<td>'+esc(b.date||'')+'</td>';
      h+='<td>'+esc(b.booking_time||b.time||'—')+'</td>';
      h+='<td>'+esc(String(b.covers||''))+'</td>';
      h+='<td>'+esc(b.table||'—')+'</td>';
      h+='<td><span class="badge '+stClass+'">'+esc(b.status||'')+'</span></td>';
      h+='<td style="font-size:10px">'+esc(b.source||'')+'</td>';
      h+='<td><button class="btn btn-danger btn-xs" data-action="delbooking" data-rid="'+rid+'" data-bid="'+esc(b.id)+'">Suppr.</button></td>';
      h+='</tr>';
    });
    if(!bks.length) h='<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--tm)">Aucune réservation</td></tr>';
    document.getElementById('bookingsTbody').innerHTML=h;
  });
}
document.getElementById('bookingRestoFilter').onchange=loadBookings;

function deleteBooking(rid,bid){
  if(!confirm('Supprimer la réservation '+bid+' ?'))return;
  apiFetch('/api/admin/restaurant/'+rid+'/booking/'+bid,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Réservation supprimée');loadBookings();loadAll()}
    else showToast('Erreur: '+(d.error||''));
  });
}

// ===== CONVERSATIONS =====
function loadConversations(){
  var sel=document.getElementById('convRestoFilter');
  var rid=sel.value;
  if(!rid && restaurants.length){rid=restaurants[0].id;sel.value=rid}
  if(!rid)return;
  apiFetch('/api/admin/restaurant/'+rid+'/conversations').then(function(r){return r.json()}).then(function(d){
    var convs=d.conversations||{};
    var keys=Object.keys(convs);
    var h='';
    if(!keys.length){h='<div style="text-align:center;padding:40px;color:var(--tm)">Aucune conversation</div>';document.getElementById('convList').innerHTML=h;return}
    keys.forEach(function(phone){
      var msgs=convs[phone];
      var last=msgs[msgs.length-1];
      h+='<div class="card conv-toggle" style="cursor:pointer">';
      h+='<div class="card-h"><h2>'+esc(phone)+'</h2><span style="font-size:11px;color:var(--tm)">'+msgs.length+' messages</span></div>';
      h+='<div class="conv-msgs" style="display:none;padding:16px;max-height:400px;overflow-y:auto">';
      msgs.forEach(function(m){
        var isUser=m.role==='user';
        var bg=isUser?'#EBF4FF':'#F0FDF4';
        var label=isUser?'Client':'IA';
        var time=m.timestamp?new Date(m.timestamp).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
        h+='<div style="margin-bottom:8px;padding:10px 14px;background:'+bg+';border-radius:10px">';
        h+='<div style="font-size:10px;font-weight:700;color:var(--tm);margin-bottom:4px">'+label+' · '+time+'</div>';
        h+='<div style="font-size:12px">'+esc(m.content)+'</div>';
        h+='</div>';
      });
      h+='</div></div>';
    });
    document.getElementById('convList').innerHTML=h;
  });
}
document.getElementById('convRestoFilter').onchange=loadConversations;

// ===== STATUS =====
function setStatus(rid,s){
  apiFetch('/api/admin/restaurant/'+rid+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Statut: '+s);loadAll()}
    else showToast('Erreur');
  });
}

// ===== DELETE =====
var pendingDeleteId='';
function confirmDelete(rid,name){
  pendingDeleteId=rid;
  document.getElementById('confirmTitle').textContent='Supprimer '+name+' ?';
  document.getElementById('confirmText').textContent='Action irréversible. Toutes les données seront supprimées.';
  document.getElementById('confirmDialog').classList.add('v');
}
function closeConfirm(){document.getElementById('confirmDialog').classList.remove('v');pendingDeleteId=''}
document.getElementById('confirmOk').onclick=function(){
  if(!pendingDeleteId)return;
  closeConfirm();
  apiFetch('/api/admin/restaurant/'+pendingDeleteId,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Restaurant supprimé');loadAll()}
    else showToast('Erreur');
    pendingDeleteId='';
  });
};

// ===== UTILS =====
function gv(id){var e=document.getElementById(id);return e?e.value:''}
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('v');setTimeout(function(){t.classList.remove('v')},3000)}

// Close modals on backdrop click
document.querySelectorAll('.modal').forEach(function(m){
  m.onclick=function(e){if(e.target===this)this.classList.remove('v')}
});

// ===== KPI CLICK DETAIL =====
var lastStatsData=null;
document.getElementById('kpis').addEventListener('click',function(e){
  var kpiEl=e.target.closest('[data-kpi]');
  if(!kpiEl||!kpiEl.getAttribute('data-kpi'))return;
  var key=kpiEl.getAttribute('data-kpi');
  if(lastStatsData)showKPIDetail(key,lastStatsData);
});

function showKPIDetail(key,d){
  var h='';var title='';
  var rp=d.restaurant_performance||[];
  
  if(key==='mrr'){
    title='MRR - Monthly Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.mrr+'\u20ac/mois</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Actifs</div><div style="font-size:20px;font-weight:800">'+d.active_count+'</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Prix unitaire</div><div style="font-size:20px;font-weight:800">'+d.price_per_month+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR potentiel</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+d.potential_mrr+'\u20ac</div></div>';
    h+='</div>';
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Revenus par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>MRR</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var mrr=r.status==='active'?d.price_per_month:0;
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td style="font-weight:700;color:'+(mrr>0?'var(--ok)':'var(--tm)')+'">'+mrr+'\u20ac</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='arr'){
    title='ARR - Annual Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.arr+'\u20ac/an</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR actuel</div><div style="font-size:20px;font-weight:800">'+d.mrr+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">ARR potentiel (si tous convertis)</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.potential_mrr*12)+'\u20ac</div></div>';
    h+='</div>';
    h+='<div style="padding:16px;background:var(--bl);border-radius:8px;margin-bottom:16px">';
    h+='<div style="font-size:12px;color:var(--ts)">Projection : si chaque mois vous ajoutez <strong>5 restaurants</strong>, ARR dans 12 mois :</div>';
    var projected=0;for(var m=1;m<=12;m++){projected+=(d.active_count+m*5)*d.price_per_month}
    h+='<div style="font-size:28px;font-weight:800;color:var(--ac);margin-top:4px">~'+Math.round(projected/1000)+'K\u20ac</div>';
    h+='</div>';
  }
  
  else if(key==='actifs'){
    title='Restaurants actifs';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='active'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.contacts+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
    if(!rp.filter(function(r){return r.status==='active'}).length) h='<p style="color:var(--tm);padding:20px;text-align:center">Aucun restaurant actif</p>';
  }
  
  else if(key==='trial'){
    title='Restaurants en essai';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='trial'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='conversion'){
    title='Taux de conversion';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.conversion_rate+'%</div>';
    h+='<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ac)">'+d.total_restaurants+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">INSCRITS</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--wa)">'+d.trial_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">EN ESSAI</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--okb);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ok)">'+d.active_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">ACTIFS</div></div>';
    h+='</div>';
  }
  
  else if(key==='churn'){
    title='Churn';
    h+='<div style="font-size:36px;font-weight:800;color:var(--da);margin-bottom:16px">'+d.churn_rate+'%</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Suspendus</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.suspended_count||0)+'</div></div>';
    h+='<div style="padding:12px;background:var(--dab);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Churned</div><div style="font-size:20px;font-weight:800;color:var(--da)">'+(d.cancelled_count||0)+'</div></div>';
    h+='</div>';
    var churned=rp.filter(function(r){return r.status==='cancelled'||r.status==='suspended'});
    if(churned.length){
      h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th></tr></thead><tbody>';
      churned.forEach(function(r){h+='<tr><td>'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td>'+r.bookings+'</td></tr>'});
      h+='</tbody></table>';
    } else {
      h+='<p style="color:var(--ok);text-align:center;padding:20px">Aucun churn ! </p>';
    }
  }
  
  else if(key==='resas'){
    title='Reservations';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.total_bookings+' total</div>';
    if(d.bookings_timeline&&d.bookings_timeline.length){
      h+='<div style="margin-bottom:16px">'+mbc(d.bookings_timeline,'count','var(--ac)')+'</div>';
    }
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Total</th><th>Auj.</th></tr></thead><tbody>';
    rp.sort(function(a,b){return b.bookings-a.bookings}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.bookings_today+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='whatsapp'){
    title='WhatsApp';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Messages</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var wa=r.whatsapp?'<span class="badge badge-ok">Connecte</span>':'<span class="badge badge-da">Non</span>';
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+wa+'</td><td>'+r.messages+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  document.getElementById('detailTitle').textContent=title;
  document.getElementById('detailContent').innerHTML=h;
  document.getElementById('detailModal').classList.add('v');
}

// ===== CONV TOGGLE =====
document.addEventListener('click',function(e){
  var card=e.target.closest('.conv-toggle');
  if(!card)return;
  var msgs=card.querySelector('.conv-msgs');
  if(msgs)msgs.style.display=msgs.style.display==='block'?'none':'block';
});

// ===== EVENT DELEGATION =====
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-action]');
  if(!t)return;
  var action=t.getAttribute('data-action');
  var id=t.getAttribute('data-id');
  if(action==='detail')openDetail(id);
  else if(action==='edit'){closeDetail();openEdit(id)}
  else if(action==='delete')confirmDelete(id,t.getAttribute('data-name'));
  else if(action==='setstatus')setStatus(id,t.getAttribute('data-status'));
  else if(action==='delbooking')deleteBooking(t.getAttribute('data-rid'),t.getAttribute('data-bid'));
});

// ===== KPI CLICK DETAIL =====
var lastStatsData=null;
document.getElementById('kpis').addEventListener('click',function(e){
  var kpiEl=e.target.closest('[data-kpi]');
  if(!kpiEl||!kpiEl.getAttribute('data-kpi'))return;
  var key=kpiEl.getAttribute('data-kpi');
  if(lastStatsData)showKPIDetail(key,lastStatsData);
});

function showKPIDetail(key,d){
  var h='';var title='';
  var rp=d.restaurant_performance||[];
  
  if(key==='mrr'){
    title='MRR - Monthly Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.mrr+'\u20ac/mois</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Actifs</div><div style="font-size:20px;font-weight:800">'+d.active_count+'</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Prix unitaire</div><div style="font-size:20px;font-weight:800">'+d.price_per_month+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR potentiel</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+d.potential_mrr+'\u20ac</div></div>';
    h+='</div>';
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Revenus par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>MRR</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var mrr=r.status==='active'?d.price_per_month:0;
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td style="font-weight:700;color:'+(mrr>0?'var(--ok)':'var(--tm)')+'">'+mrr+'\u20ac</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='arr'){
    title='ARR - Annual Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.arr+'\u20ac/an</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR actuel</div><div style="font-size:20px;font-weight:800">'+d.mrr+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">ARR potentiel (si tous convertis)</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.potential_mrr*12)+'\u20ac</div></div>';
    h+='</div>';
    h+='<div style="padding:16px;background:var(--bl);border-radius:8px;margin-bottom:16px">';
    h+='<div style="font-size:12px;color:var(--ts)">Projection : si chaque mois vous ajoutez <strong>5 restaurants</strong>, ARR dans 12 mois :</div>';
    var projected=0;for(var m=1;m<=12;m++){projected+=(d.active_count+m*5)*d.price_per_month}
    h+='<div style="font-size:28px;font-weight:800;color:var(--ac);margin-top:4px">~'+Math.round(projected/1000)+'K\u20ac</div>';
    h+='</div>';
  }
  
  else if(key==='actifs'){
    title='Restaurants actifs';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='active'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.contacts+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
    if(!rp.filter(function(r){return r.status==='active'}).length) h='<p style="color:var(--tm);padding:20px;text-align:center">Aucun restaurant actif</p>';
  }
  
  else if(key==='trial'){
    title='Restaurants en essai';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='trial'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='conversion'){
    title='Taux de conversion';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.conversion_rate+'%</div>';
    h+='<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ac)">'+d.total_restaurants+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">INSCRITS</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--wa)">'+d.trial_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">EN ESSAI</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--okb);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ok)">'+d.active_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">ACTIFS</div></div>';
    h+='</div>';
  }
  
  else if(key==='churn'){
    title='Churn';
    h+='<div style="font-size:36px;font-weight:800;color:var(--da);margin-bottom:16px">'+d.churn_rate+'%</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Suspendus</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.suspended_count||0)+'</div></div>';
    h+='<div style="padding:12px;background:var(--dab);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Churned</div><div style="font-size:20px;font-weight:800;color:var(--da)">'+(d.cancelled_count||0)+'</div></div>';
    h+='</div>';
    var churned=rp.filter(function(r){return r.status==='cancelled'||r.status==='suspended'});
    if(churned.length){
      h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th></tr></thead><tbody>';
      churned.forEach(function(r){h+='<tr><td>'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td>'+r.bookings+'</td></tr>'});
      h+='</tbody></table>';
    } else {
      h+='<p style="color:var(--ok);text-align:center;padding:20px">Aucun churn ! </p>';
    }
  }
  
  else if(key==='resas'){
    title='Reservations';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.total_bookings+' total</div>';
    if(d.bookings_timeline&&d.bookings_timeline.length){
      h+='<div style="margin-bottom:16px">'+mbc(d.bookings_timeline,'count','var(--ac)')+'</div>';
    }
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Total</th><th>Auj.</th></tr></thead><tbody>';
    rp.sort(function(a,b){return b.bookings-a.bookings}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.bookings_today+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='whatsapp'){
    title='WhatsApp';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Messages</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var wa=r.whatsapp?'<span class="badge badge-ok">Connecte</span>':'<span class="badge badge-da">Non</span>';
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+wa+'</td><td>'+r.messages+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  document.getElementById('detailTitle').textContent=title;
  document.getElementById('detailContent').innerHTML=h;
  document.getElementById('detailModal').classList.add('v');
}

// ===== CONV TOGGLE =====
document.addEventListener('click',function(e){
  var card=e.target.closest('.conv-toggle');
  if(!card)return;
  var msgs=card.querySelector('.conv-msgs');
  if(msgs)msgs.style.display=msgs.style.display==='block'?'none':'block';
});

// ===== EVENT DELEGATION =====
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-action]');
  if(!t)return;
  var action=t.getAttribute('data-action');
  var id=t.getAttribute('data-id');
  if(action==='detail')openDetail(id);
  else if(action==='edit')openEdit(id);
  else if(action==='editfromdetail'){closeDetail();openEdit(id)}
  else if(action==='delete')confirmDelete(id,t.getAttribute('data-name'));
  else if(action==='setstatus')setStatus(id,t.getAttribute('data-newstatus'));
  else if(action==='delbooking'){var rid=t.getAttribute('data-rid');var bid=t.getAttribute('data-bid');deleteBooking(rid,bid)}
});
</script>
</body>
</html>"""


# ==============================================================
# STRIPE BILLING
# ==============================================================

@app.get("/api/subscription")
async def api_subscription(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = restaurants_cache.get(rid, {})
    settings = rest.get("settings", {})
    status = settings.get("subscription_status", "trial")
    plan = settings.get("subscription_plan", "founder")
    trial_ends = rest.get("trial_ends_at", "")
    trial_days_left = 30
    trial_expired = False
    if trial_ends:
        try:
            from datetime import datetime as dt_cls
            ends = dt_cls.fromisoformat(trial_ends.replace('Z', '+00:00')) if isinstance(trial_ends, str) else trial_ends
            diff = (ends.replace(tzinfo=None) - datetime.utcnow()).days
            trial_days_left = max(0, diff)
            trial_expired = diff < 0
        except Exception:
            pass
    return {
        "status": status,
        "plan": plan,
        "trial_days_left": trial_days_left,
        "trial_expired": trial_expired if status == "trial" else False,
    }

@app.get("/api/usage")
async def api_usage(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    month = now_paris().strftime("%Y-%m")
    rest = restaurants_cache.get(rid, {})
    plan = rest.get("settings", {}).get("subscription_plan", "trial")
    limit = PLAN_LIMITS.get(plan, 500)
    rate = PLAN_RATES.get(plan, 0.08)
    counters = usage_counters.get(rid, {})
    current = counters.get(month, {"total": 0, "missed_call": 0, "reminder": 0, "review": 0, "other": 0})
    total = current["total"]
    overage = max(0, total - limit)
    # History
    history = []
    for m, c in sorted(counters.items(), reverse=True):
        if m != month:
            m_limit = limit
            m_over = max(0, c["total"] - m_limit)
            history.append({"month": m, "messages_sent": c["total"], "overage": m_over, "cost": round(m_over * rate, 2)})
    return {
        "month": month, "plan": plan,
        "messages_sent": total, "messages_included": limit,
        "messages_remaining": max(0, limit - total),
        "messages_overage": overage, "overage_rate": rate,
        "overage_cost": round(overage * rate, 2),
        "usage_percent": round(total / max(limit, 1) * 100, 1),
        "detail": {"missed_call": current.get("missed_call", 0), "reminder": current.get("reminder", 0), "review": current.get("review", 0), "other": current.get("other", 0)},
        "history": history[:6],
    }

@app.post("/api/stripe/checkout")
async def api_stripe_checkout(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    if not stripe_mod.api_key:
        return JSONResponse(status_code=503, content={"error": "Stripe not configured"})
    rid = auth["restaurant_id"]
    data = await request.json()
    plan = data.get("plan", "founder")
    price_id = STRIPE_PRICE_FOUNDER if plan == "founder" else STRIPE_PRICE_STANDARD
    if not price_id:
        return JSONResponse(status_code=400, content={"error": "Plan non configure"})
    email = auth.get("email", "")
    customer_id = get_restaurant_stripe_config(rid, "stripe_customer_id")
    if not customer_id:
        customer = stripe_mod.Customer.create(email=email, metadata={"restaurant_id": rid})
        customer_id = customer.id
        set_restaurant_stripe_config(rid, "stripe_customer_id", customer_id)
    session = stripe_mod.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"https://{APP_DOMAIN}/dashboard?p=account&subscription=success",
        cancel_url=f"https://{APP_DOMAIN}/dashboard?p=account&subscription=cancelled",
        metadata={"restaurant_id": rid, "plan": plan},
        allow_promotion_codes=True,
    )
    return {"checkout_url": session.url}

@app.post("/api/stripe/portal")
async def api_stripe_portal(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    customer_id = get_restaurant_stripe_config(rid, "stripe_customer_id")
    if not customer_id:
        return JSONResponse(status_code=400, content={"error": "Pas d'abonnement"})
    session = stripe_mod.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"https://{APP_DOMAIN}/dashboard?p=account",
    )
    return {"portal_url": session.url}

@app.post("/api/stripe/webhook")
async def api_stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe_mod.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return Response(status_code=400)
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    if etype == "checkout.session.completed":
        rid = obj.get("metadata", {}).get("restaurant_id")
        plan = obj.get("metadata", {}).get("plan", "founder")
        sub_id = obj.get("subscription")
        if rid:
            set_restaurant_stripe_config(rid, "stripe_subscription_id", sub_id)
            set_restaurant_stripe_config(rid, "subscription_plan", plan)
            set_restaurant_stripe_config(rid, "subscription_status", "active")
            rest = restaurants_cache.get(rid)
            if rest:
                rest["status"] = "active"
                await db_save_restaurant(rid, rest)
            bump_version(rid)
            logger.info(f"Stripe: subscription activated for {rid[:8]}... plan={plan}")
    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        cid = obj.get("customer")
        rid = find_restaurant_by_stripe_customer(cid)
        if rid:
            status = "canceled" if etype.endswith("deleted") else obj.get("status", "active")
            set_restaurant_stripe_config(rid, "subscription_status", status)
            bump_version(rid)
    elif etype == "invoice.payment_failed":
        cid = obj.get("customer")
        rid = find_restaurant_by_stripe_customer(cid)
        if rid:
            set_restaurant_stripe_config(rid, "subscription_status", "past_due")
            bump_version(rid)
    return {"status": "ok"}

# ==============================================================
# HEALTH CHECK
# ==============================================================

@app.get("/health")
@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def api_info():
    return {"version": "1.0.0", "name": "GuestScale API"}


# ==============================================================
# STATIC FILES (Vite build)
# ==============================================================

_dashboard_assets = Path(__file__).parent / "guestscale-dashboard" / "dist" / "assets"
if _dashboard_assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_dashboard_assets)), name="dashboard-assets")


# ==============================================================
# RUN
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

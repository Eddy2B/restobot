"""
GuestScale — Multi-Tenant Restaurant AI Platform
Version 5.0 — Multi-tenant, JWT auth, PostgreSQL
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

import anthropic
import httpx
import asyncpg
import bcrypt
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse
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

# Legacy support — kept for initial migration only
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("guestscale")

# ==============================================================
# IN-MEMORY CACHES (keyed by restaurant_id)
# ==============================================================

restaurants_cache = {}      # restaurant_id (UUID str): {id, slug, name, owner_phone, whatsapp_phone_number_id, whatsapp_access_token, whatsapp_verify_token, google_review_link, settings, floor_tables, status, trial_ends_at}
pid_to_restaurant = {}      # whatsapp_phone_number_id: restaurant_id (for webhook routing)
conversations = {}          # "restaurant_id:phone": [messages]
bookings = {}               # restaurant_id: [bookings]
floor_tables = {}           # restaurant_id: [{id, seats, zone, x, y, w, h, shape}]
table_slots = {}            # restaurant_id: {"12:30": {"T1": "available"}}
review_queue = {}           # restaurant_id: [reviews]
contacts = {}               # restaurant_id: {phone: contact_data}
restaurant_status = {}      # restaurant_id: {status, message, closed_dates, full_dates, temp_message, ...}
stats = {}                  # restaurant_id: {messages_today, bookings_today, languages, last_reset}
daily_stats_history = {}    # restaurant_id: [snapshots]

# Waitlist per restaurant
# waitlist[rid] = [{"id": "W1", "phone": ..., "name": ..., "covers": 2, "service": "soir", "date": "2026-03-26", "added_at": ..., "status": "waiting"|"notified"|"accepted"|"declined"|"expired", "notified_at": None, "position": 1}]
waitlist = {}               # restaurant_id: [entries]

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
    except Exception:
        return datetime.utcnow()

# ==============================================================
# JWT AUTH
# ==============================================================

import hmac
import base64

def jwt_encode(payload: dict) -> str:
    """Simple JWT encode (HS256)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload["iat"] = int(time_mod.time())
    payload["exp"] = int(time_mod.time()) + 86400 * 30  # 30 days
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
    """Extract and verify JWT from Authorization header or query param."""
    # Try Authorization header first
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # Fallback to query param
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

            # Load review queues
            rows = await conn.fetch("SELECT restaurant_id, data FROM mt_review_queue ORDER BY created_at DESC LIMIT 2000")
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
    candidates = []
    for t in tables:
        if slots.get(t["id"]) != "available":
            continue
        if t["seats"] < covers:
            continue
        if zone_pref and t["zone"] != zone_pref:
            continue
        candidates.append(t)
    if not candidates and zone_pref:
        for t in tables:
            if slots.get(t["id"]) != "available":
                continue
            if t["seats"] < covers:
                continue
            candidates.append(t)
    if not candidates:
        return None
    candidates.sort(key=lambda t: t["seats"])
    return candidates[0]["id"]


def assign_table(rid: str, slot_time: str, table_id: str, booking_id: str):
    if rid in table_slots and slot_time in table_slots[rid]:
        table_slots[rid][slot_time][table_id] = f"booked:{booking_id}"


def release_table(rid: str, slot_time: str, table_id: str):
    if rid in table_slots and slot_time in table_slots[rid]:
        table_slots[rid][slot_time][table_id] = "available"


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
    max_seats = max(t["seats"] for t in tables) if tables else 0
    lines.append(f"Capacité max par table : {max_seats} personnes")
    lines.append(f"Zones : salle, terrasse, bar")
    lines.append("")
    lines.append("INSTRUCTIONS RÉSERVATION :")
    lines.append("- Quand un client veut réserver, collecte : nombre de personnes, DATE, heure souhaitée, nom, et préférence zone (salle/terrasse) si demandée.")
    lines.append("- Si le client ne précise pas de date, DEMANDE-LUI pour quelle date.")
    lines.append("- Le client peut réserver pour aujourd'hui, demain, ou n'importe quel jour futur.")
    lines.append("- Les disponibilités en temps réel ci-dessus sont pour AUJOURD'HUI uniquement. Pour les autres jours, accepte la réservation et le restaurant validera.")
    lines.append("- Si le créneau demandé est complet AUJOURD'HUI, propose les créneaux les plus proches disponibles OU propose de réserver un autre jour.")
    lines.append("- Si un créneau est dispo, confirme la réservation en précisant le créneau et la date.")
    lines.append("- NE JAMAIS mentionner les numéros de table au client. Dis simplement que la réservation est confirmée.")
    return "\n".join(lines)


def extract_booking_date(message: str) -> str:
    import re
    msg = message.lower().strip()
    today = today_paris()

    # "ce soir", "aujourd'hui", "tonight", "today", "this evening"
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
    logger.info(f"Review request sent to {customer_phone}")


async def handle_review_response(rid: str, customer_phone: str, message_text: str) -> str | None:
    rq = review_queue.get(rid, [])
    pending = [r for r in rq if r["phone"] == customer_phone and r["sent"] and not r.get("responded")]
    if not pending:
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
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    for rid, rq in review_queue.items():
        rest = restaurants_cache.get(rid)
        if not rest:
            continue
        for r in rq:
            if r["sent"] or r.get("responded"):
                continue
            booking_time_str = r.get("booking_time", "")
            if booking_time_str and ":" in booking_time_str:
                try:
                    bh, bm = booking_time_str.split(":")
                    meal_dt = datetime.strptime(f"{today} {int(bh):02d}:{int(bm):02d}", "%Y-%m-%d %H:%M")
                    send_after = meal_dt.replace(hour=meal_dt.hour + 2) if meal_dt.hour < 22 else meal_dt.replace(hour=23, minute=0)
                    if now >= send_after:
                        await send_review_request(rid, r["phone"], r["name"])
                        r["sent"] = True
                except Exception as e:
                    logger.warning(f"Review timing error: {e}")
                    scheduled = datetime.fromisoformat(r["scheduled_at"])
                    if (now - scheduled).total_seconds() > 10800:
                        await send_review_request(rid, r["phone"], r["name"])
                        r["sent"] = True
            else:
                scheduled = datetime.fromisoformat(r["scheduled_at"])
                if (now - scheduled).total_seconds() > 10800:
                    await send_review_request(rid, r["phone"], r["name"])
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
            "message": "Via liste d'attente", "timestamp": datetime.utcnow().isoformat(),
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
🟡 *FERMÉ AUJOURD'HUI* — Fermeture exceptionnelle aujourd'hui
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
        status_map = {"open": "🟢 Ouvert", "full_tonight": "🔴 Complet ce soir", "full_lunch": "🔴 Complet ce midi", "closed_today": "🟡 Fermé aujourd'hui"}
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
        return "🟡 Fermeture exceptionnelle enregistrée pour aujourd'hui. L'agent prévient les clients. Envoyez *OUVERT* demain."

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
        status_context = f"\n⚠️ IMPORTANT : Le restaurant est COMPLET ({period}) aujourd'hui. Informe poliment et propose un autre créneau."

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
        booking_section = "\nRÉSERVATION : Si le client veut réserver, collecte : nombre de personnes, date, heure, nom. Confirme et dis que le restaurant va valider."

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

    return f"""Tu es l'assistant virtuel du restaurant "{rest['name']}".

RÔLE : Tu réponds aux clients sur WhatsApp de manière naturelle et chaleureuse.
Tu parles comme un membre de l'équipe, pas comme un robot.

📆 NOUS SOMMES LE : {today_paris().strftime('%A %d %B %Y')}

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

ALLERGÈNES : {ctx.get('allergens_policy', 'Demander au restaurant')}
{booking_section}
{availability_context}
{customer_context}

RÈGLES STRICTES :
- Ne JAMAIS inventer d'information. Si tu ne sais pas, dis-le et propose d'appeler le restaurant.
- Sur les allergènes/santé : TOUJOURS recommander de confirmer directement avec le restaurant.
- Reste dans ton rôle : tu ne parles QUE du restaurant et de sujets liés.
- Si le message n'a rien à voir, redirige poliment.
- Sois concis : 2-4 phrases max par réponse, sauf si le client pose plusieurs questions.
- Si une demande est complexe ou urgente, propose de transférer au restaurant.
- N'explicite JAMAIS que tu as acces a un profil CRM ou a des donnees personnelles. Utilise les infos naturellement.
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


def save_message(rid: str, customer_phone: str, role: str, content: str):
    key = f"{rid}:{customer_phone}"
    if key not in conversations:
        conversations[key] = []
    conversations[key].append({
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat(),
    })
    conversations[key] = conversations[key][-30:]
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

async def notify_owner(rid: str, rest: dict, customer_phone: str, customer_name: str, message: str):
    booking_keywords = ["réserv", "reserv", "book", "table", "prenot"]
    is_booking = any(kw in message.lower() for kw in booking_keywords)
    if is_booking:
        import re
        time_match = re.search(r'(\d{1,2})[h:](\d{2})?', message)
        booking_time = None
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2) or 0)
            m = (m // 15) * 15
            booking_time = f"{h:02d}:{m:02d}"
        covers = 2
        covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax|invit)', message.lower())
        if covers_match:
            covers = int(covers_match.group(1))
        else:
            covers_match2 = re.search(r'(?:pour|for|de|table)\s+(\d+)', message.lower())
            if covers_match2:
                covers = int(covers_match2.group(1))
            else:
                covers_match3 = re.search(r'(?:serons|sera|sommes|seront|being)\s+(\d+)', message.lower())
                if covers_match3:
                    covers = int(covers_match3.group(1))
        if covers < 1 or covers > 30:
            covers = 2
        zone_pref = None
        if "terrasse" in message.lower():
            zone_pref = "terrasse"
        elif "bar" in message.lower():
            zone_pref = "bar"

        rid_bookings = bookings.setdefault(rid, [])
        booking_id = f"R{len(rid_bookings)+1}"
        booking_date = extract_booking_date(message)
        assigned_table = None
        is_today_booking = booking_date == today_paris().isoformat()
        if booking_time and is_today_booking:
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
            "zone": zone_pref, "source": "whatsapp",
        }
        rid_bookings.append(new_booking)
        track_stats(rid, is_booking=True)
        await db_save_booking(rid, new_booking)
        bump_version(rid)
        await schedule_review_followup(rid, customer_phone, customer_name, booking_time or "")
        logger.info(f"Booking {booking_id}: {customer_name} {covers}p @ {booking_date} {booking_time} -> {assigned_table or 'unassigned'}")

    if not rest.get("owner_phone") or not rest.get("whatsapp_phone_number_id"):
        return
    if is_booking:
        date_label = booking_date
        try:
            bd = date.fromisoformat(booking_date)
            if bd == today_paris():
                date_label = "aujourd'hui"
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

    save_message(rid, customer_phone, "user", message_text)
    save_message(rid, customer_phone, "assistant", response)
    track_stats(rid, language="fr")
    track_contact(rid, customer_phone, customer_name)
    detect_preferences(rid, customer_phone, message_text)

    await send_whatsapp_message(phone_number_id, access_token, customer_phone, response)
    await notify_owner(rid, rest, customer_phone, customer_name, message_text)

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
  <div class="lsub">Restaurant AI Platform</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Identifiants incorrects. Veuillez reessayer.</div>
    <input class="linp" type="email" id="loginEmail" placeholder="Email" autocomplete="email" style="margin-bottom:10px" oninput="document.getElementById('loginError').style.display='none'">
    <div style="position:relative">
      <input class="linp" type="password" id="loginPwd" placeholder="Mot de passe" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()" oninput="document.getElementById('loginError').style.display='none';this.style.borderColor='#374151'">
      <button data-togglePwd onclick="togglePwdVis()" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;color:#6B7280;padding:4px" id="pwdToggle" type="button" title="Afficher le mot de passe">&#128065;</button>
    </div>
    <button class="lbtn" type="button" onclick="doLogin()" data-doLogin>Se connecter</button>
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
    <button class="nb" data-pg="bookings"><span class="ic">&#9673;</span> Reservations <span class="nb-badge" id="bookBadge" style="background:var(--wa);color:#fff">0</span></button>
    <button class="nb" data-pg="menu"><span class="ic">&#9680;</span> Menu</button>
    <div class="sb-l">CLIENTS</div>
    <button class="nb" data-pg="conversations"><span class="ic">&#9672;</span> Conversations <span class="nb-badge" id="convBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="reviews"><span class="ic">&#9733;</span> Avis <span class="nb-badge" id="reviewBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="contacts"><span class="ic">&#9671;</span> Contacts</button>
    <button class="nb" data-pg="waitlist"><span class="ic">&#9201;</span> Liste d&#39;attente <span class="nb-badge" id="waitBadge" style="background:var(--wa);color:#fff">0</span></button>
    <div class="sb-l">PARAMETRES</div>
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
    <button class="mobile-nav-btn" data-pg="floorplan"><span>&#8862;</span>Plan</button>
    <button class="mobile-nav-btn" data-pg="bookings"><span>&#128197;</span>Resas</button>
    <button class="mobile-nav-btn" data-pg="conversations"><span>&#128172;</span>Chat</button>
    <button class="mobile-nav-btn" data-pg="contacts"><span>&#128101;</span>Contacts</button>
  </div>
</div>

<div class="toast" id="toast"></div>
<div id="onboardingOverlay" style="display:none"></div>

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

function doLogout(){
  TOKEN=null;USER_DATA=null;
  try{sessionStorage.removeItem('gs_token')}catch(e){}
  location.reload();
}

var pageTitles={overview:"Vue d'ensemble",floorplan:"Plan de salle",bookings:"Réservations",menu:"Menu",conversations:"Conversations",reviews:"Avis",contacts:"Contacts",config:"Configuration",stats:"Statistiques",account:"Mon compte",waitlist:"Liste d'attente"};

function switchPage(id,btn){
  currentPage=id;
  document.getElementById('pageTitle').textContent=pageTitles[id]||id;
  document.querySelectorAll('.nb').forEach(function(b){b.classList.remove('on')});
  if(btn&&btn.classList&&!btn.classList.contains('mobile-nav-btn'))btn.classList.add('on');
  else{var b=document.querySelector('.sidebar [data-pg="'+id+'"]');if(b)b.classList.add('on')}
  document.querySelectorAll('.mobile-nav-btn').forEach(function(b){b.classList.remove('active')});
  var mb=document.querySelector('.mobile-nav-btn[data-pg="'+id+'"]');
  if(mb)mb.classList.add('active');
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
    if(b.table && (b.date||'').startsWith(selectedDate))tableBookings[b.table]=b.name;
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
  if(np.length<6){showToast('Minimum 6 caracteres');return}
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
  var dateLabel=isToday?"aujourd'hui":parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});
  
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
    h+='<div class="g2" id="ov-book"><div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+tb.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle</button></div>';
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
      h+='<div class="cc"><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm);margin-top:4px">'+phone+'</div><div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px"><span style="font-size:11px;color:var(--ts)">'+(ct.visits||0)+' visites</span><span class="src-badge" style="color:'+(srcColors2[src]||'#A8A29E')+';background:'+(srcColors2[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
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
  var tb={};filtered.forEach(function(b){if(b.table)tb[b.table]=b.name});
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
  h+='<div style="display:flex;gap:6px"><button style="padding:8px 16px;border-radius:8px;border:1.5px solid '+(fpMode==='resa'?'var(--ac)':'var(--b)')+';background:'+(fpMode==='resa'?'var(--al)':'var(--card)')+';color:'+(fpMode==='resa'?'var(--ac)':'var(--ts)')+';font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpModeResa>Reservations</button>';
  h+='<button style="padding:8px 16px;border-radius:8px;border:1.5px solid '+(fpMode==='edit'?'var(--ac)':'var(--b)')+';background:'+(fpMode==='edit'?'var(--al)':'var(--card)')+';color:'+(fpMode==='edit'?'var(--ac)':'var(--ts)')+';font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpModeEdit>Modifier plan</button></div></div>';
  if(fpMode==='edit'){
    h+='<div style="display:flex;gap:5px;margin-bottom:10px;padding:8px 12px;background:var(--bg);border-radius:10px;overflow-x:auto;align-items:center"><span style="font-size:11px;font-weight:700;color:var(--tm);white-space:nowrap;margin-right:4px">Ajouter :</span>';
    [{s:'round',n:2},{s:'round',n:4},{s:'round',n:6},{s:'rect',n:2},{s:'rect',n:4},{s:'rect',n:6},{s:'rect',n:8}].forEach(function(p){h+='<button style="padding:5px 10px;border-radius:7px;border:1.5px solid var(--b);background:var(--card);color:var(--t);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f);white-space:nowrap;display:flex;align-items:center;gap:3px" data-fpAdd="'+p.s+'-'+p.n+'"><span style="width:'+(p.s==='round'?12:16)+'px;height:12px;border-radius:'+(p.s==='round'?'50%':'2px')+';border:2px solid var(--ac);display:inline-block"></span>'+p.n+'p</button>'});
    h+='<div style="margin-left:auto"><button class="ba" data-fpSave>Enregistrer le plan</button></div></div>';
  }
  if(fpMode==='resa'){
    h+='<div style="display:flex;gap:0;margin-bottom:10px"><button style="flex:1;padding:10px;border:1.5px solid '+(fpService==='midi'?'var(--ac)':'var(--b)')+';border-right:none;border-radius:8px 0 0 8px;background:'+(fpService==='midi'?'var(--al)':'var(--card)')+';color:'+(fpService==='midi'?'var(--ac)':'var(--ts)')+';font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSvc="midi">&#9728; Midi</button>';
    h+='<button style="flex:1;padding:10px;border:1.5px solid '+(fpService==='soir'?'var(--ac)':'var(--b)')+';border-radius:0 8px 8px 0;background:'+(fpService==='soir'?'var(--al)':'var(--card)')+';color:'+(fpService==='soir'?'var(--ac)':'var(--ts)')+';font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSvc="soir">&#9790; Soir</button></div>';
    var slots=fpService==='midi'?["all","12:00","12:30","13:00","13:30","14:00"]:["all","19:00","19:30","20:00","20:30","21:00","21:30","22:00"];
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
function renderBookings(c){
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
  var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
  var today=fmtDate(new Date());
  var isToday=selectedDate===today;
  var dateLabel=isToday?"aujourd'hui":parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});
  var filtered=getBookingsForDate(selectedDate);

  var h='<div class="ov-layout" style="display:flex;gap:14px;align-items:flex-start">';
  h+='<div style="flex:1;min-width:0">';
  h+='<div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+filtered.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle</button></div>';
  filtered.forEach(function(b){
    var globalIdx=bookings.indexOf(b);
    h+='<div class="rw" data-editResa="'+globalIdx+'" style="cursor:pointer"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+(b.phone?' · '+b.phone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div></div>';
  });
  if(!filtered.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucune reservation '+dateLabel+'</div>';
  h+='</div>';
  h+='</div>'; // close left column
  h+='<div style="width:280px;flex-shrink:0">';
  h+=buildCalendar();
  h+='</div>';
  h+='</div>'; // close ov-layout
  c.innerHTML=h;
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
    h+='<div class="bubble '+(m.role==='user'?'bubble-user':'bubble-bot')+'">'+(m.content||m.text||'')+'</div>';
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
    h+='<div class="rw" data-contact="'+phone+'" style="cursor:pointer"><div class="rl"><div style="width:36px;height:36px;border-radius:50%;background:var(--al);display:flex;align-items:center;justify-content:center;color:var(--ac);font-size:13px;font-weight:700">'+(ct.name||'?').charAt(0).toUpperCase()+'</div><div><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm)">'+phone+(ct.email?' · '+ct.email:'')+'</div></div></div><div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:var(--ts)">'+(ct.visits||0)+' visites</span><span class="src-badge" style="color:'+(srcColors[src]||'#A8A29E')+';background:'+(srcColors[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
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
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--ok)">'+resas.length+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Reservations</div></div>';
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
      h+='<div style="max-width:80%;padding:8px 12px;border-radius:12px;background:'+(isBot?'var(--bg)':'var(--ac)')+';color:'+(isBot?'var(--t)':'white')+';font-size:13px">'+((m.content||m.text||'').substring(0,200))+'</div>';
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
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Reservations par canal</div>';
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

    task1 = asyncio.create_task(review_loop())
    task2 = asyncio.create_task(recap_loop())
    task3 = asyncio.create_task(slot_reset_loop())
    task4 = asyncio.create_task(waitlist_loop())
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    if db_pool:
        await db_pool.close()
    logger.info("GuestScale stopped")


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ==============================================================
# BREVO EMAIL
# ==============================================================

async def send_brevo_welcome(email: str, first_name: str, restaurant_name: str):
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

            # 2. Send transactional welcome email
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
<p style="font-size:14px;color:#374151;margin:0 0 8px"><strong>Email :</strong> {email}</p>
<p style="font-size:14px;color:#374151;margin:0"><strong>Mot de passe :</strong> celui que vous avez choisi a l'inscription</p>
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
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    phone = data.get("phone", "")
    restaurant_name = data.get("restaurant_name", "")
    restaurant_address = data.get("restaurant_address", "")

    if not email or not password or not restaurant_name:
        return {"error": "Email, mot de passe et nom du restaurant requis"}
    if len(password) < 6:
        return {"error": "Le mot de passe doit contenir au moins 6 caractères"}

    if not db_pool:
        return {"error": "Base de données non disponible"}

    # Generate slug from restaurant name
    slug = re_mod.sub(r'[^a-z0-9]+', '', restaurant_name.lower().replace(" ", ""))[:30] or "restaurant"

    try:
        async with db_pool.acquire() as conn:
            # Check if email already exists
            existing = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
            if existing:
                return {"error": "Un compte avec cet email existe déjà"}

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
        asyncio.create_task(send_brevo_welcome(email, first_name or restaurant_name, restaurant_name))
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
        return {"error": "Erreur lors de la création du compte"}


@app.post("/api/login")
async def api_login(request: Request):
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    if not email or not password:
        return {"error": "Email et mot de passe requis"}
    if not db_pool:
        return {"error": "Base de données non disponible"}
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
                return {"error": "Email ou mot de passe incorrect"}
            if not verify_password(password, row["password_hash"]):
                return {"error": "Email ou mot de passe incorrect"}
            rid_str = str(row["restaurant_id"])
            token = jwt_encode({
                "user_id": str(row["id"]), "restaurant_id": rid_str,
                "email": row["email"], "role": row["role"],
            })
            return {
                "status": "ok",
                "token": token,
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
            }
    except Exception as e:
        logger.error(f"Login error: {e}")
        return {"error": "Erreur serveur"}


@app.get("/api/me")
async def api_me(request: Request):
    auth = get_auth(request)
    if not auth:
        return {"error": "Non authentifié"}
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
    data = await request.json()
    current = data.get("current_password", "")
    new_pwd = data.get("new_password", "")
    if not current or not new_pwd:
        return {"error": "Champs requis"}
    if len(new_pwd) < 6:
        return {"error": "Minimum 6 caractères"}
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1::uuid", auth["user_id"])
            if not row or not verify_password(current, row["password_hash"]):
                return {"error": "Mot de passe actuel incorrect"}
            await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2::uuid", hash_password(new_pwd), auth["user_id"])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Change password error: {e}")
        return {"error": "Erreur serveur"}


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
async def admin_dashboard_page():
    return HTMLResponse(ADMIN_DASHBOARD_HTML)


@app.get("/login", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/{slug}", response_class=HTMLResponse)
async def dashboard_page(request: Request, slug: str = ""):
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
        recent.append({"phone": phone, "last_message": last["content"][:100], "time": last.get("timestamp", "")[:16].replace("T", " ")})
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
            "last_message": msgs[-1]["content"][:100],
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
    tag = data.get("tag", "")
    tags_list = data.get("tags", [])
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
    note = data.get("note", "")
    rid_contacts = contacts.get(rid, {})
    if phone in rid_contacts:
        rid_contacts[phone]["notes"] = note
        await db_save_contact(rid, phone, rid_contacts[phone])
    return {"status": "ok"}


@app.post("/api/contacts/preferences")
async def api_preferences_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone")
    preferences = data.get("preferences", "")
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
    for field in ["description", "menu", "hours", "address", "phone", "tone", "languages", "special_info", "booking_link", "allergens_policy"]:
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
    status["daily_message"] = data.get("message", "")
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
    msg = data.get("message", "")
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
    history = list(daily_stats_history.get(rid, []))
    for i in range(14, 0, -1):
        d = (today_paris() - timedelta(days=i)).isoformat()
        if not any(h["date"] == d for h in history):
            day_bk = [b for b in rid_bookings if (b.get("date") or "").startswith(d)]
            if day_bk:
                src = {}
                for b in day_bk:
                    s = b.get("source", "autre")
                    src[s] = src.get(s, 0) + 1
                history.append({"date": d, "bookings": len(day_bk), "covers": sum(b.get("covers", 0) for b in day_bk), "messages": 0, "sources": src})
    history.sort(key=lambda x: x["date"])
    return {"history": history[-30:], "today": today_data}


@app.post("/api/bookings/add")
@app.post("/api/bookings/manual")
async def api_add_manual_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
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
    return {
        "pages": status.get("dashboard_pages", {
            "floorplan": True, "bookings": True, "conversations": True,
            "reviews": True, "contacts": True, "dashboard": True,
        }),
        "onboarding_done": status.get("onboarding_done", "0"),
    }


@app.post("/api/settings")
async def api_update_settings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = restaurant_status.setdefault(rid, {})
    status["dashboard_pages"] = data.get("pages", {})
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
    return {"error": "Déjà sur la liste d'attente"}


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
    message = data.get("message", "").strip()
    visitor_name = data.get("name", "")
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

    base_url = str(request.base_url).rstrip("/")
    if "railway.app" in base_url or "guestscale.com" in base_url or request.headers.get("x-forwarded-proto") == "https":
        base_url = base_url.replace("http://", "https://")

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
    wa_connected = sum(1 for r in restaurants_cache.values() if r.get("whatsapp_phone_number_id"))
    # Users count from DB
    users_count = 0
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                users_count = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
        except Exception:
            pass
    return {
        "total_restaurants": total_restaurants, "trial_count": trial_count, "active_count": active_count,
        "total_bookings": total_bookings, "total_contacts": total_contacts,
        "total_conversations": total_conversations, "total_messages_today": total_messages,
        "total_tables": total_tables, "whatsapp_connected": wa_connected, "users_count": users_count,
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
        "restaurant": rest, "user": user_info,
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
  --bg:#F4F5F9;--card:#FFF;--t:#111827;--ts:#6B7280;--tm:#9CA3AF;--b:#E5E7EB;
  --ac:#2D7DD2;--ac2:#4ECDC4;--acg:linear-gradient(135deg,#2D7DD2,#4ECDC4);
  --ok:#4ECDC4;--wa:#F59E0B;--da:#EF4444;
  --f:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --shadow:0 1px 3px rgba(0,0,0,.06);
  --radius:12px;
}
body{font-family:var(--f);background:var(--bg);color:var(--t);min-height:100vh;-webkit-font-smoothing:antialiased}

/* Login overlay */
.lo{position:fixed;inset:0;background:#0F1117;display:flex;align-items:center;justify-content:center;z-index:100}
.lbox{text-align:center;width:380px}
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

/* Main layout */
.wrap{display:none;max-width:1100px;margin:0 auto;padding:24px 32px}
.wrap.v{display:block}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px}
.topbar h1{font-size:22px;font-weight:800;letter-spacing:-.03em}
.topbar h1 span{background:var(--acg);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{display:inline-flex;align-items:center;gap:5px;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700}
.badge-ok{background:#E6FAF8;color:#0D9488}
.badge-wa{background:#FFFBEB;color:#D97706}
.badge-da{background:#FEF2F2;color:#DC2626}
.badge-ac{background:#EBF4FF;color:#2563EB}
.btn{padding:8px 16px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:var(--f);transition:all .15s}
.btn-sm{padding:5px 10px;font-size:11px;border-radius:6px}
.btn-primary{background:var(--acg);color:#fff}
.btn-primary:hover{opacity:.9}
.btn-ghost{background:transparent;color:var(--ts);border:1px solid var(--b)}
.btn-ghost:hover{background:var(--bg);color:var(--t)}
.btn-danger{background:#FEF2F2;color:var(--da);border:1px solid #FECACA}
.btn-danger:hover{background:#FEE2E2}
.logout{background:none;border:none;font-size:13px;color:var(--ts);cursor:pointer;font-family:var(--f);font-weight:500}
.logout:hover{color:var(--t)}

/* KPI row */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--card);border-radius:var(--radius);padding:18px 16px;box-shadow:var(--shadow);border:1px solid var(--b)}
.kpi-val{font-size:28px;font-weight:800;letter-spacing:-.03em}
.kpi-label{font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.08em;margin-top:4px}

/* Table */
.card{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--b);overflow:hidden}
.card-h{padding:18px 20px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}
.card-h h2{font-size:15px;font-weight:700}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:10px 16px;font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--b);background:#FAFBFC}
td{padding:12px 16px;font-size:13px;border-bottom:1px solid #F3F4F6;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#F9FAFB}
.rest-name{font-weight:700;color:var(--t)}
.rest-slug{font-size:11px;color:var(--tm);font-weight:500}
.rest-meta{font-size:11px;color:var(--ts);display:flex;gap:12px;margin-top:3px}
.rest-meta span{display:flex;align-items:center;gap:3px}
.dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.dot-ok{background:var(--ok)}
.dot-wa{background:var(--wa)}
.dot-da{background:var(--da)}
.actions{display:flex;gap:6px}

/* Detail modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:50;display:none;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal-bg.v{display:flex}
.modal{background:var(--card);border-radius:16px;width:560px;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.15);padding:28px}
.modal h2{font-size:18px;font-weight:800;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}
.modal-close{background:none;border:none;font-size:22px;cursor:pointer;color:var(--tm);padding:4px}
.modal-close:hover{color:var(--t)}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}
.detail-item{padding:12px;background:var(--bg);border-radius:10px}
.detail-item .val{font-size:20px;font-weight:800}
.detail-item .lbl{font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;margin-top:2px}
.detail-section{margin-top:16px;padding-top:16px;border-top:1px solid var(--b)}
.detail-section h3{font-size:13px;font-weight:700;color:var(--ts);margin-bottom:8px}
.detail-row{display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid #F3F4F6}
.detail-row:last-child{border-bottom:none}
.detail-row .k{color:var(--ts)}
.detail-row .v{font-weight:600}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#111827;color:#fff;padding:10px 20px;border-radius:10px;font-size:13px;font-weight:600;z-index:999;display:none;box-shadow:0 8px 30px rgba(0,0,0,.2)}
.toast.v{display:block;animation:fadeInUp .3s ease}
@keyframes fadeInUp{from{opacity:0;transform:translateX(-50%) translateY(10px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}

/* Confirm dialog */
.confirm-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:60;display:none;align-items:center;justify-content:center}
.confirm-bg.v{display:flex}
.confirm{background:var(--card);border-radius:16px;padding:28px;width:380px;text-align:center}
.confirm h3{font-size:16px;font-weight:700;margin-bottom:8px}
.confirm p{font-size:13px;color:var(--ts);margin-bottom:20px}
.confirm .btns{display:flex;gap:10px;justify-content:center}

@media(max-width:768px){
  .wrap{padding:16px}
  .kpis{grid-template-columns:repeat(2,1fr)}
  table{font-size:12px}
  th,td{padding:8px 10px}
  .modal{width:95%;margin:0 10px}
}
</style>
</head>
<body>

<!-- LOGIN -->
<div class="lo" id="loginOverlay">
  <div class="lbox">
    <div class="l-logo"><div class="l-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div class="lwm">Guest<span style="color:#4ECDC4">Scale</span></div></div>
    <div class="lsub">Super Admin</div>
    <div class="lcd">
      <div class="lerr" id="loginError"></div>
      <div style="position:relative">
        <input class="linp" id="loginSecret" type="password" placeholder="Admin secret" autocomplete="off" style="margin-bottom:12px;padding-right:44px">
        <button type="button" id="toggleSecret" style="position:absolute;right:12px;top:12px;background:none;border:none;color:#6B7280;cursor:pointer;font-size:16px">&#128065;</button>
      </div>
      <button class="lbtn" id="loginBtn">Connexion</button>
    </div>
  </div>
</div>

<!-- MAIN -->
<div class="wrap" id="app">
  <div class="topbar">
    <h1>Guest<span>Scale</span> Admin</h1>
    <div style="display:flex;align-items:center;gap:12px">
      <button class="btn btn-ghost" id="refreshBtn">Actualiser</button>
      <button class="logout" id="logoutBtn">Deconnexion</button>
    </div>
  </div>

  <div class="kpis" id="kpis"></div>
  <div class="card" id="tableCard">
    <div class="card-h"><h2>Restaurants</h2><span id="countLabel" style="font-size:12px;color:var(--tm)"></span></div>
    <div style="overflow-x:auto"><table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th><th>Actions</th></tr></thead><tbody id="tbody"></tbody></table></div>
  </div>
</div>

<!-- DETAIL MODAL -->
<div class="modal-bg" id="detailModal">
  <div class="modal" id="detailContent"></div>
</div>

<!-- CONFIRM DIALOG -->
<div class="confirm-bg" id="confirmDialog">
  <div class="confirm">
    <h3 id="confirmTitle">Confirmer</h3>
    <p id="confirmText"></p>
    <div class="btns">
      <button class="btn btn-ghost" id="confirmCancel">Annuler</button>
      <button class="btn btn-danger" id="confirmOk">Supprimer</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var SECRET='';
var restaurants=[];

// ===== AUTH =====
(function(){
  var saved=null;
  try{saved=sessionStorage.getItem('gs_admin_secret')}catch(e){}
  if(saved){SECRET=saved;tryAuth()}
})();

document.getElementById('loginBtn').onclick=function(){
  SECRET=document.getElementById('loginSecret').value.trim();
  if(!SECRET){document.getElementById('loginError').style.display='block';document.getElementById('loginError').textContent='Entrez le secret admin.';return}
  tryAuth();
};
document.getElementById('loginSecret').onkeydown=function(e){if(e.key==='Enter')document.getElementById('loginBtn').click()};
document.getElementById('toggleSecret').onclick=function(){
  var inp=document.getElementById('loginSecret');
  if(inp.type==='password'){inp.type='text';this.innerHTML='&#128274;'}
  else{inp.type='password';this.innerHTML='&#128065;'}
};

function tryAuth(){
  apiFetch('/api/admin/stats').then(function(r){
    if(r.status===401){
      SECRET='';try{sessionStorage.removeItem('gs_admin_secret')}catch(e){}
      document.getElementById('loginError').style.display='block';
      document.getElementById('loginError').textContent='Secret incorrect.';
      document.getElementById('loginOverlay').style.display='flex';
      document.getElementById('app').classList.remove('v');
      return;
    }
    return r.json();
  }).then(function(d){
    if(!d)return;
    try{sessionStorage.setItem('gs_admin_secret',SECRET)}catch(e){}
    document.getElementById('loginOverlay').style.display='none';
    document.getElementById('app').classList.add('v');
    loadAll();
  }).catch(function(){
    document.getElementById('loginError').style.display='block';
    document.getElementById('loginError').textContent='Erreur de connexion.';
  });
}

function apiFetch(url,opts){
  opts=opts||{};
  opts.headers=opts.headers||{};
  opts.headers['x-admin-secret']=SECRET;
  return fetch(url,opts);
}

document.getElementById('logoutBtn').onclick=function(){
  SECRET='';try{sessionStorage.removeItem('gs_admin_secret')}catch(e){}
  location.reload();
};

document.getElementById('refreshBtn').onclick=function(){loadAll()};

// ===== LOAD DATA =====
function loadAll(){
  apiFetch('/api/admin/stats').then(function(r){return r.json()}).then(renderKPIs);
  apiFetch('/api/admin/restaurants').then(function(r){return r.json()}).then(function(d){
    restaurants=d.restaurants||[];
    document.getElementById('countLabel').textContent=d.total+' restaurant'+(d.total>1?'s':'');
    renderTable();
  });
}

function renderKPIs(d){
  var h='';
  h+=kpi(d.total_restaurants,'Restaurants','var(--ac)');
  h+=kpi(d.trial_count,'En essai','var(--wa)');
  h+=kpi(d.active_count,'Actifs','var(--ok)');
  h+=kpi(d.whatsapp_connected,'WhatsApp','#25D366');
  h+=kpi(d.total_bookings,'Resas totales','var(--ac)');
  h+=kpi(d.total_contacts,'Contacts','var(--ok)');
  h+=kpi(d.total_conversations,'Conversations','var(--wa)');
  h+=kpi(d.users_count,'Utilisateurs','var(--ts)');
  document.getElementById('kpis').innerHTML=h;
}
function kpi(v,l,c){return '<div class="kpi"><div class="kpi-val" style="color:'+c+'">'+v+'</div><div class="kpi-label">'+l+'</div></div>'}

function renderTable(){
  var h='';
  restaurants.forEach(function(r){
    var statusBadge='';
    if(r.status==='trial')statusBadge='<span class="badge badge-wa">Essai</span>';
    else if(r.status==='active')statusBadge='<span class="badge badge-ok">Actif</span>';
    else if(r.status==='suspended')statusBadge='<span class="badge badge-da">Suspendu</span>';
    else statusBadge='<span class="badge badge-ac">'+r.status+'</span>';
    var wa=r.whatsapp_connected?'<span class="dot dot-ok"></span> Oui':'<span class="dot dot-da"></span> Non';
    var created=r.created_at?new Date(r.created_at).toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'}):'—';
    h+='<tr>';
    h+='<td><div class="rest-name">'+esc(r.name)+'</div><div class="rest-slug">/'+esc(r.slug)+'</div><div class="rest-meta"><span>Cree le '+created+'</span>'+(r.tables_count?'<span>'+r.tables_count+' tables</span>':'')+'</div></td>';
    h+='<td>'+statusBadge+'</td>';
    h+='<td><strong>'+r.total_bookings+'</strong><br><span style="font-size:11px;color:var(--tm)">'+r.bookings_today+' auj.</span></td>';
    h+='<td>'+r.total_contacts+'</td>';
    h+='<td>'+r.messages_today+'</td>';
    h+='<td>'+wa+'</td>';
    h+='<td><div class="actions">';
    h+='<button class="btn btn-sm btn-ghost" data-detail="'+r.id+'">Details</button>';
    h+='<button class="btn btn-sm btn-danger" data-del="'+r.id+'" data-delname="'+esc(r.name)+'">Suppr.</button>';
    h+='</div></td>';
    h+='</tr>';
  });
  if(!restaurants.length){
    h='<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--tm)">Aucun restaurant enregistre</td></tr>';
  }
  document.getElementById('tbody').innerHTML=h;
}

// ===== DETAIL MODAL =====
function openDetail(rid){
  document.getElementById('detailModal').classList.add('v');
  document.getElementById('detailContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--tm)">Chargement...</div>';
  apiFetch('/api/admin/restaurant/'+rid).then(function(r){return r.json()}).then(function(d){
    if(d.error){document.getElementById('detailContent').innerHTML='<p>'+d.error+'</p>';return}
    var r=d.restaurant;
    var u=d.user;
    var settings=r.settings||{};
    var trial=r.trial_ends_at?new Date(r.trial_ends_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'}):'—';
    var created=r.created_at?new Date(r.created_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'}):'—';
    var h='<h2>'+esc(r.name)+'<button class="modal-close" onclick="closeDetail()">&times;</button></h2>';
    // Status + actions
    h+='<div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">';
    var statuses=['trial','active','suspended','cancelled'];
    statuses.forEach(function(s){
      var label={trial:'Essai',active:'Actif',suspended:'Suspendu',cancelled:'Annule'}[s];
      var cls=s===r.status?'btn-primary':'btn-ghost';
      h+='<button class="btn btn-sm '+cls+'" data-setstatus="'+rid+'" data-newstatus="'+s+'">'+label+'</button>';
    });
    h+='</div>';
    // KPIs
    h+='<div class="detail-grid">';
    h+='<div class="detail-item"><div class="val" style="color:var(--ac)">'+d.bookings_count+'</div><div class="lbl">Reservations</div></div>';
    h+='<div class="detail-item"><div class="val" style="color:var(--ok)">'+d.contacts_count+'</div><div class="lbl">Contacts</div></div>';
    h+='<div class="detail-item"><div class="val" style="color:var(--wa)">'+(d.stats_today.messages_today||0)+'</div><div class="lbl">Messages auj.</div></div>';
    h+='<div class="detail-item"><div class="val" style="color:var(--ts)">'+(d.tables||[]).length+'</div><div class="lbl">Tables</div></div>';
    h+='</div>';
    // Restaurant info
    h+='<div class="detail-section"><h3>Restaurant</h3>';
    h+=drow('Slug','/'+r.slug);
    h+=drow('Statut',r.status);
    h+=drow('Cree le',created);
    h+=drow('Fin essai',trial);
    h+=drow('Adresse',settings.address||'—');
    h+=drow('Telephone',settings.phone||r.owner_phone||'—');
    h+=drow('WhatsApp',r.whatsapp_phone_number_id?'Connecte ('+r.whatsapp_phone_number_id+')':'Non connecte');
    h+=drow('Google Review',r.google_review_link?'Configure':'Non');
    h+=drow('Menu',settings.menu?'Oui ('+settings.menu.length+' car.)':'Non');
    h+='</div>';
    // User info
    if(u){
      h+='<div class="detail-section"><h3>Proprietaire</h3>';
      h+=drow('Nom',u.first_name+' '+u.last_name);
      h+=drow('Email',u.email);
      h+=drow('Telephone',u.phone||'—');
      h+=drow('Inscrit le',u.created_at?new Date(u.created_at).toLocaleDateString('fr-FR'):'—');
      h+='</div>';
    }
    // Waitlist
    if(d.waitlist_active>0){
      h+='<div class="detail-section"><h3>Liste d&#39;attente</h3>';
      h+=drow('En attente',''+d.waitlist_active);
      h+='</div>';
    }
    // Dashboard link
    h+='<div style="margin-top:20px;text-align:center">';
    h+='<a href="/dashboard/'+r.slug+'" target="_blank" class="btn btn-primary" style="text-decoration:none;display:inline-block">Ouvrir le dashboard</a>';
    h+='</div>';
    document.getElementById('detailContent').innerHTML=h;
  }).catch(function(err){
    document.getElementById('detailContent').innerHTML='<p style="color:var(--da)">Erreur: '+err+'</p>';
  });
}

function drow(k,v){return '<div class="detail-row"><span class="k">'+k+'</span><span class="v">'+esc(String(v||''))+'</span></div>'}

function closeDetail(){document.getElementById('detailModal').classList.remove('v')}
document.getElementById('detailModal').onclick=function(e){if(e.target===this)closeDetail()};

// ===== STATUS UPDATE =====
function setStatus(rid,s){
  apiFetch('/api/admin/restaurant/'+rid+'/status',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})
  }).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Statut mis a jour: '+s);loadAll();openDetail(rid)}
    else showToast('Erreur: '+(d.error||''));
  });
}

// ===== DELETE =====
var pendingDeleteId='';
function confirmDelete(rid,name){
  pendingDeleteId=rid;
  document.getElementById('confirmTitle').textContent='Supprimer '+name+' ?';
  document.getElementById('confirmText').textContent='Cette action est irreversible. Toutes les donnees (reservations, contacts, conversations) seront supprimees.';
  document.getElementById('confirmDialog').classList.add('v');
}
document.getElementById('confirmCancel').onclick=function(){document.getElementById('confirmDialog').classList.remove('v');pendingDeleteId=''};
document.getElementById('confirmOk').onclick=function(){
  if(!pendingDeleteId)return;
  document.getElementById('confirmDialog').classList.remove('v');
  apiFetch('/api/admin/restaurant/'+pendingDeleteId,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Restaurant supprime: '+d.deleted);closeDetail();loadAll()}
    else showToast('Erreur: '+(d.error||''));
    pendingDeleteId='';
  });
};
document.getElementById('confirmDialog').onclick=function(e){if(e.target===this){this.classList.remove('v');pendingDeleteId=''}};

// ===== EVENT DELEGATION =====
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-detail]');
  if(t){openDetail(t.getAttribute('data-detail'));return}
  t=e.target.closest('[data-del]');
  if(t){confirmDelete(t.getAttribute('data-del'),t.getAttribute('data-delname'));return}
  t=e.target.closest('[data-setstatus]');
  if(t){setStatus(t.getAttribute('data-setstatus'),t.getAttribute('data-newstatus'));return}
});

// ===== UTILS =====
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('v');setTimeout(function(){t.classList.remove('v')},3000)}
</script>
</body>
</html>"""


# ==============================================================
# HEALTH CHECK
# ==============================================================

@app.get("/health")
async def health():
    return {"status": "ok", "restaurants": len(restaurants_cache), "version": "5.0"}


# ==============================================================
# RUN
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

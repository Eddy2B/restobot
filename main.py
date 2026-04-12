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


# Text utils extracted to app/utils/text_utils.py (Phase 2 refactoring)
from app.utils.text_utils import sanitize_input, sanitize_dict, normalize_phone
# Date utils extracted to app/utils/date_utils.py (Phase 2 refactoring)
from app.utils.date_utils import today_paris, now_paris, format_date_fr, MOIS_FR, JOURS_FR
# Auth extracted to app/auth.py (Phase 2 refactoring)
from app.auth import jwt_encode, jwt_decode, get_auth, hash_password, verify_password, verify_admin
# DB helpers + state management extracted to app/services/db_helpers.py (Phase 3b)
from app.services.db_helpers import (
    db_save_booking, db_save_contact, db_save_conversation, db_save_review,
    db_mark_review_sent, db_save_restaurant_status, db_save_daily_stats,
    db_save_waitlist_entry, db_update_waitlist_status, db_save_restaurant,
    bump_version, _refresh_rest_from_db, _refresh_all_restaurants_from_db,
    save_message, compute_effective_status, is_active_or_trial_valid, expired_402,
)

import anthropic
import httpx
import asyncpg
import bcrypt

# Config imported from app/config.py (refactored Phase 1)
from app.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, DATABASE_URL, PORT, JWT_SECRET, ADMIN_SECRET,
    APP_DOMAIN, BREVO_API_KEY, BREVO_LIST_ID, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICE_FOUNDER, STRIPE_PRICE_STANDARD, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER, DASHBOARD_PASSWORD, DASHBOARD_SECRET, TONE_PROMPTS,
    PLAN_LIMITS, PLAN_RATES, WHATSAPP_BROADCAST_COST_CENTS, WALLET_TOPUP_AMOUNTS_CENTS,
    RATE_LIMITS,
)
from pathlib import Path
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter  # AUDIT FIX 2026-04-12 — rate limiting
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.middleware.cors import CORSMiddleware

# Stripe SDK init (side effect — must stay in main.py, not config.py)
import stripe as stripe_mod
stripe_mod.api_key = STRIPE_SECRET_KEY

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

# Twilio and legacy env vars now imported from app.config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("guestscale")

# ==============================================================
# RATE LIMITING
# ==============================================================

# Mutable state imported from app/state.py (Phase 3 refactoring)
# Dicts are imported by name (mutations propagate — same object).
# db_pool is accessed via _state.db_pool (reassignment at startup).
import app.state as _state
from app.state import (
    restaurants_cache, pid_to_restaurant, phone_to_restaurant,
    conversations, bookings, floor_tables, table_slots, table_statuses,
    table_groups, review_queue, contacts, campaigns_store, restaurant_status,
    stats, daily_stats_history, waitlist, data_versions, usage_counters,
    ai_paused_conversations, escalations, missed_call_tracker, expired_reply_tracker,
    web_sessions, password_reset_tokens, rate_limit_store, login_failures,
)
# RATE_LIMITS now imported from app.config

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

# All 20 per-restaurant dicts now imported from app.state (see top of file)

# Waitlist per restaurant
# waitlist[rid] = [{"id": "W1", "phone": ..., "name": ..., "covers": 2, "service": "soir", "date": "2026-03-26", "added_at": ..., "status": "waiting"|"notified"|"accepted"|"declined"|"expired", "notified_at": None, "position": 1}]
# waitlist now imported from app.state

# PLAN_LIMITS, PLAN_RATES now imported from app.config


# compute_effective_status now in app/services/db_helpers.py

# is_active_or_trial_valid now in app/services/db_helpers.py

# expired_402 now in app/services/db_helpers.py

# _refresh_rest_from_db now in app/services/db_helpers.py

# _refresh_all_restaurants_from_db now in app/services/db_helpers.py

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
# data_versions now imported from app.state
# bump_version now in app/services/db_helpers.py


# today_paris, now_paris, format_date_fr, normalize_phone now in app/utils/
# jwt_encode, jwt_decode, get_auth, hash_password, verify_password now in app/auth.py

# ==============================================================
# DATABASE
# ==============================================================

# db_pool lives in app.state — local alias for backward compat (6000+ references)
db_pool = _state.db_pool  # initially None


async def init_db():
    """Initialize database pool and create/migrate tables."""
    global db_pool
    if not DATABASE_URL:
        logger.warning("No DATABASE_URL — running in-memory only")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        _state.db_pool = db_pool  # propagate to app.state for other modules
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

                CREATE TABLE IF NOT EXISTS mt_wallet_transactions (
                    id SERIAL PRIMARY KEY,
                    restaurant_id UUID NOT NULL REFERENCES restaurants(id),
                    txn_type TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    balance_after_cents INTEGER NOT NULL,
                    description TEXT DEFAULT '',
                    stripe_session_id TEXT,
                    campaign_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_mt_wallet_txn_rid ON mt_wallet_transactions(restaurant_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mt_wallet_txn_stripe ON mt_wallet_transactions(stripe_session_id) WHERE stripe_session_id IS NOT NULL;
            """)
        logger.info("Database connected, multi-tenant tables ready")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        db_pool = None
        _state.db_pool = None


# ==============================================================
# DB HELPERS (multi-tenant)
# ==============================================================

# db_save_booking through db_save_restaurant now in app/services/db_helpers.py


# (remaining db_save_* functions removed — see app/services/db_helpers.py)


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
    if not is_active_or_trial_valid(rid):
        logger.warning(f"Review request skipped (expired/inactive) for {rid[:8]}")
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


# save_message now in app/services/db_helpers.py

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
        if not is_active_or_trial_valid(rid):
            logger.warning(f"Daily recap skipped (expired/inactive) for {rid[:8]} {rest.get('name', '?')!r}")
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

    # Trial expired / subscription inactive : envoie un message auto une fois par 24h,
    # ne fait PAS tourner l'IA (pas de coût Anthropic + pas d'engagement service).
    if not is_active_or_trial_valid(rid):
        rest_phone = rest.get("settings", {}).get("phone", "") or rest.get("phone", "")
        rest_name = rest.get("name", "notre restaurant")
        # Cooldown 24h par customer pour éviter de spammer si le client envoie 10 messages
        tracker = expired_reply_tracker.setdefault(rid, {})
        last = tracker.get(customer_phone, "")
        send_auto = True
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")).replace(tzinfo=None)
                if (datetime.utcnow() - last_dt).total_seconds() < 86400:
                    send_auto = False
            except Exception:
                pass
        if send_auto:
            if rest_phone:
                msg = (
                    f"Bonjour, le restaurant {rest_name} n'est pas disponible sur ce canal "
                    f"pour le moment. Vous pouvez le contacter directement au {rest_phone}."
                )
            else:
                msg = (
                    f"Bonjour, le restaurant {rest_name} n'est pas disponible sur ce canal "
                    f"pour le moment. Merci de le contacter directement."
                )
            try:
                await send_whatsapp_message(phone_number_id, access_token, customer_phone, msg)
                tracker[customer_phone] = now_paris().isoformat()
                save_message(rid, customer_phone, "user", message_text)
                save_message(rid, customer_phone, "assistant", msg)
            except Exception as e:
                logger.error(f"Expired auto-reply send failed for {rid[:8]}: {e}")
        logger.info(f"WhatsApp AI skipped (expired/inactive) for {rid[:8]} {rest_name!r}")
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

    # AUDIT FIX 2026-04-12 — RGPD notice for new contacts (first interaction only)
    if len(history) == 0 and '{"action":"escalate"' not in response:
        rest_name = rest.get("name", "le restaurant")
        response += f"\n\n_Vos données sont traitées par {rest_name} via GuestScale conformément au RGPD. Plus d'infos : guestscale.com/privacy.html_"

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

from app.templates.dashboard_legacy import DASHBOARD_HTML  # refactored: ~2831 lines extracted



# ==============================================================
# WEB CHAT SESSIONS
# ==============================================================

# web_sessions now imported from app.state


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

    async def cache_refresh_loop():
        """Periodically refresh trial_ends_at + settings + status from DB so that
        out-of-band SQL UPDATEs (manual debug, external scripts) propagate to
        the in-memory cache and the trial-blocking gates within ~30 seconds."""
        while True:
            try:
                await _refresh_all_restaurants_from_db()
            except Exception as e:
                logger.error(f"Cache refresh loop error: {e}")
            await asyncio.sleep(30)

    task1 = asyncio.create_task(review_loop())
    task2 = asyncio.create_task(recap_loop())
    task3 = asyncio.create_task(slot_reset_loop())
    task4 = asyncio.create_task(waitlist_loop())
    task5 = asyncio.create_task(reminder_loop())
    task6 = asyncio.create_task(cache_refresh_loop())
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()
    task6.cancel()
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

# AUDIT FIX 2026-04-12 — Rate limiting (slowapi)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Trop de tentatives. Réessayez dans quelques minutes."})

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


async def send_subscription_welcome_emails(user_email: str, first_name: str, restaurant_name: str, plan: str):
    """Send subscription welcome to user + admin notification when a paid plan is activated."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping subscription welcome emails")
        return
    plan_label = "Fondateur" if plan == "founder" else "Standard"
    plan_price = "99 €" if plan == "founder" else "149 €"
    perks_html = (
        '<li style="margin:4px 0">500 messages IA inclus / mois</li>'
        '<li style="margin:4px 0">Agent WhatsApp 24/7</li>'
        '<li style="margin:4px 0">CRM, plan de salle, campagnes</li>'
    )
    if plan == "founder":
        perks_html += (
            '<li style="margin:4px 0"><strong>Support prioritaire WhatsApp</strong></li>'
            '<li style="margin:4px 0"><strong>Tarif bloqué à vie</strong></li>'
            '<li style="margin:4px 0"><strong>Configuration offerte (valeur 299 €)</strong></li>'
        )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Welcome to the user
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": user_email, "name": first_name or restaurant_name}],
                    "subject": f"Bienvenue dans GuestScale — Plan {plan_label} activé !",
                    "htmlContent": f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<div style="text-align:center;margin-bottom:24px">
<svg viewBox="0 0 32 32" fill="none" width="40" height="40"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
<h1 style="font-size:24px;font-weight:800;color:#111827;margin:12px 0 4px">Bienvenue dans GuestScale !</h1>
<p style="font-size:14px;color:#6B7280">Votre Plan {plan_label} est désormais actif.</p>
</div>
<p style="font-size:14px;color:#374151;line-height:1.6">Bonjour {first_name or 'cher restaurateur'},</p>
<p style="font-size:14px;color:#374151;line-height:1.6">Merci de nous faire confiance pour <strong>{restaurant_name}</strong>. Voici ce qui est inclus dans votre Plan {plan_label} ({plan_price} HT/mois) :</p>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:18px;margin:18px 0">
<ul style="margin:0;padding-left:20px;font-size:14px;color:#374151;line-height:1.7">
{perks_html}
</ul>
</div>
<div style="text-align:center;margin:24px 0">
<a href="https://app.guestscale.com/dashboard" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#2D7DD2,#4ECDC4);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px">Accéder à mon dashboard</a>
</div>
<p style="font-size:13px;color:#6B7280;line-height:1.6">Une question ? Une demande ? Répondez directement à cet email ou écrivez-nous à contact@guestscale.com — on vous répond rapidement.</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Restaurant AI Platform · Nice, France</p>
</div>""",
                }
            )
            # 2. Admin notification
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": "contact@guestscale.com", "name": "GuestScale Admin"}],
                    "subject": f"💰 Nouveau client payant : {restaurant_name} — Plan {plan_label}",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:24px">
<h2 style="color:#111827;margin:0 0 16px">Nouveau client payant</h2>
<div style="background:#E6FAF8;border:1px solid #4ECDC4;border-radius:12px;padding:16px;margin-bottom:16px">
<p style="margin:4px 0;font-size:14px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Email :</strong> {user_email}</p>
<p style="margin:4px 0;font-size:14px"><strong>Plan :</strong> {plan_label} ({plan_price} HT/mois)</p>
<p style="margin:4px 0;font-size:13px;color:#6B7280"><strong>Date :</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
</div>
</div>""",
                }
            )
            logger.info(f"Subscription welcome emails sent for {restaurant_name}")
    except Exception as e:
        logger.error(f"Subscription welcome email error: {e}")


async def send_cancellation_emails(user_email: str, first_name: str, restaurant_name: str,
                                   effective_date: str, reason: str = ""):
    """Send cancellation confirmation to user + admin notification to GuestScale."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping cancellation emails")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Confirmation to the user
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": user_email, "name": first_name}],
                    "subject": "Confirmation de résiliation — GuestScale",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<h1 style="font-size:22px;font-weight:800;color:#111827;margin:0 0 16px">Résiliation confirmée</h1>
<p style="font-size:14px;color:#374151;line-height:1.6">Bonjour {first_name},</p>
<p style="font-size:14px;color:#374151;line-height:1.6">Nous avons bien enregistré la résiliation de votre abonnement GuestScale pour <strong>{restaurant_name}</strong>.</p>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:18px;margin:18px 0">
<p style="margin:0 0 6px;font-size:14px"><strong>Fin d'abonnement :</strong> {effective_date}</p>
<p style="margin:0;font-size:13px;color:#6B7280">Votre dashboard reste accessible jusqu'à cette date. Aucun frais supplémentaire ne sera prélevé.</p>
</div>
<p style="font-size:13px;color:#374151;line-height:1.6">Vos données restent exportables pendant 30 jours après cette date depuis l'onglet « Mon compte » → « Données personnelles ».</p>
<p style="font-size:13px;color:#374151;line-height:1.6">Vous pouvez annuler la résiliation à tout moment avant le {effective_date} depuis votre dashboard.</p>
<p style="font-size:13px;color:#6B7280;line-height:1.6;margin-top:20px">Merci pour votre confiance,<br>L'équipe GuestScale</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Nice, France · contact@guestscale.com</p>
</div>""",
                }
            )
            # 2. Admin notification
            reason_html = f"<p style='margin:4px 0;font-size:14px'><strong>Motif :</strong> {reason}</p>" if reason else "<p style='margin:4px 0;font-size:13px;color:#6B7280'>Aucun motif fourni.</p>"
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": "contact@guestscale.com", "name": "GuestScale Admin"}],
                    "subject": f"⚠️ Résiliation : {restaurant_name}",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:24px">
<h2 style="color:#111827;margin:0 0 16px">Résiliation d'abonnement</h2>
<div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:12px;padding:16px;margin-bottom:16px">
<p style="margin:4px 0;font-size:14px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Email :</strong> {user_email}</p>
<p style="margin:4px 0;font-size:14px"><strong>Fin effective :</strong> {effective_date}</p>
{reason_html}
<p style="margin:4px 0;font-size:13px;color:#6B7280"><strong>Date résiliation :</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
</div>
</div>""",
                }
            )
            logger.info(f"Cancellation emails sent for {restaurant_name}")
    except Exception as e:
        logger.error(f"Cancellation email error: {e}")


# ==============================================================
# AUTH ENDPOINTS
# ==============================================================

@app.post("/api/register")
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
# password_reset_tokens now imported from app.state


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
        return JSONResponse(status_code=401, content={"error": "Code invalide ou expiré"})
    if time_mod.time() > token_data["expires"]:
        password_reset_tokens.pop(code, None)
        return JSONResponse(status_code=401, content={"error": "Code expiré. Veuillez en demander un nouveau."})

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

    if not is_active_or_trial_valid(rid):
        logger.warning(f"Missed call relance skipped (expired/inactive) for {rid[:8]} {rest.get('name', '?')!r}")
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

# WHATSAPP_BROADCAST_COST_CENTS, WALLET_TOPUP_AMOUNTS_CENTS now imported from app.config

def get_wallet_cents(rid: str) -> int:
    rest = restaurants_cache.get(rid, {})
    return int(rest.get("settings", {}).get("wallet_balance_cents", 0) or 0)

async def _log_wallet_txn(rid: str, txn_type: str, amount_cents: int, balance_after: int,
                          description: str = "", stripe_session_id: str = None, campaign_id: str = None):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_wallet_transactions
                (restaurant_id, txn_type, amount_cents, balance_after_cents, description, stripe_session_id, campaign_id)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (stripe_session_id) DO NOTHING
            """, rid, txn_type, amount_cents, balance_after, description, stripe_session_id, campaign_id)
    except Exception as e:
        logger.error(f"Wallet txn log error: {e}")

async def credit_wallet(rid: str, amount_cents: int, description: str = "Recharge",
                        stripe_session_id: str = None) -> bool:
    """Crédite le wallet et journalise la transaction. Idempotent via stripe_session_id."""
    rest = restaurants_cache.get(rid)
    if not rest or amount_cents <= 0:
        return False
    # Idempotency: skip if this session was already credited
    if stripe_session_id and db_pool:
        try:
            async with db_pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT 1 FROM mt_wallet_transactions WHERE stripe_session_id = $1",
                    stripe_session_id,
                )
                if existing:
                    logger.info(f"Wallet topup already processed for session {stripe_session_id}")
                    return False
        except Exception as e:
            logger.error(f"Wallet idempotency check error: {e}")
    settings = rest.setdefault("settings", {})
    current = int(settings.get("wallet_balance_cents", 0) or 0)
    new_balance = current + amount_cents
    settings["wallet_balance_cents"] = new_balance
    await db_save_restaurant(rid, rest)
    await _log_wallet_txn(rid, "topup", amount_cents, new_balance, description, stripe_session_id=stripe_session_id)
    bump_version(rid)
    return True

async def debit_wallet(rid: str, amount_cents: int, description: str = "",
                       campaign_id: str = None) -> bool:
    """Débite le wallet et journalise. Retourne False si solde insuffisant."""
    rest = restaurants_cache.get(rid)
    if not rest or amount_cents <= 0:
        return False
    settings = rest.setdefault("settings", {})
    current = int(settings.get("wallet_balance_cents", 0) or 0)
    if current < amount_cents:
        return False
    new_balance = current - amount_cents
    settings["wallet_balance_cents"] = new_balance
    await db_save_restaurant(rid, rest)
    if description or campaign_id:
        await _log_wallet_txn(rid, "debit", -amount_cents, new_balance, description, campaign_id=campaign_id)
    return True


async def get_wallet_transactions(rid: str, limit: int = 10) -> list:
    if not db_pool:
        return []
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT txn_type, amount_cents, balance_after_cents, description, created_at
                FROM mt_wallet_transactions
                WHERE restaurant_id = $1::uuid
                ORDER BY created_at DESC
                LIMIT $2
            """, rid, limit)
            return [{
                "type": r["txn_type"],
                "amount_cents": r["amount_cents"],
                "amount_eur": round(r["amount_cents"] / 100, 2),
                "balance_after_cents": r["balance_after_cents"],
                "description": r["description"] or "",
                "date": r["created_at"].isoformat() if r["created_at"] else "",
            } for r in rows]
    except Exception as e:
        logger.error(f"Wallet txn fetch error: {e}")
        return []


@app.get("/api/wallet")
async def api_get_wallet(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    cents = get_wallet_cents(rid)
    txns = await get_wallet_transactions(rid, limit=10)
    return {
        "balance_cents": cents,
        "balance_eur": round(cents / 100, 2),
        "wa_msg_cost_cents": WHATSAPP_BROADCAST_COST_CENTS,
        "topup_amounts_cents": list(WALLET_TOPUP_AMOUNTS_CENTS),
        "transactions": txns,
    }


@app.post("/api/wallet/checkout")
@limiter.limit("5/minute")  # AUDIT FIX 2026-04-12
async def api_wallet_checkout(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    if not stripe_mod.api_key:
        return JSONResponse(status_code=503, content={"error": "Stripe non configuré"})
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    try:
        amount_cents = int(data.get("amount_cents", 0))
    except (TypeError, ValueError):
        amount_cents = 0
    if amount_cents not in WALLET_TOPUP_AMOUNTS_CENTS:
        return JSONResponse(status_code=400, content={"error": "Montant non autorisé"})
    rest = restaurants_cache.get(rid, {})
    rest_name = rest.get("name", "Restaurant")
    amount_eur = amount_cents / 100
    try:
        session = stripe_mod.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": f"Recharge wallet WhatsApp — {amount_eur:.0f} €",
                        "description": f"Crédit campagnes WhatsApp GuestScale ({rest_name})",
                    },
                },
                "quantity": 1,
            }],
            metadata={
                "type": "wallet_topup",
                "restaurant_id": rid,
                "amount_cents": str(amount_cents),
            },
            success_url=f"https://{APP_DOMAIN}/dashboard?p=campaigns&wallet=success",
            cancel_url=f"https://{APP_DOMAIN}/dashboard?p=campaigns&wallet=cancel",
        )
        return {"checkout_url": session.url}
    except Exception as e:
        logger.error(f"Stripe wallet checkout error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur Stripe"})


@app.post("/api/campaigns/send")
@limiter.limit("10/minute")  # AUDIT FIX 2026-04-12
async def api_campaign_send(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
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
    campaign_id_local = f"C{len(campaigns_store.get(rid, []))+1}"
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
        # WhatsApp (no per-message debit — aggregated below)
        if "whatsapp" in channels:
            ct_phone = ct.get("phone")
            if ct_phone and wa_phone_id and wa_token:
                # Re-check wallet at each message to avoid overdraft if pre-flight is stale
                if get_wallet_cents(rid) < (sent_wa + 1) * WHATSAPP_BROADCAST_COST_CENTS:
                    logger.warning(f"Wallet drained mid-campaign: {rid}")
                    break
                try:
                    await send_whatsapp_message(wa_phone_id, wa_token, ct_phone, text_body)
                    sent_wa += 1
                    await increment_message_count(rid, "broadcast")
                except Exception as e:
                    logger.error(f"Campaign WhatsApp error: {e}")

    # Single aggregated debit for the whole campaign (one row in mt_wallet_transactions)
    if sent_wa > 0:
        total_cost = sent_wa * WHATSAPP_BROADCAST_COST_CENTS
        debit_desc = f"Campagne « {template_label or subject or 'sans nom'} » — {sent_wa} WhatsApp"
        await debit_wallet(rid, total_cost, description=debit_desc, campaign_id=campaign_id_local)

    sent_count = sent_email + sent_wa
    campaign = {
        "id": campaign_id_local,
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
    if not is_active_or_trial_valid(rid):
        return expired_402()
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
    if not is_active_or_trial_valid(rid):
        return expired_402()
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
    if not is_active_or_trial_valid(rid):
        return expired_402()
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


# AUDIT FIX 2026-04-12 — KPIs IA (performance de l'agent)
@app.get("/api/stats/ai-kpis")
async def api_stats_ai_kpis(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    cutoff = (today_paris() - timedelta(days=30)).isoformat()

    # 1. Taux de conversion message → réservation
    #    conversations ayant abouti à une résa / total conversations (30 derniers jours)
    rid_convs = {k: v for k, v in conversations.items() if k.startswith(rid + ":")}
    total_convs = len(rid_convs)
    rid_bookings = bookings.get(rid, [])
    booking_phones = {b.get("phone") for b in rid_bookings if b.get("source") == "whatsapp" and (b.get("date") or "") >= cutoff}
    conv_phones_with_booking = 0
    for conv_key in rid_convs:
        phone = conv_key.split(":", 1)[1] if ":" in conv_key else ""
        if phone in booking_phones:
            conv_phones_with_booking += 1
    conversion_rate = round((conv_phones_with_booking / total_convs * 100), 1) if total_convs > 0 else 0

    # 2. Taux de réponse IA
    #    messages IA / messages entrants (exclure les manuels et escalations)
    total_user_msgs = 0
    total_ai_msgs = 0
    for msgs in rid_convs.values():
        for m in msgs:
            ts = m.get("timestamp", "")
            if ts and ts < cutoff:
                continue
            if m.get("role") == "user":
                total_user_msgs += 1
            elif m.get("role") == "assistant" and m.get("sender_type") != "human":
                total_ai_msgs += 1
    ai_response_rate = round((total_ai_msgs / total_user_msgs * 100), 1) if total_user_msgs > 0 else 0

    # 3. Avis Google envoyés
    rq = review_queue.get(rid, [])
    reviews_sent = sum(1 for r in rq if r.get("sent"))
    reviews_responded = sum(1 for r in rq if r.get("responded"))
    reviews_positive = sum(1 for r in rq if r.get("sentiment") == "POSITIVE")

    return {
        "period_days": 30,
        "total_conversations": total_convs,
        "conversations_with_booking": conv_phones_with_booking,
        "conversion_rate": conversion_rate,
        "total_user_messages": total_user_msgs,
        "total_ai_responses": total_ai_msgs,
        "ai_response_rate": ai_response_rate,
        "reviews_sent": reviews_sent,
        "reviews_responded": reviews_responded,
        "reviews_positive": reviews_positive,
    }


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
    if not is_active_or_trial_valid(rid):
        return expired_402()
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
    if not is_active_or_trial_valid(rid):
        return expired_402()
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
    if not is_active_or_trial_valid(rid):
        return expired_402()
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

from app.templates.widget import WIDGET_JS  # refactored: ~86 lines extracted



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


# verify_admin now imported from app.auth (Phase 2 refactoring)


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
            "effective_status": compute_effective_status(rest),
            "subscription_status": rest.get("settings", {}).get("subscription_status", "trial"),
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


# AUDIT FIX 2026-04-12 — Métriques business SaaS (MRR/ARR/ARPU/LTV/Churn)
@app.get("/api/admin/metrics")
async def admin_metrics(request: Request):
    if not verify_admin(request):
        return Response(status_code=401)

    paying, trial_valid, expired_list, churned = [], [], [], []
    for rid, rest in restaurants_cache.items():
        eff = compute_effective_status(rest)
        if eff == "active":
            paying.append(rest)
        elif eff == "trial":
            trial_valid.append(rest)
        elif eff == "expired":
            expired_list.append(rest)
        elif eff in ("canceled", "cancelled", "suspended"):
            churned.append(rest)

    # MRR : somme par plan (founder=99, standard=149)
    mrr = 0
    for rest in paying:
        plan = rest.get("settings", {}).get("subscription_plan", "founder")
        mrr += 99 if plan == "founder" else 149
    arr = mrr * 12
    n_paying = len(paying)
    arpu = round(mrr / n_paying, 2) if n_paying > 0 else 0

    # Churn mensuel (nb churned / (payants + churned) au début du mois — approximation)
    n_churned = len(churned)
    base = n_paying + n_churned
    churn_rate = round(n_churned / base * 100, 1) if base > 0 else 0

    # LTV estimée : ARPU / churn. Si churn=0 → 18 mois par défaut
    ltv = round(arpu / (churn_rate / 100), 2) if churn_rate > 0 else round(arpu * 18, 2)

    # Wallet revenue (total recharges Stripe)
    wallet_revenue = 0.0
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                val = await conn.fetchval("SELECT COALESCE(SUM(amount_cents), 0) FROM mt_wallet_transactions WHERE txn_type = 'topup'")
                wallet_revenue = round(val / 100, 2)
        except Exception:
            pass

    # Totaux globaux
    total_msgs = sum(
        sum(snap.get("messages", 0) for snap in dsh) for dsh in daily_stats_history.values()
    ) + sum(s.get("messages_today", 0) for s in stats.values())
    total_bks = sum(len(b) for b in bookings.values())
    total_cts = sum(len(c) for c in contacts.values())
    total_rev = sum(1 for rq in review_queue.values() for r in rq if r.get("sent"))

    return {
        "mrr": mrr, "arr": arr,
        "total_clients_paying": n_paying,
        "total_clients_trial": len(trial_valid),
        "total_clients_expired": len(expired_list),
        "total_clients_churned": n_churned,
        "churn_rate_monthly": churn_rate,
        "avg_revenue_per_user": arpu,
        "ltv_estimate": ltv,
        "cac_estimate": None,
        "ltv_cac_ratio": None,
        "wallet_revenue_total": wallet_revenue,
        "total_messages_sent": total_msgs,
        "total_bookings": total_bks,
        "total_contacts": total_cts,
        "total_reviews_sent": total_rev,
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
    if new_status not in ("trial", "active", "suspended", "cancelled"):
        return {"error": "Invalid status"}

    rest["status"] = new_status
    settings = rest.setdefault("settings", {})

    # "Activer" depuis le super-admin = comp manuel : on force aussi
    # subscription_status="active" pour bypasser le check trial_ends_at.
    # Sinon compute_effective_status retourne "expired" sur un trial dépassé.
    if new_status == "active":
        settings["subscription_status"] = "active"
        settings_json_for_db = json.dumps(settings)
    else:
        settings_json_for_db = None  # ne touche pas settings pour les autres statuts

    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                if settings_json_for_db is not None:
                    await conn.execute(
                        "UPDATE restaurants SET status = $1, settings = $2::jsonb, updated_at = NOW() WHERE id = $3::uuid",
                        new_status, settings_json_for_db, rid,
                    )
                else:
                    await conn.execute(
                        "UPDATE restaurants SET status = $1, updated_at = NOW() WHERE id = $2::uuid",
                        new_status, rid,
                    )
        except Exception as e:
            logger.error(f"Admin status update error: {e}")
            return {"error": str(e)}

    # Belt + bretelles : re-read DB into cache pour s'aligner avec n'importe
    # quel autre changement out-of-band éventuel.
    await _refresh_rest_from_db(rid)
    bump_version(rid)
    rest = restaurants_cache.get(rid, rest)
    logger.info(f"Admin: status -> {new_status} for {rest.get('name')} ({rid[:8]}...)")
    return {
        "status": "ok",
        "new_status": new_status,
        "effective_status": compute_effective_status(rest),
    }


@app.post("/api/admin/restaurant/{rid}/extend-trial")
async def admin_extend_trial(rid: str, request: Request):
    """Offre X jours d'essai gratuit (admin manuel : compensation, prospect, etc.).
    Réinitialise trial_ends_at = NOW() + X days, status = 'trial', et purge
    settings.subscription_status si présent (pour redonner accès via essai)."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    data = await request.json()
    try:
        days = int(data.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    if days < 1 or days > 365:
        return {"error": "days doit être entre 1 et 365"}

    new_end = datetime.utcnow() + timedelta(days=days)
    settings = rest.setdefault("settings", {})
    # Purge l'état d'abonnement pour que compute_effective_status retourne "trial"
    settings.pop("subscription_status", None)
    rest["status"] = "trial"
    rest["trial_ends_at"] = new_end.isoformat()

    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE restaurants SET
                        trial_ends_at = $1,
                        status = 'trial',
                        settings = COALESCE(settings, '{}'::jsonb) - 'subscription_status',
                        updated_at = NOW()
                       WHERE id = $2::uuid""",
                    new_end, rid,
                )
        except Exception as e:
            logger.error(f"Admin extend-trial error: {e}")
            return {"error": str(e)}

    await _refresh_rest_from_db(rid)
    bump_version(rid)
    rest = restaurants_cache.get(rid, rest)
    logger.info(f"Admin: trial extended +{days}d for {rest.get('name')} ({rid[:8]}...) → {new_end.date().isoformat()}")
    return {
        "status": "ok",
        "days": days,
        "trial_ends_at": new_end.isoformat(),
        "effective_status": compute_effective_status(rest),
    }


# AUDIT FIX 2026-04-12 — Purge test data from a restaurant
@app.post("/api/admin/purge-test-data/{rid}")
async def admin_purge_test_data(rid: str, request: Request):
    """Supprime les contacts/réservations/conversations dont le nom contient 'test' (case insensitive).
    Utile pour nettoyer des données de dev visibles en démo."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}

    rid_contacts_dict = contacts.get(rid, {})
    rid_bookings_list = bookings.get(rid, [])
    purged_contacts = 0
    purged_bookings = 0
    purged_convs = 0
    phones_to_purge = []

    # 1. Identify test contacts
    for phone, ct in list(rid_contacts_dict.items()):
        name = (ct.get("name") or "").lower()
        if "test" in name:
            phones_to_purge.append(phone)
            del rid_contacts_dict[phone]
            purged_contacts += 1

    # 2. Purge associated bookings
    for phone in phones_to_purge:
        before = len(rid_bookings_list)
        bookings[rid] = [b for b in rid_bookings_list if b.get("phone") != phone]
        rid_bookings_list = bookings[rid]
        purged_bookings += before - len(rid_bookings_list)

    # 3. Purge associated conversations
    conv_keys_to_remove = []
    for phone in phones_to_purge:
        conv_key = f"{rid}:{phone}"
        if conv_key in conversations:
            conv_keys_to_remove.append(conv_key)
    for ck in conv_keys_to_remove:
        conversations.pop(ck, None)
        purged_convs += 1

    # 4. Persist to DB
    if db_pool and phones_to_purge:
        try:
            async with db_pool.acquire() as conn:
                for phone in phones_to_purge:
                    await conn.execute("DELETE FROM mt_contacts WHERE phone = $1 AND restaurant_id = $2::uuid", phone, rid)
                    await conn.execute("DELETE FROM mt_conversations WHERE conv_key = $1 AND restaurant_id = $2::uuid", f"{rid}:{phone}", rid)
                    await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid AND data->>'phone' = $2", rid, phone)
        except Exception as e:
            logger.error(f"Admin purge test data DB error: {e}")

    bump_version(rid)
    logger.info(f"Admin: purged test data for {rest.get('name')}: {purged_contacts} contacts, {purged_bookings} bookings, {purged_convs} conversations")
    return {"status": "ok", "purged": {"contacts": purged_contacts, "bookings": purged_bookings, "conversations": purged_convs}}


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


from app.templates.admin_dashboard import ADMIN_DASHBOARD_HTML  # refactored: ~1046 lines extracted



# ==============================================================
# STRIPE BILLING
# ==============================================================

def _last_day_of_current_month_iso() -> str:
    """Returns ISO date string of last day of current month in Paris TZ."""
    import calendar
    today = today_paris()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return f"{today.year:04d}-{today.month:02d}-{last_day:02d}"


@app.get("/api/subscription")
async def api_subscription(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    # Always refresh from DB so manual SQL updates (or out-of-band changes) are
    # picked up immediately by the frontend on the next poll.
    await _refresh_rest_from_db(rid)
    rest = restaurants_cache.get(rid, {})
    settings = rest.get("settings", {})
    status = settings.get("subscription_status", "trial")
    plan = settings.get("subscription_plan", "founder")
    cancel_pending = bool(settings.get("cancel_pending", False))
    cancel_effective = settings.get("cancel_effective_date", "") if cancel_pending else ""
    cancel_reason = settings.get("cancel_reason", "") if cancel_pending else ""
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
    effective_status = compute_effective_status(rest)
    access_blocked = effective_status not in ("active", "trial")
    if effective_status == "active":
        blocked_reason = None
    elif effective_status == "suspended":
        blocked_reason = "suspended"
    elif effective_status == "canceled":
        blocked_reason = "canceled"
    elif effective_status == "expired":
        blocked_reason = "trial_expired" if status == "trial" else "past_due"
    else:
        blocked_reason = None
    return {
        "status": status,                            # legacy : settings.subscription_status
        "effective_status": effective_status,        # nouveau : état réel croisé
        "plan": plan,
        "trial_days_left": trial_days_left,
        "trial_expired": trial_expired if status == "trial" else False,
        "cancel_pending": cancel_pending,
        "cancel_effective_date": cancel_effective,
        "cancel_reason": cancel_reason,
        "access_blocked": access_blocked,
        "blocked_reason": blocked_reason,
    }


@app.post("/api/account/cancel")
async def api_account_cancel(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = restaurants_cache.get(rid)
    if not rest:
        return JSONResponse(status_code=404, content={"error": "Restaurant introuvable"})
    try:
        data = await request.json()
    except Exception:
        data = {}
    reason = sanitize_input((data.get("reason") or "").strip(), 1000)

    settings = rest.setdefault("settings", {})
    if settings.get("cancel_pending"):
        return JSONResponse(status_code=400, content={"error": "Résiliation déjà demandée"})

    effective_date = _last_day_of_current_month_iso()
    settings["cancel_pending"] = True
    settings["cancel_effective_date"] = effective_date
    settings["cancel_reason"] = reason
    settings["cancel_requested_at"] = now_paris().isoformat()
    await db_save_restaurant(rid, rest)
    bump_version(rid)

    # Look up first_name (not in JWT, so query DB)
    first_name = ""
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT first_name FROM users WHERE id = $1::uuid", auth["user_id"])
                if row:
                    first_name = row["first_name"] or ""
        except Exception as e:
            logger.error(f"Cancel: user lookup failed: {e}")

    # Email user + admin (best-effort, non-blocking on failure)
    try:
        await send_cancellation_emails(
            user_email=auth.get("email", ""),
            first_name=first_name,
            restaurant_name=rest.get("name", "Restaurant"),
            effective_date=effective_date,
            reason=reason,
        )
    except Exception as e:
        logger.error(f"Cancellation email failed (non-blocking): {e}")

    logger.info(f"Account cancel scheduled for {rid[:8]}... effective={effective_date}")
    return {"status": "cancelled", "effective_date": effective_date}


@app.post("/api/account/cancel/undo")
async def api_account_cancel_undo(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = restaurants_cache.get(rid)
    if not rest:
        return JSONResponse(status_code=404, content={"error": "Restaurant introuvable"})
    settings = rest.setdefault("settings", {})
    if not settings.get("cancel_pending"):
        return JSONResponse(status_code=400, content={"error": "Aucune résiliation en attente"})
    settings["cancel_pending"] = False
    settings.pop("cancel_effective_date", None)
    settings.pop("cancel_reason", None)
    settings.pop("cancel_requested_at", None)
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    logger.info(f"Account cancel undone for {rid[:8]}...")
    return {"status": "active"}

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

def _sg(obj, key, default=None):
    """Safe getter for Stripe webhook objects.

    StripeObject inherits from dict in some SDK versions but not all — bracket
    access via `in` works in every version. Returns default if key absent or
    obj is None.
    """
    if obj is None:
        return default
    try:
        if key in obj:
            val = obj[key]
            return default if val is None else val
    except (TypeError, KeyError):
        pass
    # Fallback: attribute access (some StripeObject builds expose only that)
    try:
        val = getattr(obj, key)
        return default if val is None else val
    except AttributeError:
        return default


@app.post("/api/stripe/webhook")
async def api_stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe_mod.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe_mod.error.SignatureVerificationError as e:
        logger.error(f"Stripe webhook signature verification failed: {e}")
        return Response(status_code=400)
    except Exception as e:
        logger.error(f"Stripe webhook parse error: {e}")
        return Response(status_code=400)

    # Use _sg() helper instead of .get() — Stripe SDK versions vary on whether
    # StripeObject implements .get(); bracket-access via `in` works everywhere.
    etype = _sg(event, "type", "") or ""
    data = _sg(event, "data", None)
    obj = _sg(data, "object", None)
    if obj is None:
        logger.warning(f"Stripe webhook {etype} with no data.object")
        return {"status": "ok"}

    if etype == "checkout.session.completed":
        meta = _sg(obj, "metadata", None) or {}
        rid = _sg(meta, "restaurant_id", None)
        session_id = _sg(obj, "id", "") or ""
        if not rid:
            logger.warning(f"Stripe webhook checkout.session.completed without restaurant_id metadata: session={session_id[:20]}")
            return {"status": "ok"}
        meta_type = _sg(meta, "type", "subscription") or "subscription"
        if meta_type == "wallet_topup":
            # Wallet recharge — credit balance and log transaction (idempotent)
            try:
                amount_cents = int(_sg(meta, "amount_cents", 0) or 0)
            except (TypeError, ValueError):
                amount_cents = 0
            if amount_cents > 0:
                ok = await credit_wallet(
                    rid, amount_cents,
                    description=f"Recharge Stripe ({amount_cents/100:.2f} €)",
                    stripe_session_id=session_id,
                )
                if ok:
                    logger.info(f"Stripe: wallet topup +{amount_cents}c for {rid[:8]}... session={session_id[:20]}")
                else:
                    logger.info(f"Stripe: wallet topup skipped (already processed) session={session_id[:20]}")
        else:
            # Subscription activation — atomic DB update first (works even if cache cold/empty),
            # then sync the in-memory cache, then notify by email.
            plan = _sg(meta, "plan", "founder") or "founder"
            sub_id = _sg(obj, "subscription", "") or ""
            persisted = False
            if db_pool:
                try:
                    async with db_pool.acquire() as conn:
                        await conn.execute("""
                            UPDATE restaurants SET
                                settings = COALESCE(settings, '{}'::jsonb) || jsonb_build_object(
                                    'stripe_subscription_id', $2::text,
                                    'subscription_plan', $3::text,
                                    'subscription_status', 'active'
                                ),
                                status = 'active',
                                updated_at = NOW()
                            WHERE id = $1::uuid
                        """, rid, sub_id, plan)
                        persisted = True
                except Exception as e:
                    logger.error(f"Stripe webhook DB persist failed for {rid[:8]}: {e}")
            # Sync the cache (if loaded) so subsequent /api/subscription calls return fresh data
            rest = restaurants_cache.get(rid)
            if rest is not None:
                rest["status"] = "active"
                rest.setdefault("settings", {})["stripe_subscription_id"] = sub_id
                rest["settings"]["subscription_plan"] = plan
                rest["settings"]["subscription_status"] = "active"
            bump_version(rid)
            logger.info(f"Stripe: subscription activated for {rid[:8]}... plan={plan} persisted={persisted}")
            # Send welcome emails (best-effort, non-blocking)
            if persisted:
                user_email = ""
                first_name = ""
                rest_name = (rest or {}).get("name", "Restaurant") if rest else "Restaurant"
                if db_pool:
                    try:
                        async with db_pool.acquire() as conn:
                            row = await conn.fetchrow(
                                "SELECT u.email, u.first_name, r.name FROM users u JOIN restaurants r ON r.id = u.restaurant_id WHERE u.restaurant_id = $1::uuid LIMIT 1",
                                rid,
                            )
                            if row:
                                user_email = row["email"] or ""
                                first_name = row["first_name"] or ""
                                rest_name = row["name"] or rest_name
                    except Exception as e:
                        logger.error(f"Stripe webhook user lookup failed: {e}")
                if user_email:
                    try:
                        await send_subscription_welcome_emails(user_email, first_name, rest_name, plan)
                    except Exception as e:
                        logger.error(f"Subscription welcome email failed (non-blocking): {e}")
    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        cid = _sg(obj, "customer", None)
        rid = find_restaurant_by_stripe_customer(cid) if cid else None
        if rid:
            status = "canceled" if etype.endswith("deleted") else (_sg(obj, "status", "active") or "active")
            set_restaurant_stripe_config(rid, "subscription_status", status)
            bump_version(rid)
    elif etype == "invoice.payment_failed":
        cid = _sg(obj, "customer", None)
        rid = find_restaurant_by_stripe_customer(cid) if cid else None
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

"""
Orso — Agent IA WhatsApp pour la Restauration
Version 4.0 — PostgreSQL persistent + Dashboard + Web Chat
"""

import os
import json
import logging
import hashlib
import secrets
import re as re_mod
from datetime import datetime, date, time, timedelta
from contextlib import asynccontextmanager

import anthropic
import httpx
import asyncpg
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ==============================================================
# CONFIG
# ==============================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "orso-verify-2026")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
PORT = int(os.getenv("PORT", 8000))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "orso2026").strip()
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", secrets.token_urlsafe(32))
DATABASE_URL = os.getenv("DATABASE_URL", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orso")

# ==============================================================
# IN-MEMORY DATABASE
# ==============================================================

restaurants = {}
conversations = {}
bookings = []

# Floor plan tables
floor_tables = {}  # phone_number_id: [{"id": "T1", "seats": 4, "zone": "salle", ...}]

# Table availability per slot: {phone_number_id: {"12:30": {"T1": "available", "T2": "booked:R1"}, ...}}
table_slots = {}

# Review followup queue: [{"phone": ..., "name": ..., "time": ..., "restaurant_pid": ..., "scheduled_at": ...}]
review_queue = []

# Data version counter - incremented on any change, used for real-time dashboard
data_version = 0
def bump_version():
    global data_version
    data_version += 1

# CRM Contacts database
contacts = {}  # phone: {"name":..,"phone":..,"first_seen":..,"last_seen":..,"visits":0,"bookings":[],"tags":[],"language":"fr","notes":""}

# Google Review link per restaurant
GOOGLE_REVIEW_LINK = os.getenv("GOOGLE_REVIEW_LINK", "")

# Database connection pool
db_pool = None


async def init_db():
    """Initialize database pool and create tables."""
    global db_pool
    if not DATABASE_URL:
        logger.warning("⚠️ No DATABASE_URL — running in-memory only (data lost on restart)")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS restaurant_config (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS contacts (
                    phone TEXT PRIMARY KEY,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conv_key TEXT PRIMARY KEY,
                    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS review_queue (
                    id SERIAL PRIMARY KEY,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS restaurant_status (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
        logger.info("✅ Database connected and tables created")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        db_pool = None


async def db_save(table: str, key: str, data: dict):
    """Save a record to the database."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(f"""
                INSERT INTO {table} (id, data, updated_at) VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE SET data = $2::jsonb, updated_at = NOW()
            """, key, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save error ({table}/{key}): {e}")


async def db_save_contact(phone: str, data: dict):
    """Save a contact to the database."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO contacts (phone, data, updated_at) VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (phone) DO UPDATE SET data = $2::jsonb, updated_at = NOW()
            """, phone, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save contact error: {e}")


async def db_save_conversation(conv_key: str, messages: list):
    """Save conversation messages to the database."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO conversations (conv_key, messages, updated_at) VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (conv_key) DO UPDATE SET messages = $2::jsonb, updated_at = NOW()
            """, conv_key, json.dumps(messages, default=str))
    except Exception as e:
        logger.error(f"DB save conversation error: {e}")


async def db_save_booking(booking: dict):
    """Save a booking to the database."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bookings (id, data, created_at) VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE SET data = $2::jsonb
            """, booking["id"], json.dumps(booking, default=str))
    except Exception as e:
        logger.error(f"DB save booking error: {e}")


async def db_save_review(review: dict):
    """Save a review queue item."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO review_queue (data, created_at) VALUES ($1::jsonb, NOW())
            """, json.dumps(review, default=str))
    except Exception as e:
        logger.error(f"DB save review error: {e}")


async def db_load_all():
    """Load all data from database into in-memory caches."""
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            # Load restaurant config
            rows = await conn.fetch("SELECT id, data FROM restaurant_config")
            for row in rows:
                d = json.loads(row["data"])
                pid = row["id"]
                if "context" in d:
                    restaurants[pid] = d
                    logger.info(f"📦 Loaded restaurant config: {d.get('name', pid)}")

            # Load contacts
            rows = await conn.fetch("SELECT phone, data FROM contacts ORDER BY updated_at DESC LIMIT 10000")
            for row in rows:
                contacts[row["phone"]] = json.loads(row["data"])
            logger.info(f"📦 Loaded {len(rows)} contacts")

            # Load bookings
            rows = await conn.fetch("SELECT id, data FROM bookings ORDER BY created_at DESC LIMIT 500")
            for row in rows:
                bookings.append(json.loads(row["data"]))
            logger.info(f"📦 Loaded {len(rows)} bookings")

            # Load conversations
            rows = await conn.fetch("SELECT conv_key, messages FROM conversations ORDER BY updated_at DESC LIMIT 1000")
            for row in rows:
                conversations[row["conv_key"]] = json.loads(row["messages"])
            logger.info(f"📦 Loaded {len(rows)} conversations")

            # Load review queue
            rows = await conn.fetch("SELECT data FROM review_queue ORDER BY created_at DESC LIMIT 200")
            for row in rows:
                review_queue.append(json.loads(row["data"]))
            logger.info(f"📦 Loaded {len(rows)} review queue items")

            # Load restaurant status
            rows = await conn.fetch("SELECT id, data FROM restaurant_status")
            for row in rows:
                restaurant_status[row["id"]] = json.loads(row["data"])
            logger.info(f"📦 Loaded restaurant status")

    except Exception as e:
        logger.error(f"DB load error: {e}")

# Restaurant status (dynamic, updated by owner)
restaurant_status = {
    # phone_number_id: {
    #   "status": "open" | "full_tonight" | "full_lunch" | "closed_today" | "closed_until",
    #   "message": "Custom message from owner",
    #   "closed_dates": ["2026-03-01", ...],
    #   "full_dates": {"2026-02-25": "soir", ...},
    #   "temp_message": "Message temporaire affiché aux clients",
    #   "updated_at": "2026-02-24T19:00:00"
    # }
}

# Stats tracking
stats = {
    # phone_number_id: {
    #   "messages_today": 0,
    #   "bookings_today": 0,
    #   "languages": {"fr": 10, "en": 5, "it": 2},
    #   "last_reset": "2026-02-24"
    # }
}


# ==============================================================
# SAMPLE RESTAURANT
# ==============================================================

def load_sample_restaurant():
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "1025551323971723")
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    owner_phone = os.getenv("OWNER_PHONE", "")

    restaurants[phone_number_id] = {
        "name": os.getenv("RESTAURANT_NAME", "Le Cosi Nice"),
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "owner_phone": owner_phone,
        "context": {
            "description": os.getenv("RESTAURANT_DESCRIPTION", ""),
            "menu": os.getenv("RESTAURANT_MENU", ""),
            "hours": os.getenv("RESTAURANT_HOURS", ""),
            "address": os.getenv("RESTAURANT_ADDRESS", ""),
            "phone": os.getenv("RESTAURANT_PHONE", ""),
            "tone": os.getenv("RESTAURANT_TONE", ""),
            "languages": "français, anglais, italien",
            "special_info": os.getenv("RESTAURANT_SPECIAL_INFO", ""),
            "booking_link": os.getenv("RESTAURANT_BOOKING_LINK", ""),
            "allergens_policy": "Nous prenons les allergies très au sérieux. Merci de préciser vos allergies, notre chef adapte les plats.",
        },
    }

    # Init status
    restaurant_status[phone_number_id] = {
        "status": "open",
        "message": "",
        "closed_dates": [],
        "full_dates": {},
        "temp_message": "",
        "updated_at": datetime.utcnow().isoformat(),
    }

    # Init stats
    stats[phone_number_id] = {
        "messages_today": 0,
        "bookings_today": 0,
        "languages": {},
        "last_reset": date.today().isoformat(),
    }

    logger.info(f"✅ Restaurant chargé : {restaurants[phone_number_id]['name']}")
    logger.info(f"🔗 Dashboard URL : /dashboard/{DASHBOARD_SECRET}")
    logger.info(f"🔑 Dashboard password : {DASHBOARD_PASSWORD}")

    # Init floor plan
    floor_tables[phone_number_id] = json.loads(os.getenv("FLOOR_TABLES", json.dumps([
        {"id": "T1", "seats": 2, "zone": "salle", "x": 8, "y": 18, "shape": "round"},
        {"id": "T2", "seats": 2, "zone": "salle", "x": 22, "y": 18, "shape": "round"},
        {"id": "T3", "seats": 4, "zone": "salle", "x": 8, "y": 42, "shape": "rect"},
        {"id": "T4", "seats": 4, "zone": "salle", "x": 22, "y": 42, "shape": "rect"},
        {"id": "T5", "seats": 6, "zone": "salle", "x": 15, "y": 66, "shape": "rect"},
        {"id": "T6", "seats": 4, "zone": "salle", "x": 38, "y": 18, "shape": "rect"},
        {"id": "T7", "seats": 4, "zone": "salle", "x": 38, "y": 42, "shape": "rect"},
        {"id": "T8", "seats": 8, "zone": "salle", "x": 38, "y": 66, "shape": "rect"},
        {"id": "T9", "seats": 2, "zone": "terrasse", "x": 60, "y": 18, "shape": "round"},
        {"id": "T10", "seats": 2, "zone": "terrasse", "x": 74, "y": 18, "shape": "round"},
        {"id": "T11", "seats": 4, "zone": "terrasse", "x": 60, "y": 42, "shape": "rect"},
        {"id": "T12", "seats": 4, "zone": "terrasse", "x": 74, "y": 42, "shape": "rect"},
        {"id": "T13", "seats": 6, "zone": "terrasse", "x": 67, "y": 66, "shape": "rect"},
        {"id": "B1", "seats": 2, "zone": "bar", "x": 88, "y": 18, "shape": "round"},
        {"id": "B2", "seats": 2, "zone": "bar", "x": 88, "y": 38, "shape": "round"},
        {"id": "B3", "seats": 2, "zone": "bar", "x": 88, "y": 58, "shape": "round"},
    ])))

    # Init table slots for today
    table_slots[phone_number_id] = {}
    init_daily_slots(phone_number_id)


# ==============================================================
# FLOOR PLAN & TABLE MANAGEMENT
# ==============================================================

MIDI_SLOTS = ["12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45","14:00","14:15"]
SOIR_SLOTS = ["19:00","19:15","19:30","19:45","20:00","20:15","20:30","20:45","21:00","21:15","21:30","21:45","22:00","22:15","22:30"]
ALL_SLOTS = MIDI_SLOTS + SOIR_SLOTS


def init_daily_slots(phone_number_id: str):
    """Initialize all table slots for the day."""
    tables = floor_tables.get(phone_number_id, [])
    slots = {}
    for slot_time in ALL_SLOTS:
        slots[slot_time] = {}
        for t in tables:
            slots[slot_time][t["id"]] = "available"
    table_slots[phone_number_id] = slots


def find_best_table(phone_number_id: str, slot_time: str, covers: int, zone_pref: str = None) -> str | None:
    """Find best available table for a given slot and party size."""
    tables = floor_tables.get(phone_number_id, [])
    slots = table_slots.get(phone_number_id, {}).get(slot_time, {})

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
        # Fallback: any zone
        for t in tables:
            if slots.get(t["id"]) != "available":
                continue
            if t["seats"] < covers:
                continue
            candidates.append(t)

    if not candidates:
        return None

    # Best fit: smallest table that fits
    candidates.sort(key=lambda t: t["seats"])
    return candidates[0]["id"]


def assign_table(phone_number_id: str, slot_time: str, table_id: str, booking_id: str):
    """Mark a table as booked for a slot."""
    if phone_number_id in table_slots and slot_time in table_slots[phone_number_id]:
        table_slots[phone_number_id][slot_time][table_id] = f"booked:{booking_id}"


def release_table(phone_number_id: str, slot_time: str, table_id: str):
    """Release a table for a slot."""
    if phone_number_id in table_slots and slot_time in table_slots[phone_number_id]:
        table_slots[phone_number_id][slot_time][table_id] = "available"


def get_available_slots(phone_number_id: str, covers: int, service: str = None) -> list:
    """Get list of available time slots for a given party size."""
    slots_to_check = ALL_SLOTS
    if service == "midi":
        slots_to_check = MIDI_SLOTS
    elif service == "soir":
        slots_to_check = SOIR_SLOTS

    available = []
    for slot_time in slots_to_check:
        if find_best_table(phone_number_id, slot_time, covers):
            available.append(slot_time)
    return available


def get_slot_summary(phone_number_id: str) -> dict:
    """Get summary of availability for all slots."""
    tables = floor_tables.get(phone_number_id, [])
    slots = table_slots.get(phone_number_id, {})
    summary = {}
    for slot_time in ALL_SLOTS:
        slot_data = slots.get(slot_time, {})
        total = len(tables)
        avail = sum(1 for t in tables if slot_data.get(t["id"]) == "available")
        summary[slot_time] = {"total": total, "available": avail, "booked": total - avail}
    return summary


def build_availability_context(phone_number_id: str) -> str:
    """Build a text summary of current availability for the AI agent."""
    summary = get_slot_summary(phone_number_id)
    tables = floor_tables.get(phone_number_id, [])
    total_tables = len(tables)

    midi_avail = [t for t in MIDI_SLOTS if summary[t]["available"] > 0]
    soir_avail = [t for t in SOIR_SLOTS if summary[t]["available"] > 0]

    lines = ["\n📅 DISPONIBILITÉS EN TEMPS RÉEL :"]

    if not midi_avail:
        lines.append("MIDI : COMPLET (aucune table disponible)")
    else:
        lines.append(f"MIDI : {len(midi_avail)} créneaux disponibles ({', '.join(midi_avail[:5])}{'...' if len(midi_avail) > 5 else ''})")

    if not soir_avail:
        lines.append("SOIR : COMPLET (aucune table disponible)")
    else:
        lines.append(f"SOIR : {len(soir_avail)} créneaux disponibles ({', '.join(soir_avail[:5])}{'...' if len(soir_avail) > 5 else ''})")

    # Capacity info
    max_seats = max(t["seats"] for t in tables) if tables else 0
    lines.append(f"Capacité max par table : {max_seats} personnes")
    lines.append(f"Zones : salle, terrasse, bar")

    lines.append("")
    lines.append("INSTRUCTIONS RÉSERVATION :")
    lines.append("- Quand un client veut réserver, collecte : nombre de personnes, heure souhaitée, nom, et préférence zone (salle/terrasse) si demandée.")
    lines.append("- Si le créneau demandé est complet, propose les créneaux les plus proches disponibles.")
    lines.append("- Si un créneau est dispo, confirme la réservation en précisant le créneau.")
    lines.append("- NE JAMAIS mentionner les numéros de table au client. Dis simplement que la réservation est confirmée.")

    return "\n".join(lines)


# ==============================================================
# REVIEW FOLLOWUP (post-meal Google review request)
# ==============================================================

async def schedule_review_followup(phone_number_id: str, customer_phone: str, customer_name: str, booking_time: str):
    """Schedule a review request to be sent 2h after the booking time."""
    review_queue.append({
        "phone": customer_phone,
        "name": customer_name,
        "booking_time": booking_time,
        "restaurant_pid": phone_number_id,
        "scheduled_at": datetime.utcnow().isoformat(),
        "sent": False,
    })
    logger.info(f"📋 Review followup scheduled for {customer_name} ({customer_phone})")
    await db_save_review(review_queue[-1])


async def send_review_request(phone_number_id: str, customer_phone: str, customer_name: str):
    """Send the initial review request message."""
    restaurant = restaurants.get(phone_number_id)
    if not restaurant:
        return

    name = customer_name.split()[0] if customer_name else ""
    greeting = f"Bonjour {name} ! " if name else "Bonjour ! "

    message = (
        f"{greeting}Merci d'avoir choisi {restaurant['name']} ! 😊\n\n"
        f"Comment s'est passé votre repas ? Votre avis nous intéresse !"
    )

    await send_whatsapp_message(
        phone_number_id, restaurant["access_token"], customer_phone, message
    )
    logger.info(f"⭐ Review request sent to {customer_phone}")


async def handle_review_response(phone_number_id: str, customer_phone: str, message_text: str) -> str | None:
    """Check if user is responding to a review request. Returns response or None."""
    # Check if this user has a pending review
    pending = [r for r in review_queue if r["phone"] == customer_phone and r["sent"] and not r.get("responded")]
    if not pending:
        return None

    restaurant = restaurants.get(phone_number_id)
    if not restaurant:
        return None

    # Use Claude to analyze sentiment
    sentiment_prompt = """Analyze the following restaurant review response. 
Reply with ONLY one word: POSITIVE, NEGATIVE, or NEUTRAL.
The response is: """

    client = get_claude()
    try:
        resp = await client.messages.create(
            model=CLAUDE_MODEL, max_tokens=10,
            system=sentiment_prompt,
            messages=[{"role": "user", "content": message_text}],
            temperature=0,
        )
        sentiment = resp.content[0].text.strip().upper()
    except Exception:
        sentiment = "NEUTRAL"

    # Mark as responded
    for r in pending:
        r["responded"] = True
        r["sentiment"] = sentiment
        r["response"] = message_text[:200]

    google_link = GOOGLE_REVIEW_LINK or os.getenv("GOOGLE_REVIEW_LINK", "")

    if "POSITIVE" in sentiment:
        if google_link:
            return (
                f"Merci beaucoup, c'est adorable ! 🥰\n\n"
                f"Votre avis compte énormément pour nous et notre équipe. "
                f"Si vous avez 30 secondes, un petit mot sur Google nous aiderait beaucoup :\n\n"
                f"⭐ {google_link}\n\n"
                f"Merci et à très bientôt !"
            )
        else:
            return "Merci beaucoup pour votre retour ! 🥰 Nous sommes ravis que vous ayez passé un bon moment. À très bientôt !"
    elif "NEGATIVE" in sentiment:
        return (
            f"Merci pour votre retour, nous sommes désolés que l'expérience n'ait pas été à la hauteur. 😔\n\n"
            f"Votre avis est précieux et nous allons le transmettre directement à notre équipe. "
            f"Nous ferons tout pour nous améliorer.\n\n"
            f"N'hésitez pas à nous donner plus de détails, nous prenons chaque retour très au sérieux. 🙏"
        )
    else:
        if google_link:
            return (
                f"Merci pour votre retour ! 😊\n\n"
                f"Si vous souhaitez partager votre expérience, votre avis sur Google nous aiderait beaucoup :\n\n"
                f"⭐ {google_link}\n\n"
                f"À très bientôt !"
            )
        else:
            return "Merci pour votre retour ! 😊 À très bientôt !"


async def process_review_queue():
    """Check and send pending review requests (called periodically)."""
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    for r in review_queue:
        if r["sent"] or r.get("responded"):
            continue
        # Calculate when to send: booking_time + 2 hours
        booking_time_str = r.get("booking_time", "")
        if booking_time_str and ":" in booking_time_str:
            try:
                bh, bm = booking_time_str.split(":")
                # Build datetime for today at booking time
                meal_dt = datetime.strptime(f"{today} {int(bh):02d}:{int(bm):02d}", "%Y-%m-%d %H:%M")
                send_after = meal_dt.replace(hour=meal_dt.hour + 2) if meal_dt.hour < 22 else meal_dt.replace(hour=23, minute=0)
                if now >= send_after:
                    restaurant_pid = r["restaurant_pid"]
                    await send_review_request(restaurant_pid, r["phone"], r["name"])
                    r["sent"] = True
                    logger.info(f"⭐ Review sent to {r['name']} (meal was at {booking_time_str})")
            except Exception as e:
                logger.warning(f"Review timing error: {e}")
                # Fallback: send 3h after creation
                scheduled = datetime.fromisoformat(r["scheduled_at"])
                if (now - scheduled).total_seconds() > 10800:
                    await send_review_request(r["restaurant_pid"], r["phone"], r["name"])
                    r["sent"] = True
        else:
            # No booking time - fallback: send 3h after creation
            scheduled = datetime.fromisoformat(r["scheduled_at"])
            if (now - scheduled).total_seconds() > 10800:
                await send_review_request(r["restaurant_pid"], r["phone"], r["name"])
                r["sent"] = True


# ==============================================================
# OWNER COMMANDS
# ==============================================================

OWNER_COMMANDS_HELP = """ *Commandes Orso :*

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


async def handle_owner_command(phone_number_id: str, message: str) -> str:
    """Handle commands from the restaurant owner."""
    msg = message.strip().upper()
    status = restaurant_status.get(phone_number_id, {})
    today = date.today()

    # AIDE / HELP
    if msg in ("AIDE", "HELP", "?"):
        return OWNER_COMMANDS_HELP

    # STATUS
    if msg == "STATUS":
        s = status.get("status", "open")
        status_map = {
            "open": "🟢 Ouvert",
            "full_tonight": "🔴 Complet ce soir",
            "full_lunch": "🔴 Complet ce midi",
            "closed_today": "🟡 Fermé aujourd'hui",
        }
        text = f"📊 *Statut actuel :* {status_map.get(s, s)}\n"
        if status.get("temp_message"):
            text += f"💬 Message actif : \"{status['temp_message']}\"\n"
        if status.get("closed_dates"):
            text += f"📅 Fermetures prévues : {', '.join(status['closed_dates'])}\n"
        if status.get("full_dates"):
            text += f"📅 Complet : {', '.join(f'{d} ({p})' for d, p in status['full_dates'].items())}\n"
        return text

    # STATS
    if msg == "STATS":
        st = stats.get(phone_number_id, {})
        # Reset if new day
        if st.get("last_reset") != today.isoformat():
            st["messages_today"] = 0
            st["bookings_today"] = 0
            st["last_reset"] = today.isoformat()
        return (
            f"📈 *Statistiques du jour :*\n\n"
            f"💬 Messages traités : {st.get('messages_today', 0)}\n"
            f"🍽️ Réservations : {st.get('bookings_today', 0)}\n"
            f"🌍 Langues : {', '.join(f'{l}: {c}' for l, c in st.get('languages', {}).items())}\n"
            f"👥 Conversations actives : {sum(1 for k in conversations if k.startswith(phone_number_id))}"
        )

    # COMPLET CE SOIR
    if msg in ("COMPLET CE SOIR", "COMPLET SOIR", "FULL TONIGHT"):
        status["status"] = "full_tonight"
        status["full_dates"][today.isoformat()] = "soir"
        status["updated_at"] = datetime.utcnow().isoformat()
        return "🔴 C'est noté ! L'agent informe les clients que vous êtes complet ce soir. Envoyez *OUVERT* pour revenir à la normale."

    # COMPLET MIDI
    if msg in ("COMPLET MIDI", "COMPLET CE MIDI", "FULL LUNCH"):
        status["status"] = "full_lunch"
        status["full_dates"][today.isoformat()] = "midi"
        status["updated_at"] = datetime.utcnow().isoformat()
        return "🔴 C'est noté ! L'agent informe les clients que vous êtes complet ce midi. Envoyez *OUVERT* pour revenir à la normale."

    # COMPLET [date]
    if msg.startswith("COMPLET "):
        date_str = msg.replace("COMPLET ", "").strip()
        try:
            d = datetime.strptime(date_str, "%d/%m").replace(year=today.year).date()
            status["full_dates"][d.isoformat()] = "journée"
            status["updated_at"] = datetime.utcnow().isoformat()
            return f"🔴 Noté : complet le {d.strftime('%d/%m/%Y')}."
        except ValueError:
            return "❌ Format de date non reconnu. Utilisez : COMPLET 28/02"

    # FERMÉ AUJOURD'HUI
    if msg in ("FERMÉ AUJOURD'HUI", "FERME AUJOURD'HUI", "FERMÉ", "FERME", "CLOSED TODAY"):
        status["status"] = "closed_today"
        status["closed_dates"].append(today.isoformat())
        status["updated_at"] = datetime.utcnow().isoformat()
        return "🟡 Fermeture exceptionnelle enregistrée pour aujourd'hui. L'agent prévient les clients. Envoyez *OUVERT* demain."

    # FERMÉ [date]
    if msg.startswith("FERMÉ ") or msg.startswith("FERME "):
        date_str = msg.replace("FERMÉ ", "").replace("FERME ", "").strip()
        # Handle "DU xx/xx AU xx/xx"
        if "AU" in date_str:
            parts = date_str.split("AU")
            try:
                start = datetime.strptime(parts[0].replace("DU", "").strip(), "%d/%m").replace(year=today.year).date()
                end = datetime.strptime(parts[1].strip(), "%d/%m").replace(year=today.year).date()
                current = start
                while current <= end:
                    status["closed_dates"].append(current.isoformat())
                    current += timedelta(days=1)
                status["updated_at"] = datetime.utcnow().isoformat()
                return f"🟡 Fermeture enregistrée du {start.strftime('%d/%m')} au {end.strftime('%d/%m')}."
            except ValueError:
                return "❌ Format non reconnu. Utilisez : FERMÉ DU 01/03 AU 15/03"
        else:
            try:
                d = datetime.strptime(date_str, "%d/%m").replace(year=today.year).date()
                status["closed_dates"].append(d.isoformat())
                status["updated_at"] = datetime.utcnow().isoformat()
                return f"🟡 Fermeture enregistrée le {d.strftime('%d/%m/%Y')}."
            except ValueError:
                return "❌ Format non reconnu. Utilisez : FERMÉ 01/03"

    # OUVERT
    if msg in ("OUVERT", "OPEN", "NORMAL"):
        status["status"] = "open"
        status["updated_at"] = datetime.utcnow().isoformat()
        return "🟢 Statut remis à *ouvert*. L'agent reprend normalement."

    # MESSAGE [texte]
    if msg.startswith("MESSAGE "):
        text = message[8:].strip()  # Keep original case
        if text.upper() == "OFF":
            status["temp_message"] = ""
            status["updated_at"] = datetime.utcnow().isoformat()
            return "💬 Message temporaire supprimé."
        else:
            status["temp_message"] = text
            status["updated_at"] = datetime.utcnow().isoformat()
            return f"💬 Message temporaire activé :\n\"{text}\"\n\nLes clients verront ce message. Envoyez *MESSAGE OFF* pour le retirer."

    # Not a command — treat as regular message but warn
    return None  # Return None = not a command, process normally


# ==============================================================
# CLAUDE AI
# ==============================================================

claude_client = None


def get_claude():
    global claude_client
    if claude_client is None:
        claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return claude_client


def build_system_prompt(restaurant: dict, phone_number_id: str, customer_phone: str = None) -> str:
    ctx = restaurant["context"]
    status = restaurant_status.get(phone_number_id, {})

    # Build status context
    status_context = ""
    current_status = status.get("status", "open")
    today_str = date.today().isoformat()

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

    # Check future closed dates
    future_closed = [d for d in status.get("closed_dates", []) if d > today_str]
    if future_closed:
        status_context += f"\nFermetures prévues : {', '.join(future_closed)}. Si le client veut réserver à ces dates, informe-le que c'est fermé."

    # Temp message
    temp_msg = ""
    if status.get("temp_message"):
        temp_msg = f"\n📢 MESSAGE DU RESTAURANT : {status['temp_message']}. Mentionne cette info si c'est pertinent pour le client."

    booking_section = ""
    if ctx.get("booking_link"):
        booking_section = f"\nRÉSERVATION : Si le client veut réserver, envoie-lui ce lien : {ctx['booking_link']}"
    else:
        booking_section = "\nRÉSERVATION : Si le client veut réserver, collecte : nombre de personnes, date, heure, nom. Confirme et dis que le restaurant va valider."

    # Availability context from floor plan
    availability_context = build_availability_context(phone_number_id)

    # === CRM CUSTOMER PROFILE ===
    customer_context = ""
    if customer_phone and customer_phone in contacts:
        ct = contacts[customer_phone]
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
        # Add booking history
        client_bookings = [b for b in bookings if b.get("phone") == customer_phone]
        if client_bookings:
            recent = client_bookings[-3:]  # Last 3 bookings
            bk_lines = []
            for b in recent:
                bk_lines.append(f"  - {b.get('covers', '?')}p, {b.get('booking_time') or b.get('time', '?')}, table {b.get('table', '?')}")
            customer_context += f"\n- Dernieres reservations :\n" + "\n".join(bk_lines)
            # Detect preferences from history
            tables_used = [b.get("table", "") for b in client_bookings if b.get("table")]
            zones_used = [b.get("zone", "") for b in client_bookings if b.get("zone")]
            avg_covers = sum(b.get("covers", 0) for b in client_bookings) / len(client_bookings) if client_bookings else 0
            if tables_used:
                from collections import Counter
                fav_table = Counter(tables_used).most_common(1)[0][0]
                customer_context += f"\n- Table favorite : {fav_table}"
            if zones_used:
                from collections import Counter
                fav_zone = Counter(zones_used).most_common(1)[0][0]
                customer_context += f"\n- Zone preferee : {fav_zone}"
            if avg_covers > 0:
                customer_context += f"\n- Taille groupe habituelle : {round(avg_covers)}p"
        
        if ct.get("visits", 0) >= 3:
            customer_context += "\n- ⭐ CLIENT FIDELE — traite-le avec une attention particuliere, mentionne que tu es content de le/la revoir."
        elif ct.get("visits", 0) == 0:
            customer_context += "\n- 🆕 NOUVEAU CLIENT — sois particulierement accueillant et propose de l'aider a choisir."

    return f"""Tu es l'assistant virtuel du restaurant "{restaurant['name']}".

RÔLE : Tu réponds aux clients sur WhatsApp de manière naturelle et chaleureuse.
Tu parles comme un membre de l'équipe, pas comme un robot.

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
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
            logger.info(f"✅ Message envoyé à {to}")
        except httpx.HTTPError as e:
            logger.error(f"❌ Erreur envoi WhatsApp: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"   Détail: {e.response.text}")


async def mark_as_read(phone_number_id: str, access_token: str, message_id: str):
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, headers=headers, timeout=5.0)
        except Exception:
            pass


def parse_webhook(body: dict) -> dict | None:
    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        if "messages" not in value:
            return None
        message = value["messages"][0]
        if message.get("type") != "text":
            return None
        return {
            "phone_number_id": value["metadata"]["phone_number_id"],
            "from": message["from"],
            "message_id": message["id"],
            "text": message["text"]["body"],
            "name": value.get("contacts", [{}])[0].get("profile", {}).get("name", ""),
        }
    except (KeyError, IndexError) as e:
        logger.warning(f"Parse error: {e}")
        return None


# ==============================================================
# CONVERSATION & STATS
# ==============================================================

def get_conversation(phone_number_id: str, customer_phone: str) -> list:
    key = f"{phone_number_id}:{customer_phone}"
    if key not in conversations:
        conversations[key] = []
    return conversations[key]


def save_message(phone_number_id: str, customer_phone: str, role: str, content: str):
    key = f"{phone_number_id}:{customer_phone}"
    if key not in conversations:
        conversations[key] = []
    conversations[key].append({
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat()
    })
    conversations[key] = conversations[key][-20:]
    bump_version()
    # Persist to DB in background
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_conversation(key, conversations[key]))
    except Exception:
        pass


def detect_preferences(customer_phone: str, message: str):
    """Auto-detect dietary preferences, zone preferences etc from customer messages."""
    if customer_phone not in contacts:
        return
    ct = contacts[customer_phone]
    prefs = ct.get("preferences", "")
    msg = message.lower()
    new_prefs = []

    # Dietary
    dietary_map = {
        "vegetarien": ["végétarien", "vegetarien", "vegetarian", "veggie", "pas de viande", "sans viande"],
        "vegan": ["vegan", "végan", "vegane", "plant-based", "pas de produit animal"],
        "sans gluten": ["gluten", "gluten-free", "sans gluten", "coeliaque", "celiac"],
        "halal": ["halal"],
        "casher": ["casher", "kosher"],
        "allergique fruits de mer": ["allergi", "fruits de mer", "crustac", "shellfish"],
        "allergique noix": ["noix", "noisette", "amande", "nuts", "arachide"],
        "sans lactose": ["lactose", "sans lait", "dairy-free", "intolerant au lait"],
    }
    for pref, keywords in dietary_map.items():
        if any(kw in msg for kw in keywords) and pref not in prefs:
            new_prefs.append(pref)

    # Zone preference
    if "terrasse" in msg and "terrasse" not in prefs:
        new_prefs.append("prefere terrasse")
    elif "interieur" in msg or "intérieur" in msg or "dedans" in msg:
        if "prefere interieur" not in prefs:
            new_prefs.append("prefere interieur")

    # Group size hints
    if any(w in msg for w in ["anniversaire", "birthday", "fete", "fête", "celebration"]):
        if "evenement" not in prefs:
            new_prefs.append("evenement special")

    if new_prefs:
        existing = [p.strip() for p in prefs.split(",") if p.strip()] if prefs else []
        existing.extend(new_prefs)
        ct["preferences"] = ", ".join(existing)
        logger.info(f"🏷️ Preferences detected for {ct.get('name', customer_phone)}: {', '.join(new_prefs)}")



    st = stats.get(phone_number_id, {})
    today = date.today().isoformat()
    if st.get("last_reset") != today:
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
    stats[phone_number_id] = st


def track_contact(customer_phone: str, customer_name: str = "", language: str = "fr"):
    """Track/update a customer contact in the CRM."""
    now = datetime.utcnow().isoformat()
    if customer_phone not in contacts:
        contacts[customer_phone] = {
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
        c = contacts[customer_phone]
        c["last_seen"] = now
        c["visits"] = c.get("visits", 0) + 1
        if customer_name and customer_name != customer_phone:
            c["name"] = customer_name
        if language:
            c["language"] = language
    # Persist to DB in background
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_contact(customer_phone, contacts[customer_phone]))
    except Exception:
        pass


# ==============================================================
# NOTIFICATION
# ==============================================================

async def notify_owner(restaurant: dict, customer_phone: str, customer_name: str, message: str):
    booking_keywords = ["réserv", "reserv", "book", "table", "prenot"]
    is_booking = any(kw in message.lower() for kw in booking_keywords)
    if is_booking:
        # Try to extract time from message for auto table assignment
        import re
        time_match = re.search(r'(\d{1,2})[h:](\d{2})?', message)
        booking_time = None
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2) or 0)
            # Round to nearest 15 min
            m = (m // 15) * 15
            booking_time = f"{h:02d}:{m:02d}"

        # Try to extract covers - multiple patterns
        covers = 2  # default
        covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax|invit)', message.lower())
        if covers_match:
            covers = int(covers_match.group(1))
        else:
            # "pour 6" or "for 6" or "table de 6" or "6 personnes"
            covers_match2 = re.search(r'(?:pour|for|de|table)\s+(\d+)', message.lower())
            if covers_match2:
                covers = int(covers_match2.group(1))
            else:
                # "nous serons 6" or "on sera 4"
                covers_match3 = re.search(r'(?:serons|sera|sommes|seront|being)\s+(\d+)', message.lower())
                if covers_match3:
                    covers = int(covers_match3.group(1))
        # Sanity check
        if covers < 1 or covers > 30:
            covers = 2

        # Zone preference
        zone_pref = None
        if "terrasse" in message.lower():
            zone_pref = "terrasse"
        elif "bar" in message.lower():
            zone_pref = "bar"

        booking_id = f"R{len(bookings)+1}"

        # Auto assign table if time found
        assigned_table = None
        pid = restaurant["phone_number_id"]
        logger.info(f"🔍 Booking attempt: time={booking_time}, covers={covers}, pid={pid}")
        logger.info(f"🔍 Tables loaded: {len(floor_tables.get(pid, []))}, Slots initialized: {bool(table_slots.get(pid))}")
        
        if booking_time:
            # Try exact slot match first
            if booking_time in ALL_SLOTS:
                assigned_table = find_best_table(pid, booking_time, covers, zone_pref)
            else:
                # Try to find nearest slot
                for slot in ALL_SLOTS:
                    if abs(int(slot.split(':')[0])*60+int(slot.split(':')[1]) - int(booking_time.split(':')[0])*60-int(booking_time.split(':')[1])) <= 15:
                        assigned_table = find_best_table(pid, slot, covers, zone_pref)
                        if assigned_table:
                            booking_time = slot
                            break
            if assigned_table:
                assign_table(pid, booking_time, assigned_table, booking_id)
                logger.info(f"✅ Table assigned: {assigned_table} for {covers}p at {booking_time}")
            else:
                logger.warning(f"⚠️ No table found for {covers}p at {booking_time}")

        bookings.append({
            "id": booking_id,
            "phone": customer_phone,
            "name": customer_name or customer_phone,
            "message": message[:200],
            "timestamp": datetime.utcnow().isoformat(),
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "status": "confirmed" if assigned_table else "pending",
            "time": booking_time or "",
            "booking_time": booking_time or "",
            "covers": covers,
            "table": assigned_table,
            "zone": zone_pref,
            "source": "whatsapp",
        })
        track_stats(restaurant["phone_number_id"], is_booking=True)

        # Persist booking to DB
        await db_save_booking(bookings[-1])
        bump_version()

        # Schedule review followup
        await schedule_review_followup(pid, customer_phone, customer_name, booking_time or "")

        logger.info(f"🍽️ Booking {booking_id}: {customer_name} {covers}p @ {booking_time} -> {assigned_table or 'unassigned'}")

    if not restaurant.get("owner_phone"):
        return
    if is_booking:
        notif = (
            f"🍽️ Demande de réservation !\n\n"
            f"👤 {customer_name or customer_phone}\n"
            f"📱 {customer_phone}\n"
            f"💬 \"{message[:200]}\"\n\n"
            f"Orso a répondu automatiquement."
        )
        await send_whatsapp_message(
            restaurant["phone_number_id"],
            restaurant["access_token"],
            restaurant["owner_phone"],
            notif,
        )


# ==============================================================
# MAIN MESSAGE PROCESSING
# ==============================================================

async def process_and_reply(
    phone_number_id: str,
    customer_phone: str,
    customer_name: str,
    message_text: str,
):
    restaurant = restaurants.get(phone_number_id)
    if not restaurant:
        logger.warning(f"No restaurant for phone_number_id: {phone_number_id}")
        return

    # Check if message is from the owner
    owner_phone = restaurant.get("owner_phone", "")
    if owner_phone and customer_phone == owner_phone:
        response = await handle_owner_command(phone_number_id, message_text)
        if response is not None:
            await send_whatsapp_message(
                phone_number_id, restaurant["access_token"], customer_phone, response
            )
            logger.info(f"👨‍🍳 Commande propriétaire : {message_text[:50]}")
            return
        # If None, it's not a command — process normally (owner asking as client)

    # Check if this is a response to a review request
    review_response = await handle_review_response(phone_number_id, customer_phone, message_text)
    if review_response:
        await send_whatsapp_message(
            phone_number_id, restaurant["access_token"], customer_phone, review_response
        )
        save_message(phone_number_id, customer_phone, "user", message_text)
        save_message(phone_number_id, customer_phone, "assistant", review_response)
        logger.info(f"⭐ Review response from {customer_phone}: {message_text[:50]}")
        return

    # Build system prompt with current status and customer profile
    system_prompt = build_system_prompt(restaurant, phone_number_id, customer_phone)

    # Get conversation history
    history = get_conversation(phone_number_id, customer_phone)

    # Build messages for Claude
    claude_messages = []
    for msg in history[-10:]:
        claude_messages.append({"role": msg["role"], "content": msg["content"]})
    claude_messages.append({"role": "user", "content": message_text})

    # Get AI response
    response = await ask_claude(system_prompt, claude_messages)

    # Save to history
    save_message(phone_number_id, customer_phone, "user", message_text)
    save_message(phone_number_id, customer_phone, "assistant", response)

    # Track stats
    track_stats(phone_number_id, language="fr")

    # Track contact in CRM
    track_contact(customer_phone, customer_name)

    # Detect and store preferences from customer message
    detect_preferences(customer_phone, message_text)

    # Send reply
    await send_whatsapp_message(
        phone_number_id, restaurant["access_token"], customer_phone, response
    )

    # Notify owner if booking
    await notify_owner(restaurant, customer_phone, customer_name, message_text)

    logger.info(f"💬 [{restaurant['name']}] {customer_name or customer_phone}: {message_text[:80]}")
    logger.info(f" Réponse: {response[:80]}")


# ==============================================================
# DASHBOARD HTML
# ==============================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuestScale — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#F4F5F9;--card:#FFF;--sb:#0F1117;--sbh:#1A1D27;--sba:#252836;--sbt:#6B7280;
  --t:#111827;--ts:#6B7280;--tm:#9CA3AF;--b:#E5E7EB;--bl:#F3F4F6;
  --ac:#6366F1;--ac2:#818CF8;--acg:linear-gradient(135deg,#6366F1,#8B5CF6);
  --al:#EEF2FF;--ok:#10B981;--okb:#ECFDF5;--wa:#F59E0B;--wab:#FFFBEB;
  --da:#EF4444;--bl2:#3B82F6;--blb:#EFF6FF;
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
.l-icon{width:40px;height:40px;background:var(--acg);border-radius:10px;display:flex;align-items:center;justify-content:center}
.l-icon svg{width:22px;height:22px;fill:none;stroke:#fff;stroke-width:2}
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
.sb-icon{width:32px;height:32px;background:var(--acg);border-radius:8px;display:flex;align-items:center;justify-content:center}
.sb-icon svg{width:18px;height:18px;fill:none;stroke:#fff;stroke-width:2}
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
.toast{position:fixed;bottom:24px;right:24px;background:var(--sb);color:#fff;padding:12px 24px;border-radius:10px;font-weight:600;font-size:13px;box-shadow:var(--shadow-lg);z-index:200;display:none;animation:su .3s ease}
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

/* === DATE STRIP (calendar) === */
.date-strip{display:flex;align-items:center;gap:6px;margin-bottom:14px;padding:4px 0}
.date-strip-arrow{width:32px;height:32px;border-radius:8px;border:1.5px solid var(--b);background:var(--card);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:14px;color:var(--ts);transition:all .15s;flex-shrink:0}
.date-strip-arrow:hover{border-color:var(--ac);color:var(--ac);background:var(--al)}
.date-strip-days{display:flex;gap:4px;overflow-x:auto;flex:1;scrollbar-width:none;-ms-overflow-style:none}
.date-strip-days::-webkit-scrollbar{display:none}
.date-day{display:flex;flex-direction:column;align-items:center;gap:1px;padding:6px 10px;border-radius:10px;border:1.5px solid transparent;background:transparent;cursor:pointer;font-family:var(--f);transition:all .15s;min-width:48px;flex-shrink:0}
.date-day:hover{background:var(--bl)}
.date-day.today{border-color:var(--b);background:var(--bl)}
.date-day.sel{border-color:var(--ac);background:var(--al)}
.date-day .dd-dow{font-size:9px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.04em}
.date-day.sel .dd-dow{color:var(--ac)}
.date-day .dd-num{font-size:16px;font-weight:800;color:var(--t);line-height:1.1}
.date-day.sel .dd-num{color:var(--ac)}
.date-day .dd-badge{width:5px;height:5px;border-radius:50%;background:var(--ok);margin-top:1px;opacity:0}
.date-day .dd-badge.has{opacity:1}
.date-today-btn{padding:5px 12px;border-radius:8px;border:1.5px solid var(--b);background:var(--card);font-size:11px;font-weight:700;color:var(--ts);cursor:pointer;font-family:var(--f);transition:all .15s;white-space:nowrap;flex-shrink:0}
.date-today-btn:hover{border-color:var(--ac);color:var(--ac);background:var(--al)}

/* === FLOORPLAN WITH SIDEBAR === */
.fp-layout{display:flex;gap:14px;align-items:flex-start}
.fp-main{flex:1;min-width:0}
.fp-sidebar{width:300px;flex-shrink:0;background:var(--card);border-radius:var(--radius);border:1px solid var(--b);box-shadow:var(--shadow);overflow:hidden;max-height:calc(440px + 140px);display:flex;flex-direction:column}
.fp-sb-header{padding:14px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center}
.fp-sb-title{font-size:13px;font-weight:700;color:var(--t)}
.fp-sb-count{font-size:11px;font-weight:600;color:var(--tm)}
.fp-sb-list{flex:1;overflow-y:auto;scrollbar-width:thin}
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
.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--sb);padding:8px 0 12px;z-index:50;border-top:1px solid #1F2937}
.mobile-nav-items{display:flex;justify-content:space-around}
.mobile-nav-btn{background:none;border:none;color:#6B7280;font-size:10px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 8px;transition:color .15s}
.mobile-nav-btn.active{color:var(--ac)}
.mobile-nav-btn span{font-size:20px}
@media(max-width:768px){
  .sidebar{display:none}.main{margin-left:0}.sg{grid-template-columns:repeat(2,1fr)}.g2{grid-template-columns:1fr}.cg3{grid-template-columns:1fr}.content{padding:16px;padding-bottom:80px}.topbar{padding:14px 16px}
  .mobile-nav{display:block}
  .fp-layout{flex-direction:column}.fp-sidebar{width:100%;max-height:260px}
  .date-day{min-width:42px;padding:5px 8px}.date-day .dd-num{font-size:14px}
}
</style>
</head>
<body>

<div class="lo" id="loginOverlay">
<div class="lbox">
  <div class="l-logo"><div class="l-icon"><svg viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linejoin="round" stroke-linecap="round"/></svg></div><div class="lwm">GuestScale</div></div>
  <div class="lsub">Restaurant AI Platform</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Mot de passe incorrect. Veuillez reessayer.</div>
    <div style="position:relative">
      <input class="linp" type="password" id="loginPwd" placeholder="Mot de passe" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()" oninput="document.getElementById('loginError').style.display='none';this.style.borderColor='#374151'">
      <button data-togglePwd onclick="togglePwdVis()" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;color:#6B7280;padding:4px" id="pwdToggle" type="button" title="Afficher le mot de passe">&#128065;</button>
    </div>
    <button class="lbtn" type="button" onclick="doLogin()" data-doLogin>Continuer</button>
  </div>
</div>
</div>

<div class="app" id="app">
<div class="sidebar">
  <div class="sb-b"><div class="sb-logo"><div class="sb-icon"><svg viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linejoin="round" stroke-linecap="round"/></svg></div><div><div class="sb-wm">GuestScale</div><div class="sb-s">Restaurant AI</div></div></div></div>
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
    <div class="sb-l">PARAMETRES</div>
    <button class="nb" data-pg="config"><span class="ic">&#9881;</span> Configuration</button>
    <button class="nb" data-pg="stats"><span class="ic">&#9899;</span> Statistiques</button>
  </div>
  <div class="sb-u">
    <div class="uav">GS</div>
    <div><div style="color:#E5E7EB;font-size:13px;font-weight:600">Restaurant</div><div style="color:#6B7280;font-size:11px">Admin</div></div>
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

<div class="mobile-nav">
  <div class="mobile-nav-items">
    <button class="mobile-nav-btn active" data-pg="overview"><span>&#9672;</span>Accueil</button>
    <button class="mobile-nav-btn" data-pg="floorplan"><span>&#8862;</span>Plan</button>
    <button class="mobile-nav-btn" data-pg="bookings"><span>&#9673;</span>Resas</button>
    <button class="mobile-nav-btn" data-pg="conversations"><span>&#9672;</span>Chat</button>
    <button class="mobile-nav-btn" data-pg="config"><span>&#9881;</span>Config</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var SK='{{SECRET_KEY}}',DP='{{DASHBOARD_PASSWORD}}';
var dailyMsg='';
var resaSelTable=null;
var selectedDate=new Date().toISOString().slice(0,10);

// === DATE HELPERS ===
function fmtDate(d){return d.toISOString().slice(0,10)}
function parseDateLocal(s){var p=s.split('-');return new Date(parseInt(p[0]),parseInt(p[1])-1,parseInt(p[2]))}
var DOW_SHORT=['dim','lun','mar','mer','jeu','ven','sam'];

function buildDateStrip(containerId,onChange){
  var base=parseDateLocal(selectedDate);
  var today=fmtDate(new Date());
  var h='<div class="date-strip">';
  h+='<div class="date-strip-arrow" data-dateShift="-7">&#8249;</div>';
  h+='<div class="date-today-btn" data-dateToday>Aujourd\'hui</div>';
  h+='<div class="date-strip-days">';
  for(var i=-2;i<=8;i++){
    var d=new Date(base);d.setDate(d.getDate()+i);
    var ds=fmtDate(d);
    var isToday=ds===today;
    var isSel=ds===selectedDate;
    // Count bookings for this date
    var cnt=bookings.filter(function(b){return(b.date||'').startsWith(ds)}).length;
    h+='<div class="date-day'+(isSel?' sel':'')+(isToday&&!isSel?' today':'')+'" data-dateSelect="'+ds+'">';
    h+='<span class="dd-dow">'+DOW_SHORT[d.getDay()]+'</span>';
    h+='<span class="dd-num">'+d.getDate()+'</span>';
    h+='<span class="dd-badge'+(cnt>0?' has':'')+'"></span>';
    h+='</div>';
  }
  h+='</div>';
  h+='<div class="date-strip-arrow" data-dateShift="7">&#8250;</div>';
  h+='</div>';
  return h;
}

function getBookingsForDate(dateStr){
  return bookings.filter(function(b){return(b.date||'').startsWith(dateStr)});
}

function doLogin(){
  var inp=document.getElementById('loginPwd');
  var err=document.getElementById('loginError');
  var pwd=inp.value;
  console.log('doLogin called, pwd length:', pwd.length, 'pwd:', pwd);
  // Also try client-side first for immediate response
  if(pwd===DP){
    console.log('Client-side match OK');
    document.getElementById('loginOverlay').style.display='none';
    document.getElementById('app').classList.add('v');
    try{sessionStorage.setItem('orso_auth','1')}catch(e){}
    loadAll();
    return;
  }
  // Then try server-side
  if(!pwd){err.style.display='block';return}
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pwd})})
  .then(function(r){console.log('Login response status:', r.status);return r.json()})
  .then(function(d){
    console.log('Login response:', d);
    if(d.status==='ok'){
      document.getElementById('loginOverlay').style.display='none';
      document.getElementById('app').classList.add('v');
      try{sessionStorage.setItem('orso_auth','1')}catch(e){}
      loadAll();
    }else{
      err.style.display='block';
      inp.style.borderColor='var(--da)';
      inp.classList.remove('shake');
      void inp.offsetWidth;
      inp.classList.add('shake');
      inp.focus();
    }
  })
  .catch(function(){
    // Fallback to client-side check if server unreachable
    if(pwd===DP){
      document.getElementById('loginOverlay').style.display='none';
      document.getElementById('app').classList.add('v');
      try{sessionStorage.setItem('orso_auth','1')}catch(e){}
      loadAll();
    }else{
      err.style.display='block';
      inp.style.borderColor='var(--da)';
      inp.classList.remove('shake');
      void inp.offsetWidth;
      inp.classList.add('shake');
      inp.focus();
    }
  });
}
// Auto-login if session exists
try{if(sessionStorage.getItem('orso_auth')==='1'){document.getElementById('loginOverlay').style.display='none';document.getElementById('app').classList.add('v');setTimeout(loadAll,100)}}catch(e){}
function togglePwdVis(){
  var inp=document.getElementById('loginPwd');
  var btn=document.getElementById('pwdToggle');
  if(inp.type==='password'){inp.type='text';btn.textContent='🔒'}
  else{inp.type='password';btn.textContent='👁'}
}

var pageTitles={overview:"Vue d'ensemble",floorplan:"Plan de salle",bookings:"Réservations",menu:"Menu",conversations:"Conversations",reviews:"Avis",contacts:"Contacts",config:"Configuration",stats:"Statistiques"};

function switchPage(id,btn){
  currentPage=id;
  document.getElementById('pageTitle').textContent=pageTitles[id]||id;
  document.querySelectorAll('.nb').forEach(function(b){b.classList.remove('on')});
  if(btn&&btn.classList)btn.classList.add('on');
  else{var b=document.querySelector('[data-pg="'+id+'"]');if(b)b.classList.add('on')}
  renderPage(id);
}

function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(function(){t.style.display='none'},2500)}

function updateTime(){var n=new Date();document.getElementById('currentDate').textContent=n.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});document.getElementById('currentTime').textContent=n.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}

// ===== DATA =====
var bookings=[],contacts={},conversations={},floorplan=[],reviewQueue=[];
var floorSlots={};
var cancelledCount=0;
var restaurantConfig={};
var overviewBlocks={daily:true,stats:true,floor:true,bookings:true,contacts:true};

function mergeBookingsIntoFloor(){
  // Find current time slot to show which tables are currently occupied
  var now=new Date();
  var hh=String(now.getHours()).padStart(2,'0');
  var mm=String(Math.floor(now.getMinutes()/15)*15).padStart(2,'0');
  var currentSlot=hh+':'+mm;
  // Check nearby slots too (current and next 2 hours)
  var checkSlots=[];
  for(var s in floorSlots){checkSlots.push(s)}
  // Build a map of table -> booking name from bookings list
  var tableBookings={};
  bookings.forEach(function(b){
    if(b.table)tableBookings[b.table]=b.name;
  });
  // Mark tables with booking info
  floorplan.forEach(function(t){
    t.booking_name=tableBookings[t.id]||null;
  });
}

var currentPage='overview';
var lastVersion=0;

function loadAll(){
  updateTime();setInterval(updateTime,30000);
  fetchData();
  // Check for updates every 3 seconds
  setInterval(checkUpdates,3000);
}

function checkUpdates(){
  fetch('/api/version?key='+SK).then(function(r){return r.json()}).then(function(d){
    if(d.v&&d.v!==lastVersion){
      lastVersion=d.v;
      fetchDataSilent();
    }
  }).catch(function(){});
}

function fetchDataSilent(){
  var ok=function(r){return r.ok?r.json():Promise.resolve(null)};
  Promise.all([
    fetch('/api/bookings?key='+SK).then(ok).catch(function(){return null}),
    fetch('/api/contacts?key='+SK).then(ok).catch(function(){return null}),
    fetch('/api/conversations?key='+SK).then(ok).catch(function(){return null}),
    fetch('/api/floorplan?key='+SK).then(ok).catch(function(){return null}),
    fetch('/api/reviews?key='+SK).then(ok).catch(function(){return null}),
    fetch('/api/daily?key='+SK).then(ok).catch(function(){return null}),
    fetch('/api/menu?key='+SK).then(ok).catch(function(){return null})
  ]).then(function(res){
    if(res[0])bookings=(res[0].bookings)||[];
    if(res[1]){contacts={};(res[1].contacts||[]).forEach(function(c){if(c.phone)contacts[c.phone]=c})}
    if(res[2]){conversations={};(res[2].conversations||[]).forEach(function(cv){conversations[cv.phone||cv.id]=cv})}
    if(res[3]){floorplan=(res[3].tables||[]);floorSlots=(res[3].slots||{});mergeBookingsIntoFloor()}
    if(res[4])reviewQueue=(res[4].queue||[]);
    if(res[5])dailyMsg=(res[5].message)||'';
    if(res[6])menuSections=(res[6].sections)||[];
    updateBadges();
    if(currentPage==='overview'||currentPage==='bookings'||currentPage==='conversations'||currentPage==='floorplan')renderPage(currentPage);
  }).catch(function(){});
}

function fetchData(){
  var ok=function(r){return r.ok?r.json():Promise.resolve(null)};
  Promise.all([
    fetch('/api/bookings?key='+SK).then(ok).catch(function(){return []}),
    fetch('/api/contacts?key='+SK).then(ok).catch(function(){return {}}),
    fetch('/api/conversations?key='+SK).then(ok).catch(function(){return {}}),
    fetch('/api/floorplan?key='+SK).then(ok).catch(function(){return []}),
    fetch('/api/reviews?key='+SK).then(ok).catch(function(){return []}),
    fetch('/api/config?key='+SK).then(ok).catch(function(){return {}}),
    fetch('/api/daily?key='+SK).then(ok).catch(function(){return {message:''}}),
    fetch('/api/menu?key='+SK).then(ok).catch(function(){return {sections:[]}})
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
    // Merge booking info into floorplan tables
    mergeBookingsIntoFloor();
    var rvData=res[4]||{};
    reviewQueue=(rvData.queue||[]);
    restaurantConfig=res[5]||{};
    dailyMsg=(res[6]&&res[6].message)||'';
    menuSections=(res[7]&&res[7].sections)||[];
    updateBadges();
    renderPage(currentPage||'overview');
  }).catch(function(err){
    console.error('Load error:',err);
    renderPage(currentPage||'overview');
  });
}

function updateBadges(){
  var today=new Date().toISOString().slice(0,10);
  var todayBookings=bookings.filter(function(b){return(b.date||'').startsWith(today)});
  document.getElementById('bookBadge').textContent=todayBookings.length;
  var convCount=Object.keys(conversations).length;
  document.getElementById('convBadge').textContent=convCount;
  var pendingReviews=reviewQueue.filter(function(r){return!r.sent}).length;
  document.getElementById('reviewBadge').textContent=pendingReviews;
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
}

// ===== OVERVIEW =====
function renderOverview(c){
  var tb=getBookingsForDate(selectedDate);
  var convArr=Object.entries(conversations);
  var ctArr=Object.entries(contacts);
  var totalSeats=floorplan.reduce(function(a,t){return a+t.seats},0);
  var today=fmtDate(new Date());
  var isToday=selectedDate===today;
  var dateLabel=isToday?'aujourd\'hui':parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});
  
  var h='';
  
  // Date strip
  h+=buildDateStrip('ov-datestrip');
  
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
  fetch('/api/daily?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:dailyMsg})});
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
  fetch('/api/broadcast?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:dailyMsg})});
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

function renderFloorplan(c){
  var nowH=new Date().getHours();
  if(fpSlot==='all'&&nowH>=17)fpService='soir';
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

  // Date strip
  if(fpMode==='resa'){
    h+=buildDateStrip('fp-datestrip');
  }

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

    // Sidebar with reservation list
    h+='<div class="fp-sidebar">';
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
  fetch('/api/bookings/update?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
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
    fetch('/api/bookings/update?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:newTableBooking.id,table:oldTable})});
    newTableBooking.table=oldTable;
  }
  fetch('/api/bookings/update?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bookingId,table:newTableId})});
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
  fetch('/api/bookings/delete?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bookingId})}).then(function(){
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
  fetch('/api/config?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tables:floorplan})}).then(function(){
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
  var dateLabel=isToday?'aujourd\'hui':parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});
  var filtered=getBookingsForDate(selectedDate);

  var h=buildDateStrip('bk-datestrip');
  h+='<div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+filtered.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle</button></div>';
  filtered.forEach(function(b){
    var globalIdx=bookings.indexOf(b);
    h+='<div class="rw" data-editResa="'+globalIdx+'" style="cursor:pointer"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+(b.phone?' · '+b.phone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div></div>';
  });
  if(!filtered.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucune reservation '+dateLabel+'</div>';
  h+='</div>';
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
  fetch('/api/bookings/update?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
    fetchData();
    closeEditResa();
    showToast('Reservation modifiee');
  });
}
function deleteResa(){
  if(editResaIdx===null)return;
  var b=bookings[editResaIdx];
  if(!confirm('Supprimer la reservation de '+b.name+' ?'))return;
  fetch('/api/bookings/delete?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:b.id})}).then(function(){
    fetchData();
    closeEditResa();
    showToast('Reservation supprimee');
  });
}

// ===== MENU =====
// ===== MENU EDITOR =====
var menuSections=[];

function loadMenu(){
  fetch('/api/menu?key='+SK).then(function(r){return r.json()}).then(function(d){
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
  fetch('/api/menu?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sections:menuSections})}).then(function(){
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
        fetch('/api/menu/scan?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:b64,media_type:mt})}).then(function(r){return r.json()}).then(function(d){
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
  var h='<div class="card">';
  reviewQueue.forEach(function(r){
    h+='<div class="review-card"><div style="display:flex;justify-content:space-between;align-items:center"><div style="font-size:14px;font-weight:600">'+(r.name||r.phone)+'</div><span class="badge" style="background:'+(r.sent?'var(--okb)':'var(--wab)')+';color:'+(r.sent?'var(--ok)':'var(--wa)')+'">'+(r.sent?'Envoyé':'En attente')+'</span></div><div style="font-size:12px;color:var(--tm);margin-top:4px">'+(r.booking_time||'')+'</div></div>';
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

  // Preferences, tags, notes
  var hasProfile=ct.preferences||ct.tags&&ct.tags.length||ct.notes||ct.language;
  if(hasProfile){
    h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="card-t" style="margin-bottom:12px">Profil client</div>';
    if(ct.preferences){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Preferences</span><div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">';
      ct.preferences.split(',').forEach(function(p){if(p.trim())h+='<span style="padding:3px 8px;border-radius:6px;background:var(--al);color:var(--ac);font-size:11px;font-weight:600">'+p.trim()+'</span>'});
      h+='</div></div>'}
    if(ct.tags&&ct.tags.length){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Tags</span><div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">';
      ct.tags.forEach(function(t){h+='<span style="padding:3px 8px;border-radius:6px;background:var(--okb);color:var(--ok);font-size:11px;font-weight:600">'+t+'</span>'});
      h+='</div></div>'}
    if(ct.notes){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Notes</span><div style="margin-top:4px;font-size:13px;color:var(--ts)">'+ct.notes+'</div></div>'}
    if(ct.language){h+='<div><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Langue</span><span style="margin-left:8px;font-size:13px;color:var(--ts)">'+ct.language+'</span></div>'}
    h+='</div>';
  }

  // Reservations
  if(resas.length){
    h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="card-t" style="margin-bottom:12px">Historique reservations</div>';
    resas.forEach(function(b){
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--bl)">';
      h+='<div><span style="font-weight:600">'+b.covers+'p</span> · '+(b.booking_time||b.time||'')+'</div>';
      h+='<div style="display:flex;gap:6px"><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div>';
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
    fetch('/api/config?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(restaurantConfig)});
    renderConfig(document.getElementById('mainContent'));
    showToast(label+' mis a jour');
  }
}

// ===== STATS =====
function renderStats(c){
  var totalMsgs=0;
  var convArr=Object.entries(conversations);
  convArr.forEach(function(e){var d=e[1];totalMsgs+=((d.messages&&d.messages.length)||d.count||0)});
  var totalContacts=Object.keys(contacts).length;
  var totalResas=bookings.length;
  var totalTables=floorplan.length;
  var totalSeats=floorplan.reduce(function(a,t){return a+(t.seats||0)},0);
  var occupiedTables=bookings.filter(function(b){return b.table}).length;
  var occRate=totalTables?Math.round(occupiedTables/totalTables*100):0;
  var totalCovers=bookings.reduce(function(a,b){return a+(b.covers||0)},0);
  var avgCovers=totalResas?Math.round(totalCovers/totalResas*10)/10:0;
  var cancelled=cancelledCount||0;
  
  // Source breakdown
  var sources={};
  bookings.forEach(function(b){var s=b.source||'autre';sources[s]=(sources[s]||0)+1});
  var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Telephone','walk-in':'Walk-in',zenchef:'Zenchef'};
  var srcColors={whatsapp:'#25D366',web:'#3B82F6',phone:'#9CA3AF','walk-in':'#6B7280',zenchef:'#F59E0B'};
  
  var h='';
  // KPI cards - 2 rows
  h+='<div class="sg" style="margin-bottom:14px">';
  h+='<div class="sc"><div class="sl">Reservations</div><div class="sv" style="color:var(--ok)">'+totalResas+'</div><div class="ss2">confirmees</div></div>';
  h+='<div class="sc"><div class="sl">Annulations</div><div class="sv" style="color:var(--da)">'+cancelled+'</div><div class="ss2">'+(totalResas+cancelled?Math.round(cancelled/(totalResas+cancelled)*100):0)+'% taux annulation</div></div>';
  h+='<div class="sc"><div class="sl">Couverts</div><div class="sv" style="color:var(--ac)">'+totalCovers+'</div><div class="ss2">'+avgCovers+' moy/resa</div></div>';
  h+='<div class="sc"><div class="sl">Taux occupation</div><div class="sv" style="color:var(--bl2)">'+occRate+'%</div><div class="ss2">'+occupiedTables+'/'+totalTables+' tables</div></div>';
  h+='</div>';

  h+='<div class="g2" style="margin-bottom:14px">';

  // Sources chart (left)
  h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Reservations par canal</div>';
  var srcEntries=Object.entries(sources).sort(function(a,b){return b[1]-a[1]});
  if(srcEntries.length){
    srcEntries.forEach(function(e){
      var pct=totalResas?Math.round(e[1]/totalResas*100):0;
      var col=srcColors[e[0]]||'#9CA3AF';
      h+='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">';
      h+='<div style="width:80px;font-size:12px;font-weight:600;color:var(--ts)">'+(srcLabels[e[0]]||e[0])+'</div>';
      h+='<div style="flex:1;height:28px;background:var(--bg);border-radius:6px;overflow:hidden;position:relative"><div style="width:'+Math.max(pct,2)+'%;height:100%;background:'+col+';border-radius:6px;transition:width .3s"></div><span style="position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:11px;font-weight:700;color:var(--t)">'+e[1]+' ('+pct+'%)</span></div>';
      h+='</div>';
    });
  }else{
    h+='<div style="text-align:center;color:var(--tm);padding:20px">Aucune donnee</div>';
  }
  h+='</div>';

  // Right column: contacts + messages
  h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Communication</div>';
  h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">';
  h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:26px;font-weight:800;color:var(--ac)">'+totalMsgs+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Messages</div></div>';
  h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:26px;font-weight:800;color:var(--ok)">'+convArr.length+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Conversations</div></div>';
  h+='</div>';
  h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">';
  h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:26px;font-weight:800;color:var(--wa)">'+totalContacts+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Contacts CRM</div></div>';
  h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:26px;font-weight:800;color:var(--bl2)">'+(convArr.length?Math.round(totalMsgs/convArr.length):0)+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Msg/client</div></div>';
  h+='</div>';
  h+='</div>';

  h+='</div>';

  // Canal detail table
  h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:12px">Detail par canal</div>';
  h+='<div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:0;font-size:12px">';
  h+='<div style="padding:8px 12px;font-weight:700;color:var(--tm);border-bottom:2px solid var(--b)">Canal</div>';
  h+='<div style="padding:8px 12px;font-weight:700;color:var(--tm);border-bottom:2px solid var(--b);text-align:right">Reservations</div>';
  h+='<div style="padding:8px 12px;font-weight:700;color:var(--tm);border-bottom:2px solid var(--b);text-align:right">Couverts</div>';
  h+='<div style="padding:8px 12px;font-weight:700;color:var(--tm);border-bottom:2px solid var(--b);text-align:right">% du total</div>';
  var allSrcs=["whatsapp","web","phone","walk-in","zenchef"];
  allSrcs.forEach(function(s){
    var cnt=sources[s]||0;
    if(!cnt)return;
    var cov=0;bookings.forEach(function(b){if(b.source===s)cov+=(b.covers||0)});
    var pct=totalResas?Math.round(cnt/totalResas*100):0;
    var col=srcColors[s]||'#9CA3AF';
    h+='<div style="padding:10px 12px;border-bottom:1px solid var(--bl);display:flex;align-items:center;gap:8px"><div style="width:8px;height:8px;border-radius:50%;background:'+col+'"></div><span style="font-weight:600">'+(srcLabels[s]||s)+'</span></div>';
    h+='<div style="padding:10px 12px;border-bottom:1px solid var(--bl);text-align:right;font-weight:700">'+cnt+'</div>';
    h+='<div style="padding:10px 12px;border-bottom:1px solid var(--bl);text-align:right;font-weight:600;color:var(--ts)">'+cov+'</div>';
    h+='<div style="padding:10px 12px;border-bottom:1px solid var(--bl);text-align:right;font-weight:700;color:'+col+'">'+pct+'%</div>';
  });
  h+='</div></div>';

  c.innerHTML=h;
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
  fetch('/api/bookings/manual?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(d){
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

  // Date strip navigation
  var t=e.target.closest('[data-dateSelect]');
  if(t){selectedDate=t.getAttribute('data-dateSelect');mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-dateShift]');
  if(t){var shift=parseInt(t.getAttribute('data-dateShift'));var d=parseDateLocal(selectedDate);d.setDate(d.getDate()+shift);selectedDate=fmtDate(d);mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-dateToday]');
  if(t){selectedDate=fmtDate(new Date());mergeBookingsIntoFloor();renderPage(currentPage);return}

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
# FASTAPI APP
# ==============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init database
    await init_db()
    await db_load_all()

    # Load sample restaurant (from env vars if not in DB)
    load_sample_restaurant()

    # Save restaurant config to DB
    pid = list(restaurants.keys())[0] if restaurants else None
    if pid:
        await db_save("restaurant_config", pid, restaurants[pid])
        await db_save("restaurant_status", pid, restaurant_status.get(pid, {}))

    logger.info("🚀 Orso v4.0 démarré")
    import asyncio
    async def review_loop():
        while True:
            try:
                await process_review_queue()
            except Exception as e:
                logger.error(f"Review queue error: {e}")
            await asyncio.sleep(300)
    task = asyncio.create_task(review_loop())
    yield
    task.cancel()
    if db_pool:
        await db_pool.close()
    logger.info("👋 Orso arrêté")


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --- Webhook ---
@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    parsed = parse_webhook(body)
    if not parsed:
        return {"status": "ignored"}
    restaurant = restaurants.get(parsed["phone_number_id"])
    if restaurant:
        background_tasks.add_task(
            mark_as_read, parsed["phone_number_id"], restaurant["access_token"], parsed["message_id"]
        )
    background_tasks.add_task(
        process_and_reply, parsed["phone_number_id"], parsed["from"], parsed["name"], parsed["text"]
    )
    return {"status": "ok"}


# --- Dashboard ---
@app.get("/api/version")
async def get_version(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    return {"v": data_version}


@app.get("/dashboard/{secret_key}", response_class=HTMLResponse)
async def dashboard(secret_key: str):
    if secret_key != DASHBOARD_SECRET:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return DASHBOARD_HTML.replace("{{SECRET_KEY}}", secret_key).replace("{{DASHBOARD_PASSWORD}}", DASHBOARD_PASSWORD)


@app.post("/api/login")
async def api_login(request: Request):
    data = await request.json()
    pwd = data.get("password", "").strip()
    expected = DASHBOARD_PASSWORD.strip()
    logger.info(f"🔐 Login attempt: got '{pwd}' (len={len(pwd)}), expected '{expected}' (len={len(expected)}), match={pwd == expected}")
    if pwd == expected:
        return {"status": "ok", "key": DASHBOARD_SECRET}
    return {"status": "error"}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_redirect():
    return HTMLResponse("<h1>404</h1>", status_code=404)


# --- API endpoints ---
@app.get("/api/dashboard")
async def dashboard_data(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"stats": {}, "status": {}, "conversations_count": 0, "recent_conversations": []}
    st = stats.get(pid, {})
    today_str = date.today().isoformat()
    if st.get("last_reset") != today_str:
        st["messages_today"] = 0
        st["bookings_today"] = 0
        st["languages"] = {}
        st["last_reset"] = today_str
    status = restaurant_status.get(pid, {})
    recent = []
    for k, msgs in sorted(conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True)[:20]:
        if not msgs:
            continue
        phone = k.split(":")[1] if ":" in k else k
        last = msgs[-1]
        recent.append({"phone": phone, "last_message": last["content"][:100], "time": last.get("timestamp", "")[:16].replace("T", " ")})
    return {"stats": st, "status": status, "conversations_count": sum(1 for k in conversations if k.startswith(pid)), "recent_conversations": recent}


@app.post("/api/status")
async def update_status(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.get(pid, {})
    status["status"] = data.get("status", "open")
    status["updated_at"] = datetime.utcnow().isoformat()
    await db_save("restaurant_status", pid, status)
    return {"status": "updated"}


@app.post("/api/message")
async def update_message(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.get(pid, {})
    status["temp_message"] = data.get("message", "")
    await db_save("restaurant_status", pid, status)
    return {"status": "updated"}


@app.get("/api/conversations")
async def list_conversations(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"conversations": []}
    result = []
    for k, msgs in sorted(conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True):
        if not k.startswith(pid) or not msgs:
            continue
        phone = k.split(":")[1] if ":" in k else k
        result.append({"phone": phone, "messages": [{"role": m["role"], "content": m["content"], "time": m.get("timestamp", "")[:16].replace("T", " ")} for m in msgs], "last_message": msgs[-1]["content"][:100], "last_time": msgs[-1].get("timestamp", "")[:16].replace("T", " "), "count": len(msgs)})
    return {"conversations": result}


@app.get("/api/bookings")
async def list_bookings(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    return {"bookings": bookings[-50:]}


@app.get("/api/floorplan")
async def get_floorplan(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"tables": [], "slots": {}, "bookings": []}
    return {"tables": floor_tables.get(pid, []), "slots": table_slots.get(pid, {}), "bookings": bookings[-100:], "slot_summary": get_slot_summary(pid)}


@app.post("/api/floorplan/assign")
async def assign_table_api(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    booking_id = data.get("booking_id")
    table_id = data.get("table_id")
    slot_time = data.get("slot_time")
    if not all([booking_id, table_id, slot_time]):
        return {"error": "Missing fields"}
    for b in bookings:
        if b.get("id") == booking_id:
            if b.get("table") and b.get("time"):
                release_table(pid, b["time"], b["table"])
            b["table"] = table_id
            b["status"] = "confirmed"
            break
    assign_table(pid, slot_time, table_id, booking_id)
    return {"status": "assigned"}


@app.post("/api/floorplan/release")
async def release_table_api(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    booking_id = data.get("booking_id")
    for b in bookings:
        if b.get("id") == booking_id and b.get("table") and b.get("time"):
            release_table(pid, b["time"], b["table"])
            b["table"] = None
            b["status"] = "pending"
            break
    return {"status": "released"}


@app.get("/api/reviews")
async def get_reviews(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    return {"queue": review_queue[-50:], "stats": {"total": len(review_queue), "sent": sum(1 for r in review_queue if r.get("sent")), "responded": sum(1 for r in review_queue if r.get("responded")), "positive": sum(1 for r in review_queue if r.get("sentiment") == "POSITIVE"), "negative": sum(1 for r in review_queue if r.get("sentiment") == "NEGATIVE")}}


@app.get("/api/contacts")
async def get_contacts(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    contact_list = sorted(contacts.values(), key=lambda c: c.get("last_seen", ""), reverse=True)
    return {
        "contacts": contact_list[:200],
        "total": len(contacts),
    }


@app.post("/api/contacts/tag")
async def tag_contact(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    phone = data.get("phone")
    tag = data.get("tag", "")
    if phone in contacts and tag:
        if tag not in contacts[phone].get("tags", []):
            contacts[phone].setdefault("tags", []).append(tag)
        await db_save_contact(phone, contacts[phone])
    return {"status": "ok"}


@app.post("/api/contacts/note")
async def note_contact(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    phone = data.get("phone")
    note = data.get("note", "")
    if phone in contacts:
        contacts[phone]["notes"] = note
        await db_save_contact(phone, contacts[phone])
    return {"status": "ok"}


# --- Restaurant Config (self-service) ---
@app.get("/api/config")
async def get_config(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    r = restaurants[pid]
    return {
        "name": r.get("name", ""),
        "description": r["context"].get("description", ""),
        "menu": r["context"].get("menu", ""),
        "hours": r["context"].get("hours", ""),
        "address": r["context"].get("address", ""),
        "phone": r["context"].get("phone", ""),
        "tone": r["context"].get("tone", ""),
        "languages": r["context"].get("languages", ""),
        "special_info": r["context"].get("special_info", ""),
        "booking_link": r["context"].get("booking_link", ""),
        "allergens_policy": r["context"].get("allergens_policy", ""),
        "tables": floor_tables.get(pid, []),
    }


@app.post("/api/config")
async def update_config(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    r = restaurants[pid]
    ctx = r["context"]
    # Update fields if provided
    for field in ["description", "menu", "hours", "address", "phone", "tone", "languages", "special_info", "booking_link", "allergens_policy"]:
        if field in data:
            ctx[field] = data[field]
    if "name" in data:
        r["name"] = data["name"]
    # Update tables if provided
    if "tables" in data:
        floor_tables[pid] = data["tables"]
        init_daily_slots(pid)
    logger.info(f"✏️ Config updated: {list(data.keys())}")
    # Persist to DB
    await db_save("restaurant_config", pid, r)
    return {"status": "updated"}


# --- Structured Menu ---
@app.get("/api/menu")
async def get_menu(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"sections": []}
    ctx = restaurants[pid]["context"]
    return {"sections": ctx.get("menu_sections", [])}


@app.post("/api/menu")
async def save_menu(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    sections = data.get("sections", [])
    r = restaurants[pid]
    r["context"]["menu_sections"] = sections
    # Generate text version for the AI agent
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
    r["context"]["menu"] = "\n".join(text_lines)
    await db_save("restaurant_config", pid, r)
    logger.info(f"📋 Menu updated: {len(sections)} sections")
    return {"status": "ok"}


@app.post("/api/menu/scan")
async def scan_menu_image(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    image_b64 = data.get("image", "")
    media_type = data.get("media_type", "image/jpeg")
    if not image_b64:
        return {"error": "No image provided"}
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,")[1]
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "No API key"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                        {"type": "text", "text": 'Transcris ce menu de restaurant en JSON. Retourne UNIQUEMENT du JSON valide sans backticks. Format: {"sections": [{"title": "Entrees", "items": [{"name": "Salade Cesar", "description": "Romaine, parmesan", "price": "12"}]}]}. Identifie les sections (Entrees, Plats, Desserts, Boissons, Vins etc). Pour chaque plat: nom, description si visible, prix sans symbole euro. Garde l orthographe exacte.'}
                    ]}]
                }
            )
            result = resp.json()
            text = result.get("content", [{}])[0].get("text", "")
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            sections = json.loads(text).get("sections", [])
            logger.info(f"📸 Menu scanned: {len(sections)} sections")
            return {"sections": sections}
    except Exception as e:
        logger.error(f"Menu scan error: {e}")
        return {"error": str(e), "sections": []}


@app.get("/api/daily")
async def get_daily(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"message": ""}
    status = restaurant_status.get(pid, {})
    return {"message": status.get("daily_message", "")}


@app.post("/api/daily")
async def set_daily(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.setdefault(pid, {})
    status["daily_message"] = data.get("message", "")
    await db_save("restaurant_status", pid, status)
    # Update AI system prompt context so agent knows about daily message
    if pid in restaurants:
        restaurants[pid]["context"]["special_info"] = data.get("message", "")
    logger.info(f"📢 Daily message updated: {data.get('message', '')[:60]}")
    return {"status": "ok"}


@app.post("/api/broadcast")
async def broadcast_daily(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    msg = data.get("message", "")
    if not msg:
        return {"error": "No message"}
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    # Send to all contacts with phone numbers
    sent = 0
    for phone, ct in contacts.items():
        if phone and phone.startswith("+"):
            try:
                await send_whatsapp_message(pid, phone, msg)
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast failed for {phone}: {e}")
    logger.info(f"📤 Broadcast sent to {sent} contacts")
    return {"status": "ok", "sent": sent}


# --- Manual Booking ---
@app.post("/api/bookings/add")
@app.post("/api/bookings/manual")
async def add_manual_booking(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}

    booking_id = f"R{len(bookings)+1}"
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

    # Use requested table if provided, otherwise auto assign
    assigned_table = None
    if requested_table:
        assigned_table = requested_table
        if booking_time and booking_time in ALL_SLOTS:
            assign_table(pid, booking_time, assigned_table, booking_id)
    elif booking_time and booking_time in ALL_SLOTS:
        assigned_table = find_best_table(pid, booking_time, covers, zone or None)
        if assigned_table:
            assign_table(pid, booking_time, assigned_table, booking_id)

    bookings.append({
        "id": booking_id,
        "phone": phone,
        "email": email,
        "name": name or phone or "Client",
        "message": notes,
        "timestamp": datetime.utcnow().isoformat(),
        "date": booking_date,
        "status": "confirmed" if assigned_table else "pending",
        "booking_time": booking_time,
        "time": booking_time,
        "covers": covers,
        "table": assigned_table,
        "zone": zone,
        "source": source,
    })

    # Track contact if phone provided
    if phone:
        track_contact(phone, name)
        if email and phone in contacts:
            contacts[phone]["email"] = email
        if source and phone in contacts:
            contacts[phone]["source"] = source

    track_stats(pid, is_booking=True)
    logger.info(f"📝 Manual booking {booking_id}: {name} {covers}p @ {booking_time} -> {assigned_table or 'unassigned'}")
    await db_save_booking(bookings[-1])
    bump_version()
    return {"status": "created", "booking_id": booking_id, "table": assigned_table}


@app.post("/api/bookings/update")
async def update_booking(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    bid = data.get("booking_id", "")
    for b in bookings:
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
            await db_save_booking(b)
            bump_version()
            logger.info(f"📝 Booking updated: {bid} -> {b['name']} table={b.get('table')}")
            return {"status": "updated"}
    return {"error": "Booking not found"}


@app.post("/api/bookings/delete")
async def delete_booking(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    bid = data.get("booking_id", "")
    for i, b in enumerate(bookings):
        if b["id"] == bid:
            # Release table if assigned
            pid = list(restaurants.keys())[0] if restaurants else None
            if pid and b.get("table") and b.get("booking_time"):
                release_table(pid, b["booking_time"], b["table"])
            bookings.pop(i)
            bump_version()
            logger.info(f"🗑️ Booking deleted: {bid}")
            return {"status": "deleted"}
    return {"error": "Booking not found"}


# --- Dashboard visibility settings ---
@app.get("/api/settings")
async def get_settings(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"pages": {}}
    return {"pages": restaurant_status.get(pid, {}).get("dashboard_pages", {
        "floorplan": True, "bookings": True, "conversations": True,
        "reviews": True, "contacts": True, "dashboard": True,
    })}


@app.post("/api/settings")
async def update_settings(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.get(pid, {})
    status["dashboard_pages"] = data.get("pages", {})
    return {"status": "updated"}


# ==============================================================
# WEB CHAT API
# ==============================================================

# In-memory web chat sessions
web_sessions = {}  # session_id: {"messages": [...], "name": "", "phone": "", "created": "..."}


@app.post("/api/webchat/message")
async def webchat_message(request: Request):
    """Handle a message from the web chat widget."""
    data = await request.json()
    session_id = data.get("session_id", "")
    message = data.get("message", "").strip()
    visitor_name = data.get("name", "")

    if not session_id or not message:
        return {"error": "Missing session_id or message"}

    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant", "reply": "Service temporairement indisponible."}

    restaurant = restaurants[pid]

    # Init or get session
    if session_id not in web_sessions:
        web_sessions[session_id] = {
            "messages": [],
            "name": visitor_name or "",
            "phone": "",
            "created": datetime.utcnow().isoformat(),
            "last_active": datetime.utcnow().isoformat(),
        }

    session = web_sessions[session_id]
    session["last_active"] = datetime.utcnow().isoformat()
    if visitor_name and not session["name"]:
        session["name"] = visitor_name

    # Try to extract name from message patterns like "au nom de X", "je suis X", "c'est X"
    import re as re_mod
    name_patterns = [
        r"(?:au nom de|je suis|je m'appelle|my name is|c'est|nom\s*:\s*)[\s]*([A-Z][a-zéèêëàâùûôîïç]+(?:\s+[A-Z][a-zéèêëàâùûôîïç]+)?)",
    ]
    for pat in name_patterns:
        nm = re_mod.search(pat, message, re_mod.IGNORECASE)
        if nm and not session["name"]:
            session["name"] = nm.group(1).strip()

    # Save user message
    session["messages"].append({"role": "user", "content": message, "timestamp": datetime.utcnow().isoformat()})

    # Build system prompt (same as WhatsApp)
    system_prompt = build_system_prompt(restaurant, pid)
    # Add web chat context
    system_prompt += "\n\nCONTEXTE : Tu réponds via le chat web du site internet du restaurant (pas WhatsApp). Sois concis et accueillant.\nIMPORTANT CHAT WEB : Pour toute demande de réservation, tu DOIS collecter le numéro de téléphone et l'adresse email du client EN PLUS du nom, nombre de personnes, date et heure. Demande-les poliment, par exemple : 'Pour finaliser votre réservation, pourriez-vous me donner un numéro de téléphone et une adresse email ? C'est pour vous envoyer la confirmation.' Ne confirme JAMAIS une réservation web sans avoir le téléphone."

    # Build messages for Claude
    claude_messages = []
    for msg in session["messages"][-10:]:
        claude_messages.append({"role": msg["role"], "content": msg["content"]})

    # Get AI response
    reply = await ask_claude(system_prompt, claude_messages)

    # Save assistant reply
    session["messages"].append({"role": "assistant", "content": reply, "timestamp": datetime.utcnow().isoformat()})

    # Also save in main conversations for dashboard visibility
    conv_key = f"{pid}:web_{session_id[:8]}"
    if conv_key not in conversations:
        conversations[conv_key] = []
    conversations[conv_key].append({"role": "user", "content": message, "timestamp": datetime.utcnow().isoformat()})
    conversations[conv_key].append({"role": "assistant", "content": reply, "timestamp": datetime.utcnow().isoformat()})
    conversations[conv_key] = conversations[conv_key][-20:]

    # Track stats and contact
    track_stats(pid, language="fr")

    # Extract phone and email from messages
    import re
    phone_match = re.search(r'(?:0|\+33|33)\s*[1-9](?:[\s.-]*\d{2}){4}', message)
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', message)
    if phone_match:
        session["phone"] = re.sub(r'[\s.-]', '', phone_match.group())
    if email_match:
        session["email"] = email_match.group()

    contact_id = session.get("phone") or f"web_{session_id[:8]}"
    contact_name = session.get("name", "")
    track_contact(contact_id, contact_name)
    # Save email if found
    if session.get("email") and contact_id in contacts:
        contacts[contact_id]["email"] = session["email"]
    if session.get("phone") and contact_id in contacts:
        contacts[contact_id]["phone"] = session["phone"]

    # Check for booking keywords
    booking_keywords = ["réserv", "reserv", "book", "table", "prenot"]
    if any(kw in message.lower() for kw in booking_keywords):
        import re
        time_match = re.search(r'(\d{1,2})[h:](\d{2})?', message)
        covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax)', message.lower())
        booking_id = f"R{len(bookings)+1}"
        booking_time = None
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2) or 0)
            m = (m // 15) * 15
            booking_time = f"{h:02d}:{m:02d}"
        covers = int(covers_match.group(1)) if covers_match else 2
        assigned_table = None
        if booking_time and booking_time in ALL_SLOTS:
            assigned_table = find_best_table(pid, booking_time, covers)
            if assigned_table:
                assign_table(pid, booking_time, assigned_table, booking_id)
        bookings.append({
            "id": booking_id, "phone": f"web_{session_id[:8]}", "name": session.get("name", "Web visitor"),
            "message": message[:200], "timestamp": datetime.utcnow().isoformat(),
            "status": "confirmed" if assigned_table else "pending",
            "time": booking_time or "", "covers": covers, "table": assigned_table, "zone": "", "source": "web",
        })
        track_stats(pid, is_booking=True)
        logger.info(f"🌐 Web booking {booking_id}: {session.get('name','')} {covers}p @ {booking_time} -> {assigned_table or 'unassigned'}")

    logger.info(f"🌐 [Web] {session.get('name','visitor')}: {message[:60]}")

    return {"reply": reply, "session_id": session_id}


@app.get("/api/webchat/history")
async def webchat_history(request: Request):
    """Get chat history for a session."""
    session_id = request.query_params.get("session_id", "")
    if not session_id or session_id not in web_sessions:
        return {"messages": []}
    return {"messages": web_sessions[session_id]["messages"][-20:]}


# --- Widget JS ---
WIDGET_JS = """
(function(){
  var BASE='__BASE_URL__';
  var COLOR='__COLOR__';
  var WELCOME='__WELCOME__';
  var RESTAURANT='__RESTAURANT__';
  var SESSION=localStorage.getItem('rb_sid')||('rb_'+Math.random().toString(36).substr(2,12));
  localStorage.setItem('rb_sid',SESSION);
  var open=false,loaded=false;

  // Inject CSS
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

  // Inject HTML
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
    if(!loaded){
      addBotMsg(WELCOME);
      loaded=true;
    }
    if(open)document.getElementById('rb-input').focus();
  }

  function addBotMsg(text){
    var el=document.getElementById('rb-messages');
    var d=document.createElement('div');
    d.className='rb-msg rb-msg-bot';
    d.textContent=text;
    el.appendChild(d);
    el.scrollTop=el.scrollHeight;
  }

  function addUserMsg(text){
    var el=document.getElementById('rb-messages');
    var d=document.createElement('div');
    d.className='rb-msg rb-msg-user';
    d.textContent=text;
    el.appendChild(d);
    el.scrollTop=el.scrollHeight;
  }

  function showTyping(){
    var el=document.getElementById('rb-messages');
    var d=document.createElement('div');
    d.className='rb-typing';
    d.id='rb-typing';
    d.textContent='En train de taper...';
    el.appendChild(d);
    el.scrollTop=el.scrollHeight;
  }

  function hideTyping(){
    var t=document.getElementById('rb-typing');
    if(t)t.remove();
  }

  document.getElementById('rb-send').onclick=async function(){
    var input=document.getElementById('rb-input');
    var msg=input.value.trim();
    if(!msg)return;
    input.value='';
    addUserMsg(msg);
    showTyping();
    try{
      var r=await fetch(BASE+'/api/webchat/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:SESSION,message:msg})});
      if(!r.ok){hideTyping();addBotMsg('Erreur de connexion ('+r.status+'). Reessayez.');return;}
      var d=await r.json();
      hideTyping();
      if(d.reply)addBotMsg(d.reply);
    }catch(e){
      hideTyping();
      addBotMsg('Desole, un probleme technique est survenu. Reessayez dans un instant.');
    }
  };

  // Show badge after 3 seconds
  setTimeout(function(){
    if(!open)document.getElementById('rb-badge').style.display='flex';
  },3000);
})();
"""


@app.get("/widget.js")
async def serve_widget(request: Request):
    """Serve the embeddable chat widget JS."""
    pid = list(restaurants.keys())[0] if restaurants else None
    restaurant_name = restaurants[pid]["name"] if pid else "Restaurant"
    color = request.query_params.get("color", "#C2410C")
    welcome = request.query_params.get("welcome", f"Bonjour ! Bienvenue chez {restaurant_name} 😊 Comment puis-je vous aider ?")

    # Build base URL from request - handle Railway proxy
    base_url = str(request.base_url).rstrip("/")
    # Railway uses HTTPS but proxy sends HTTP internally
    if "railway.app" in base_url or request.headers.get("x-forwarded-proto") == "https":
        base_url = base_url.replace("http://", "https://")

    js = WIDGET_JS.replace("__BASE_URL__", base_url)
    js = js.replace("__COLOR__", color)
    js = js.replace("__WELCOME__", welcome.replace("'", "\\'"))
    js = js.replace("__RESTAURANT__", restaurant_name.replace("'", "\\'"))

    return Response(content=js, media_type="application/javascript")


@app.get("/widget-preview", response_class=HTMLResponse)
async def widget_preview():
    """Preview page for the chat widget."""
    pid = list(restaurants.keys())[0] if restaurants else None
    restaurant_name = restaurants[pid]["name"] if pid else "Restaurant"
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{restaurant_name} — Widget Preview</title>
<style>body{{font-family:Inter,-apple-system,sans-serif;background:#FAF9F7;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.preview-box{{text-align:center;color:#78716C}}.preview-box h1{{color:#1C1917;font-size:24px;margin-bottom:8px}}.preview-box p{{font-size:14px}}</style>
</head><body>
<div class="preview-box"><h1> {restaurant_name}</h1><p>Cliquez sur la bulle en bas a droite pour tester le chat</p></div>
<script src="/widget.js"></script>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    return PRIVACY_HTML


@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return TERMS_HTML


# ==============================================================
# RUN
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

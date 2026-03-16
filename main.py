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
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "orso2026")
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
    for r in review_queue:
        if r["sent"] or r.get("responded"):
            continue
        # Send 2 hours after scheduled (in production, check booking_time + 2h)
        scheduled = datetime.fromisoformat(r["scheduled_at"])
        if (now - scheduled).total_seconds() > 7200:  # 2 hours
            restaurant_pid = r["restaurant_pid"]
            await send_review_request(restaurant_pid, r["phone"], r["name"])
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


def build_system_prompt(restaurant: dict, phone_number_id: str) -> str:
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

RÈGLES STRICTES :
- Ne JAMAIS inventer d'information. Si tu ne sais pas, dis-le et propose d'appeler le restaurant.
- Sur les allergènes/santé : TOUJOURS recommander de confirmer directement avec le restaurant.
- Reste dans ton rôle : tu ne parles QUE du restaurant et de sujets liés.
- Si le message n'a rien à voir, redirige poliment.
- Sois concis : 2-4 phrases max par réponse, sauf si le client pose plusieurs questions.
- Si une demande est complexe ou urgente, propose de transférer au restaurant.
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
    # Persist to DB in background
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_conversation(key, conversations[key]))
    except Exception:
        pass


def track_stats(phone_number_id: str, is_booking: bool = False, language: str = "fr"):
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

        # Try to extract covers
        covers_match = re.search(r'(\d+)\s*(?:pers|couv|place|people|pax)', message.lower())
        covers = int(covers_match.group(1)) if covers_match else 2

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
        if booking_time and booking_time in ALL_SLOTS:
            assigned_table = find_best_table(pid, booking_time, covers, zone_pref)
            if assigned_table:
                assign_table(pid, booking_time, assigned_table, booking_id)

        bookings.append({
            "id": booking_id,
            "phone": customer_phone,
            "name": customer_name or customer_phone,
            "message": message[:200],
            "timestamp": datetime.utcnow().isoformat(),
            "status": "confirmed" if assigned_table else "pending",
            "time": booking_time or "",
            "covers": covers,
            "table": assigned_table,
            "zone": zone_pref,
            "source": "whatsapp",
        })
        track_stats(restaurant["phone_number_id"], is_booking=True)

        # Persist booking to DB
        await db_save_booking(bookings[-1])

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

    # Build system prompt with current status
    system_prompt = build_system_prompt(restaurant, phone_number_id)

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
<title>Orso — Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#FAF9F7;--card:#FFF;--sb:#1C1917;--sbh:#292524;--sba:#44403C;--t:#1C1917;--ts:#78716C;--tm:#A8A29E;--b:#E7E5E4;--bl:#F5F5F4;--ac:#C2410C;--al:#FFF7ED;--ok:#16A34A;--okb:#F0FDF4;--wa:#D97706;--wab:#FFFBEB;--da:#DC2626;--bl2:#2563EB;--blb:#EFF6FF;--f:'DM Sans',-apple-system,sans-serif;--se:Georgia,'Times New Roman',serif}
body{font-family:var(--f);background:var(--bg);color:var(--t);min-height:100vh}
.lo{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
.lbox{text-align:center;width:340px}
.lwm{font-family:var(--se);font-size:44px;font-weight:400;color:var(--t);letter-spacing:-1px}
.lsub{font-size:10px;color:var(--tm);letter-spacing:3px;margin:4px 0 36px}
.lcd{background:var(--card);border-radius:20px;padding:32px 28px;border:1px solid var(--b)}
.linp{width:100%;padding:14px 16px;border-radius:12px;background:var(--bg);border:1.5px solid var(--b);font-size:15px;color:var(--t);outline:none;font-family:var(--f)}
.linp:focus{border-color:var(--ac)}
.lbtn{width:100%;padding:14px;border-radius:12px;border:none;background:var(--sb);color:#fff;font-size:15px;font-weight:600;cursor:pointer;font-family:var(--f);margin-top:12px}
.lbtn:hover{opacity:.9}
.lerr{color:var(--da);font-size:13px;margin-bottom:14px;display:none;background:#FEF2F2;padding:10px 14px;border-radius:10px;border:1px solid #FECACA}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-6px)}40%,80%{transform:translateX(6px)}}
.shake{animation:shake .4s ease}
.app{display:none}.app.v{display:flex}
.sidebar{width:232px;background:var(--sb);position:fixed;height:100vh;display:flex;flex-direction:column;z-index:40}
.sb-b{padding:28px 24px 32px}
.sb-wm{font-family:var(--se);font-size:24px;color:#fff;font-weight:400;letter-spacing:-.5px}
.sb-s{font-size:10px;color:#78716C;letter-spacing:2px;margin-top:2px}
.sb-n{padding:0 12px;flex:1;overflow-y:auto}
.sb-l{font-size:10px;font-weight:600;color:#57534E;letter-spacing:.08em;padding:0 8px;margin-bottom:8px}
.nb{width:100%;display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;border:none;background:transparent;color:#A8A29E;font-size:13px;font-weight:400;text-align:left;font-family:var(--f);cursor:pointer;margin-bottom:2px;transition:all .15s}
.nb:hover{background:var(--sbh);color:#D6D3D1}.nb.on{background:var(--sba);color:#fff;font-weight:600}
.nb .ic{font-size:13px;width:20px;text-align:center;opacity:.5}.nb.on .ic{opacity:1}
.nb-badge{margin-left:auto;min-width:18px;height:18px;border-radius:9px;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;padding:0 5px}
.sb-u{padding:20px 24px;border-top:1px solid #292524;display:flex;align-items:center;gap:10px}
.uav{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#C2410C,#EA580C);display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:700}
.main{flex:1;margin-left:232px}
.topbar{padding:18px 36px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:30;background:rgba(250,249,247,.92);backdrop-filter:blur(16px);border-bottom:1px solid var(--bl)}
.topbar h1{font-size:20px;font-weight:700;letter-spacing:-.02em}
.sp{display:flex;align-items:center;gap:6px;padding:6px 14px;border-radius:20px}
.sd2{width:7px;height:7px;border-radius:50%;box-shadow:0 0 6px rgba(22,163,74,.4)}
.content{padding:28px 36px;max-width:1100px}
.sg{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.sc{background:var(--card);border-radius:16px;padding:24px 20px;border:1px solid var(--b);transition:transform .2s,box-shadow .2s;cursor:default}
.sc:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.06)}
.sl{font-size:11px;font-weight:600;color:var(--tm);letter-spacing:.08em;text-transform:uppercase;margin-bottom:12px}
.sv{font-size:32px;font-weight:700;letter-spacing:-.02em;line-height:1}
.ss2{font-size:12px;color:var(--ts);margin-top:8px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.card{background:var(--card);border-radius:16px;border:1px solid var(--b);overflow:hidden}
.card-h{padding:16px 20px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center}
.card-t{font-size:15px;font-weight:600}.card-s{font-size:12px;color:var(--tm);margin-top:2px}
.ba{padding:6px 14px;border-radius:8px;border:none;background:var(--ac);color:#fff;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f)}
.ba:hover{opacity:.9}
.rw{padding:12px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bl)}
.rw:last-child{border-bottom:none}
.rl{display:flex;align-items:center;gap:10px}
.dot{width:6px;height:6px;border-radius:50%}
.badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:6px}
.src-badge{font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px}
.cr{padding:12px 20px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--bl)}
.cr:last-child{border-bottom:none}
.cav{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.cmsg{font-size:12px;color:var(--ts);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cg3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.cc{padding:16px;border-radius:12px;background:var(--bg);border:1px solid var(--bl)}
.db{background:linear-gradient(135deg,var(--al),#FEF3C7);border:1px solid #FDE68A;border-radius:14px;padding:18px 20px;margin-bottom:20px}
.db-top{display:flex;align-items:flex-start;gap:14px}
.di{width:40px;height:40px;border-radius:10px;background:var(--ac);display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;flex-shrink:0}
.dlb{font-size:11px;font-weight:600;color:var(--ac);letter-spacing:.06em;text-transform:uppercase}
.dtx{font-size:15px;font-weight:600;color:var(--t);margin-top:4px;cursor:pointer;padding:4px 8px;border-radius:8px;border:1.5px solid transparent;transition:border .2s}
.dtx:hover{border-color:#FDE68A}
.dtx-edit{font-size:15px;font-weight:600;color:var(--t);margin-top:4px;padding:8px 10px;border-radius:10px;border:1.5px solid var(--ac);background:#fff;width:100%;outline:none;font-family:var(--f);resize:none;min-height:44px}
.dme{font-size:11px;color:var(--ts);margin-top:4px}
.db-act{display:flex;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid #FDE68A40}
.dbb{padding:7px 14px;border-radius:8px;border:none;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;align-items:center;gap:5px}
.dbb-s{background:var(--ac);color:#fff}.dbb-s:hover{opacity:.9}
.dbb-b{background:var(--bl2);color:#fff}.dbb-b:hover{opacity:.9}
.dbb-c{background:#fff;color:var(--ts);border:1px solid var(--b)}.dbb-c:hover{background:var(--bl)}
.fm{background:var(--card);border-radius:16px;border:1px solid var(--b);padding:20px;margin-bottom:16px;cursor:pointer;transition:transform .2s,box-shadow .2s}
.fm:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.06)}
.fc{position:relative;height:180px;background:var(--bg);border-radius:12px;border:1px solid var(--bl);overflow:hidden;margin-top:12px}
.ftbl{position:absolute;display:flex;flex-direction:column;align-items:center;justify-content:center;border:2px solid;font-size:10px;font-weight:700}
.ms{margin-bottom:20px}
.mc{font-size:13px;font-weight:700;color:var(--ac);letter-spacing:.04em;text-transform:uppercase;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bl)}
.mi-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bl)}
.mi-row:last-child{border-bottom:none}
.mi-n{font-size:14px;font-weight:600}.mi-d{font-size:12px;color:var(--ts);margin-top:2px}.mi-p{font-size:14px;font-weight:700;color:var(--ac);white-space:nowrap}
.cfs{margin-bottom:28px}
.cft{font-size:16px;font-weight:700;margin-bottom:4px}
.cfsb{font-size:12px;color:var(--ts);margin-bottom:16px}
.cfr{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--bl)}
.cfr:last-child{border-bottom:none}
.cfl{font-size:14px;font-weight:500}.cfd{font-size:12px;color:var(--tm)}
.tog{position:relative;width:44px;height:24px;background:var(--b);border-radius:12px;cursor:pointer;transition:background .2s;flex-shrink:0}
.tog.on{background:var(--ac)}
.togd{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:transform .2s;box-shadow:0 1px 3px rgba(0,0,0,.1)}
.tog.on .togd{transform:translateX(20px)}
.toast{position:fixed;bottom:24px;right:24px;background:var(--sb);color:#fff;padding:12px 24px;border-radius:12px;font-weight:600;font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.15);z-index:200;display:none;animation:su .3s ease}
@keyframes su{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.4);display:none;align-items:center;justify-content:center;z-index:150}
.modal-bg.show{display:flex}
.modal{background:var(--card);border-radius:20px;padding:28px;width:420px;max-width:90vw;max-height:90vh;overflow-y:auto}
.modal h2{font-size:18px;font-weight:700;margin-bottom:4px}
.finp{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:14px;color:var(--t);outline:none;font-family:var(--f);margin-bottom:10px}
.finp:focus{border-color:var(--ac)}
.finp-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.finp-label{font-size:11px;font-weight:600;color:var(--tm);letter-spacing:.06em;text-transform:uppercase;margin-bottom:4px}
.finp-group{margin-bottom:4px}
.modal-act{display:flex;gap:8px;margin-top:16px}
.mbtn{flex:1;padding:12px;border-radius:10px;border:none;font-size:14px;font-weight:600;cursor:pointer;font-family:var(--f)}
.mbtn-p{background:var(--ac);color:#fff}.mbtn-p:hover{opacity:.9}
.mbtn-s{background:var(--bg);color:var(--ts);border:1px solid var(--b)}
.at-box{background:var(--okb);border:1px solid #BBF7D0;border-radius:10px;padding:12px 14px;margin-top:8px;display:none}
.at-l{font-size:11px;font-weight:600;color:var(--ok);letter-spacing:.06em;text-transform:uppercase}
.at-v{font-size:20px;font-weight:700;color:var(--ok);margin-top:4px}
.at-c{font-size:12px;color:var(--ac);cursor:pointer;font-weight:600;margin-top:4px}
.tsel{display:none;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:8px}
.tsb{padding:8px;border-radius:8px;border:1.5px solid var(--b);background:var(--card);font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);text-align:center}
.tsb:hover{border-color:var(--ac);background:var(--al)}
.tsb.sel{border-color:var(--ok);background:var(--okb);color:var(--ok)}
.tsb.taken{opacity:.3;cursor:not-allowed}
.dinp{width:100%;padding:14px 16px;border-radius:12px;background:var(--bg);border:1.5px solid var(--b);font-size:14px;color:var(--t);outline:none;font-family:var(--f);resize:none;min-height:60px}
.dinp:focus{border-color:var(--ac)}
.msg-input{flex:1;padding:11px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);color:var(--t);font-size:13px;outline:none;font-family:var(--f)}
.msg-btn{padding:11px 18px;border-radius:10px;border:none;background:var(--ac);color:#fff;font-weight:700;font-size:13px;cursor:pointer;font-family:var(--f);white-space:nowrap}
.ctrl-btn{padding:8px 16px;border-radius:8px;border:1.5px solid var(--b);background:var(--card);font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);transition:all .15s}
.ctrl-btn.on{border-color:var(--ac);background:var(--al);color:var(--ac)}
.slot-btn{position:relative;padding:6px 14px;border-radius:6px;border:none;font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)}
.slot-badge{position:absolute;top:-4px;right:-4px;width:14px;height:14px;border-radius:50%;background:var(--ac);color:#fff;font-size:8px;font-weight:800;display:flex;align-items:center;justify-content:center}
.bubble{padding:10px 14px;border-radius:14px;max-width:80%;font-size:13px;line-height:1.5;margin-bottom:8px}
.bubble-user{background:var(--sb);color:#fff;margin-left:auto;border-bottom-right-radius:4px}
.bubble-bot{background:var(--bg);color:var(--t);margin-right:auto;border-bottom-left-radius:4px}
.conv-list-item{padding:12px 14px;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.conv-list-item.selected{background:var(--al);border-left:3px solid var(--ac)}
.star{color:var(--wa)}
.review-card{padding:16px 20px;border-bottom:1px solid var(--bl)}
.review-card:last-child{border-bottom:none}
.ph{background:var(--card);border-radius:16px;padding:60px;border:1px solid var(--b);text-align:center}
.phi{font-size:36px;opacity:.2;margin-bottom:12px}
.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--sb);padding:8px 0 12px;z-index:50;border-top:1px solid #292524}
.mobile-nav-items{display:flex;justify-content:space-around}
.mobile-nav-btn{background:none;border:none;color:#A8A29E;font-size:10px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 8px}
.mobile-nav-btn.active{color:var(--ac)}
.mobile-nav-btn span{font-size:20px}
@media(max-width:768px){
  .sidebar{display:none}.main{margin-left:0}.sg{grid-template-columns:repeat(2,1fr)}.g2{grid-template-columns:1fr}.cg3{grid-template-columns:1fr}.content{padding:16px;padding-bottom:80px}.topbar{padding:14px 16px}
  .mobile-nav{display:block}
}
</style>
</head>
<body>

<div class="lo" id="loginOverlay">
<div class="lbox">
  <div class="lwm">orso</div>
  <div class="lsub">RESTAURANT AI</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Mot de passe incorrect. Veuillez réessayer.</div>
    <div style="position:relative">
      <input class="linp" type="password" id="loginPwd" placeholder="Mot de passe" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()" oninput="document.getElementById('loginError').style.display='none';this.style.borderColor='var(--b)'">
      <button onclick="togglePwdVis()" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;color:var(--tm);padding:4px" id="pwdToggle" title="Afficher le mot de passe">👁</button>
    </div>
    <button class="lbtn" onclick="doLogin()">Continuer</button>
  </div>
</div>
</div>

<div class="app" id="app">
<div class="sidebar">
  <div class="sb-b"><div class="sb-wm">orso</div><div class="sb-s">RESTAURANT AI</div></div>
  <div class="sb-n">
    <div class="sb-l">NAVIGATION</div>
    <button class="nb on" data-pg="overview"><span class="ic">◎</span> Vue d'ensemble</button>
    <button class="nb" data-pg="floorplan"><span class="ic">⊞</span> Plan de salle</button>
    <button class="nb" data-pg="bookings"><span class="ic">◉</span> Réservations <span class="nb-badge" id="bookBadge" style="background:var(--wa);color:#fff">0</span></button>
    <button class="nb" data-pg="menu"><span class="ic">◐</span> Menu</button>
    <button class="nb" data-pg="conversations"><span class="ic">◈</span> Conversations <span class="nb-badge" id="convBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="reviews"><span class="ic">★</span> Avis <span class="nb-badge" id="reviewBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="contacts"><span class="ic">◇</span> Contacts</button>
    <button class="nb" data-pg="config"><span class="ic">⚙</span> Configuration</button>
    <button class="nb" data-pg="stats"><span class="ic">◫</span> Statistiques</button>
  </div>
  <div class="sb-u">
    <div class="uav">EC</div>
    <div><div style="color:#E7E5E4;font-size:13px;font-weight:500">Edouard C.</div><div style="color:#78716C;font-size:11px">Admin</div></div>
  </div>
</div>

<div class="main">
  <div class="topbar">
    <div><h1 id="pageTitle">Vue d'ensemble</h1><span style="font-size:12px;color:var(--tm)" id="currentDate"></span></div>
    <div style="display:flex;align-items:center;gap:16px">
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--tm)">
        <span style="display:flex;align-items:center;gap:3px"><span class="dot" style="background:#25D366"></span> WhatsApp</span>
        <span style="display:flex;align-items:center;gap:3px"><span class="dot" style="background:#FF6B35"></span> Zenchef</span>
      </div>
      <div class="sp" id="statusPill" style="background:var(--okb)"><div class="sd2" id="statusDot" style="background:var(--ok)"></div> <span id="statusLabel" style="color:var(--ok);font-size:12px;font-weight:600">En ligne</span></div>
      <span style="font-size:13px;color:var(--tm)" id="currentTime"></span>
    </div>
  </div>

  <div class="content" id="mainContent">
  </div>
</div>
</div>

<!-- RESERVATION MODAL -->
<div class="modal-bg" id="resaModal" onclick="if(event.target===this)closeResaModal()">
<div class="modal">
  <h2>Nouvelle réservation</h2>
  <div class="card-s" style="margin-bottom:20px">Remplissez les informations du client</div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Prénom</div><input class="finp" id="resaFirst" placeholder="Marie"></div><div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="resaLast" placeholder="Laurent"></div></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="resaCovers" type="number" min="1" max="20" value="2" onchange="resaAutoAssign()"></div><div class="finp-group"><div class="finp-label">Heure</div><input class="finp" id="resaTime" type="time" value="20:00"></div></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Téléphone</div><input class="finp" id="resaPhone" placeholder="+33 6 ..."></div><div class="finp-group"><div class="finp-label">Email</div><input class="finp" id="resaEmail" placeholder="marie@email.com"></div></div>
  <div class="finp-group"><div class="finp-label">Source</div><select class="finp" id="resaSource" style="cursor:pointer"><option value="phone">Téléphone</option><option value="walk-in">Walk-in</option><option value="whatsapp">WhatsApp</option><option value="web">Chat web</option><option value="zenchef">Zenchef</option></select></div>
  <div class="at-box" id="resaTableBox"><div class="at-l">Table assignée automatiquement</div><div class="at-v" id="resaTableVal"></div><div class="at-c" onclick="showResaTableSelect()">Changer de table</div></div>
  <div class="tsel" id="resaTableSel"></div>
  <div class="modal-act"><button class="mbtn mbtn-s" onclick="closeResaModal()">Annuler</button><button class="mbtn mbtn-p" onclick="submitResa()">Confirmer</button></div>
</div>
</div>

<div class="mobile-nav">
  <div class="mobile-nav-items">
    <button class="mobile-nav-btn active" data-pg="overview"><span>◎</span>Accueil</button>
    <button class="mobile-nav-btn" data-pg="floorplan"><span>⊞</span>Plan</button>
    <button class="mobile-nav-btn" data-pg="bookings"><span>◉</span>Résas</button>
    <button class="mobile-nav-btn" data-pg="conversations"><span>◈</span>Chat</button>
    <button class="mobile-nav-btn" data-pg="config"><span>⚙</span>Config</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var SK='{{SECRET_KEY}}',DP='{{DASHBOARD_PASSWORD}}';
var dailyMsg='';
var resaSelTable=null;

function doLogin(){
  var inp=document.getElementById('loginPwd');
  var err=document.getElementById('loginError');
  if(inp.value===DP){
    document.getElementById('loginOverlay').style.display='none';
    document.getElementById('app').classList.add('v');
    loadAll();
  }else{
    err.style.display='block';
    inp.style.borderColor='var(--da)';
    inp.classList.remove('shake');
    void inp.offsetWidth;
    inp.classList.add('shake');
    inp.focus();
  }
}
function togglePwdVis(){
  var inp=document.getElementById('loginPwd');
  var btn=document.getElementById('pwdToggle');
  if(inp.type==='password'){inp.type='text';btn.textContent='🔒'}
  else{inp.type='password';btn.textContent='👁'}
}

var pageTitles={overview:"Vue d'ensemble",floorplan:"Plan de salle",bookings:"Réservations",menu:"Menu",conversations:"Conversations",reviews:"Avis",contacts:"Contacts",config:"Configuration",stats:"Statistiques"};

function switchPage(id,btn){
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
var restaurantConfig={};
var overviewBlocks={daily:true,stats:true,floor:true,bookings:true,contacts:true};

function loadAll(){
  updateTime();setInterval(updateTime,30000);
  fetchData();
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
    var rvData=res[4]||{};
    reviewQueue=(rvData.queue||[]);
    restaurantConfig=res[5]||{};
    dailyMsg=(res[6]&&res[6].message)||'';
    menuSections=(res[7]&&res[7].sections)||[];
    updateBadges();
    renderPage('overview');
  }).catch(function(err){
    console.error('Load error:',err);
    renderPage('overview');
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
  var today=new Date().toISOString().slice(0,10);
  var tb=bookings.filter(function(b){return(b.date||'').startsWith(today)});
  var convArr=Object.entries(conversations);
  var ctArr=Object.entries(contacts);
  var totalSeats=floorplan.reduce(function(a,t){return a+t.seats},0);
  
  var h='';
  
  // Daily message
  if(overviewBlocks.daily){
    h+='<div class="db" id="ov-daily"><div class="db-top"><div class="di">📢</div><div style="flex:1"><div class="dlb">Message du jour <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--ts)">— cliquez pour modifier</span></div>';
    h+='<div class="dtx" id="dailyView" onclick="editDaily()">'+(dailyMsg||'Aucun message — cliquez pour ajouter')+'</div>';
    h+='<textarea class="dtx-edit" id="dailyEdit" style="display:none"></textarea>';
    h+='<div class="dme" id="dailyMeta">Transmis automatiquement par l agent IA aux clients</div></div></div>';
    h+='<div class="db-act" id="dailyActions" style="display:none"><button class="dbb dbb-s" onclick="saveDaily()">💾 Enregistrer</button><button class="dbb dbb-b" onclick="broadcastDaily()">📤 Envoyer aux contacts</button><button class="dbb dbb-c" onclick="cancelDaily()">Annuler</button></div></div>';
  }
  
  // Stats
  if(overviewBlocks.stats){
    h+='<div class="sg" id="ov-stats">';
    h+='<div class="sc"><div class="sl">Messages</div><div class="sv" style="color:var(--ac)">'+convArr.reduce(function(a,e){var d=e[1];return a+((d.messages&&d.messages.length)||d.count||0)},0)+'</div><div class="ss2">total</div></div>';
    h+='<div class="sc"><div class="sl">Réservations</div><div class="sv" style="color:var(--ok)">'+tb.length+'</div><div class="ss2">aujourd&#39;hui</div></div>';
    h+='<div class="sc"><div class="sl">Conversations</div><div class="sv" style="color:var(--bl2)">'+convArr.length+'</div><div class="ss2">clients actifs</div></div>';
    h+='<div class="sc"><div class="sl">Contacts</div><div class="sv" style="color:var(--wa)">'+ctArr.length+'</div><div class="ss2">en base</div></div>';
    h+='</div>';
  }
  
  // Floor plan mini
  if(overviewBlocks.floor){
    h+='<div class="fm" id="ov-floor" data-nav="floorplan"><div style="display:flex;justify-content:space-between;align-items:center"><div><div class="card-t">Plan de salle</div><div class="card-s">'+floorplan.length+' tables · '+totalSeats+' places</div></div><span style="font-size:12px;color:var(--ac);font-weight:600">Modifier →</span></div><div class="fc" id="floorMiniCanvas"></div></div>';
  }
  
  // Bookings + Conversations
  if(overviewBlocks.bookings){
    var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
    h+='<div class="g2" id="ov-book"><div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+tb.length+' aujourd&#39;hui</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle</button></div>';
    tb.slice(0,5).forEach(function(b){
      h+='<div class="rw"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+b.booking_time+'</div></div></div><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div>';
    });
    if(tb.length===0) h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucune réservation aujourd&#39;hui</div>';
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
function renderFloorplan(c){
  c.innerHTML='<div class="card" style="padding:20px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px"><div><div class="card-t">Plan de salle</div><div class="card-s">'+floorplan.length+' tables · '+floorplan.reduce(function(a,t){return a+t.seats},0)+' places</div></div><button class="ba">+ Ajouter une table</button></div><div style="position:relative;height:400px;background:var(--bg);border-radius:14px;border:2px solid var(--b);overflow:hidden" id="floorFullCanvas"><div style="position:absolute;top:10px;left:16px;font-size:10px;color:var(--tm);font-weight:700">SALLE</div><div style="position:absolute;top:10px;left:52%;font-size:10px;color:var(--tm);font-weight:700">TERRASSE</div><div style="position:absolute;top:10px;right:16px;font-size:10px;color:var(--tm);font-weight:700">BAR</div><div style="position:absolute;left:46%;top:0;bottom:0;width:1px;border-left:1px dashed var(--b)"></div><div style="position:absolute;left:84%;top:0;bottom:0;width:1px;border-left:1px dashed var(--b)"></div></div></div>';
  drawFloorFull();
}
function drawFloorFull(){
  var el=document.getElementById('floorFullCanvas');
  if(!el||!floorplan.length)return;
  el.querySelectorAll('.ftbl').forEach(function(e){e.remove()});
  var zoneColors={salle:'#2563EB',terrasse:'#16A34A',bar:'#D97706'};
  floorplan.forEach(function(t){
    var d=document.createElement('div');d.className='ftbl';
    var w=t.shape==='round'?(t.seats<=2?42:t.seats<=4?50:58):(t.seats<=2?42:t.seats<=4?54:t.seats<=6?64:74);
    var h2=t.shape==='round'?w:(t.seats<=4?42:46);
    var c2=zoneColors[t.zone]||'#2563EB';
    var bk=t.booking_name;
    d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'10px')+';border-color:'+(bk?'#DC262660':c2+'50')+';background:'+(bk?'#DC262610':c2+'08')+';color:'+(bk?'#DC2626':c2)+';cursor:grab';
    d.innerHTML='<div style="font-size:11px;font-weight:800">'+t.id+'</div><div style="font-size:9px;color:'+(bk?'#DC2626':'var(--tm)')+'">'+( bk||t.seats+'p')+'</div>';
    el.appendChild(d);
  });
}

// ===== BOOKINGS =====
function renderBookings(c){
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
  var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
  var h='<div class="card"><div class="card-h"><div><div class="card-t">Toutes les réservations</div><div class="card-s">'+bookings.length+' au total</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle</button></div>';
  bookings.forEach(function(b){
    h+='<div class="rw"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.date||'')+' '+b.booking_time+(b.phone?' · '+b.phone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div></div>';
  });
  if(!bookings.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucune réservation</div>';
  h+='</div>';
  c.innerHTML=h;
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

  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><div><div class="card-t">La Carte</div><div class="card-s">'+(restaurantConfig.name||'Restaurant')+'</div></div><div style="display:flex;gap:8px"><button class="ba" data-addSection>+ Section</button></div></div>';

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
  var srcLabels={whatsapp:'WhatsApp',web:'Web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
  var h='<div class="card"><div class="card-h"><div><div class="card-t">Tous les contacts</div><div class="card-s">'+entries.length+' clients</div></div></div>';
  entries.forEach(function(e){
    var phone=e[0],ct=e[1];
    var src=ct.source||'phone';
    h+='<div class="rw"><div class="rl"><div style="width:36px;height:36px;border-radius:50%;background:var(--al);display:flex;align-items:center;justify-content:center;color:var(--ac);font-size:13px;font-weight:700">'+(ct.name||'?').charAt(0).toUpperCase()+'</div><div><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm)">'+phone+(ct.email?' · '+ct.email:'')+'</div></div></div><div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:var(--ts)">'+(ct.visits||0)+' visites</span><span class="src-badge" style="color:'+(srcColors[src]||'#A8A29E')+';background:'+(srcColors[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
  });
  if(!entries.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucun contact</div>';
  h+='</div>';
  c.innerHTML=h;
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
  c.innerHTML='<div class="ph"><div class="phi">◫</div><div style="font-size:18px;font-weight:600;margin-bottom:4px">Statistiques</div><div style="font-size:14px;color:var(--tm)">Graphiques et analytics (prochainement)</div></div>';
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
  document.getElementById('resaModal').classList.add('show');
  resaAutoAssign();
}
function closeResaModal(){document.getElementById('resaModal').classList.remove('show')}

function resaAutoAssign(){
  var covers=parseInt(document.getElementById('resaCovers').value)||2;
  var best=null;
  var bookedTables=bookings.map(function(b){return b.table});
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
  var bookedTables=bookings.map(function(b){return b.table});
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
  if(!first||!last){showToast('Veuillez remplir le nom et prénom');return}
  if(!resaSelTable){showToast('Aucune table disponible');return}
  var data={
    name:first+' '+last,
    covers:parseInt(document.getElementById('resaCovers').value)||2,
    time:document.getElementById('resaTime').value,
    phone:document.getElementById('resaPhone').value.trim(),
    email:document.getElementById('resaEmail').value.trim(),
    source:document.getElementById('resaSource').value,
    table:resaSelTable
  };
  fetch('/api/bookings/manual?key='+SK,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
    fetchData();
    closeResaModal();
    showToast('✅ '+data.name+' — Table '+resaSelTable);
  }).catch(function(){
    showToast('Erreur lors de la creation');
  });
}

// === EVENT DELEGATION ===
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-pg]');
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
@app.get("/dashboard/{secret_key}", response_class=HTMLResponse)
async def dashboard(secret_key: str):
    if secret_key != DASHBOARD_SECRET:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return DASHBOARD_HTML.replace("{{SECRET_KEY}}", secret_key).replace("{{DASHBOARD_PASSWORD}}", DASHBOARD_PASSWORD)


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
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
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
    return {"status": "created", "booking_id": booking_id, "table": assigned_table}


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

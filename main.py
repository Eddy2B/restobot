"""
RestoBot — Agent IA WhatsApp pour la Restauration
Version 3.0 — Commandes restaurateur + Dashboard + Privacy Policy
"""

import os
import json
import logging
import hashlib
import secrets
from datetime import datetime, date, time, timedelta
from contextlib import asynccontextmanager

import anthropic
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ==============================================================
# CONFIG
# ==============================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "restobot-verify-2026")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
PORT = int(os.getenv("PORT", 8000))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "restobot2026")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", secrets.token_urlsafe(32))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("restobot")

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

OWNER_COMMANDS_HELP = """🤖 *Commandes RestoBot :*

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
            f"RestoBot a répondu automatiquement."
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
    logger.info(f"🤖 Réponse: {response[:80]}")


# ==============================================================
# DASHBOARD HTML
# ==============================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RestoBot Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',-apple-system,sans-serif;background:#F1F5F9;color:#0F1B2D;min-height:100vh}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#CBD5E1;border-radius:3px}
input::placeholder{color:#94A3B8}
@keyframes slideUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}

.login-overlay{position:fixed;inset:0;background:#F1F5F9;display:flex;justify-content:center;align-items:center;z-index:1000}
.login-box{background:white;border-radius:20px;padding:48px 40px;width:400px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.login-logo{width:56px;height:56px;border-radius:14px;background:#0F1B2D;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:28px}
.login-box h2{color:#0F1B2D;font-size:22px;font-weight:800;margin-bottom:2px}
.login-box .sub{color:#94A3B8;font-size:13px;margin-bottom:28px}
.login-box input{width:100%;padding:13px 16px;border-radius:10px;background:#F8FAFC;border:1.5px solid #E2E8F0;color:#0F1B2D;font-size:14px;outline:none;margin-bottom:14px;font-family:inherit}
.login-box input:focus{border-color:#00D4AA}
.login-box button{width:100%;padding:13px;border-radius:10px;border:none;background:#00D4AA;color:white;font-size:14px;font-weight:700;cursor:pointer;font-family:inherit}
.login-error{color:#EF4444;font-size:13px;margin-bottom:12px;display:none}

.app{display:flex;min-height:100vh}
.sidebar{width:220px;background:#0F1B2D;padding:24px 0;display:flex;flex-direction:column;position:fixed;height:100vh;z-index:40}
.sidebar-brand{padding:0 20px;margin-bottom:32px;display:flex;align-items:center;gap:10px}
.nav-item{width:100%;display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;border:none;cursor:pointer;margin-bottom:2px;background:transparent;color:#94A3B8;font-size:13px;font-weight:500;text-align:left;font-family:inherit;transition:all .15s}
.nav-item:hover{background:rgba(255,255,255,.05)}
.nav-item.active{background:rgba(0,212,170,.12);color:#00D4AA;font-weight:600}
.nav-badge{margin-left:auto;background:#EF4444;color:white;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px}
.main{flex:1;margin-left:220px}
.topbar{background:white;padding:14px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #E2E8F0;position:sticky;top:0;z-index:30}
.topbar h1{font-size:18px;font-weight:800;color:#0F1B2D}
.status-pill{display:inline-flex;align-items:center;gap:6px;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700}
.content{padding:24px 32px}
.page{display:none}.page.active{display:block}
.card{background:white;border-radius:14px;padding:22px 20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.stat-label{color:#94A3B8;font-size:11px;font-weight:700;letter-spacing:.06em;margin-bottom:10px}
.stat-value{font-size:36px;font-weight:800;color:#0F1B2D;line-height:1}
.card-title{font-size:16px;font-weight:700;color:#0F1B2D;margin-bottom:4px}
.card-sub{font-size:12px;color:#94A3B8}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}
.ctrl-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:20px}
.ctrl-btn{padding:12px;border-radius:10px;border:2px solid #E2E8F0;background:white;color:#64748B;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .2s}
.ctrl-btn.on{border-color:#00D4AA;background:rgba(0,212,170,.06);color:#00D4AA}
.msg-row{display:flex;gap:8px}
.msg-input{flex:1;padding:11px 14px;border-radius:10px;background:#F8FAFC;border:1.5px solid #E2E8F0;color:#0F1B2D;font-size:13px;outline:none;font-family:inherit}
.msg-btn{padding:11px 18px;border-radius:10px;border:none;background:#00D4AA;color:white;font-weight:700;font-size:13px;cursor:pointer;font-family:inherit;white-space:nowrap}
.msg-active{margin-top:10px;padding:10px 14px;border-radius:10px;background:rgba(0,212,170,.05);border:1px solid rgba(0,212,170,.15);display:flex;justify-content:space-between;align-items:center;font-size:13px}

/* Floor plan */
.fp-zone-label{font-size:10px;color:#CBD5E1;font-weight:700;position:absolute;top:8px}
.fp-table{position:absolute;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;border:2px solid #E2E8F0;background:white;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.fp-table:hover{transform:scale(1.08);z-index:10}
.fp-table.occupied{border-color:rgba(0,212,170,.5);background:rgba(0,212,170,.08)}
.fp-table.blocked{border-color:rgba(239,68,68,.4);background:rgba(239,68,68,.06)}
.fp-table.assign-target{border:2px dashed #2563EB;background:rgba(37,99,235,.08);box-shadow:0 0 12px rgba(37,99,235,.2)}
.fp-tid{font-size:11px;font-weight:800}
.fp-tsub{font-size:9px;font-weight:600}

.slot-btn{padding:6px 10px;border-radius:8px;border:none;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;flex-shrink:0;position:relative}
.slot-btn.active{background:#0F1B2D;color:white}
.slot-btn.has-bookings{background:rgba(0,212,170,.12);color:#00D4AA}
.slot-badge{position:absolute;top:-4px;right:-4px;width:14px;height:14px;border-radius:50%;background:#00D4AA;color:white;font-size:8px;font-weight:800;display:flex;align-items:center;justify-content:center}

.booking-card{padding:14px 20px;border-bottom:1px solid #F1F5F9;cursor:pointer;transition:background .15s}
.booking-card:hover{background:#F8FAFC}
.booking-card.selected{background:rgba(37,99,235,.04)}
.src-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}

/* Conversations */
.conv-list-item{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid #F1F5F9;cursor:pointer;transition:background .15s}
.conv-list-item:hover{background:#F8FAFC}
.conv-list-item.selected{background:rgba(0,212,170,.06);border-left:3px solid #00D4AA}
.conv-avatar{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;flex-shrink:0}
.bubble{max-width:75%;padding:10px 14px;border-radius:14px;font-size:13px;line-height:1.5;margin-bottom:8px;word-wrap:break-word}
.bubble-user{background:#E8F5E9;color:#1B5E20;margin-left:auto;border-bottom-right-radius:4px}
.bubble-bot{background:#F1F5F9;color:#0F1B2D;margin-right:auto;border-bottom-left-radius:4px}

.empty-state{text-align:center;padding:60px 20px;color:#94A3B8}
.empty-state span{font-size:48px;display:block;margin-bottom:12px}
.hidden{display:none!important}
.toast{position:fixed;bottom:24px;right:24px;background:#00D4AA;color:white;padding:12px 24px;border-radius:12px;font-weight:700;font-size:14px;box-shadow:0 8px 24px rgba(0,212,170,.4);animation:slideUp .3s ease;z-index:100;display:none}

/* Mobile responsive */
@media(max-width:768px){
  .sidebar{display:none}
  .main{margin-left:0}
  .topbar{padding:12px 16px}
  .topbar h1{font-size:15px}
  .content{padding:16px}
  .app{flex-direction:column}
  body::before{content:'';display:block;background:#0F1B2D;padding:10px 16px;color:white;font-size:14px;font-weight:700}
}
@media(max-width:768px){
  #page-floorplan>div:first-child{flex-wrap:wrap}
  #page-floorplan .card>div:first-child{display:none}
  #floorplanCanvas{height:300px!important}
  #page-floorplan>div:last-child{grid-template-columns:1fr!important}
  #page-conversations .card>div{grid-template-columns:1fr!important;height:auto!important}
  #fpSummary{flex-wrap:wrap}
}

/* Mobile nav */
.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:#0F1B2D;padding:8px 0 12px;z-index:50;border-top:1px solid #1E293B}
.mobile-nav-items{display:flex;justify-content:space-around}
.mobile-nav-btn{background:none;border:none;color:#94A3B8;font-size:10px;font-weight:600;cursor:pointer;font-family:inherit;display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 8px}
.mobile-nav-btn.active{color:#00D4AA}
.mobile-nav-btn span{font-size:20px}
@media(max-width:768px){
  .mobile-nav{display:block}
  .content{padding-bottom:80px}
}
</style>
</head>
<body>

<div class="login-overlay" id="loginOverlay">
<div class="login-box">
  <div class="login-logo">🤖</div>
  <h2>RestoBot</h2>
  <p class="sub">Tableau de bord restaurateur</p>
  <div class="login-error" id="loginError">Mot de passe incorrect</div>
  <input type="password" id="loginPwd" placeholder="Mot de passe" onkeydown="if(event.key==='Enter')doLogin()">
  <button onclick="doLogin()">Connexion</button>
</div>
</div>

<div class="app hidden" id="app">
<div class="sidebar">
  <div class="sidebar-brand">
    <div style="width:34px;height:34px;border-radius:10px;background:#00D4AA;display:flex;align-items:center;justify-content:center;font-size:18px">🤖</div>
    <div><div style="color:white;font-size:15px;font-weight:800">RestoBot</div><div style="color:#64748B;font-size:10px">Le Cosi Nice</div></div>
  </div>
  <div style="padding:0 12px;flex:1">
    <div style="color:#475569;font-size:10px;font-weight:700;letter-spacing:.08em;padding:0 8px;margin-bottom:8px">PRINCIPAL</div>
    <button class="nav-item active" onclick="switchPage('floorplan',this)">🗺️ Plan de salle</button>
    <button class="nav-item" onclick="switchPage('bookings',this)">📋 Réservations <span class="nav-badge" id="bookBadge" style="background:#F59E0B">0</span></button>
    <button class="nav-item" onclick="switchPage('conversations',this)">💬 Conversations <span class="nav-badge" id="convBadge">0</span></button>
    <button class="nav-item" onclick="switchPage('reviews',this)">⭐ Avis Google <span class="nav-badge" id="reviewBadge" style="background:#00D4AA">0</span></button>
    <button class="nav-item" onclick="switchPage('contacts',this)">👥 Contacts <span class="nav-badge" id="contactBadge" style="background:#8B5CF6">0</span></button>
    <button class="nav-item" onclick="switchPage('dashboard',this)">📊 Statistiques</button>
  </div>
  <div style="padding:16px 20px;border-top:1px solid #1E293B;display:flex;align-items:center;gap:10px">
    <div style="width:32px;height:32px;border-radius:50%;background:#00D4AA;display:flex;align-items:center;justify-content:center;color:white;font-size:13px;font-weight:700">EC</div>
    <div><div style="color:white;font-size:12px;font-weight:600">Edouard F.</div><div style="color:#64748B;font-size:10px">Admin</div></div>
  </div>
</div>

<div class="main">
  <div class="topbar">
    <div><h1 id="pageTitle">Plan de salle</h1><span style="font-size:12px;color:#94A3B8" id="currentDate"></span></div>
    <div style="display:flex;align-items:center;gap:16px">
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#94A3B8">
        <span style="display:flex;align-items:center;gap:3px"><span class="src-dot" style="background:#25D366"></span> WhatsApp</span>
        <span style="display:flex;align-items:center;gap:3px"><span class="src-dot" style="background:#FF6B35"></span> Zenchef</span>
      </div>
      <div class="status-pill" id="statusPill" style="background:rgba(0,212,170,.1);color:#00D4AA"><div style="width:7px;height:7px;border-radius:50%;background:#00D4AA" id="statusDot"></div> <span id="statusLabel">En ligne</span></div>
      <span style="font-size:13px;color:#94A3B8;font-family:monospace" id="currentTime"></span>
    </div>
  </div>

  <div class="content">

    <!-- PAGE: FLOOR PLAN -->
    <div class="page active" id="page-floorplan">
      <div style="display:flex;gap:4;margin-bottom:12px;align-items:center">
        <div style="display:flex;gap:4px;background:#F1F5F9;border-radius:8px;padding:3px;margin-right:12px">
          <button class="slot-btn" onclick="switchService('midi',this)" id="svc-midi" style="background:#0F1B2D;color:white;padding:6px 14px;border-radius:6px;border:none;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit">☀️ Midi</button>
          <button class="slot-btn" onclick="switchService('soir',this)" id="svc-soir" style="padding:6px 14px;border-radius:6px;border:none;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;background:transparent;color:#94A3B8">🌙 Soir</button>
        </div>
        <div id="slotSelector" style="display:flex;gap:4px;overflow-x:auto;padding-bottom:4px;flex:1"></div>
      </div>

      <div id="assignBanner" class="hidden" style="background:rgba(37,99,235,.08);border:1.5px dashed #2563EB;border-radius:12px;padding:10px 16px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:13px;font-weight:600;color:#2563EB">🎯 Cliquez sur une table libre pour assigner</span>
        <button onclick="cancelAssign()" style="background:white;border:1px solid #2563EB;border-radius:6px;padding:4px 12px;font-size:11px;font-weight:600;color:#2563EB;cursor:pointer;font-family:inherit">Annuler</button>
      </div>

      <div style="display:flex;gap:12px;margin-bottom:16px" id="fpSummary"></div>

      <div style="display:grid;grid-template-columns:1fr 340px;gap:0">
        <div class="card" style="padding:20px;border-radius:14px 0 0 14px">
          <div style="display:flex;gap:12px;margin-bottom:12px">
            <span style="font-size:11px;font-weight:700;color:#94A3B8;padding:3px 10px;background:#F8FAFC;border-radius:6px">🏠 Salle</span>
            <span style="font-size:11px;font-weight:700;color:#94A3B8;padding:3px 10px;background:#F8FAFC;border-radius:6px">🌿 Terrasse</span>
            <span style="font-size:11px;font-weight:700;color:#94A3B8;padding:3px 10px;background:#F8FAFC;border-radius:6px">🍸 Bar</span>
          </div>
          <div id="floorplanCanvas" style="position:relative;height:400px;background:#FAFBFD;border-radius:12px;border:1px solid #E2E8F0">
            <div style="position:absolute;left:52%;top:0;bottom:0;width:1px;border-left:1px dashed #E2E8F0"></div>
            <div style="position:absolute;left:82%;top:0;bottom:0;width:1px;border-left:1px dashed #E2E8F0"></div>
            <div class="fp-zone-label" style="left:20%">SALLE</div>
            <div class="fp-zone-label" style="left:63%">TERRASSE</div>
            <div class="fp-zone-label" style="left:85%">BAR</div>
          </div>
        </div>

        <div style="background:white;border-left:1px solid #E2E8F0;border-radius:0 14px 14px 0;overflow-y:auto;max-height:520px;box-shadow:0 1px 3px rgba(0,0,0,.04)">
          <div style="padding:16px 20px;border-bottom:1px solid #E2E8F0">
            <div style="font-size:15px;font-weight:700;color:#0F1B2D" id="fpPanelTitle">Reservations</div>
            <div style="font-size:12px;color:#94A3B8" id="fpPanelSub"></div>
          </div>
          <div id="fpBookingList"></div>
        </div>
      </div>
    </div>

    <!-- PAGE: BOOKINGS -->
    <div class="page" id="page-bookings">
      <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:20px 24px;border-bottom:1px solid #E2E8F0"><div class="card-title">Toutes les reservations</div><div class="card-sub">WhatsApp + Zenchef</div></div>
        <div id="allBookingsList"></div>
      </div>
    </div>

    <!-- PAGE: CONVERSATIONS -->
    <div class="page" id="page-conversations">
      <div class="card" style="padding:0;overflow:hidden">
        <div style="display:grid;grid-template-columns:360px 1fr;height:calc(100vh - 130px)">
          <div style="border-right:1px solid #E2E8F0;overflow-y:auto" id="convSidebar"></div>
          <div style="display:flex;flex-direction:column;height:100%">
            <div style="padding:16px 20px;border-bottom:1px solid #E2E8F0;font-weight:700;font-size:15px;color:#0F1B2D" id="chatHeader">Selectionnez une conversation</div>
            <div style="flex:1;overflow-y:auto;padding:20px" id="chatBody"><div class="empty-state"><span>💬</span>Cliquez sur une conversation</div></div>
          </div>
        </div>
      </div>
    </div>

    <!-- PAGE: REVIEWS -->
    <div class="page" id="page-reviews">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px">
        <div class="card" style="border-top:3px solid #00D4AA"><div class="stat-label">AVIS POSITIFS</div><div class="stat-value" id="revPositive" style="color:#00D4AA">0</div><div style="font-size:12px;color:#94A3B8;margin-top:4px">Redirigés vers Google</div></div>
        <div class="card" style="border-top:3px solid #EF4444"><div class="stat-label">RETOURS NÉGATIFS</div><div class="stat-value" id="revNegative" style="color:#EF4444">0</div><div style="font-size:12px;color:#94A3B8;margin-top:4px">Gérés en privé</div></div>
        <div class="card" style="border-top:3px solid #F59E0B"><div class="stat-label">EN ATTENTE</div><div class="stat-value" id="revPending" style="color:#F59E0B">0</div><div style="font-size:12px;color:#94A3B8;margin-top:4px">Relance planifiée</div></div>
      </div>
      <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:20px 24px;border-bottom:1px solid #E2E8F0;display:flex;justify-content:space-between;align-items:center">
          <div><div class="card-title">File de relance</div><div class="card-sub">Envoi automatique 2h apres le repas</div></div>
          <div style="padding:6px 12px;background:rgba(0,212,170,.08);border-radius:8px;font-size:11px;font-weight:700;color:#00D4AA">Automatique ✓</div>
        </div>
        <div id="reviewList"></div>
      </div>
      <div style="background:rgba(37,99,235,.06);border:1px solid rgba(37,99,235,.15);border-radius:12px;padding:16px;margin-top:16px">
        <div style="font-size:13px;font-weight:600;color:#2563EB;margin-bottom:6px">💡 Comment ca marche</div>
        <div style="font-size:12px;color:#64748B;line-height:1.6">2h apres le repas, RestoBot envoie : "Comment s'est passe votre repas ?". Si <b style="color:#00D4AA">positif</b> → lien Google Reviews. Si <b style="color:#EF4444">negatif</b> → feedback prive. Le restaurateur ne recoit que des avis positifs sur Google.</div>
      </div>
    </div>

    
    <!-- PAGE: CONTACTS CRM -->
    <div class="page" id="page-contacts">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px">
        <div class="card" style="border-top:3px solid #8B5CF6"><div class="stat-label">TOTAL CONTACTS</div><div class="stat-value" id="crmTotal" style="color:#8B5CF6">0</div><div style="font-size:12px;color:#94A3B8;margin-top:4px">Base clients WhatsApp</div></div>
        <div class="card" style="border-top:3px solid #00D4AA"><div class="stat-label">CETTE SEMAINE</div><div class="stat-value" id="crmWeek" style="color:#00D4AA">0</div><div style="font-size:12px;color:#94A3B8;margin-top:4px">Nouveaux contacts</div></div>
        <div class="card" style="border-top:3px solid #2563EB"><div class="stat-label">FIDELES</div><div class="stat-value" id="crmLoyal" style="color:#2563EB">0</div><div style="font-size:12px;color:#94A3B8;margin-top:4px">2+ visites</div></div>
      </div>
      <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:20px 24px;border-bottom:1px solid #E2E8F0;display:flex;justify-content:space-between;align-items:center">
          <div><div class="card-title">Base de contacts</div><div class="card-sub">Tous les clients WhatsApp</div></div>
        </div>
        <div id="contactsList"></div>
      </div>
      <div style="background:rgba(139,92,246,.06);border:1px solid rgba(139,92,246,.15);border-radius:12px;padding:16px;margin-top:16px">
        <div style="font-size:13px;font-weight:600;color:#8B5CF6;margin-bottom:6px">💡 CRM automatique</div>
        <div style="font-size:12px;color:#64748B;line-height:1.6">Chaque client qui contacte le restaurant via WhatsApp est automatiquement enregistre. Utilisez cette base pour envoyer des messages promo, invitations evenements, ou offres speciales.</div>
      </div>
    </div>

    <!-- PAGE: STATS -->
    <div class="page" id="page-dashboard">
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px">
        <div class="card" style="border-top:3px solid #2563EB"><div class="stat-label">MESSAGES</div><div class="stat-value" id="msgCount">0</div><div style="font-size:12px;color:#00D4AA;font-weight:600;margin-top:6px">aujourd'hui</div></div>
        <div class="card" style="border-top:3px solid #00D4AA"><div class="stat-label">RESERVATIONS</div><div class="stat-value" id="bookCount">0</div><div style="font-size:12px;color:#00D4AA;font-weight:600;margin-top:6px">aujourd'hui</div></div>
        <div class="card" style="border-top:3px solid #8B5CF6"><div class="stat-label">CONVERSATIONS</div><div class="stat-value" id="convCount">0</div><div style="font-size:12px;color:#94A3B8;font-weight:600;margin-top:6px">clients actifs</div></div>
        <div class="card" style="border-top:3px solid #F59E0B"><div class="stat-label">TEMPS ECONOMISE</div><div class="stat-value" id="timeSaved">0h</div><div style="font-size:12px;color:#00D4AA;font-weight:600;margin-top:6px">vs gestion manuelle</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 300px;gap:16px">
        <div class="card" style="padding:24px">
          <div class="card-title">Activite 7 derniers jours</div>
          <div style="position:relative;height:180px;margin-top:16px"><svg id="chartSvg" viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:100%;overflow:visible"></svg></div>
          <div style="display:flex;justify-content:space-between;margin-top:8px"><span style="font-size:11px;color:#94A3B8">Lun</span><span style="font-size:11px;color:#94A3B8">Mar</span><span style="font-size:11px;color:#94A3B8">Mer</span><span style="font-size:11px;color:#94A3B8">Jeu</span><span style="font-size:11px;color:#94A3B8">Ven</span><span style="font-size:11px;color:#94A3B8">Sam</span><span style="font-size:11px;color:#94A3B8">Dim</span></div>
        </div>
        <div class="card" style="padding:24px">
          <div class="card-title">Controle rapide</div>
          <div class="ctrl-grid" style="margin-top:16px">
            <button class="ctrl-btn on" id="btn-open" onclick="setStatus('open')">🟢 Ouvert</button>
            <button class="ctrl-btn" id="btn-full_tonight" onclick="setStatus('full_tonight')">🔴 Complet soir</button>
            <button class="ctrl-btn" id="btn-full_lunch" onclick="setStatus('full_lunch')">🟠 Complet midi</button>
            <button class="ctrl-btn" id="btn-closed_today" onclick="setStatus('closed_today')">⛔ Ferme</button>
          </div>
          <div class="stat-label">LANGUES</div>
          <div id="langRow" style="display:flex;gap:8px"></div>
        </div>
      </div>
    </div>

  </div>
</div>
</div>


<div class="mobile-nav" id="mobileNav">
  <div class="mobile-nav-items">
    <button class="mobile-nav-btn active" onclick="switchPage('floorplan',null);setMobileActive(this)"><span>🗺️</span>Plan</button>
    <button class="mobile-nav-btn" onclick="switchPage('bookings',null);setMobileActive(this)"><span>📋</span>Resas</button>
    <button class="mobile-nav-btn" onclick="switchPage('conversations',null);setMobileActive(this)"><span>💬</span>Chat</button>
    <button class="mobile-nav-btn" onclick="switchPage('reviews',null);setMobileActive(this)"><span>⭐</span>Avis</button>
    <button class="mobile-nav-btn" onclick="switchPage('contacts',null);setMobileActive(this)"><span>👥</span>CRM</button>
    <button class="mobile-nav-btn" onclick="switchPage('dashboard',null);setMobileActive(this)"><span>📊</span>Stats</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const BASE=window.location.origin,SECRET='{{SECRET_KEY}}',PWD='{{DASHBOARD_PASSWORD}}';
const COLORS=['#2563EB','#00D4AA','#8B5CF6','#F59E0B','#EF4444'];
const FLAGS={fr:'🇫🇷',en:'🇬🇧',it:'🇮🇹'};
const MIDI=['12:00','12:15','12:30','12:45','13:00','13:15','13:30','13:45','14:00','14:15'];
const SOIR=['19:00','19:15','19:30','19:45','20:00','20:15','20:30','20:45','21:00','21:15','21:30','21:45','22:00','22:15','22:30'];
let curService='midi',curSlot='12:30',fpData=null,allConvs=[],assignBookingId=null;

function doLogin(){if(document.getElementById('loginPwd').value===PWD){document.getElementById('loginOverlay').classList.add('hidden');document.getElementById('app').classList.remove('hidden');sessionStorage.setItem('rb_auth','1');loadAll();}else document.getElementById('loginError').style.display='block';}
if(sessionStorage.getItem('rb_auth')==='1'){document.getElementById('loginOverlay').classList.add('hidden');document.getElementById('app').classList.remove('hidden');}

const titles={floorplan:"Plan de salle",bookings:"Reservations",conversations:"Conversations",reviews:"Avis Google",contacts:"Contacts",dashboard:"Statistiques"};
function switchPage(id,btn){document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));document.getElementById('page-'+id).classList.add('active');document.querySelectorAll('.nav-item').forEach(b=>b.classList.remove('active'));if(btn)btn.classList.add('active');document.getElementById('pageTitle').textContent=titles[id]||id;if(id==='conversations')fetchConversations();if(id==='reviews')fetchReviews();if(id==='contacts')fetchContacts();}

function showToast(m){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',2500);}
function updateClock(){const n=new Date();document.getElementById('currentTime').textContent=n.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});document.getElementById('currentDate').textContent=n.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});}
setInterval(updateClock,1000);updateClock();

function switchService(svc,btn){curService=svc;curSlot=(svc==='midi'?MIDI:SOIR)[2];document.getElementById('svc-midi').style.background=svc==='midi'?'#0F1B2D':'transparent';document.getElementById('svc-midi').style.color=svc==='midi'?'white':'#94A3B8';document.getElementById('svc-soir').style.background=svc==='soir'?'#0F1B2D':'transparent';document.getElementById('svc-soir').style.color=svc==='soir'?'white':'#94A3B8';renderFloorplan();}

function renderSlotSelector(){
  const slots=curService==='midi'?MIDI:SOIR;
  const el=document.getElementById('slotSelector');
  const bookings=fpData?fpData.bookings:[];
  el.innerHTML=slots.map(s=>{
    const count=bookings.filter(b=>b.time===s).length;
    const active=s===curSlot;
    return '<button class="slot-btn'+(active?' active':'')+(count&&!active?' has-bookings':'')+'" onclick="curSlot=''+s+'';renderFloorplan()" style="'+(active?'background:#0F1B2D;color:white':'')+'">'+s+(count&&!active?'<span class="slot-badge">'+count+'</span>':'')+'</button>';
  }).join('');
}

function renderFloorplan(){
  if(!fpData)return;
  renderSlotSelector();
  const tables=fpData.tables||[];
  const slots=fpData.slots||{};
  const bookings=(fpData.bookings||[]).filter(b=>b.time===curSlot);
  const slotData=slots[curSlot]||{};
  const canvas=document.getElementById('floorplanCanvas');
  // Keep zone labels and dividers, clear tables
  const static_html='<div style="position:absolute;left:52%;top:0;bottom:0;width:1px;border-left:1px dashed #E2E8F0"></div><div style="position:absolute;left:82%;top:0;bottom:0;width:1px;border-left:1px dashed #E2E8F0"></div><div class="fp-zone-label" style="left:20%">SALLE</div><div class="fp-zone-label" style="left:63%">TERRASSE</div><div class="fp-zone-label" style="left:85%">BAR</div>';
  let tables_html='';
  tables.forEach(t=>{
    const status=slotData[t.id]||'available';
    const booking=bookings.find(b=>b.table===t.id);
    const w=t.shape==='round'?44:(t.seats<=4?52:64);
    const h=t.shape==='round'?44:(t.seats<=4?44:48);
    const br=t.shape==='round'?'50%':'10px';
    let cls='fp-table';
    if(booking)cls+=' occupied';
    else if(status==='blocked')cls+=' blocked';
    if(assignBookingId&&!booking&&status!=='blocked')cls+=' assign-target';
    const srcColor=booking?({whatsapp:'#25D366',zenchef:'#FF6B35',phone:'#94A3B8'}[booking.source]||'#94A3B8'):'#94A3B8';
    const nameColor=booking?srcColor:'#94A3B8';
    tables_html+='<div class="'+cls+'" style="left:'+t.x+'%;top:'+t.y+'%;width:'+w+'px;height:'+h+'px;border-radius:'+br+';'+(booking?'border-color:'+srcColor+'60;background:'+srcColor+'10':'')+'" onclick="onTableClick(''+t.id+'')">';
    tables_html+='<div class="fp-tid" style="color:'+nameColor+'">'+t.id+'</div>';
    if(booking)tables_html+='<div class="fp-tsub" style="color:'+srcColor+'">'+booking.name.split(' ')[0]+'</div>';
    else tables_html+='<div class="fp-tsub" style="color:#CBD5E1">'+t.seats+'p</div>';
    tables_html+='</div>';
  });
  canvas.innerHTML=static_html+tables_html;

  // Summary
  const totalT=tables.length;
  const occupiedT=bookings.filter(b=>b.table).length;
  const blockedT=Object.values(slotData).filter(s=>s==='blocked').length;
  const freeT=totalT-occupiedT-blockedT;
  const covers=bookings.reduce((a,b)=>a+(b.covers||2),0);
  const unassigned=bookings.filter(b=>!b.table).length;
  let sum='<div style="background:white;border-radius:10px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.04);flex:1;display:flex;align-items:center;gap:8px"><span style="font-size:20px">🍽️</span><div><div style="font-size:20px;font-weight:800;color:#0F1B2D">'+bookings.length+'</div><div style="font-size:10px;color:#94A3B8;font-weight:600">resas</div></div></div>';
  sum+='<div style="background:white;border-radius:10px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.04);flex:1;display:flex;align-items:center;gap:8px"><span style="font-size:20px">👥</span><div><div style="font-size:20px;font-weight:800;color:#0F1B2D">'+covers+'</div><div style="font-size:10px;color:#94A3B8;font-weight:600">couverts</div></div></div>';
  sum+='<div style="background:white;border-radius:10px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.04);flex:1;display:flex;align-items:center;gap:8px"><span style="font-size:20px">🪑</span><div><div style="font-size:20px;font-weight:800;color:#00D4AA">'+freeT+'</div><div style="font-size:10px;color:#94A3B8;font-weight:600">tables libres</div></div></div>';
  if(unassigned>0)sum+='<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:10px;padding:10px 16px;flex:1;display:flex;align-items:center;gap:8px"><span style="font-size:20px">⚠️</span><div><div style="font-size:20px;font-weight:800;color:#F59E0B">'+unassigned+'</div><div style="font-size:10px;color:#F59E0B;font-weight:600">sans table</div></div></div>';
  document.getElementById('fpSummary').innerHTML=sum;

  // Booking list
  document.getElementById('fpPanelTitle').textContent='Reservations — '+curSlot;
  document.getElementById('fpPanelSub').textContent=bookings.length+' resa(s) · '+covers+' couverts';
  const bl=document.getElementById('fpBookingList');
  if(!bookings.length){bl.innerHTML='<div class="empty-state"><span>🪑</span>Aucune resa a '+curSlot+'</div>';return;}
  bl.innerHTML=bookings.map(b=>{
    const srcC={whatsapp:'#25D366',zenchef:'#FF6B35',phone:'#94A3B8'}[b.source]||'#94A3B8';
    const srcL={whatsapp:'WhatsApp',zenchef:'Zenchef',phone:'Tel'}[b.source]||b.source;
    return '<div class="booking-card" id="bk-'+b.id+'"><div style="display:flex;justify-content:space-between;align-items:center"><div style="display:flex;align-items:center;gap:10px"><div class="src-dot" style="background:'+srcC+'"></div><div><div style="font-size:14px;font-weight:700;color:#0F1B2D">'+b.name+'</div><div style="font-size:11px;color:#94A3B8">'+(b.covers||2)+' pers. · '+b.time+' · <span style="color:'+srcC+'">'+srcL+'</span></div></div></div><div>'+(b.table?'<span style="font-size:11px;font-weight:700;color:#00D4AA;background:rgba(0,212,170,.1);padding:3px 8px;border-radius:6px">'+b.table+'</span>':'<span style="font-size:11px;font-weight:700;color:#F59E0B;background:rgba(245,158,11,.1);padding:3px 8px;border-radius:6px">Sans table</span>')+'</div></div>'+(b.notes?'<div style="font-size:11px;color:#64748B;margin-top:6px;font-style:italic">📝 '+b.notes+'</div>':'')+'<div style="margin-top:8px;display:flex;gap:6px">'+(b.table?'<button onclick="startAssign(''+b.id+'',true)" style="padding:5px 10px;border-radius:8px;border:1px solid #2563EB;background:white;color:#2563EB;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit">🔄 Changer</button><button onclick="releaseT(''+b.id+'')" style="padding:5px 10px;border-radius:8px;border:1px solid #E2E8F0;background:white;color:#64748B;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit">✕ Liberer</button>':'<button onclick="startAssign(''+b.id+'')" style="padding:5px 10px;border-radius:8px;border:none;background:#2563EB;color:white;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit">🎯 Assigner</button>')+'</div></div>';
  }).join('');
}

function onTableClick(tid){
  if(!assignBookingId)return;
  const b=(fpData.bookings||[]).find(x=>x.id===assignBookingId);
  if(!b)return;
  const t=(fpData.tables||[]).find(x=>x.id===tid);
  if(t&&b.covers>t.seats){showToast('⚠️ Table '+tid+' ('+t.seats+'p) trop petite pour '+b.covers+' couverts');return;}
  fetch(BASE+'/api/floorplan/assign?key='+SECRET,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:b.id,table_id:tid,slot_time:curSlot})}).then(()=>{assignBookingId=null;document.getElementById('assignBanner').classList.add('hidden');showToast('✅ '+b.name+' → '+tid);fetchFloorplan();});
}
function startAssign(bid,change){
  if(change){fetch(BASE+'/api/floorplan/release?key='+SECRET,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bid})}).then(()=>{assignBookingId=bid;document.getElementById('assignBanner').classList.remove('hidden');fetchFloorplan();});return;}
  assignBookingId=bid;document.getElementById('assignBanner').classList.remove('hidden');renderFloorplan();
}
function cancelAssign(){assignBookingId=null;document.getElementById('assignBanner').classList.add('hidden');renderFloorplan();}
function releaseT(bid){fetch(BASE+'/api/floorplan/release?key='+SECRET,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bid})}).then(()=>{showToast('Table liberee');fetchFloorplan();});}

async function fetchFloorplan(){
  try{const r=await fetch(BASE+'/api/floorplan?key='+SECRET);if(r.status===403)return;fpData=await r.json();
  const unassigned=(fpData.bookings||[]).filter(b=>b.time&&!b.table).length;
  document.getElementById('bookBadge').textContent=unassigned||'0';
  renderFloorplan();}catch(e){console.error(e);}
}

async function fetchDashboard(){
  try{const r=await fetch(BASE+'/api/dashboard?key='+SECRET);if(r.status===403)return;const d=await r.json();
  document.getElementById('msgCount').textContent=d.stats.messages_today||0;
  document.getElementById('bookCount').textContent=d.stats.bookings_today||0;
  document.getElementById('convCount').textContent=d.conversations_count||0;
  document.getElementById('timeSaved').textContent=Math.max(1,Math.round((d.stats.messages_today||0)*1.5/60))+'h';
  document.getElementById('convBadge').textContent=d.conversations_count||0;
  const st=d.status.status||'open';
  document.querySelectorAll('.ctrl-btn').forEach(b=>b.className='ctrl-btn');
  const a=document.getElementById('btn-'+st);if(a)a.classList.add('on');
  const langs=d.stats.languages||{};const total=Object.values(langs).reduce((a,b)=>a+b,0)||1;
  document.getElementById('langRow').innerHTML=Object.entries(langs).map(([l,c])=>'<div style="flex:1;background:#F8FAFC;border-radius:10px;padding:12px;text-align:center;border:1px solid #E2E8F0"><div style="font-size:20px;margin-bottom:4px">'+(FLAGS[l]||'🌍')+'</div><div style="font-size:18px;font-weight:800;color:#0F1B2D">'+Math.round(c/total*100)+'%</div></div>').join('');
  const w=d.stats.messages_week||[0,0,0,0,0,0,d.stats.messages_today||0];drawChart(w);
  }catch(e){console.error(e);}
}

function drawChart(data){const svg=document.getElementById('chartSvg');if(!data||!data.length)return;const max=Math.max(...data,1);const pts=data.map((v,i)=>({x:(i/(data.length-1))*100,y:100-(v/max)*80-5}));const line=pts.map((p,i)=>(i===0?'M':'L')+' '+p.x+' '+p.y).join(' ');svg.innerHTML='<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2563EB" stop-opacity="0.25"/><stop offset="100%" stop-color="#2563EB" stop-opacity="0.03"/></linearGradient></defs><path d="'+line+' L 100 100 L 0 100 Z" fill="url(#cg)"/><path d="'+line+'" fill="none" stroke="#2563EB" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'+pts.map(p=>'<circle cx="'+p.x+'" cy="'+p.y+'" r="4" fill="white" stroke="#2563EB" stroke-width="2.5" vector-effect="non-scaling-stroke"/>').join('');}

async function fetchConversations(){
  try{const r=await fetch(BASE+'/api/conversations?key='+SECRET);if(r.status===403)return;const d=await r.json();allConvs=d.conversations||[];
  const el=document.getElementById('convSidebar');
  if(!allConvs.length){el.innerHTML='<div class="empty-state"><span>💬</span>Aucune conversation</div>';return;}
  el.innerHTML=allConvs.map((c,i)=>'<div class="conv-list-item" onclick="openConv('+i+')" id="cv-'+i+'"><div class="conv-avatar" style="background:'+COLORS[i%5]+'15;color:'+COLORS[i%5]+'">'+(c.phone||'?')[0]+'</div><div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;color:#0F1B2D">'+c.phone+'</div><div style="font-size:12px;color:#94A3B8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+c.last_message+'</div></div><div style="font-size:11px;color:#94A3B8;font-family:monospace">'+c.last_time+'</div></div>').join('');
  }catch(e){console.error(e);}
}
function openConv(i){const c=allConvs[i];if(!c)return;document.querySelectorAll('.conv-list-item').forEach(e=>e.classList.remove('selected'));document.getElementById('cv-'+i).classList.add('selected');document.getElementById('chatHeader').textContent='📱 '+c.phone+' — '+c.count+' messages';const body=document.getElementById('chatBody');body.innerHTML=c.messages.map(m=>'<div style="display:flex;flex-direction:column;align-items:'+(m.role==='user'?'flex-end':'flex-start')+'"><div class="bubble '+(m.role==='user'?'bubble-user':'bubble-bot')+'">'+m.content+'</div><div style="font-size:10px;color:#94A3B8;margin-bottom:6px">'+m.time+'</div></div>').join('');body.scrollTop=body.scrollHeight;}

async function fetchReviews(){
  try{const r=await fetch(BASE+'/api/reviews?key='+SECRET);if(r.status===403)return;const d=await r.json();
  document.getElementById('revPositive').textContent=d.stats.positive||0;
  document.getElementById('revNegative').textContent=d.stats.negative||0;
  document.getElementById('revPending').textContent=(d.stats.total||0)-(d.stats.responded||0);
  document.getElementById('reviewBadge').textContent=d.stats.positive||0;
  const el=document.getElementById('reviewList');
  const q=d.queue||[];
  if(!q.length){el.innerHTML='<div class="empty-state"><span>⭐</span>Aucune relance en file</div>';return;}
  el.innerHTML=q.map(r=>{
    let statusBg='rgba(245,158,11,.1)',statusColor='#F59E0B',statusText='En attente';
    if(r.responded&&r.sentiment==='POSITIVE'){statusBg='rgba(0,212,170,.1)';statusColor='#00D4AA';statusText='✅ Positif';}
    else if(r.responded&&r.sentiment==='NEGATIVE'){statusBg='rgba(239,68,68,.1)';statusColor='#EF4444';statusText='⚠️ Negatif';}
    else if(r.sent){statusText='📩 Envoye';}
    return '<div style="display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid #F1F5F9"><div class="conv-avatar" style="background:'+statusBg+';color:'+statusColor+'">⭐</div><div style="flex:1"><div style="font-size:14px;font-weight:600;color:#0F1B2D">'+r.name+'</div><div style="font-size:11px;color:#94A3B8">'+r.phone+(r.booking_time?' · '+r.booking_time:'')+'</div>'+(r.response?'<div style="font-size:11px;color:#64748B;margin-top:4px;font-style:italic">"'+r.response+'"</div>':'')+'</div><div style="text-align:right"><span style="font-size:11px;font-weight:700;padding:3px 8px;border-radius:6px;background:'+statusBg+';color:'+statusColor+'">'+statusText+'</span></div></div>';
  }).join('');
  }catch(e){console.error(e);}
}

async function fetchAllBookings(){
  try{const r=await fetch(BASE+'/api/bookings?key='+SECRET);if(r.status===403)return;const d=await r.json();
  const el=document.getElementById('allBookingsList');
  const bs=d.bookings||[];
  if(!bs.length){el.innerHTML='<div class="empty-state"><span>🍽️</span>Aucune reservation</div>';return;}
  el.innerHTML=bs.map(b=>{
    const srcC={whatsapp:'#25D366',zenchef:'#FF6B35',phone:'#94A3B8'}[b.source]||'#94A3B8';
    return '<div style="display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid #F1F5F9"><div class="src-dot" style="background:'+srcC+'"></div><div style="flex:1"><div style="font-size:14px;font-weight:600;color:#0F1B2D">'+b.name+'</div><div style="font-size:11px;color:#94A3B8">'+(b.covers||'?')+' pers. · '+(b.time||'?')+' · '+b.phone+'</div>'+(b.message?'<div style="font-size:11px;color:#64748B;margin-top:2px">'+b.message.substring(0,80)+'</div>':'')+'</div><div style="text-align:right">'+(b.table?'<div style="font-size:11px;font-weight:700;color:#00D4AA;background:rgba(0,212,170,.1);padding:3px 8px;border-radius:6px">'+b.table+'</div>':'<div style="font-size:11px;font-weight:700;color:#F59E0B;background:rgba(245,158,11,.1);padding:3px 8px;border-radius:6px">En attente</div>')+'<div style="font-size:10px;color:#94A3B8;margin-top:4px;font-family:monospace">'+(b.timestamp||'').substring(0,16).replace('T',' ')+'</div></div></div>';
  }).join('');
  }catch(e){console.error(e);}
}

async function setStatus(s){await fetch(BASE+'/api/status?key='+SECRET,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})});document.querySelectorAll('.ctrl-btn').forEach(b=>b.className='ctrl-btn');const a=document.getElementById('btn-'+s);if(a)a.classList.add('on');showToast('✅ Statut mis a jour');}



async function fetchContacts(){
  try{const r=await fetch(BASE+'/api/contacts?key='+SECRET);if(r.status===403)return;const d=await r.json();
  const cs=d.contacts||[];
  document.getElementById('crmTotal').textContent=d.total||0;
  document.getElementById('contactBadge').textContent=d.total||0;
  const now=new Date();const weekAgo=new Date(now-7*86400000).toISOString();
  document.getElementById('crmWeek').textContent=cs.filter(c=>c.first_seen>weekAgo).length;
  document.getElementById('crmLoyal').textContent=cs.filter(c=>(c.visits||0)>=2).length;
  const el=document.getElementById('contactsList');
  if(!cs.length){el.innerHTML='<div class="empty-state"><span>👥</span>Aucun contact enregistre</div>';return;}
  el.innerHTML=cs.map((c,i)=>{
    const tags=(c.tags||[]).map(t=>'<span style="display:inline-block;background:rgba(139,92,246,.1);color:#8B5CF6;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-right:4px">'+t+'</span>').join('');
    return '<div style="display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid #F1F5F9"><div class="conv-avatar" style="background:'+COLORS[i%5]+'15;color:'+COLORS[i%5]+'">'+(c.name||'?')[0].toUpperCase()+'</div><div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:8px"><span style="font-size:14px;font-weight:600;color:#0F1B2D">'+c.name+'</span>'+tags+'</div><div style="font-size:11px;color:#94A3B8">'+c.phone+' · '+(c.visits||1)+' visite(s) · '+({fr:"🇫🇷",en:"🇬🇧",it:"🇮🇹"}[c.language]||"🌍")+' '+(c.language||"")+'</div></div><div style="text-align:right"><div style="font-size:10px;color:#94A3B8;font-family:monospace">'+(c.last_seen||"").substring(0,10)+'</div></div></div>';
  }).join('');
  }catch(e){console.error(e);}
}

function setMobileActive(btn){document.querySelectorAll('.mobile-nav-btn').forEach(b=>b.classList.remove('active'));if(btn)btn.classList.add('active');}
function loadAll(){fetchFloorplan();fetchDashboard();fetchAllBookings();fetchConversations();fetchReviews();fetchContacts();}
if(sessionStorage.getItem('rb_auth')==='1')loadAll();
setInterval(()=>{if(sessionStorage.getItem('rb_auth')==='1'){fetchFloorplan();fetchDashboard();}},15000);
</script>
</body>
</html>
"""


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
    return {"status": "ok"}


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

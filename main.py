"""
RestoBot — Agent IA WhatsApp pour la Restauration
Version 2.0 — Commandes restaurateur + Dashboard
"""

import os
import json
import logging
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("restobot")

# ==============================================================
# IN-MEMORY DATABASE
# ==============================================================

restaurants = {}
conversations = {}
bookings = []

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


# ==============================================================
# NOTIFICATION
# ==============================================================

async def notify_owner(restaurant: dict, customer_phone: str, customer_name: str, message: str):
    if not restaurant.get("owner_phone"):
        return
    booking_keywords = ["réserv", "reserv", "book", "table", "prenot"]
    is_booking = any(kw in message.lower() for kw in booking_keywords)
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
        track_stats(restaurant["phone_number_id"], is_booking=True)


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
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0A1628; color: #fff; min-height: 100vh; }
        .header { background: #1A2744; padding: 20px 30px; display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid #C9A55C; }
        .header h1 { font-size: 24px; color: #C9A55C; }
        .header .status-badge { padding: 6px 16px; border-radius: 20px; font-size: 13px; font-weight: 600; }
        .status-open { background: #34C75920; color: #34C759; border: 1px solid #34C759; }
        .status-full { background: #FF6B6B20; color: #FF6B6B; border: 1px solid #FF6B6B; }
        .status-closed { background: #FFD60A20; color: #FFD60A; border: 1px solid #FFD60A; }
        .container { max-width: 1200px; margin: 0 auto; padding: 30px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: #1A2744; border-radius: 12px; padding: 24px; }
        .card-label { color: #8899AA; font-size: 13px; margin-bottom: 8px; }
        .card-value { font-size: 36px; font-weight: 700; color: #C9A55C; }
        .card-sub { color: #8899AA; font-size: 12px; margin-top: 4px; }
        .section { background: #1A2744; border-radius: 12px; padding: 24px; margin-bottom: 20px; }
        .section h2 { color: #C9A55C; font-size: 18px; margin-bottom: 16px; }
        .btn-group { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px; }
        .btn { padding: 10px 20px; border-radius: 8px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .btn-danger { background: #FF6B6B; color: white; }
        .btn-warning { background: #FFD60A; color: #0A1628; }
        .btn-success { background: #34C759; color: white; }
        .btn-primary { background: #4A90D9; color: white; }
        .btn:hover { transform: translateY(-1px); opacity: 0.9; }
        input[type="text"], input[type="date"] { background: #0D1E38; border: 1px solid #2A3A55; border-radius: 8px; padding: 10px 15px; color: white; font-size: 14px; width: 100%; margin-bottom: 10px; }
        input::placeholder { color: #556677; }
        .conversations { max-height: 400px; overflow-y: auto; }
        .conv-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid #0D1E38; }
        .conv-name { font-weight: 600; }
        .conv-msg { color: #8899AA; font-size: 13px; margin-top: 4px; max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .conv-time { color: #556677; font-size: 12px; }
        .toast { position: fixed; bottom: 30px; right: 30px; background: #34C759; color: white; padding: 15px 25px; border-radius: 10px; font-weight: 600; display: none; z-index: 100; }
        .lang-bar { display: flex; gap: 4px; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 10px; }
        .lang-bar div { height: 100%; border-radius: 4px; }
        @media (max-width: 768px) { .container { padding: 15px; } .grid { grid-template-columns: 1fr 1fr; } }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 RestoBot Dashboard</h1>
        <div id="statusBadge" class="status-badge status-open">🟢 Ouvert</div>
    </div>
    <div class="container">
        <div class="grid">
            <div class="card">
                <div class="card-label">Messages aujourd'hui</div>
                <div class="card-value" id="msgCount">0</div>
                <div class="card-sub">traités automatiquement</div>
            </div>
            <div class="card">
                <div class="card-label">Réservations</div>
                <div class="card-value" id="bookCount">0</div>
                <div class="card-sub">prises aujourd'hui</div>
            </div>
            <div class="card">
                <div class="card-label">Conversations actives</div>
                <div class="card-value" id="convCount">0</div>
                <div class="card-sub">clients uniques</div>
            </div>
            <div class="card">
                <div class="card-label">Langues détectées</div>
                <div id="langStats" class="card-value" style="font-size: 16px; margin-top: 10px;"></div>
                <div class="lang-bar" id="langBar"></div>
            </div>
        </div>

        <div class="section">
            <h2>⚡ Contrôle rapide</h2>
            <div class="btn-group">
                <button class="btn btn-danger" onclick="setStatus('full_tonight')">🔴 Complet ce soir</button>
                <button class="btn btn-danger" onclick="setStatus('full_lunch')">🔴 Complet ce midi</button>
                <button class="btn btn-warning" onclick="setStatus('closed_today')">🟡 Fermé aujourd'hui</button>
                <button class="btn btn-success" onclick="setStatus('open')">🟢 Ouvert</button>
            </div>
            <input type="text" id="tempMessage" placeholder="Message temporaire pour les clients (ex: Menu truffe ce soir !)">
            <div class="btn-group">
                <button class="btn btn-primary" onclick="setMessage()">💬 Activer le message</button>
                <button class="btn btn-warning" onclick="clearMessage()">Supprimer le message</button>
            </div>
        </div>

        <div class="section">
            <h2>📅 Fermetures & complet</h2>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                <input type="date" id="closedDate" style="width: auto;">
                <button class="btn btn-warning" onclick="addClosed()">Ajouter fermeture</button>
                <button class="btn btn-danger" onclick="addFull()">Marquer complet</button>
            </div>
            <div id="closedList" style="margin-top: 15px; color: #8899AA;"></div>
        </div>

        <div class="section">
            <h2>💬 Dernières conversations</h2>
            <div class="conversations" id="convList"></div>
        </div>
    </div>

    <div class="toast" id="toast">✅ Mis à jour !</div>

    <script>
        const BASE = window.location.origin;

        function showToast(msg) {
            const t = document.getElementById('toast');
            t.textContent = msg || '✅ Mis à jour !';
            t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 2500);
        }

        async function fetchData() {
            try {
                const r = await fetch(BASE + '/api/dashboard');
                const data = await r.json();
                document.getElementById('msgCount').textContent = data.stats.messages_today || 0;
                document.getElementById('bookCount').textContent = data.stats.bookings_today || 0;
                document.getElementById('convCount').textContent = data.conversations_count || 0;

                // Status badge
                const badge = document.getElementById('statusBadge');
                const statusMap = {
                    'open': ['🟢 Ouvert', 'status-open'],
                    'full_tonight': ['🔴 Complet ce soir', 'status-full'],
                    'full_lunch': ['🔴 Complet ce midi', 'status-full'],
                    'closed_today': ['🟡 Fermé aujourd\'hui', 'status-closed'],
                };
                const s = statusMap[data.status.status] || statusMap['open'];
                badge.textContent = s[0];
                badge.className = 'status-badge ' + s[1];

                // Languages
                const langs = data.stats.languages || {};
                const langEl = document.getElementById('langStats');
                langEl.innerHTML = Object.entries(langs).map(([l, c]) => `${l}: ${c}`).join(' · ') || 'Aucune donnée';

                // Lang bar
                const total = Object.values(langs).reduce((a, b) => a + b, 0) || 1;
                const colors = { fr: '#4A90D9', en: '#34C759', it: '#FF6B6B' };
                const bar = document.getElementById('langBar');
                bar.innerHTML = Object.entries(langs).map(([l, c]) =>
                    `<div style="width:${(c/total)*100}%; background:${colors[l] || '#C9A55C'}"></div>`
                ).join('');

                // Conversations
                const convList = document.getElementById('convList');
                convList.innerHTML = (data.recent_conversations || []).map(c =>
                    `<div class="conv-item">
                        <div><div class="conv-name">${c.phone}</div><div class="conv-msg">${c.last_message}</div></div>
                        <div class="conv-time">${c.time}</div>
                    </div>`
                ).join('') || '<div style="color:#556677;padding:20px;">Aucune conversation pour le moment</div>';

                // Closed dates
                const closedList = document.getElementById('closedList');
                const closedDates = data.status.closed_dates || [];
                const fullDates = data.status.full_dates || {};
                let html = '';
                closedDates.forEach(d => html += `<span style="display:inline-block;background:#FFD60A20;color:#FFD60A;padding:4px 12px;border-radius:6px;margin:3px;font-size:13px;">🟡 Fermé ${d}</span>`);
                Object.entries(fullDates).forEach(([d, p]) => html += `<span style="display:inline-block;background:#FF6B6B20;color:#FF6B6B;padding:4px 12px;border-radius:6px;margin:3px;font-size:13px;">🔴 Complet ${d} (${p})</span>`);
                closedList.innerHTML = html || 'Aucune fermeture prévue';

                if (data.status.temp_message) {
                    document.getElementById('tempMessage').value = data.status.temp_message;
                }
            } catch(e) { console.error(e); }
        }

        async function setStatus(status) {
            await fetch(BASE + '/api/status', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({status})
            });
            showToast();
            fetchData();
        }

        async function setMessage() {
            const msg = document.getElementById('tempMessage').value;
            if (!msg) return;
            await fetch(BASE + '/api/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message: msg})
            });
            showToast('💬 Message activé !');
            fetchData();
        }

        async function clearMessage() {
            await fetch(BASE + '/api/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message: ''})
            });
            document.getElementById('tempMessage').value = '';
            showToast('💬 Message supprimé');
            fetchData();
        }

        async function addClosed() {
            const d = document.getElementById('closedDate').value;
            if (!d) return;
            await fetch(BASE + '/api/closed', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({date: d, type: 'closed'})
            });
            showToast('📅 Fermeture ajoutée');
            fetchData();
        }

        async function addFull() {
            const d = document.getElementById('closedDate').value;
            if (!d) return;
            await fetch(BASE + '/api/closed', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({date: d, type: 'full'})
            });
            showToast('📅 Complet ajouté');
            fetchData();
        }

        fetchData();
        setInterval(fetchData, 15000);
    </script>
</body>
</html>"""


# ==============================================================
# FASTAPI APP
# ==============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_sample_restaurant()
    logger.info("🚀 RestoBot v2.0 démarré")
    yield
    logger.info("👋 RestoBot arrêté")


app = FastAPI(title="RestoBot", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "RestoBot",
        "status": "running",
        "version": "2.0",
        "restaurants": len(restaurants),
        "conversations": len(conversations),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- WhatsApp Webhook ---

@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("✅ Webhook vérifié")
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook/whatsapp")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    parsed = parse_webhook(body)
    if not parsed:
        return {"status": "ok"}

    logger.info(f"📩 Message de {parsed['name'] or parsed['from']}: {parsed['text'][:100]}")

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

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/api/dashboard")
async def dashboard_data():
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}

    st = stats.get(pid, {})
    status = restaurant_status.get(pid, {})

    # Recent conversations
    recent = []
    for key, msgs in sorted(conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True)[:20]:
        if not msgs:
            continue
        phone = key.split(":")[1] if ":" in key else key
        last = msgs[-1]
        recent.append({
            "phone": phone,
            "last_message": last["content"][:100],
            "time": last.get("timestamp", "")[:16].replace("T", " "),
        })

    return {
        "stats": st,
        "status": status,
        "conversations_count": sum(1 for k in conversations if k.startswith(pid)),
        "recent_conversations": recent,
    }


@app.post("/api/status")
async def update_status(request: Request):
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.get(pid, {})
    status["status"] = data.get("status", "open")
    status["updated_at"] = datetime.utcnow().isoformat()
    if data.get("status") == "closed_today":
        status["closed_dates"].append(date.today().isoformat())
    return {"status": "updated"}


@app.post("/api/message")
async def update_message(request: Request):
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.get(pid, {})
    status["temp_message"] = data.get("message", "")
    status["updated_at"] = datetime.utcnow().isoformat()
    return {"status": "updated"}


@app.post("/api/closed")
async def add_closed_date(request: Request):
    data = await request.json()
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"error": "No restaurant"}
    status = restaurant_status.get(pid, {})
    d = data.get("date", "")
    if data.get("type") == "full":
        status["full_dates"][d] = "journée"
    else:
        if d not in status.get("closed_dates", []):
            status["closed_dates"].append(d)
    status["updated_at"] = datetime.utcnow().isoformat()
    return {"status": "updated"}


@app.get("/api/restaurants")
async def list_restaurants():
    return [{"name": r["name"], "phone_number_id": pid} for pid, r in restaurants.items()]


@app.get("/api/conversations")
async def list_conversations():
    return {
        key: {"messages": len(msgs), "last": msgs[-1] if msgs else None}
        for key, msgs in conversations.items()
    }


# ==============================================================
# RUN
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

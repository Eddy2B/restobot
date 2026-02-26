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
        bookings.append({
            "phone": customer_phone,
            "name": customer_name or customer_phone,
            "message": message[:200],
            "timestamp": datetime.utcnow().isoformat(),
            "status": "pending",
        })
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
<title>RestoBot — Dashboard</title>
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
.sidebar-brand-icon{width:34px;height:34px;border-radius:10px;background:#00D4AA;display:flex;align-items:center;justify-content:center;font-size:18px}
.nav-item{width:100%;display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;border:none;cursor:pointer;margin-bottom:2px;background:transparent;color:#94A3B8;font-size:13px;font-weight:500;text-align:left;font-family:inherit;transition:all .15s}
.nav-item:hover{background:rgba(255,255,255,.05)}
.nav-item.active{background:rgba(0,212,170,.12);color:#00D4AA;font-weight:600}
.nav-badge{margin-left:auto;background:#EF4444;color:white;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px}
.sidebar-footer{padding:16px 20px;border-top:1px solid #1E293B;display:flex;align-items:center;gap:10px}
.main{flex:1;margin-left:220px}

.topbar{background:white;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #E2E8F0;position:sticky;top:0;z-index:30}
.topbar h1{font-size:18px;font-weight:800;color:#0F1B2D}
.status-pill{display:inline-flex;align-items:center;gap:6px;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700}
.status-open{background:rgba(0,212,170,.1);color:#00D4AA}
.status-full{background:rgba(239,68,68,.1);color:#EF4444}
.status-closed{background:rgba(245,158,11,.1);color:#F59E0B}

.content{padding:24px 32px}
.page{display:none}.page.active{display:block}
.grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.grid-chart{display:grid;grid-template-columns:1fr 300px;gap:16px;margin-bottom:24px}
.card{background:white;border-radius:14px;padding:22px 20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.stat-card{border-top:3px solid #2563EB}
.stat-card:nth-child(2){border-top-color:#00D4AA}
.stat-card:nth-child(3){border-top-color:#8B5CF6}
.stat-card:nth-child(4){border-top-color:#F59E0B}
.stat-label{color:#94A3B8;font-size:11px;font-weight:700;letter-spacing:.06em;margin-bottom:10px}
.stat-value{font-size:36px;font-weight:800;color:#0F1B2D;line-height:1}
.stat-trend{font-size:12px;font-weight:600;margin-top:6px}
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
.lang-row{display:flex;gap:8px;margin-top:8px}
.lang-box{flex:1;background:#F8FAFC;border-radius:10px;padding:12px;text-align:center;border:1px solid #E2E8F0}

/* Conversations page */
.conv-list-item{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid #F1F5F9;cursor:pointer;transition:background .15s}
.conv-list-item:hover{background:#F8FAFC}
.conv-list-item.selected{background:rgba(0,212,170,.06);border-left:3px solid #00D4AA}
.conv-avatar{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;flex-shrink:0}
.conv-detail{flex:1;min-width:0}
.conv-name{font-size:13px;font-weight:600;color:#0F1B2D}
.conv-preview{font-size:12px;color:#94A3B8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.conv-time{font-size:11px;color:#94A3B8;font-family:monospace;flex-shrink:0}
.conv-panel{display:grid;grid-template-columns:360px 1fr;gap:0;height:calc(100vh - 130px)}
.conv-sidebar{border-right:1px solid #E2E8F0;overflow-y:auto}
.conv-chat{display:flex;flex-direction:column;height:100%}
.conv-chat-header{padding:16px 20px;border-bottom:1px solid #E2E8F0;font-weight:700;font-size:15px;color:#0F1B2D}
.conv-chat-body{flex:1;overflow-y:auto;padding:20px}
.bubble{max-width:75%;padding:10px 14px;border-radius:14px;font-size:13px;line-height:1.5;margin-bottom:8px;word-wrap:break-word}
.bubble-user{background:#E8F5E9;color:#1B5E20;margin-left:auto;border-bottom-right-radius:4px}
.bubble-bot{background:#F1F5F9;color:#0F1B2D;margin-right:auto;border-bottom-left-radius:4px}
.bubble-time{font-size:10px;color:#94A3B8;margin-top:2px}
.bubble-wrap{display:flex;flex-direction:column}
.bubble-wrap.user{align-items:flex-end}
.bubble-wrap.bot{align-items:flex-start}

/* Bookings page */
.booking-row{display:flex;align-items:center;gap:16px;padding:14px 16px;border-bottom:1px solid #F1F5F9}
.booking-status{padding:4px 10px;border-radius:8px;font-size:11px;font-weight:700}
.booking-pending{background:rgba(245,158,11,.1);color:#F59E0B}
.booking-confirmed{background:rgba(0,212,170,.1);color:#00D4AA}

.empty-state{text-align:center;padding:60px 20px;color:#94A3B8}
.empty-state span{font-size:48px;display:block;margin-bottom:12px}

.hidden{display:none!important}
.chart-labels{display:flex;justify-content:space-between;margin-top:8px}
.chart-labels span{font-size:11px;color:#94A3B8;font-weight:500}
.toast{position:fixed;bottom:24px;right:24px;background:#00D4AA;color:white;padding:12px 24px;border-radius:12px;font-weight:700;font-size:14px;box-shadow:0 8px 24px rgba(0,212,170,.4);animation:slideUp .3s ease;z-index:100;display:none}
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
    <div class="sidebar-brand-icon">🤖</div>
    <div><div style="color:white;font-size:15px;font-weight:800">RestoBot</div><div style="color:#64748B;font-size:10px">Le Cosi Nice</div></div>
  </div>
  <div style="padding:0 12px;flex:1">
    <div style="color:#475569;font-size:10px;font-weight:700;letter-spacing:.08em;padding:0 8px;margin-bottom:8px">PRINCIPAL</div>
    <button class="nav-item active" onclick="switchPage('dashboard',this)">📊 Vue d'ensemble</button>
    <button class="nav-item" onclick="switchPage('conversations',this)">💬 Conversations <span class="nav-badge" id="convBadge">0</span></button>
    <button class="nav-item" onclick="switchPage('bookings',this)">🍽️ Réservations <span class="nav-badge" id="bookBadge" style="background:#F59E0B">0</span></button>
    <button class="nav-item" onclick="switchPage('settings',this)">⚙️ Paramètres</button>
  </div>
  <div class="sidebar-footer">
    <div style="width:32px;height:32px;border-radius:50%;background:#00D4AA;display:flex;align-items:center;justify-content:center;color:white;font-size:13px;font-weight:700">EC</div>
    <div><div style="color:white;font-size:12px;font-weight:600">Édouard F.</div><div style="color:#64748B;font-size:10px">Propriétaire</div></div>
  </div>
</div>

<div class="main">
  <div class="topbar">
    <div><h1 id="pageTitle">Vue d'ensemble</h1><span style="font-size:12px;color:#94A3B8" id="currentDate"></span></div>
    <div style="display:flex;align-items:center;gap:16px">
      <div class="status-pill status-open" id="statusPill"><div style="width:7px;height:7px;border-radius:50%;background:#00D4AA" id="statusDot"></div> <span id="statusLabel">En ligne</span></div>
      <span style="font-size:13px;color:#94A3B8;font-family:monospace" id="currentTime"></span>
    </div>
  </div>

  <div class="content">

    <!-- PAGE: DASHBOARD -->
    <div class="page active" id="page-dashboard">
      <div class="grid-4">
        <div class="card stat-card"><div class="stat-label">MESSAGES TRAITÉS</div><div class="stat-value" id="msgCount">0</div><div class="stat-trend" style="color:#00D4AA">→ aujourd'hui</div></div>
        <div class="card stat-card"><div class="stat-label">RÉSERVATIONS</div><div class="stat-value" id="bookCount">0</div><div class="stat-trend" style="color:#00D4AA">→ aujourd'hui</div></div>
        <div class="card stat-card"><div class="stat-label">CONVERSATIONS</div><div class="stat-value" id="convCount">0</div><div class="stat-trend" style="color:#94A3B8">→ clients actifs</div></div>
        <div class="card stat-card"><div class="stat-label">TEMPS ÉCONOMISÉ</div><div class="stat-value" id="timeSaved">0h</div><div class="stat-trend" style="color:#00D4AA">vs gestion manuelle</div></div>
      </div>
      <div class="grid-chart">
        <div class="card" style="padding:24px">
          <div class="card-header"><div><div class="card-title">Activité — 7 derniers jours</div><div class="card-sub">Messages traités</div></div></div>
          <div style="position:relative;height:180px"><svg id="chartSvg" viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:100%;overflow:visible"></svg></div>
          <div class="chart-labels"><span>Lun</span><span>Mar</span><span>Mer</span><span>Jeu</span><span>Ven</span><span>Sam</span><span>Dim</span></div>
        </div>
        <div class="card" style="padding:24px">
          <div class="card-title">Contrôle rapide</div>
          <div class="card-sub" style="margin-bottom:16px">Statut du restaurant</div>
          <div class="ctrl-grid">
            <button class="ctrl-btn on" id="btn-open" onclick="setStatus('open')">🟢 Ouvert</button>
            <button class="ctrl-btn" id="btn-full_tonight" onclick="setStatus('full_tonight')">🔴 Complet ce soir</button>
            <button class="ctrl-btn" id="btn-full_lunch" onclick="setStatus('full_lunch')">🟠 Complet ce midi</button>
            <button class="ctrl-btn" id="btn-closed_today" onclick="setStatus('closed_today')">⛔ Fermé</button>
          </div>
          <div class="stat-label">MESSAGE TEMPORAIRE</div>
          <div class="msg-row">
            <input class="msg-input" id="tempMessage" type="text" placeholder="Ex: Menu truffe ce soir !">
            <button class="msg-btn" onclick="sendMessage()">OK</button>
          </div>
          <div class="msg-active hidden" id="activeMsg"><span id="activeMsgText"></span><button style="background:none;border:none;color:#EF4444;cursor:pointer;font-size:16px" onclick="clearMessage()">✕</button></div>
          <div class="stat-label" style="margin-top:16px">LANGUES</div>
          <div class="lang-row" id="langRow"></div>
        </div>
      </div>
    </div>

    <!-- PAGE: CONVERSATIONS -->
    <div class="page" id="page-conversations">
      <div class="card" style="padding:0;overflow:hidden">
        <div class="conv-panel">
          <div class="conv-sidebar" id="convSidebar"></div>
          <div class="conv-chat">
            <div class="conv-chat-header" id="chatHeader">Sélectionnez une conversation</div>
            <div class="conv-chat-body" id="chatBody">
              <div class="empty-state"><span>💬</span>Cliquez sur une conversation pour voir les messages</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- PAGE: BOOKINGS -->
    <div class="page" id="page-bookings">
      <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:20px 24px;border-bottom:1px solid #E2E8F0;display:flex;justify-content:space-between;align-items:center">
          <div><div class="card-title">Demandes de réservation</div><div class="card-sub">Reçues via WhatsApp</div></div>
        </div>
        <div id="bookingsList"></div>
      </div>
    </div>

    <!-- PAGE: SETTINGS -->
    <div class="page" id="page-settings">
      <div class="card" style="padding:24px;max-width:600px">
        <div class="card-title" style="margin-bottom:20px">Paramètres du restaurant</div>
        <div class="stat-label">NOM DU RESTAURANT</div>
        <input class="msg-input" type="text" value="Le Cosi Nice" disabled style="margin-bottom:16px;background:#F1F5F9;color:#64748B">
        <div class="stat-label">MOT DE PASSE DASHBOARD</div>
        <input class="msg-input" type="text" value="restobot2026" disabled style="margin-bottom:16px;background:#F1F5F9;color:#64748B">
        <div class="stat-label">NUMÉRO WHATSAPP</div>
        <input class="msg-input" type="text" value="+1 555 156 0350" disabled style="margin-bottom:16px;background:#F1F5F9;color:#64748B">
        <div style="padding:16px;background:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-top:8px">
          <p style="font-size:13px;color:#64748B;margin:0">Pour modifier les informations du restaurant (menu, horaires, ton), contactez votre gestionnaire RestoBot.</p>
        </div>
      </div>
    </div>

  </div>
</div>
</div>

<div class="toast" id="toast"></div>

<script>
const BASE=window.location.origin,SECRET='{{SECRET_KEY}}',PWD='{{DASHBOARD_PASSWORD}}';
const COLORS=['#2563EB','#00D4AA','#8B5CF6','#F59E0B','#EF4444'];
const FLAGS={fr:'🇫🇷',en:'🇬🇧',it:'🇮🇹',de:'🇩🇪',es:'🇪🇸'};
let allConversations=[], allBookings=[];

function doLogin(){
  if(document.getElementById('loginPwd').value===PWD){
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    sessionStorage.setItem('rb_auth','1');fetchData();fetchConversations();fetchBookings();
  } else document.getElementById('loginError').style.display='block';
}
if(sessionStorage.getItem('rb_auth')==='1'){
  document.getElementById('loginOverlay').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
}

const titles={dashboard:"Vue d'ensemble",conversations:"Conversations",bookings:"Réservations",settings:"Paramètres"};
function switchPage(id,btn){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.remove('active'));
  if(btn)btn.classList.add('active');
  document.getElementById('pageTitle').textContent=titles[id]||id;
  if(id==='conversations')fetchConversations();
  if(id==='bookings')fetchBookings();
}

function showToast(m){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',2500);}
function updateClock(){const n=new Date();document.getElementById('currentTime').textContent=n.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});document.getElementById('currentDate').textContent=n.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});}
setInterval(updateClock,1000);updateClock();

function updateStatusUI(s){
  const map={open:['En ligne','status-open','#00D4AA'],full_tonight:['Complet ce soir','status-full','#EF4444'],full_lunch:['Complet ce midi','status-full','#F59E0B'],closed_today:['Fermé','status-closed','#F59E0B']};
  const v=map[s]||map.open;
  document.getElementById('statusPill').className='status-pill '+v[1];
  document.getElementById('statusLabel').textContent=v[0];
  document.getElementById('statusDot').style.background=v[2];
  document.querySelectorAll('.ctrl-btn').forEach(b=>b.className='ctrl-btn');
  const a=document.getElementById('btn-'+s);if(a)a.classList.add('on');
}

function drawChart(data){
  const svg=document.getElementById('chartSvg');if(!data||!data.length)return;
  const max=Math.max(...data,1);
  const pts=data.map((v,i)=>({x:(i/(data.length-1))*100,y:100-(v/max)*80-5}));
  const line=pts.map((p,i)=>(i===0?'M':'L')+' '+p.x+' '+p.y).join(' ');
  svg.innerHTML='<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2563EB" stop-opacity="0.25"/><stop offset="100%" stop-color="#2563EB" stop-opacity="0.03"/></linearGradient></defs><path d="'+line+' L 100 100 L 0 100 Z" fill="url(#cg)"/><path d="'+line+'" fill="none" stroke="#2563EB" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'+pts.map(p=>'<circle cx="'+p.x+'" cy="'+p.y+'" r="4" fill="white" stroke="#2563EB" stroke-width="2.5" vector-effect="non-scaling-stroke"/>').join('');
}

function renderLangs(langs){
  const t=Object.values(langs).reduce((a,b)=>a+b,0)||1;
  document.getElementById('langRow').innerHTML=Object.entries(langs).map(([l,c])=>'<div class="lang-box"><div style="font-size:20px;margin-bottom:4px">'+(FLAGS[l]||'🌍')+'</div><div style="font-size:18px;font-weight:800;color:#0F1B2D">'+Math.round(c/t*100)+'%</div><div style="font-size:10px;color:#94A3B8;font-weight:600">'+c+' msgs</div></div>').join('');
}

async function fetchData(){
  try{
    const r=await fetch(BASE+'/api/dashboard?key='+SECRET);if(r.status===403)return;
    const d=await r.json();
    document.getElementById('msgCount').textContent=d.stats.messages_today||0;
    document.getElementById('bookCount').textContent=d.stats.bookings_today||0;
    document.getElementById('convCount').textContent=d.conversations_count||0;
    document.getElementById('timeSaved').textContent=Math.max(1,Math.round((d.stats.messages_today||0)*1.5/60))+'h';
    document.getElementById('convBadge').textContent=d.conversations_count||0;
    updateStatusUI(d.status.status||'open');
    if(d.status.temp_message){document.getElementById('activeMsg').classList.remove('hidden');document.getElementById('activeMsgText').textContent='💬 '+d.status.temp_message;document.getElementById('tempMessage').value=d.status.temp_message;}
    else document.getElementById('activeMsg').classList.add('hidden');
    renderLangs(d.stats.languages||{});
    const w=d.stats.messages_week||[0,0,0,0,0,0,d.stats.messages_today||0];drawChart(w);
  }catch(e){console.error(e);}
}

async function fetchConversations(){
  try{
    const r=await fetch(BASE+'/api/conversations?key='+SECRET);if(r.status===403)return;
    const d=await r.json();allConversations=d.conversations||[];
    const el=document.getElementById('convSidebar');
    if(!allConversations.length){el.innerHTML='<div class="empty-state"><span>💬</span>Aucune conversation</div>';return;}
    el.innerHTML=allConversations.map((c,i)=>'<div class="conv-list-item" onclick="openConversation('+i+')" id="conv-'+i+'"><div class="conv-avatar" style="background:'+COLORS[i%5]+'15;color:'+COLORS[i%5]+'">'+(c.phone||'?')[0]+'</div><div class="conv-detail"><div class="conv-name">'+c.phone+'</div><div class="conv-preview">'+c.last_message+'</div></div><div><div class="conv-time">'+c.last_time+'</div><div style="text-align:right;font-size:10px;color:#94A3B8;margin-top:2px">'+c.count+' msgs</div></div></div>').join('');
  }catch(e){console.error(e);}
}

function openConversation(idx){
  const c=allConversations[idx];if(!c)return;
  document.querySelectorAll('.conv-list-item').forEach(el=>el.classList.remove('selected'));
  document.getElementById('conv-'+idx).classList.add('selected');
  document.getElementById('chatHeader').textContent='📱 '+c.phone+' — '+c.count+' messages';
  const body=document.getElementById('chatBody');
  body.innerHTML=c.messages.map(m=>'<div class="bubble-wrap '+(m.role==='user'?'user':'bot')+'"><div class="bubble '+(m.role==='user'?'bubble-user':'bubble-bot')+'">'+m.content+'</div><div class="bubble-time">'+m.time+'</div></div>').join('');
  body.scrollTop=body.scrollHeight;
}

async function fetchBookings(){
  try{
    const r=await fetch(BASE+'/api/bookings?key='+SECRET);if(r.status===403)return;
    const d=await r.json();allBookings=d.bookings||[];
    document.getElementById('bookBadge').textContent=allBookings.length;
    const el=document.getElementById('bookingsList');
    if(!allBookings.length){el.innerHTML='<div class="empty-state"><span>🍽️</span>Aucune réservation pour le moment</div>';return;}
    el.innerHTML=allBookings.map(b=>'<div class="booking-row"><div class="conv-avatar" style="background:#F59E0B15;color:#F59E0B">🍽️</div><div style="flex:1"><div style="font-size:14px;font-weight:600;color:#0F1B2D">'+b.name+'</div><div style="font-size:12px;color:#94A3B8;margin-top:2px">'+b.message+'</div></div><div style="text-align:right"><div class="booking-status booking-pending">'+b.status+'</div><div style="font-size:11px;color:#94A3B8;margin-top:4px;font-family:monospace">'+(b.timestamp||'').substring(0,16).replace('T',' ')+'</div></div></div>').join('');
  }catch(e){console.error(e);}
}

async function apiPost(ep,data){return fetch(BASE+ep+'?key='+SECRET,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
async function setStatus(s){await apiPost('/api/status',{status:s});updateStatusUI(s);showToast('✅ Statut mis à jour');}
async function sendMessage(){const m=document.getElementById('tempMessage').value;if(!m)return;await apiPost('/api/message',{message:m});document.getElementById('activeMsg').classList.remove('hidden');document.getElementById('activeMsgText').textContent='💬 '+m;showToast('💬 Message activé');}
async function clearMessage(){await apiPost('/api/message',{message:''});document.getElementById('activeMsg').classList.add('hidden');document.getElementById('tempMessage').value='';showToast('Message supprimé');}

if(sessionStorage.getItem('rb_auth')==='1'){fetchData();fetchConversations();fetchBookings();}
setInterval(()=>{if(sessionStorage.getItem('rb_auth')==='1')fetchData();},15000);
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


app = FastAPI(title="RestoBot", version="3.0.0", lifespan=lifespan)

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


# --- Dashboard (secured with secret link + password) ---

@app.get("/dashboard/{secret_key}", response_class=HTMLResponse)
async def dashboard(secret_key: str):
    if secret_key != DASHBOARD_SECRET:
        return HTMLResponse("<h1>404 — Page introuvable</h1>", status_code=404)
    return DASHBOARD_HTML.replace("{{SECRET_KEY}}", secret_key).replace("{{DASHBOARD_PASSWORD}}", DASHBOARD_PASSWORD)


# Old /dashboard route returns 404
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_redirect():
    return HTMLResponse("<h1>404 — Page introuvable</h1>", status_code=404)


@app.get("/api/dashboard")
async def dashboard_data(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
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
    if data.get("status") == "closed_today":
        status["closed_dates"].append(date.today().isoformat())
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
    status["updated_at"] = datetime.utcnow().isoformat()
    return {"status": "updated"}


@app.post("/api/closed")
async def add_closed_date(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
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
        result.append({
            "phone": phone,
            "messages": [{"role": m["role"], "content": m["content"], "time": m.get("timestamp", "")[:16].replace("T", " ")} for m in msgs],
            "last_message": msgs[-1]["content"][:100],
            "last_time": msgs[-1].get("timestamp", "")[:16].replace("T", " "),
            "count": len(msgs),
        })
    return {"conversations": result}


@app.get("/api/conversation/{phone}")
async def get_conversation_detail(phone: str, request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    pid = list(restaurants.keys())[0] if restaurants else None
    if not pid:
        return {"messages": []}
    k = f"{pid}:{phone}"
    msgs = conversations.get(k, [])
    return {
        "phone": phone,
        "messages": [{"role": m["role"], "content": m["content"], "time": m.get("timestamp", "")[:16].replace("T", " ")} for m in msgs],
    }


@app.get("/api/bookings")
async def list_bookings(request: Request):
    key = request.query_params.get("key", "")
    if key != DASHBOARD_SECRET:
        return Response(status_code=403)
    return {"bookings": bookings[-50:]}


# ==============================================================
# PRIVACY POLICY & TERMS
# ==============================================================

PRIVACY_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RestoBot — Politique de Confidentialité</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=DM+Serif+Display&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'DM Sans', sans-serif; background: #FAFAF8; color: #1A1A1A; line-height: 1.7; }
        .top-bar { height: 4px; background: linear-gradient(90deg, #C9A55C 0%, #E8D5A3 50%, #C9A55C 100%); }
        header { background: #0A1628; color: white; padding: 60px 20px 50px; text-align: center; }
        header h1 { font-family: 'DM Serif Display', serif; font-size: 42px; margin-bottom: 10px; color: #C9A55C; }
        header p { color: #8899AA; font-size: 16px; }
        .container { max-width: 780px; margin: 0 auto; padding: 50px 24px 80px; }
        .updated { display: inline-block; background: #C9A55C15; color: #C9A55C; padding: 6px 16px; border-radius: 20px; font-size: 13px; font-weight: 600; margin-bottom: 40px; }
        h2 { font-family: 'DM Serif Display', serif; font-size: 26px; color: #0A1628; margin: 40px 0 16px; padding-bottom: 10px; border-bottom: 2px solid #C9A55C30; }
        h3 { font-size: 17px; font-weight: 700; color: #0A1628; margin: 24px 0 10px; }
        p { margin-bottom: 14px; color: #444; font-size: 15px; }
        ul { margin: 10px 0 20px 24px; color: #444; }
        li { margin-bottom: 8px; font-size: 15px; }
        .highlight { background: #0A162808; border-left: 3px solid #C9A55C; padding: 20px 24px; border-radius: 0 8px 8px 0; margin: 20px 0; }
        .highlight p { margin-bottom: 0; }
        a { color: #C9A55C; }
        footer { background: #0A1628; color: #8899AA; padding: 40px 20px; text-align: center; font-size: 13px; }
        footer a { color: #C9A55C; text-decoration: none; }
    </style>
</head>
<body>
    <div class="top-bar"></div>
    <header>
        <h1>RestoBot</h1>
        <p>Politique de Confidentialité</p>
    </header>
    <div class="container">
        <span class="updated">Dernière mise à jour : 25 février 2026</span>

        <h2>1. Introduction</h2>
        <p>RestoBot est un service d'agent conversationnel intelligent destiné aux restaurants, opérant principalement via WhatsApp. Le présent document décrit comment nous collectons, utilisons et protégeons les données personnelles des utilisateurs de notre service.</p>
        <p>RestoBot est édité par Édouard Franceschi, entrepreneur individuel basé à Nice, France.</p>

        <h2>2. Données collectées</h2>
        <p>Dans le cadre du fonctionnement de notre service, nous collectons les données suivantes :</p>

        <h3>Données des clients du restaurant</h3>
        <ul>
            <li>Numéro de téléphone WhatsApp</li>
            <li>Nom du profil WhatsApp</li>
            <li>Contenu des messages échangés avec l'agent</li>
            <li>Informations de réservation (date, heure, nombre de personnes, nom)</li>
            <li>Préférences alimentaires et allergies mentionnées</li>
        </ul>

        <h3>Données des restaurateurs</h3>
        <ul>
            <li>Nom du restaurant et coordonnées</li>
            <li>Menu, horaires d'ouverture et informations pratiques</li>
            <li>Numéro WhatsApp Business</li>
        </ul>

        <h2>3. Utilisation des données</h2>
        <p>Les données collectées sont utilisées exclusivement pour :</p>
        <ul>
            <li>Répondre aux messages des clients via l'agent IA</li>
            <li>Gérer les réservations et envoyer des confirmations</li>
            <li>Notifier le restaurateur des demandes reçues</li>
            <li>Améliorer la qualité des réponses de l'agent</li>
            <li>Générer des statistiques anonymisées pour le restaurateur</li>
        </ul>

        <div class="highlight">
            <p><strong>Nous ne vendons jamais vos données personnelles à des tiers. Nous n'utilisons pas vos données à des fins publicitaires.</strong></p>
        </div>

        <h2>4. Traitement par intelligence artificielle</h2>
        <p>Les messages reçus sont traités par un modèle d'intelligence artificielle (Claude, développé par Anthropic) afin de générer des réponses pertinentes. Les messages sont envoyés à l'API d'Anthropic pour traitement et ne sont pas conservés par Anthropic après le traitement de la requête.</p>

        <h2>5. Hébergement et sécurité</h2>
        <ul>
            <li>Les données sont hébergées sur des serveurs sécurisés (Railway, infrastructure cloud)</li>
            <li>Les communications sont chiffrées via HTTPS/TLS</li>
            <li>WhatsApp assure un chiffrement de bout en bout des messages</li>
            <li>L'accès aux données est strictement limité aux personnes autorisées</li>
        </ul>

        <h2>6. Durée de conservation</h2>
        <p>Les données de conversation sont conservées pendant une durée maximale de <strong>90 jours</strong> après le dernier échange, puis automatiquement supprimées. Les données de réservation sont conservées pendant 12 mois à des fins de suivi.</p>

        <h2>7. Vos droits (RGPD)</h2>
        <p>Conformément au Règlement Général sur la Protection des Données (RGPD), vous disposez des droits suivants :</p>
        <ul>
            <li><strong>Droit d'accès</strong> — obtenir une copie de vos données personnelles</li>
            <li><strong>Droit de rectification</strong> — corriger vos données inexactes</li>
            <li><strong>Droit à l'effacement</strong> — demander la suppression de vos données</li>
            <li><strong>Droit à la portabilité</strong> — recevoir vos données dans un format structuré</li>
            <li><strong>Droit d'opposition</strong> — vous opposer au traitement de vos données</li>
        </ul>
        <p>Pour exercer ces droits, contactez-nous à : <a href="mailto:contact@restobot.fr">contact@restobot.fr</a></p>

        <h2>8. Utilisation de la plateforme Meta/WhatsApp</h2>
        <p>Notre service utilise l'API WhatsApp Cloud de Meta. En utilisant notre service via WhatsApp, vous êtes également soumis aux <a href="https://www.whatsapp.com/legal/privacy-policy" target="_blank">conditions d'utilisation de WhatsApp</a>. Nous n'accédons pas à vos contacts, photos ou autres données WhatsApp en dehors des conversations avec notre agent.</p>

        <h2>9. Cookies</h2>
        <p>Le dashboard RestoBot (interface web pour les restaurateurs) n'utilise pas de cookies de suivi ni de cookies publicitaires. Seuls des cookies techniques essentiels au fonctionnement peuvent être utilisés.</p>

        <h2>10. Modifications</h2>
        <p>Nous nous réservons le droit de modifier cette politique de confidentialité. Toute modification sera publiée sur cette page avec une date de mise à jour actualisée.</p>

        <h2>11. Contact</h2>
        <div class="highlight">
            <p><strong>RestoBot</strong><br>
            Édouard Franceschi<br>
            Nice, France<br>
            Email : <a href="mailto:contact@restobot.fr">contact@restobot.fr</a></p>
        </div>
    </div>
    <footer>
        <p>&copy; 2026 RestoBot — <a href="/privacy">Politique de confidentialité</a> · <a href="/terms">Conditions d'utilisation</a></p>
    </footer>
</body>
</html>"""

TERMS_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RestoBot — Conditions d'utilisation</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=DM+Serif+Display&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'DM Sans', sans-serif; background: #FAFAF8; color: #1A1A1A; line-height: 1.7; }
        .top-bar { height: 4px; background: linear-gradient(90deg, #C9A55C 0%, #E8D5A3 50%, #C9A55C 100%); }
        header { background: #0A1628; color: white; padding: 60px 20px 50px; text-align: center; }
        header h1 { font-family: 'DM Serif Display', serif; font-size: 42px; margin-bottom: 10px; color: #C9A55C; }
        header p { color: #8899AA; font-size: 16px; }
        .container { max-width: 780px; margin: 0 auto; padding: 50px 24px 80px; }
        .updated { display: inline-block; background: #C9A55C15; color: #C9A55C; padding: 6px 16px; border-radius: 20px; font-size: 13px; font-weight: 600; margin-bottom: 40px; }
        h2 { font-family: 'DM Serif Display', serif; font-size: 26px; color: #0A1628; margin: 40px 0 16px; padding-bottom: 10px; border-bottom: 2px solid #C9A55C30; }
        p { margin-bottom: 14px; color: #444; font-size: 15px; }
        ul { margin: 10px 0 20px 24px; color: #444; }
        li { margin-bottom: 8px; font-size: 15px; }
        .highlight { background: #0A162808; border-left: 3px solid #C9A55C; padding: 20px 24px; border-radius: 0 8px 8px 0; margin: 20px 0; }
        a { color: #C9A55C; }
        footer { background: #0A1628; color: #8899AA; padding: 40px 20px; text-align: center; font-size: 13px; }
        footer a { color: #C9A55C; text-decoration: none; }
    </style>
</head>
<body>
    <div class="top-bar"></div>
    <header>
        <h1>RestoBot</h1>
        <p>Conditions Générales d'Utilisation</p>
    </header>
    <div class="container">
        <span class="updated">Dernière mise à jour : 25 février 2026</span>

        <h2>1. Objet</h2>
        <p>Les présentes conditions régissent l'utilisation du service RestoBot, un agent conversationnel intelligent fonctionnant via WhatsApp, destiné aux professionnels de la restauration et à leurs clients.</p>

        <h2>2. Description du service</h2>
        <p>RestoBot fournit un service d'assistant virtuel qui :</p>
        <ul>
            <li>Répond automatiquement aux questions des clients du restaurant via WhatsApp</li>
            <li>Assiste dans la prise de réservations</li>
            <li>Fournit des informations sur le menu, les horaires et les services du restaurant</li>
            <li>Notifie le restaurateur des demandes reçues</li>
        </ul>

        <h2>3. Intelligence artificielle</h2>
        <p>RestoBot utilise des modèles d'intelligence artificielle pour générer ses réponses. Bien que nous nous efforcions d'assurer l'exactitude des informations fournies :</p>
        <ul>
            <li>Les réponses sont générées automatiquement et peuvent contenir des inexactitudes</li>
            <li>L'agent ne remplace pas le jugement humain pour les questions médicales (allergènes)</li>
            <li>Le restaurateur reste responsable de la validation des informations fournies à l'agent</li>
        </ul>

        <h2>4. Responsabilités du restaurateur</h2>
        <ul>
            <li>Fournir des informations exactes et à jour (menu, horaires, allergènes)</li>
            <li>Informer ses clients de l'utilisation d'un agent automatisé</li>
            <li>Vérifier et valider les réservations prises par l'agent</li>
            <li>Signaler toute erreur ou dysfonctionnement</li>
        </ul>

        <h2>5. Limitation de responsabilité</h2>
        <p>RestoBot ne saurait être tenu responsable :</p>
        <ul>
            <li>Des erreurs dans les informations fournies par le restaurateur</li>
            <li>Des interruptions de service liées à WhatsApp ou Meta</li>
            <li>Des conséquences liées à des informations sur les allergènes (le client doit toujours confirmer directement avec le restaurant)</li>
            <li>Des pertes de données en cas de force majeure</li>
        </ul>

        <h2>6. Tarification</h2>
        <p>Les tarifs du service sont communiqués lors de la souscription. Toute modification tarifaire sera notifiée avec un préavis de 30 jours. Un essai gratuit de 30 jours est proposé sans engagement.</p>

        <h2>7. Résiliation</h2>
        <p>Le restaurateur peut résilier le service à tout moment avec un préavis de 30 jours. En cas de résiliation, les données sont supprimées dans un délai de 30 jours.</p>

        <h2>8. Droit applicable</h2>
        <p>Les présentes conditions sont soumises au droit français. Tout litige sera soumis aux tribunaux compétents de Nice.</p>

        <h2>9. Contact</h2>
        <div class="highlight">
            <p><strong>RestoBot</strong><br>
            Édouard Franceschi<br>
            Nice, France<br>
            Email : <a href="mailto:contact@restobot.fr">contact@restobot.fr</a></p>
        </div>
    </div>
    <footer>
        <p>&copy; 2026 RestoBot — <a href="/privacy">Politique de confidentialité</a> · <a href="/terms">Conditions d'utilisation</a></p>
    </footer>
</body>
</html>"""


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

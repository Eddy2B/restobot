"""
RestoBot — Agent IA WhatsApp pour la Restauration
Version single-file pour déploiement simple sur Railway.app
"""

import os
import json
import logging
from datetime import datetime, date, time
from contextlib import asynccontextmanager

import anthropic
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks
from pydantic import BaseModel

# ==============================================================
# CONFIG
# ==============================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "restobot-verify-2026")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
PORT = int(os.getenv("PORT", 8000))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("restobot")

# ==============================================================
# IN-MEMORY DATABASE (simple dict storage, sufficient for MVP)
# Replace with PostgreSQL when scaling
# ==============================================================

# Restaurants indexed by phone_number_id
restaurants = {}

# Conversations indexed by "{phone_number_id}:{customer_phone}"
conversations = {}

# Bookings list
bookings = []


# ==============================================================
# SAMPLE RESTAURANT (loaded at startup)
# ==============================================================

def load_sample_restaurant():
    """Load a sample restaurant for testing. Replace with your real data."""
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "1025551323971723")
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    owner_phone = os.getenv("OWNER_PHONE", "")

    restaurants[phone_number_id] = {
        "name": os.getenv("RESTAURANT_NAME", "La Table du Port"),
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "owner_phone": owner_phone,
        "context": {
            "description": os.getenv("RESTAURANT_DESCRIPTION",
                "Restaurant méditerranéen au cœur du Port de Nice. "
                "Cuisine fraîche et locale, poissons du marché, pâtes maison."
            ),
            "menu": os.getenv("RESTAURANT_MENU",
                "ENTRÉES : Burrata & tomates confites 14€ | Soupe de poissons 12€ | "
                "Carpaccio de daurade 16€\n"
                "PLATS : Loup grillé du jour 28€ | Risotto aux fruits de mer 24€ | "
                "Filet de bœuf sauce au poivre 26€ | Pâtes aux palourdes 20€\n"
                "DESSERTS : Tiramisu maison 10€ | Panna cotta fruits rouges 9€ | "
                "Café gourmand 8€\n"
                "MENU DÉJEUNER : Entrée + Plat ou Plat + Dessert 22€"
            ),
            "hours": os.getenv("RESTAURANT_HOURS",
                "Lundi: Fermé | Mardi-Jeudi: 12h-14h30, 19h-22h30 | "
                "Vendredi-Samedi: 12h-14h30, 19h-23h | Dimanche: 12h-15h"
            ),
            "address": os.getenv("RESTAURANT_ADDRESS",
                "12 quai des Docks, 06300 Nice"
            ),
            "phone": os.getenv("RESTAURANT_PHONE", "+33 4 93 55 12 34"),
            "tone": os.getenv("RESTAURANT_TONE",
                "Chaleureux et décontracté. Tutoiement OK si le client tutoie. "
                "1-2 emojis max par message."
            ),
            "languages": "français, anglais, italien",
            "special_info": os.getenv("RESTAURANT_SPECIAL_INFO",
                "Terrasse avec vue sur le port. Chaise bébé disponible. "
                "Accès PMR. Parking Port Lympia à 2 min."
            ),
            "booking_link": os.getenv("RESTAURANT_BOOKING_LINK", ""),
            "allergens_policy": "Nous prenons les allergies très au sérieux. "
                "Merci de préciser vos allergies, notre chef adapte les plats.",
        },
    }
    logger.info(f"✅ Restaurant chargé : {restaurants[phone_number_id]['name']}")


# ==============================================================
# CLAUDE AI
# ==============================================================

claude_client = None


def get_claude():
    global claude_client
    if claude_client is None:
        claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return claude_client


def build_system_prompt(restaurant: dict) -> str:
    """Build the system prompt from restaurant context."""
    ctx = restaurant["context"]
    booking_section = ""
    if ctx.get("booking_link"):
        booking_section = f"""
RÉSERVATION :
- Si le client veut réserver, envoie-lui ce lien : {ctx['booking_link']}
- Tu peux aussi noter les détails (nombre, date, heure, nom) et les transmettre au restaurant.
"""
    else:
        booking_section = """
RÉSERVATION :
- Si le client veut réserver, collecte : nombre de personnes, date, heure, nom.
- Confirme la réservation et dis que le restaurant va valider.
"""

    return f"""Tu es l'assistant virtuel du restaurant "{restaurant['name']}".

RÔLE : Tu réponds aux clients sur WhatsApp de manière naturelle et chaleureuse.
Tu parles comme un membre de l'équipe, pas comme un robot.

TON : {ctx.get('tone', 'Professionnel mais chaleureux')}
LANGUES : Réponds dans la langue du client. Tu parles {ctx.get('languages', 'français')}.

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
    """Send messages to Claude and get a response."""
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
    """Send a text message via WhatsApp Cloud API."""
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
    """Mark a message as read (blue ticks)."""
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, headers=headers, timeout=5.0)
        except Exception:
            pass


def parse_webhook(body: dict) -> dict | None:
    """Parse incoming WhatsApp webhook payload."""
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
# CONVERSATION MANAGEMENT
# ==============================================================

def get_conversation(phone_number_id: str, customer_phone: str) -> list:
    """Get or create conversation history."""
    key = f"{phone_number_id}:{customer_phone}"
    if key not in conversations:
        conversations[key] = []
    return conversations[key]


def save_message(phone_number_id: str, customer_phone: str, role: str, content: str):
    """Save a message to conversation history (keep last 20)."""
    key = f"{phone_number_id}:{customer_phone}"
    if key not in conversations:
        conversations[key] = []
    conversations[key].append({"role": role, "content": content})
    # Keep only last 20 messages
    conversations[key] = conversations[key][-20:]


# ==============================================================
# NOTIFICATION TO RESTAURANT OWNER
# ==============================================================

async def notify_owner(restaurant: dict, customer_phone: str, customer_name: str, message: str):
    """Notify restaurant owner of a new booking or important message."""
    if not restaurant.get("owner_phone"):
        return

    # Simple keyword detection for bookings
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


# ==============================================================
# MAIN MESSAGE PROCESSING
# ==============================================================

async def process_and_reply(
    phone_number_id: str,
    customer_phone: str,
    customer_name: str,
    message_text: str,
):
    """Process incoming message and send reply."""
    restaurant = restaurants.get(phone_number_id)
    if not restaurant:
        logger.warning(f"No restaurant for phone_number_id: {phone_number_id}")
        return

    # Build system prompt
    system_prompt = build_system_prompt(restaurant)

    # Get conversation history
    history = get_conversation(phone_number_id, customer_phone)

    # Build messages for Claude
    claude_messages = []
    for msg in history[-10:]:  # Last 10 messages for context
        claude_messages.append({"role": msg["role"], "content": msg["content"]})
    claude_messages.append({"role": "user", "content": message_text})

    # Get AI response
    response = await ask_claude(system_prompt, claude_messages)

    # Save to history
    save_message(phone_number_id, customer_phone, "user", message_text)
    save_message(phone_number_id, customer_phone, "assistant", response)

    # Send reply
    await send_whatsapp_message(
        phone_number_id,
        restaurant["access_token"],
        customer_phone,
        response,
    )

    # Notify owner if it's a booking request
    await notify_owner(restaurant, customer_phone, customer_name, message_text)

    logger.info(f"💬 [{restaurant['name']}] {customer_name or customer_phone}: {message_text[:80]}")
    logger.info(f"🤖 Réponse: {response[:80]}")


# ==============================================================
# FASTAPI APP
# ==============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_sample_restaurant()
    logger.info("🚀 RestoBot démarré")
    yield
    logger.info("👋 RestoBot arrêté")


app = FastAPI(title="RestoBot", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "service": "RestoBot",
        "status": "running",
        "restaurants": len(restaurants),
        "conversations": len(conversations),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    """WhatsApp webhook verification."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("✅ Webhook vérifié")
        return Response(content=challenge, media_type="text/plain")

    logger.warning("❌ Vérification webhook échouée")
    return Response(status_code=403)


@app.post("/webhook/whatsapp")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """Receive incoming WhatsApp messages."""
    body = await request.json()

    parsed = parse_webhook(body)
    if not parsed:
        return {"status": "ok"}

    logger.info(f"📩 Message de {parsed['name'] or parsed['from']}: {parsed['text'][:100]}")

    # Mark as read
    restaurant = restaurants.get(parsed["phone_number_id"])
    if restaurant:
        background_tasks.add_task(
            mark_as_read,
            parsed["phone_number_id"],
            restaurant["access_token"],
            parsed["message_id"],
        )

    # Process and reply in background
    background_tasks.add_task(
        process_and_reply,
        parsed["phone_number_id"],
        parsed["from"],
        parsed["name"],
        parsed["text"],
    )

    return {"status": "ok"}


# ==============================================================
# API ADMIN (simple endpoints to manage restaurants)
# ==============================================================

@app.get("/api/restaurants")
async def list_restaurants():
    return [
        {"name": r["name"], "phone_number_id": pid}
        for pid, r in restaurants.items()
    ]


@app.get("/api/conversations")
async def list_conversations():
    return {
        key: {"messages": len(msgs), "last": msgs[-1] if msgs else None}
        for key, msgs in conversations.items()
    }


@app.post("/api/restaurants")
async def add_restaurant(request: Request):
    """Add a new restaurant via API."""
    data = await request.json()
    pid = data.get("phone_number_id")
    if not pid:
        return {"error": "phone_number_id required"}, 400
    restaurants[pid] = data
    return {"status": "created", "phone_number_id": pid}


# ==============================================================
# RUN
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

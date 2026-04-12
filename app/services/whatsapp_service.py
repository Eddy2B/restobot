# app/services/whatsapp_service.py — WhatsApp Business API send functions
# Dependencies: httpx, logging. No state deps (phone_number_id + access_token passed as args).

import logging
import httpx

logger = logging.getLogger("guestscale")


async def send_whatsapp_message(phone_number_id: str, access_token: str, to: str, text: str):
    """Send a text message via WhatsApp Business Cloud API."""
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
    """Mark a WhatsApp message as read."""
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, headers=headers, json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
        except Exception:
            pass


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
                fallback_text = f"Bonjour ! 👋 Vous avez essayé de joindre {restaurant_name} et nous n'avons pas pu prendre votre appel. Je suis l'assistant du restaurant, comment puis-je vous aider ? Réservation, menu, horaires... je suis là pour vous ! 😊"
                await send_whatsapp_message(phone_number_id, access_token, to, fallback_text)
                return True
        except Exception as e:
            logger.error(f"WhatsApp template send error: {e}")
            return False


def parse_webhook(body: dict) -> dict | None:
    """Parse incoming WhatsApp webhook payload into a normalized message dict."""
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

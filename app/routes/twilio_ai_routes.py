# app/routes/twilio_ai_routes.py — Twilio webhooks + AI management

import logging
from datetime import timedelta

from fastapi import APIRouter, Request, Response, BackgroundTasks
from fastapi.responses import JSONResponse

import app.state as _state
from app.auth import get_auth
from app.utils.date_utils import now_paris
from app.services.db_helpers import bump_version, is_active_or_trial_valid, db_save_restaurant

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.post("/twilio/voice")
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


@router.post("/twilio/status")
async def twilio_status_callback(request: Request):
    """Twilio status callback for call completion tracking."""
    form = await request.form()
    logger.info(f"Twilio status: {dict(form)}")
    return {"status": "ok"}

@router.post("/twilio/confirm-gather")
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


@router.post("/api/toggle-ai")
async def api_toggle_ai(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    rest = _state.restaurants_cache.get(rid)
    if rest:
        rest.setdefault("settings", {})["ai_enabled"] = data.get("enabled", True)
        await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "ok"}

@router.post("/api/pause-ai")
async def api_pause_ai(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    minutes = int(data.get("minutes", 60))
    rest = _state.restaurants_cache.get(rid)
    if rest:
        rest["ai_paused_until"] = (now_paris() + timedelta(minutes=minutes)).isoformat()
    return {"status": "ok", "paused_until": rest.get("ai_paused_until") if rest else None}

@router.post("/api/conversation/pause")
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
        _state.ai_paused_conversations.setdefault(rid, {})[phone] = (now_paris() + timedelta(minutes=minutes)).isoformat()
    else:
        _state.ai_paused_conversations.get(rid, {}).pop(phone, None)
    return {"status": "ok"}

# /api/conversations/send — dead code removed (already in extracted route module)
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
    rest = _state.restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id") or not rest.get("whatsapp_access_token"):
        return JSONResponse(status_code=400, content={"error": "WhatsApp non configure"})
    # Send via WhatsApp
    await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], phone, message)
    # Save in conversation history with human flag
    save_message(rid, phone, "assistant", message, sender_type="human")
    bump_version(rid)
    return {"status": "ok"}

# /api/escalations — dead code removed (already in extracted route module)
async def api_escalations(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"escalations": escalations.get(rid, [])}

# /api/escalations/resolve — dead code removed (already in extracted route module)
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
    _state.ai_paused_conversations.get(rid, {}).pop(phone, None)
    return {"status": "ok"}

# /api/missed-calls — dead code removed (already in extracted route module)
async def api_missed_calls(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"calls": [{"phone": p, **v} for p, v in _state.missed_call_tracker.get(rid, {}).items()]}


# ==============================================================
# WEBHOOK (routes by phone_number_id)
# ==============================================================


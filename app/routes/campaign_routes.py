# app/routes/campaign_routes.py — /api/campaigns/*, /api/broadcast

import logging
import httpx

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

import app.state as _state
from app.state import limiter
from app.config import BREVO_API_KEY, WHATSAPP_BROADCAST_COST_CENTS
from app.auth import get_auth
from app.utils.text_utils import sanitize_input
from app.utils.date_utils import now_paris
from app.services.db_helpers import is_active_or_trial_valid, expired_402, _filter_contacts, increment_message_count
from app.services.wallet_service import get_wallet_cents, debit_wallet
from app.services.whatsapp_service import send_whatsapp_message

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.get("/api/campaigns")
async def api_list_campaigns(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"campaigns": _state.campaigns_store.get(rid, [])}


@router.post("/api/campaigns/preview")
async def api_campaign_preview(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    filters = data.get("filters", {})
    matched = _filter_contacts(rid, filters)
    return {"count": len(matched)}


@router.post("/api/campaigns/send")
@limiter.limit("10/minute")
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
    rest = _state.restaurants_cache.get(rid, {})
    rest_name = rest.get("name", "Restaurant")
    matched = _filter_contacts(rid, filters)

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
    campaign_id_local = f"C{len(_state.campaigns_store.get(rid, []))+1}"
    for ct in matched:
        ct_name = (ct.get("name") or "").split()[0] if ct.get("name") else ""
        text_body = body.replace("{prenom}", ct_name).replace("{restaurant}", rest_name)
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
        if "whatsapp" in channels:
            ct_phone = ct.get("phone")
            if ct_phone and wa_phone_id and wa_token:
                if get_wallet_cents(rid) < (sent_wa + 1) * WHATSAPP_BROADCAST_COST_CENTS:
                    logger.warning(f"Wallet drained mid-campaign: {rid}")
                    break
                try:
                    await send_whatsapp_message(wa_phone_id, wa_token, ct_phone, text_body)
                    sent_wa += 1
                    await increment_message_count(rid, "broadcast")
                except Exception as e:
                    logger.error(f"Campaign WhatsApp error: {e}")

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
    _state.campaigns_store.setdefault(rid, []).append(campaign)
    return {"status": "ok", "sent": sent_count, "sent_email": sent_email,
            "sent_whatsapp": sent_wa, "cost_cents": campaign["cost_cents"],
            "wallet_balance_cents": get_wallet_cents(rid)}


@router.post("/api/broadcast")
async def api_broadcast(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    msg = sanitize_input(data.get("message", ""), 1000)
    if not msg:
        return {"error": "No message"}
    rest = _state.restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id"):
        return {"error": "WhatsApp not configured"}
    rid_contacts = _state.contacts.get(rid, {})
    sent = 0
    for phone, ct in rid_contacts.items():
        if phone and phone.startswith("+"):
            try:
                await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], phone, msg)
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast failed for {phone}: {e}")
    return {"status": "ok", "sent": sent}

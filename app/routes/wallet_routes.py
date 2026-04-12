# app/routes/wallet_routes.py — /api/wallet, /api/wallet/checkout

import logging
import stripe as stripe_mod

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

import app.state as _state
from app.state import limiter
from app.config import WHATSAPP_BROADCAST_COST_CENTS, WALLET_TOPUP_AMOUNTS_CENTS, APP_DOMAIN
from app.auth import get_auth
from app.services.db_helpers import is_active_or_trial_valid, expired_402
from app.services.wallet_service import get_wallet_cents, get_wallet_transactions

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.get("/api/wallet")
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


@router.post("/api/wallet/checkout")
@limiter.limit("5/minute")
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
    rest = _state.restaurants_cache.get(rid, {})
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

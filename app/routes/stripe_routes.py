# app/routes/stripe_routes.py — /api/stripe/checkout, portal, webhook

import json
import logging
import stripe as stripe_mod

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

import app.state as _state
from app.config import STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_FOUNDER, STRIPE_PRICE_STANDARD, APP_DOMAIN
from app.auth import get_auth
from app.services.db_helpers import (
    bump_version, _refresh_rest_from_db,
    get_restaurant_stripe_config, set_restaurant_stripe_config, find_restaurant_by_stripe_customer,
)
from app.services.wallet_service import credit_wallet
from app.services.brevo_service import send_subscription_welcome_emails

logger = logging.getLogger("guestscale")
router = APIRouter()


def _sg(obj, key, default=None):
    """Safe getter for Stripe webhook objects (SDK version-independent)."""
    if obj is None:
        return default
    try:
        if key in obj:
            val = obj[key]
            return default if val is None else val
    except (TypeError, KeyError):
        pass
    try:
        val = getattr(obj, key)
        return default if val is None else val
    except AttributeError:
        return default


@router.post("/api/stripe/checkout")
async def api_stripe_checkout(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    if not stripe_mod.api_key:
        return JSONResponse(status_code=503, content={"error": "Stripe not configured"})
    rid = auth["restaurant_id"]
    data = await request.json()
    plan = data.get("plan", "founder")
    price_id = STRIPE_PRICE_FOUNDER if plan == "founder" else STRIPE_PRICE_STANDARD
    if not price_id:
        return JSONResponse(status_code=400, content={"error": "Plan non configuré"})
    email = auth.get("email", "")
    customer_id = get_restaurant_stripe_config(rid, "stripe_customer_id")
    if not customer_id:
        customer = stripe_mod.Customer.create(email=email, metadata={"restaurant_id": rid})
        customer_id = customer.id
        set_restaurant_stripe_config(rid, "stripe_customer_id", customer_id)
    session = stripe_mod.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"https://{APP_DOMAIN}/dashboard?p=account&subscription=success",
        cancel_url=f"https://{APP_DOMAIN}/dashboard?p=account&subscription=cancelled",
        metadata={"restaurant_id": rid, "plan": plan},
        allow_promotion_codes=True,
    )
    return {"checkout_url": session.url}


@router.post("/api/stripe/portal")
async def api_stripe_portal(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    customer_id = get_restaurant_stripe_config(rid, "stripe_customer_id")
    if not customer_id:
        return JSONResponse(status_code=400, content={"error": "Pas d'abonnement"})
    session = stripe_mod.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"https://{APP_DOMAIN}/dashboard?p=account",
    )
    return {"portal_url": session.url}


@router.post("/api/stripe/webhook")
async def api_stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe_mod.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe_mod.error.SignatureVerificationError as e:
        logger.error(f"Stripe webhook signature verification failed: {e}")
        return Response(status_code=400)
    except Exception as e:
        logger.error(f"Stripe webhook parse error: {e}")
        return Response(status_code=400)

    etype = _sg(event, "type", "") or ""
    data = _sg(event, "data", None)
    obj = _sg(data, "object", None)
    if obj is None:
        logger.warning(f"Stripe webhook {etype} with no data.object")
        return {"status": "ok"}

    if etype == "checkout.session.completed":
        meta = _sg(obj, "metadata", None) or {}
        rid = _sg(meta, "restaurant_id", None)
        session_id = _sg(obj, "id", "") or ""
        if not rid:
            logger.warning(f"Stripe webhook checkout.session.completed without restaurant_id metadata: session={session_id[:20]}")
            return {"status": "ok"}
        meta_type = _sg(meta, "type", "subscription") or "subscription"
        if meta_type == "wallet_topup":
            try:
                amount_cents = int(_sg(meta, "amount_cents", 0) or 0)
            except (TypeError, ValueError):
                amount_cents = 0
            if amount_cents > 0:
                ok = await credit_wallet(rid, amount_cents,
                    description=f"Recharge Stripe ({amount_cents/100:.2f} €)",
                    stripe_session_id=session_id)
                if ok:
                    logger.info(f"Stripe: wallet topup +{amount_cents}c for {rid[:8]}... session={session_id[:20]}")
                else:
                    logger.info(f"Stripe: wallet topup skipped (already processed) session={session_id[:20]}")
        else:
            plan = _sg(meta, "plan", "founder") or "founder"
            sub_id = _sg(obj, "subscription", "") or ""
            persisted = False
            if _state.db_pool:
                try:
                    async with _state.db_pool.acquire() as conn:
                        await conn.execute("""
                            UPDATE restaurants SET
                                settings = COALESCE(settings, '{}'::jsonb) || jsonb_build_object(
                                    'stripe_subscription_id', $2::text,
                                    'subscription_plan', $3::text,
                                    'subscription_status', 'active'
                                ),
                                status = 'active',
                                updated_at = NOW()
                            WHERE id = $1::uuid
                        """, rid, sub_id, plan)
                        persisted = True
                except Exception as e:
                    logger.error(f"Stripe webhook DB persist failed for {rid[:8]}: {e}")
            rest = _state.restaurants_cache.get(rid)
            if rest is not None:
                rest["status"] = "active"
                rest.setdefault("settings", {})["stripe_subscription_id"] = sub_id
                rest["settings"]["subscription_plan"] = plan
                rest["settings"]["subscription_status"] = "active"
            bump_version(rid)
            logger.info(f"Stripe: subscription activated for {rid[:8]}... plan={plan} persisted={persisted}")
            if persisted:
                user_email = ""
                first_name = ""
                rest_name = (rest or {}).get("name", "Restaurant") if rest else "Restaurant"
                if _state.db_pool:
                    try:
                        async with _state.db_pool.acquire() as conn:
                            row = await conn.fetchrow(
                                "SELECT u.email, u.first_name, r.name FROM users u JOIN restaurants r ON r.id = u.restaurant_id WHERE u.restaurant_id = $1::uuid LIMIT 1",
                                rid,
                            )
                            if row:
                                user_email = row["email"] or ""
                                first_name = row["first_name"] or ""
                                rest_name = row["name"] or rest_name
                    except Exception as e:
                        logger.error(f"Stripe webhook user lookup failed: {e}")
                if user_email:
                    try:
                        await send_subscription_welcome_emails(user_email, first_name, rest_name, plan)
                    except Exception as e:
                        logger.error(f"Subscription welcome email failed (non-blocking): {e}")
    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        cid = _sg(obj, "customer", None)
        rid = find_restaurant_by_stripe_customer(cid) if cid else None
        if rid:
            status = "canceled" if etype.endswith("deleted") else (_sg(obj, "status", "active") or "active")
            set_restaurant_stripe_config(rid, "subscription_status", status)
            bump_version(rid)
    elif etype == "invoice.payment_failed":
        cid = _sg(obj, "customer", None)
        rid = find_restaurant_by_stripe_customer(cid) if cid else None
        if rid:
            set_restaurant_stripe_config(rid, "subscription_status", "past_due")
            bump_version(rid)
    return {"status": "ok"}

# app/routes/dashboard_routes.py — /api/version, /api/dashboard, /api/status, /api/message,
# /api/me, /api/change-password, /api/account/delete, /api/subscription, /api/usage,
# /api/account/cancel, /api/account/cancel/undo

import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

import app.state as _state
from app.config import PLAN_LIMITS, PLAN_RATES
from app.auth import get_auth, hash_password, verify_password
from app.utils.text_utils import sanitize_input, safe_json
from app.utils.date_utils import today_paris, now_paris, _last_day_of_current_month_iso
from app.services.db_helpers import (
    db_save_restaurant, db_save_restaurant_status, bump_version,
    compute_effective_status, is_active_or_trial_valid, _refresh_rest_from_db,
)
from app.services.brevo_service import send_cancellation_emails

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.get("/api/version")
async def api_version(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"v": _state.data_versions.get(rid, 0)}


@router.get("/api/dashboard")
async def api_dashboard_data(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    st = _state.stats.get(rid, {})
    today_str = today_paris().isoformat()
    if st.get("last_reset") != today_str:
        st["messages_today"] = 0
        st["bookings_today"] = 0
        st["languages"] = {}
        st["last_reset"] = today_str
    status = _state.restaurant_status.get(rid, {})
    recent = []
    for k, msgs in sorted(_state.conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True)[:20]:
        if not k.startswith(rid) or not msgs:
            continue
        phone = k.split(":")[1] if ":" in k else k
        last = msgs[-1]
        recent.append({"phone": phone, "last_message": last["content"][:200], "time": last.get("timestamp", "")[:16].replace("T", " ")})
    return {"stats": st, "status": status, "conversations_count": sum(1 for k in _state.conversations if k.startswith(rid)), "recent_conversations": recent}


@router.post("/api/status")
async def api_update_status(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = _state.restaurant_status.get(rid, {})
    status["status"] = data.get("status", "open")
    status["updated_at"] = datetime.utcnow().isoformat()
    _state.restaurant_status[rid] = status
    await db_save_restaurant_status(rid, status)
    return {"status": "updated"}


@router.post("/api/message")
async def api_update_message(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = _state.restaurant_status.get(rid, {})
    status["temp_message"] = data.get("message", "")
    _state.restaurant_status[rid] = status
    await db_save_restaurant_status(rid, status)
    return {"status": "updated"}


@router.get("/api/me")
async def api_me(request: Request):
    auth = get_auth(request)
    if not auth:
        return JSONResponse(status_code=401, content={"error": "Non authentifié"})
    rid = auth.get("restaurant_id", "")
    rest = _state.restaurants_cache.get(rid, {})
    return {
        "user": {
            "email": auth.get("email", ""),
            "user_id": auth.get("user_id", ""),
            "restaurant_id": rid,
            "restaurant_name": rest.get("name", ""),
            "restaurant_status": rest.get("status", "trial"),
            "trial_ends_at": rest.get("trial_ends_at"),
            "role": auth.get("role", "owner"),
        }
    }


@router.post("/api/change-password")
async def api_change_password(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    data = await safe_json(request)
    if not data:
        return JSONResponse(status_code=400, content={"error": "Requête invalide"})
    current = data.get("current_password", "")
    new_pwd = data.get("new_password", "")
    if not current or not new_pwd:
        return JSONResponse(status_code=400, content={"error": "Champs requis"})
    if len(new_pwd) < 12:
        return JSONResponse(status_code=400, content={"error": "Minimum 12 caractères"})
    try:
        async with _state.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1::uuid", auth["user_id"])
            if not row or not verify_password(current, row["password_hash"]):
                return JSONResponse(status_code=401, content={"error": "Mot de passe actuel incorrect"})
            await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2::uuid", hash_password(new_pwd), auth["user_id"])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Change password error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur serveur"})


@router.delete("/api/account/delete")
async def api_delete_account(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    user_id = auth.get("user_id", "")
    try:
        if _state.db_pool:
            async with _state.db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_contacts WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_conversations WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_review_queue WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM users WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM restaurants WHERE id = $1::uuid", rid)
        for store in [_state.bookings, _state.contacts, _state.conversations, _state.floor_tables,
                      _state.table_slots, _state.review_queue, _state.stats, _state.daily_stats_history,
                      _state.waitlist, _state.data_versions, _state.restaurant_status,
                      _state.table_statuses, _state.table_groups, _state.escalations,
                      _state.missed_call_tracker, _state.campaigns_store]:
            store.pop(rid, None)
        _state.restaurants_cache.pop(rid, None)
        logger.info(f"Account deleted: restaurant {rid[:8]}... by user {user_id}")
        response = JSONResponse(content={"status": "ok", "message": "Compte et données supprimés"})
        response.delete_cookie("gs_token", path="/")
        return response
    except Exception as e:
        logger.error(f"Account deletion error: {e}")
        return JSONResponse(status_code=500, content={"error": "Erreur lors de la suppression"})


@router.get("/api/subscription")
async def api_subscription(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    await _refresh_rest_from_db(rid)
    rest = _state.restaurants_cache.get(rid, {})
    settings = rest.get("settings", {})
    status = settings.get("subscription_status", "trial")
    plan = settings.get("subscription_plan", "founder")
    cancel_pending = bool(settings.get("cancel_pending", False))
    cancel_effective = settings.get("cancel_effective_date", "") if cancel_pending else ""
    cancel_reason = settings.get("cancel_reason", "") if cancel_pending else ""
    trial_ends = rest.get("trial_ends_at", "")
    trial_days_left = 30
    trial_expired = False
    if trial_ends:
        try:
            from datetime import datetime as dt_cls
            ends = dt_cls.fromisoformat(trial_ends.replace('Z', '+00:00')) if isinstance(trial_ends, str) else trial_ends
            diff = (ends.replace(tzinfo=None) - datetime.utcnow()).days
            trial_days_left = max(0, diff)
            trial_expired = diff < 0
        except Exception:
            pass
    effective_status = compute_effective_status(rest)
    access_blocked = effective_status not in ("active", "trial")
    if effective_status == "active":
        blocked_reason = None
    elif effective_status == "suspended":
        blocked_reason = "suspended"
    elif effective_status == "canceled":
        blocked_reason = "canceled"
    elif effective_status == "expired":
        blocked_reason = "trial_expired" if status == "trial" else "past_due"
    else:
        blocked_reason = None
    return {
        "status": status,
        "effective_status": effective_status,
        "plan": plan,
        "trial_days_left": trial_days_left,
        "trial_expired": trial_expired if status == "trial" else False,
        "cancel_pending": cancel_pending,
        "cancel_effective_date": cancel_effective,
        "cancel_reason": cancel_reason,
        "access_blocked": access_blocked,
        "blocked_reason": blocked_reason,
    }


@router.post("/api/account/cancel")
async def api_account_cancel(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return JSONResponse(status_code=404, content={"error": "Restaurant introuvable"})
    try:
        data = await request.json()
    except Exception:
        data = {}
    reason = sanitize_input((data.get("reason") or "").strip(), 1000)
    settings = rest.setdefault("settings", {})
    if settings.get("cancel_pending"):
        return JSONResponse(status_code=400, content={"error": "Résiliation déjà demandée"})
    effective_date = _last_day_of_current_month_iso()
    settings["cancel_pending"] = True
    settings["cancel_effective_date"] = effective_date
    settings["cancel_reason"] = reason
    settings["cancel_requested_at"] = now_paris().isoformat()
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    first_name = ""
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT first_name FROM users WHERE id = $1::uuid", auth["user_id"])
                if row:
                    first_name = row["first_name"] or ""
        except Exception as e:
            logger.error(f"Cancel: user lookup failed: {e}")
    try:
        await send_cancellation_emails(
            user_email=auth.get("email", ""),
            first_name=first_name,
            restaurant_name=rest.get("name", "Restaurant"),
            effective_date=effective_date,
            reason=reason,
        )
    except Exception as e:
        logger.error(f"Cancellation email failed (non-blocking): {e}")
    logger.info(f"Account cancel scheduled for {rid[:8]}... effective={effective_date}")
    return {"status": "cancelled", "effective_date": effective_date}


@router.post("/api/account/cancel/undo")
async def api_account_cancel_undo(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return JSONResponse(status_code=404, content={"error": "Restaurant introuvable"})
    settings = rest.setdefault("settings", {})
    if not settings.get("cancel_pending"):
        return JSONResponse(status_code=400, content={"error": "Aucune résiliation en attente"})
    settings["cancel_pending"] = False
    settings.pop("cancel_effective_date", None)
    settings.pop("cancel_reason", None)
    settings.pop("cancel_requested_at", None)
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    logger.info(f"Account cancel undone for {rid[:8]}...")
    return {"status": "active"}


@router.get("/api/usage")
async def api_usage(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    month = now_paris().strftime("%Y-%m")
    rest = _state.restaurants_cache.get(rid, {})
    plan = rest.get("settings", {}).get("subscription_plan", "trial")
    limit = PLAN_LIMITS.get(plan, 500)
    rate = PLAN_RATES.get(plan, 0.08)
    counters = _state.usage_counters.get(rid, {})
    current = counters.get(month, {"total": 0, "missed_call": 0, "reminder": 0, "review": 0, "other": 0})
    total = current["total"]
    overage = max(0, total - limit)
    history = []
    for m, c in sorted(counters.items(), reverse=True):
        if m != month:
            m_over = max(0, c["total"] - limit)
            history.append({"month": m, "messages_sent": c["total"], "overage": m_over, "cost": round(m_over * rate, 2)})
    return {
        "month": month, "plan": plan,
        "messages_sent": total, "messages_included": limit,
        "messages_remaining": max(0, limit - total),
        "messages_overage": overage, "overage_rate": rate,
        "overage_cost": round(overage * rate, 2),
        "usage_percent": round(total / max(limit, 1) * 100, 1),
        "detail": {"missed_call": current.get("missed_call", 0), "reminder": current.get("reminder", 0), "review": current.get("review", 0), "other": current.get("other", 0)},
        "history": history[:6],
    }

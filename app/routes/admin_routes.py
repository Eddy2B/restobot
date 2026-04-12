# app/routes/admin_routes.py — /api/admin/* endpoints

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

import app.state as _state
from app.config import PLAN_LIMITS
from app.auth import verify_admin
from app.utils.text_utils import sanitize_input, sanitize_dict, normalize_phone
from app.utils.date_utils import today_paris, now_paris
from app.services.db_helpers import (
    bump_version, db_save_restaurant, db_save_contact, init_daily_slots,
    compute_effective_status, _refresh_rest_from_db, _refresh_all_restaurants_from_db,
    sanitize_restaurant,
    get_restaurant_stripe_config, set_restaurant_stripe_config, find_restaurant_by_stripe_customer,
)

logger = logging.getLogger("guestscale")
router = APIRouter()


# verify_admin now imported from app.auth (Phase 2 refactoring)


@router.get("/api/admin/restaurants")
async def admin_list_restaurants(request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    result = []
    for rid, rest in _state.restaurants_cache.items():
        rid_bookings = _state.bookings.get(rid, [])
        rid_contacts = _state.contacts.get(rid, {})
        rid_convs = sum(1 for k in _state.conversations if k.startswith(rid))
        st = _state.stats.get(rid, {})
        result.append({
            "id": rid, "slug": rest.get("slug", ""), "name": rest.get("name", ""),
            "status": rest.get("status", "trial"),
            "effective_status": compute_effective_status(rest),
            "subscription_status": rest.get("settings", {}).get("subscription_status", "trial"),
            "trial_ends_at": rest.get("trial_ends_at"),
            "created_at": rest.get("created_at"),
            "owner_phone": rest.get("owner_phone", ""),
            "whatsapp_connected": bool(rest.get("whatsapp_phone_number_id")),
            "google_review_link": bool(rest.get("google_review_link")),
            "total_bookings": len(rid_bookings),
            "total_contacts": len(rid_contacts),
            "total_conversations": rid_convs,
            "messages_today": st.get("messages_today", 0),
            "bookings_today": st.get("bookings_today", 0),
            "tables_count": len(_state.floor_tables.get(rid, [])),
            "has_menu": bool(rest.get("settings", {}).get("menu")),
            "has_address": bool(rest.get("settings", {}).get("address")),
            "waitlist_count": len([w for w in _state.waitlist.get(rid, []) if w.get("status") == "waiting"]),
            "messages_this_month": _state.usage_counters.get(rid, {}).get(today_paris().strftime("%Y-%m"), {}).get("total", 0),
            "plan_limit": PLAN_LIMITS.get(rest.get("settings", {}).get("subscription_plan", "trial"), 500),
        })
    result.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"restaurants": result, "total": len(result)}


@router.get("/api/admin/stats")
async def admin_global_stats(request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    total_restaurants = len(_state.restaurants_cache)
    total_bookings = sum(len(b) for b in _state.bookings.values())
    total_contacts = sum(len(c) for c in _state.contacts.values())
    total_conversations = len(_state.conversations)
    total_messages = sum(s.get("messages_today", 0) for s in _state.stats.values())
    total_tables = sum(len(ft) for ft in _state.floor_tables.values())
    trial_count = sum(1 for r in _state.restaurants_cache.values() if r.get("status") == "trial")
    active_count = sum(1 for r in _state.restaurants_cache.values() if r.get("status") == "active")
    suspended_count = sum(1 for r in _state.restaurants_cache.values() if r.get("status") == "suspended")
    cancelled_count = sum(1 for r in _state.restaurants_cache.values() if r.get("status") == "cancelled")
    wa_connected = sum(1 for r in _state.restaurants_cache.values() if r.get("whatsapp_phone_number_id"))

    # MRR / ARR
    price_per_month = 149
    mrr = active_count * price_per_month
    arr = mrr * 12
    potential_mrr = (active_count + trial_count) * price_per_month

    # Conversion rate trial -> active
    total_ever = total_restaurants
    conversion_rate = round((active_count / total_ever * 100), 1) if total_ever > 0 else 0
    churn_rate = round((cancelled_count / total_ever * 100), 1) if total_ever > 0 else 0

    # Growth: registrations over time
    registrations_timeline = []
    bookings_timeline = []
    messages_timeline = []
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                # Users count
                users_count = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
                # Registrations per week (last 12 weeks)
                rows = await conn.fetch("""
                    SELECT date_trunc('week', created_at) as week, COUNT(*) as cnt
                    FROM restaurants
                    WHERE created_at > NOW() - INTERVAL '12 weeks'
                    GROUP BY week ORDER BY week
                """)
                for row in rows:
                    registrations_timeline.append({
                        "date": row["week"].strftime("%Y-%m-%d") if row["week"] else "",
                        "count": row["cnt"]
                    })
                # Bookings per day (last 30 days)
                rows = await conn.fetch("""
                    SELECT booking_date, COUNT(*) as cnt
                    FROM mt_bookings
                    WHERE created_at > NOW() - INTERVAL '30 days'
                    GROUP BY booking_date ORDER BY booking_date
                """)
                for row in rows:
                    bookings_timeline.append({
                        "date": row["booking_date"] or "",
                        "count": row["cnt"]
                    })
                # Messages per day from daily_stats (last 30 days)
                rows = await conn.fetch("""
                    SELECT stat_date, SUM((data->>'messages')::int) as msgs, SUM((data->>'bookings')::int) as bks
                    FROM mt_daily_stats
                    WHERE stat_date > (NOW() - INTERVAL '30 days')::date::text
                    GROUP BY stat_date ORDER BY stat_date
                """)
                for row in rows:
                    messages_timeline.append({
                        "date": row["stat_date"] or "",
                        "messages": row["msgs"] or 0,
                        "bookings": row["bks"] or 0,
                    })
        except Exception as e:
            logger.error(f"Admin _state.stats query error: {e}")
            users_count = 0
    else:
        users_count = 0

    # Total messages all time (sum of daily _state.stats)
    total_messages_alltime = sum(
        sum(snap.get("messages", 0) for snap in dsh)
        for dsh in _state.daily_stats_history.values()
    )
    total_bookings_alltime = total_bookings

    # Per-restaurant performance
    restaurant_performance = []
    for rid, rest in _state.restaurants_cache.items():
        rid_bks = _state.bookings.get(rid, [])
        rid_cts = _state.contacts.get(rid, {})
        rid_st = _state.stats.get(rid, {})
        dsh = _state.daily_stats_history.get(rid, [])
        total_msgs_resto = sum(snap.get("messages", 0) for snap in dsh) + rid_st.get("messages_today", 0)
        restaurant_performance.append({
            "name": rest.get("name", ""),
            "status": rest.get("status", ""),
            "bookings": len(rid_bks),
            "contacts": len(rid_cts),
            "messages": total_msgs_resto,
            "bookings_today": rid_st.get("bookings_today", 0),
            "messages_today": rid_st.get("messages_today", 0),
            "whatsapp": bool(rest.get("whatsapp_phone_number_id")),
            "created": rest.get("created_at", ""),
        })

    # Avg _state.bookings per restaurant per month (estimate)
    avg_bookings_per_resto = round(total_bookings / max(active_count, 1), 1)

    return {
        "total_restaurants": total_restaurants, "trial_count": trial_count,
        "active_count": active_count, "suspended_count": suspended_count, "cancelled_count": cancelled_count,
        "total_bookings": total_bookings, "total_contacts": total_contacts,
        "total_conversations": total_conversations, "total_messages_today": total_messages,
        "total_messages_alltime": total_messages_alltime,
        "total_tables": total_tables, "whatsapp_connected": wa_connected, "users_count": users_count,
        "mrr": mrr, "arr": arr, "potential_mrr": potential_mrr, "price_per_month": price_per_month,
        "conversion_rate": conversion_rate, "churn_rate": churn_rate,
        "avg_bookings_per_resto": avg_bookings_per_resto,
        "registrations_timeline": registrations_timeline,
        "bookings_timeline": bookings_timeline,
        "messages_timeline": messages_timeline,
        "restaurant_performance": restaurant_performance,
        "total_messages_month": sum(c.get(today_paris().strftime("%Y-%m"), {}).get("total", 0) for c in _state.usage_counters.values()),
    }


# AUDIT FIX 2026-04-12 — Métriques business SaaS (MRR/ARR/ARPU/LTV/Churn)
@router.get("/api/admin/metrics")
async def admin_metrics(request: Request):
    if not verify_admin(request):
        return Response(status_code=401)

    paying, trial_valid, expired_list, churned = [], [], [], []
    for rid, rest in _state.restaurants_cache.items():
        eff = compute_effective_status(rest)
        if eff == "active":
            paying.append(rest)
        elif eff == "trial":
            trial_valid.append(rest)
        elif eff == "expired":
            expired_list.append(rest)
        elif eff in ("canceled", "cancelled", "suspended"):
            churned.append(rest)

    # MRR : somme par plan (founder=99, standard=149)
    mrr = 0
    for rest in paying:
        plan = rest.get("settings", {}).get("subscription_plan", "founder")
        mrr += 99 if plan == "founder" else 149
    arr = mrr * 12
    n_paying = len(paying)
    arpu = round(mrr / n_paying, 2) if n_paying > 0 else 0

    # Churn mensuel (nb churned / (payants + churned) au début du mois — approximation)
    n_churned = len(churned)
    base = n_paying + n_churned
    churn_rate = round(n_churned / base * 100, 1) if base > 0 else 0

    # LTV estimée : ARPU / churn. Si churn=0 → 18 mois par défaut
    ltv = round(arpu / (churn_rate / 100), 2) if churn_rate > 0 else round(arpu * 18, 2)

    # Wallet revenue (total recharges Stripe)
    wallet_revenue = 0.0
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                val = await conn.fetchval("SELECT COALESCE(SUM(amount_cents), 0) FROM mt_wallet_transactions WHERE txn_type = 'topup'")
                wallet_revenue = round(val / 100, 2)
        except Exception:
            pass

    # Totaux globaux
    total_msgs = sum(
        sum(snap.get("messages", 0) for snap in dsh) for dsh in _state.daily_stats_history.values()
    ) + sum(s.get("messages_today", 0) for s in _state.stats.values())
    total_bks = sum(len(b) for b in _state.bookings.values())
    total_cts = sum(len(c) for c in _state.contacts.values())
    total_rev = sum(1 for rq in _state.review_queue.values() for r in rq if r.get("sent"))

    return {
        "mrr": mrr, "arr": arr,
        "total_clients_paying": n_paying,
        "total_clients_trial": len(trial_valid),
        "total_clients_expired": len(expired_list),
        "total_clients_churned": n_churned,
        "churn_rate_monthly": churn_rate,
        "avg_revenue_per_user": arpu,
        "ltv_estimate": ltv,
        "cac_estimate": None,
        "ltv_cac_ratio": None,
        "wallet_revenue_total": wallet_revenue,
        "total_messages_sent": total_msgs,
        "total_bookings": total_bks,
        "total_contacts": total_cts,
        "total_reviews_sent": total_rev,
    }


@router.get("/api/admin/restaurant/{rid}")
async def admin_restaurant_detail(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_bookings = _state.bookings.get(rid, [])
    rid_contacts = _state.contacts.get(rid, {})
    st = _state.stats.get(rid, {})
    dsh = _state.daily_stats_history.get(rid, [])
    wl = _state.waitlist.get(rid, [])
    status_data = _state.restaurant_status.get(rid, {})
    # Get user info
    user_info = None
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT email, first_name, last_name, phone, created_at FROM users WHERE restaurant_id = $1::uuid LIMIT 1",
                    rid
                )
                if row:
                    user_info = {
                        "email": row["email"], "first_name": row["first_name"] or "",
                        "last_name": row["last_name"] or "", "phone": row["phone"] or "",
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                    }
        except Exception:
            pass
    return {
        "restaurant": sanitize_restaurant(rest), "user": user_info,
        "bookings_count": len(rid_bookings), "contacts_count": len(rid_contacts),
        "stats_today": st, "daily_history": dsh[-14:],
        "waitlist_active": len([w for w in wl if w.get("status") in ("waiting", "notified")]),
        "status": status_data, "tables": _state.floor_tables.get(rid, []),
    }


@router.post("/api/admin/restaurant/{rid}/status")
async def admin_update_restaurant_status(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    data = await request.json()
    new_status = data.get("status", "")
    if new_status not in ("trial", "active", "suspended", "cancelled"):
        return {"error": "Invalid status"}

    rest["status"] = new_status
    settings = rest.setdefault("settings", {})

    # "Activer" depuis le super-admin = comp manuel : on force aussi
    # subscription_status="active" pour bypasser le check trial_ends_at.
    # Sinon compute_effective_status retourne "expired" sur un trial dépassé.
    if new_status == "active":
        settings["subscription_status"] = "active"
        settings_json_for_db = json.dumps(settings)
    else:
        settings_json_for_db = None  # ne touche pas settings pour les autres statuts

    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                if settings_json_for_db is not None:
                    await conn.execute(
                        "UPDATE restaurants SET status = $1, settings = $2::jsonb, updated_at = NOW() WHERE id = $3::uuid",
                        new_status, settings_json_for_db, rid,
                    )
                else:
                    await conn.execute(
                        "UPDATE restaurants SET status = $1, updated_at = NOW() WHERE id = $2::uuid",
                        new_status, rid,
                    )
        except Exception as e:
            logger.error(f"Admin status update error: {e}")
            return {"error": str(e)}

    # Belt + bretelles : re-read DB into cache pour s'aligner avec n'importe
    # quel autre changement out-of-band éventuel.
    await _refresh_rest_from_db(rid)
    bump_version(rid)
    rest = _state.restaurants_cache.get(rid, rest)
    logger.info(f"Admin: status -> {new_status} for {rest.get('name')} ({rid[:8]}...)")
    return {
        "status": "ok",
        "new_status": new_status,
        "effective_status": compute_effective_status(rest),
    }


@router.post("/api/admin/restaurant/{rid}/extend-trial")
async def admin_extend_trial(rid: str, request: Request):
    """Offre X jours d'essai gratuit (admin manuel : compensation, prospect, etc.).
    Réinitialise trial_ends_at = NOW() + X days, status = 'trial', et purge
    settings.subscription_status si présent (pour redonner accès via essai)."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    data = await request.json()
    try:
        days = int(data.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    if days < 1 or days > 365:
        return {"error": "days doit être entre 1 et 365"}

    new_end = datetime.utcnow() + timedelta(days=days)
    settings = rest.setdefault("settings", {})
    # Purge l'état d'abonnement pour que compute_effective_status retourne "trial"
    settings.pop("subscription_status", None)
    rest["status"] = "trial"
    rest["trial_ends_at"] = new_end.isoformat()

    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE restaurants SET
                        trial_ends_at = $1,
                        status = 'trial',
                        settings = COALESCE(settings, '{}'::jsonb) - 'subscription_status',
                        updated_at = NOW()
                       WHERE id = $2::uuid""",
                    new_end, rid,
                )
        except Exception as e:
            logger.error(f"Admin extend-trial error: {e}")
            return {"error": str(e)}

    await _refresh_rest_from_db(rid)
    bump_version(rid)
    rest = _state.restaurants_cache.get(rid, rest)
    logger.info(f"Admin: trial extended +{days}d for {rest.get('name')} ({rid[:8]}...) → {new_end.date().isoformat()}")
    return {
        "status": "ok",
        "days": days,
        "trial_ends_at": new_end.isoformat(),
        "effective_status": compute_effective_status(rest),
    }


# AUDIT FIX 2026-04-12 — Purge test data from a restaurant
@router.post("/api/admin/purge-test-data/{rid}")
async def admin_purge_test_data(rid: str, request: Request):
    """Supprime les _state.contacts/réservations/_state.conversations dont le nom contient 'test' (case insensitive).
    Utile pour nettoyer des données de dev visibles en démo."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}

    rid_contacts_dict = _state.contacts.get(rid, {})
    rid_bookings_list = _state.bookings.get(rid, [])
    purged_contacts = 0
    purged_bookings = 0
    purged_convs = 0
    phones_to_purge = []

    # 1. Identify test _state.contacts
    for phone, ct in list(rid_contacts_dict.items()):
        name = (ct.get("name") or "").lower()
        if "test" in name:
            phones_to_purge.append(phone)
            del rid_contacts_dict[phone]
            purged_contacts += 1

    # 2. Purge associated _state.bookings
    for phone in phones_to_purge:
        before = len(rid_bookings_list)
        _state.bookings[rid] = [b for b in rid_bookings_list if b.get("phone") != phone]
        rid_bookings_list = _state.bookings[rid]
        purged_bookings += before - len(rid_bookings_list)

    # 3. Purge associated _state.conversations
    conv_keys_to_remove = []
    for phone in phones_to_purge:
        conv_key = f"{rid}:{phone}"
        if conv_key in _state.conversations:
            conv_keys_to_remove.append(conv_key)
    for ck in conv_keys_to_remove:
        _state.conversations.pop(ck, None)
        purged_convs += 1

    # 4. Persist to DB
    if _state.db_pool and phones_to_purge:
        try:
            async with _state.db_pool.acquire() as conn:
                for phone in phones_to_purge:
                    await conn.execute("DELETE FROM mt_contacts WHERE phone = $1 AND restaurant_id = $2::uuid", phone, rid)
                    await conn.execute("DELETE FROM mt_conversations WHERE conv_key = $1 AND restaurant_id = $2::uuid", f"{rid}:{phone}", rid)
                    await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid AND data->>'phone' = $2", rid, phone)
        except Exception as e:
            logger.error(f"Admin purge test data DB error: {e}")

    bump_version(rid)
    logger.info(f"Admin: purged test data for {rest.get('name')}: {purged_contacts} _state.contacts, {purged_bookings} _state.bookings, {purged_convs} conversations")
    return {"status": "ok", "purged": {"contacts": purged_contacts, "bookings": purged_bookings, "conversations": purged_convs}}


@router.put("/api/admin/restaurant/{rid}")
async def admin_update_restaurant(rid: str, request: Request):
    """Update restaurant details: name, slug, owner_phone, settings fields, whatsapp config, google_review_link."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    data = await request.json()
    # Updatable top-level fields
    for field in ("name", "slug", "owner_phone", "whatsapp_phone_number_id", "whatsapp_access_token", "google_review_link"):
        if field in data:
            rest[field] = data[field]
    # Updatable settings fields
    if "settings" in data:
        current_settings = rest.get("settings", {})
        for key in ("description", "menu", "hours", "address", "phone", "tone", "languages", "special_info", "allergens_policy", "booking_link", "twilio_number"):
            if key in data["settings"]:
                current_settings[key] = data["settings"][key]
        rest["settings"] = current_settings
    # Update pid mapping if whatsapp_phone_number_id changed
    if "whatsapp_phone_number_id" in data:
        # Remove old mapping
        old_pids = [k for k, v in _state.pid_to_restaurant.items() if v == rid]
        for k in old_pids:
            _state.pid_to_restaurant.pop(k, None)
        if data["whatsapp_phone_number_id"]:
            _state.pid_to_restaurant[data["whatsapp_phone_number_id"]] = rid
    # Update phone mapping if phone changed
    if "settings" in data and "phone" in data["settings"]:
        new_phone = normalize_phone(data["settings"]["phone"])
        if new_phone:
            _state.phone_to_restaurant[new_phone] = rid
    # Persist to DB
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE restaurants SET name=$1, slug=$2, owner_phone=$3, 
                       whatsapp_phone_number_id=$4, whatsapp_access_token=$5, 
                       google_review_link=$6, settings=$7::jsonb
                       WHERE id=$8::uuid""",
                    rest.get("name", ""), rest.get("slug", ""), rest.get("owner_phone", ""),
                    rest.get("whatsapp_phone_number_id", ""), rest.get("whatsapp_access_token", ""),
                    rest.get("google_review_link", ""), json.dumps(rest.get("settings", {})),
                    rid
                )
        except Exception as e:
            logger.error(f"Admin update restaurant error: {e}")
            return {"error": str(e)}
    bump_version(rid)
    logger.info(f"Admin: updated restaurant {rest.get('name')} ({rid[:8]}...)")
    return {"status": "ok", "restaurant": sanitize_restaurant(rest)}


@router.get("/api/admin/restaurant/{rid}/bookings")
async def admin_restaurant_bookings(rid: str, request: Request):
    """Get all _state.bookings for a restaurant."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_bookings = _state.bookings.get(rid, [])
    return {"bookings": rid_bookings, "total": len(rid_bookings)}


@router.get("/api/admin/restaurant/{rid}/contacts")
async def admin_restaurant_contacts(rid: str, request: Request):
    """Get all _state.contacts for a restaurant."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_contacts = _state.contacts.get(rid, {})
    return {"contacts": list(rid_contacts.values()), "total": len(rid_contacts)}


@router.get("/api/admin/restaurant/{rid}/conversations")
async def admin_restaurant_conversations(rid: str, request: Request):
    """Get all _state.conversations for a restaurant."""
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    rid_convs = {k.split(":", 1)[1]: v for k, v in _state.conversations.items() if k.startswith(rid + ":")}
    return {"conversations": rid_convs, "total": len(rid_convs)}


@router.delete("/api/admin/restaurant/{rid}/booking/{booking_id}")
async def admin_delete_booking(rid: str, booking_id: str, request: Request):
    """Delete a specific booking."""
    if not verify_admin(request):
        return Response(status_code=401)
    rid_bookings = _state.bookings.get(rid, [])
    booking = next((b for b in rid_bookings if b.get("id") == booking_id), None)
    if not booking:
        return {"error": "Booking not found"}
    rid_bookings.remove(booking)
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_bookings WHERE id = $1 AND restaurant_id = $2::uuid", booking_id, rid)
        except Exception as e:
            logger.error(f"Admin delete booking error: {e}")
    bump_version(rid)
    return {"status": "ok", "deleted": booking_id}


@router.delete("/api/admin/restaurant/{rid}")
@router.delete("/api/admin/restaurant/{rid}")
async def admin_delete_restaurant(rid: str, request: Request):
    if not verify_admin(request):
        return Response(status_code=401)
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "Restaurant not found"}
    # Remove from memory
    _state.restaurants_cache.pop(rid, None)
    _state.bookings.pop(rid, None)
    _state.floor_tables.pop(rid, None)
    _state.table_slots.pop(rid, None)
    _state.review_queue.pop(rid, None)
    _state.contacts.pop(rid, None)
    _state.stats.pop(rid, None)
    _state.daily_stats_history.pop(rid, None)
    _state.waitlist.pop(rid, None)
    _state.restaurant_status.pop(rid, None)
    _state.data_versions.pop(rid, None)
    # Remove _state.conversations
    keys_to_remove = [k for k in _state.conversations if k.startswith(rid)]
    for k in keys_to_remove:
        _state.conversations.pop(k, None)
    # Remove from pid mapping
    pid_keys = [k for k, v in _state.pid_to_restaurant.items() if v == rid]
    for k in pid_keys:
        _state.pid_to_restaurant.pop(k, None)
    # Remove from DB
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_waitlist WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_daily_stats WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_restaurant_status WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_review_queue WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_conversations WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_contacts WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM users WHERE restaurant_id = $1::uuid", rid)
                await conn.execute("DELETE FROM restaurants WHERE id = $1::uuid", rid)
            logger.info(f"Admin: deleted restaurant {rest.get('name')} ({rid[:8]}...)")
        except Exception as e:
            logger.error(f"Admin delete error: {e}")
    return {"status": "ok", "deleted": rest.get("name", "")}




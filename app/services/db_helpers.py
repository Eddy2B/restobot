# app/services/db_helpers.py — Database CRUD helpers + state management
# Dependencies: app.state (for _state.db_pool, dicts), json, logging. No circular imports.

import json
import logging
from datetime import datetime, timedelta
from fastapi.responses import JSONResponse

import app.state as _state
from app.config import ALL_SLOTS, MEAL_DURATION_SLOTS

logger = logging.getLogger("guestscale")


def init_daily_slots(rid: str):
    """Initialize table availability slots for a restaurant."""
    tables = _state.floor_tables.get(rid, [])
    slots = {}
    for slot_time in ALL_SLOTS:
        slots[slot_time] = {}
        for t in tables:
            slots[slot_time][t["id"]] = "available"
    _state.table_slots[rid] = slots


def bump_version(restaurant_id: str):
    _state.data_versions[restaurant_id] = _state.data_versions.get(restaurant_id, 0) + 1


def _split_table_ids(table_id: str) -> list:
    """Split a combined table id like 'T5+T3' into ['T5','T3']."""
    return [t.strip() for t in table_id.split("+") if t.strip()]


def assign_table(rid: str, slot_time: str, table_id: str, booking_id: str):
    """Block a table (or multi-table combo) for 2h starting from the booking slot."""
    if rid not in _state.table_slots:
        return
    ids = _split_table_ids(table_id)
    try:
        start_idx = ALL_SLOTS.index(slot_time)
    except ValueError:
        if slot_time in _state.table_slots[rid]:
            for tid in ids:
                _state.table_slots[rid][slot_time][tid] = f"booked:{booking_id}"
        return
    for i in range(MEAL_DURATION_SLOTS):
        idx = start_idx + i
        if idx >= len(ALL_SLOTS):
            break
        s = ALL_SLOTS[idx]
        if s in _state.table_slots[rid]:
            for tid in ids:
                _state.table_slots[rid][s][tid] = f"booked:{booking_id}"


def release_table(rid: str, slot_time: str, table_id: str):
    """Release a table (or multi-table combo) for 2h starting from the slot."""
    if rid not in _state.table_slots:
        return
    ids = _split_table_ids(table_id)
    try:
        start_idx = ALL_SLOTS.index(slot_time)
    except ValueError:
        if slot_time in _state.table_slots.get(rid, {}):
            for tid in ids:
                _state.table_slots[rid][slot_time][tid] = "available"
        return
    for i in range(MEAL_DURATION_SLOTS):
        idx = start_idx + i
        if idx >= len(ALL_SLOTS):
            break
        s = ALL_SLOTS[idx]
        if s in _state.table_slots[rid]:
            for tid in ids:
                _state.table_slots[rid][s][tid] = "available"


def get_slot_summary(rid: str) -> dict:
    """Get availability summary per time slot for a restaurant."""
    tables = _state.floor_tables.get(rid, [])
    slots = _state.table_slots.get(rid, {})
    summary = {}
    for slot_time in ALL_SLOTS:
        slot_data = slots.get(slot_time, {})
        total = len(tables)
        avail = sum(1 for t in tables if slot_data.get(t["id"]) == "available")
        summary[slot_time] = {"total": total, "available": avail, "booked": total - avail}
    return summary


def find_best_table(rid: str, slot_time: str, covers: int, zone_pref: str = None) -> str | None:
    """Find the best available table (or combo) for the given party size."""
    tables = _state.floor_tables.get(rid, [])
    slots = _state.table_slots.get(rid, {}).get(slot_time, {})
    available = [t for t in tables if slots.get(t["id"]) == "available"]

    for try_zone in ([zone_pref, None] if zone_pref else [None]):
        candidates = []
        for t in available:
            if t["seats"] < covers:
                continue
            if try_zone and t["zone"] != try_zone:
                continue
            candidates.append(t)
        if candidates:
            candidates.sort(key=lambda t: t["seats"])
            return candidates[0]["id"]

    pool = sorted(available, key=lambda t: t["seats"], reverse=True)
    if zone_pref:
        pool = sorted(pool, key=lambda t: (0 if t["zone"] == zone_pref else 1, -t["seats"]))
    if not pool:
        return None
    best_combo = None
    best_waste = 999
    for i, big in enumerate(pool):
        remaining = covers - big["seats"]
        if remaining <= 0:
            continue
        combo = [big]
        total = big["seats"]
        rest_pool = sorted([t for j, t in enumerate(pool) if j != i], key=lambda t: t["seats"])
        for t in rest_pool:
            combo.append(t)
            total += t["seats"]
            if total >= covers:
                break
        if total >= covers:
            waste = total - covers
            if waste < best_waste:
                best_waste = waste
                best_combo = list(combo)
    if best_combo:
        best_combo.sort(key=lambda t: t["seats"], reverse=True)
        return "+".join(t["id"] for t in best_combo)
    return None



async def db_save_booking(restaurant_id: str, booking: dict):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_bookings (id, restaurant_id, data, booking_date, created_at)
                VALUES ($1, $2::uuid, $3::jsonb, $4, NOW())
                ON CONFLICT (id, restaurant_id) DO UPDATE SET data = $3::jsonb
            """, booking["id"], restaurant_id, json.dumps(booking, default=str), booking.get("date", ""))
    except Exception as e:
        logger.error(f"DB save booking error: {e}")


async def db_save_contact(restaurant_id: str, phone: str, data: dict):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_contacts (phone, restaurant_id, data, updated_at)
                VALUES ($1, $2::uuid, $3::jsonb, NOW())
                ON CONFLICT (phone, restaurant_id) DO UPDATE SET data = $3::jsonb, updated_at = NOW()
            """, phone, restaurant_id, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save contact error: {e}")


async def db_save_conversation(restaurant_id: str, conv_key: str, messages: list):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_conversations (conv_key, restaurant_id, messages, updated_at)
                VALUES ($1, $2::uuid, $3::jsonb, NOW())
                ON CONFLICT (conv_key, restaurant_id) DO UPDATE SET messages = $3::jsonb, updated_at = NOW()
            """, conv_key, restaurant_id, json.dumps(messages, default=str))
    except Exception as e:
        logger.error(f"DB save conversation error: {e}")


async def db_save_review(restaurant_id: str, review: dict):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_review_queue (restaurant_id, data, created_at)
                VALUES ($1::uuid, $2::jsonb, NOW())
            """, restaurant_id, json.dumps(review, default=str))
    except Exception as e:
        logger.error(f"DB save review error: {e}")


async def db_mark_review_sent(restaurant_id: str, phone: str):
    """Mark all reviews for a phone as sent in the DB to prevent re-sending on restart."""
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            # Update the JSON data to set sent=true for matching phone
            await conn.execute("""
                UPDATE mt_review_queue 
                SET data = jsonb_set(data, '{sent}', 'true')
                WHERE restaurant_id = $1::uuid 
                AND data->>'phone' = $2
                AND (data->>'sent')::text != 'true'
            """, restaurant_id, phone)
    except Exception as e:
        logger.error(f"DB mark review sent error: {e}")


async def db_save_restaurant_status(restaurant_id: str, data: dict):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_restaurant_status (restaurant_id, data, updated_at)
                VALUES ($1::uuid, $2::jsonb, NOW())
                ON CONFLICT (restaurant_id) DO UPDATE SET data = $2::jsonb, updated_at = NOW()
            """, restaurant_id, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save restaurant status error: {e}")


async def db_save_daily_stats(restaurant_id: str, stat_date: str, data: dict):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_daily_stats (restaurant_id, stat_date, data, created_at)
                VALUES ($1::uuid, $2, $3::jsonb, NOW())
            """, restaurant_id, stat_date, json.dumps(data, default=str))
    except Exception as e:
        logger.error(f"DB save daily stats error: {e}")


async def db_save_waitlist_entry(restaurant_id: str, entry: dict):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_waitlist (restaurant_id, data, wait_date, status, created_at)
                VALUES ($1::uuid, $2::jsonb, $3, $4, NOW())
            """, restaurant_id, json.dumps(entry, default=str), entry.get("date", ""), entry.get("status", "waiting"))
    except Exception as e:
        logger.error(f"DB save waitlist error: {e}")


async def db_update_waitlist_status(restaurant_id: str, entry_id: str, status: str):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE mt_waitlist SET status = $3, data = jsonb_set(data, '{status}', to_jsonb($3::text))
                WHERE restaurant_id = $1::uuid AND data->>'id' = $2
            """, restaurant_id, entry_id, status)
    except Exception as e:
        logger.error(f"DB update waitlist error: {e}")


async def db_save_restaurant(restaurant_id: str, rest: dict):
    """Persist restaurant settings + floor_tables back to DB."""
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE restaurants SET
                    name = $2, owner_phone = $3, settings = $4::jsonb,
                    floor_tables = $5::jsonb, google_review_link = $6,
                    updated_at = NOW()
                WHERE id = $1::uuid
            """, restaurant_id, rest.get("name", ""),
                rest.get("owner_phone", ""),
                json.dumps(rest.get("settings", {})),
                json.dumps(rest.get("floor_tables", [])),
                rest.get("google_review_link", ""))
    except Exception as e:
        logger.error(f"DB save restaurant error: {e}")


def compute_effective_status(rest: dict) -> str:
    """Croise les 3 sources de vérité (column status, settings.subscription_status,
    trial_ends_at) pour calculer le vrai état utilisateur. Retourne :
    - "suspended" : column status = suspended (admin a suspendu manuellement)
    - "active"    : abonnement payant actif
    - "canceled"  : abonnement annulé / résilié
    - "expired"   : essai expiré sans abonnement, OU paiement past_due
    - "trial"     : essai en cours et non expiré
    Aucune ambiguïté possible : 1 état retourné, exclusif.
    """
    if not rest:
        return "unknown"
    # Suspension admin > tout le reste (le admin peut suspendre un compte payant)
    if rest.get("status") == "suspended":
        return "suspended"
    settings = rest.get("settings", {}) or {}
    sub_status = settings.get("subscription_status", "trial")
    if sub_status == "active":
        return "active"
    if sub_status in ("canceled", "cancelled"):
        return "canceled"
    if sub_status == "past_due":
        return "expired"
    # En essai (sub_status == "trial" ou absent) : check trial_ends_at
    trial_ends = rest.get("trial_ends_at", "")
    if trial_ends:
        try:
            from datetime import datetime as _dt
            if isinstance(trial_ends, str):
                ends = _dt.fromisoformat(trial_ends.replace("Z", "+00:00"))
            else:
                ends = trial_ends
            if ends.replace(tzinfo=None) < datetime.utcnow():
                return "expired"
        except Exception:
            pass
    return "trial"


def is_active_or_trial_valid(rid: str) -> bool:
    """True si le restaurant peut consommer. Délègue à compute_effective_status
    qui croise les 3 sources de vérité (column status, settings.subscription_status,
    trial_ends_at). Seuls "active" et "trial" donnent accès."""
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return False
    return compute_effective_status(rest) in ("active", "trial")


def expired_402() -> JSONResponse:
    """Standardised 402 Payment Required response for expired/blocked accounts."""
    return JSONResponse(
        status_code=402,
        content={
            "error": "Essai expiré ou abonnement inactif — passez à un plan payant pour continuer",
            "code": "subscription_required",
        },
    )


async def _refresh_rest_from_db(rid: str) -> bool:
    """Re-read trial_ends_at, settings, status from DB and update the in-memory cache.
    Used to pick up out-of-band DB changes (manual SQL UPDATE, external scripts).
    Returns True if the cache entry was updated."""
    if not _state.db_pool:
        return False
    try:
        async with _state.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT settings, status, trial_ends_at FROM restaurants WHERE id = $1::uuid",
                rid,
            )
            if not row:
                return False
            rest = _state.restaurants_cache.setdefault(rid, {})
            rest["settings"] = json.loads(row["settings"]) if row["settings"] else {}
            rest["status"] = row["status"] or "trial"
            rest["trial_ends_at"] = row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None
            return True
    except Exception as e:
        logger.error(f"_refresh_rest_from_db failed for {rid[:8]}: {e}")
        return False


async def _refresh_all_restaurants_from_db():
    """Bulk refresh of trial_ends_at + settings + status for all known restaurants.
    Called periodically by the background loop to catch out-of-band DB changes
    within ~30s of them happening."""
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, settings, status, trial_ends_at FROM restaurants")
            for row in rows:
                rid = str(row["id"])
                if rid not in _state.restaurants_cache:
                    continue  # only refresh restos already loaded; new ones come via load_all
                rest = _state.restaurants_cache[rid]
                rest["settings"] = json.loads(row["settings"]) if row["settings"] else {}
                rest["status"] = row["status"] or "trial"
                rest["trial_ends_at"] = row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None
    except Exception as e:
        logger.error(f"_refresh_all_restaurants_from_db failed: {e}")


def save_message(rid: str, customer_phone: str, role: str, content: str, sender_type=None):
    key = f"{rid}:{customer_phone}"
    if key not in _state.conversations:
        _state.conversations[key] = []
    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if sender_type:
        msg["sender_type"] = sender_type
    _state.conversations[key].append(msg)
    _state.conversations[key] = _state.conversations[key][-30:]
    bump_version(rid)
    # Persist async
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_save_conversation(rid, customer_phone, _state.conversations[key]))
    except Exception:
        pass

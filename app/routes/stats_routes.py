# app/routes/stats_routes.py — /api/daily, /api/stats/ai-kpis, /api/stats/history

from datetime import date, timedelta
from fastapi import APIRouter, Request, Response

import app.state as _state
from app.auth import get_auth
from app.utils.text_utils import sanitize_input
from app.utils.date_utils import today_paris
from app.services.db_helpers import db_save_restaurant_status, bump_version

router = APIRouter()


@router.get("/api/daily")
async def api_get_daily(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    status = _state.restaurant_status.get(rid, {})
    return {"message": status.get("daily_message", "")}


@router.post("/api/daily")
async def api_set_daily(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = _state.restaurant_status.setdefault(rid, {})
    status["daily_message"] = sanitize_input(data.get("message", ""), 1000)
    await db_save_restaurant_status(rid, status)
    rest = _state.restaurants_cache.get(rid)
    if rest:
        rest.setdefault("settings", {})["special_info"] = data.get("message", "")
    bump_version(rid)
    return {"status": "ok"}


@router.get("/api/stats/ai-kpis")
async def api_stats_ai_kpis(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    cutoff = (today_paris() - timedelta(days=30)).isoformat()

    rid_convs = {k: v for k, v in _state.conversations.items() if k.startswith(rid + ":")}
    total_convs = len(rid_convs)
    rid_bookings = _state.bookings.get(rid, [])
    booking_phones = {b.get("phone") for b in rid_bookings if b.get("source") == "whatsapp" and (b.get("date") or "") >= cutoff}
    conv_phones_with_booking = 0
    for conv_key in rid_convs:
        phone = conv_key.split(":", 1)[1] if ":" in conv_key else ""
        if phone in booking_phones:
            conv_phones_with_booking += 1
    conversion_rate = round((conv_phones_with_booking / total_convs * 100), 1) if total_convs > 0 else 0

    total_user_msgs = 0
    total_ai_msgs = 0
    for msgs in rid_convs.values():
        for m in msgs:
            ts = m.get("timestamp", "")
            if ts and ts < cutoff:
                continue
            if m.get("role") == "user":
                total_user_msgs += 1
            elif m.get("role") == "assistant" and m.get("sender_type") != "human":
                total_ai_msgs += 1
    ai_response_rate = round((total_ai_msgs / total_user_msgs * 100), 1) if total_user_msgs > 0 else 0

    rq = _state.review_queue.get(rid, [])
    reviews_sent = sum(1 for r in rq if r.get("sent"))
    reviews_responded = sum(1 for r in rq if r.get("responded"))
    reviews_positive = sum(1 for r in rq if r.get("sentiment") == "POSITIVE")

    return {
        "period_days": 30,
        "total_conversations": total_convs,
        "conversations_with_booking": conv_phones_with_booking,
        "conversion_rate": conversion_rate,
        "total_user_messages": total_user_msgs,
        "total_ai_responses": total_ai_msgs,
        "ai_response_rate": ai_response_rate,
        "reviews_sent": reviews_sent,
        "reviews_responded": reviews_responded,
        "reviews_positive": reviews_positive,
    }


@router.get("/api/stats/history")
async def api_stats_history(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_bookings = _state.bookings.get(rid, [])
    today_str = today_paris().isoformat()
    tomorrow_str = (today_paris() + timedelta(days=1)).isoformat()
    today_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(today_str)]
    tomorrow_bookings = [b for b in rid_bookings if (b.get("date") or "").startswith(tomorrow_str)]
    total_tables = len(_state.floor_tables.get(rid, []))
    occupied = len([b for b in today_bookings if b.get("table")])
    st = _state.stats.get(rid, {})
    rid_contacts = _state.contacts.get(rid, {})
    rq = _state.review_queue.get(rid, [])
    sources = {}
    for b in today_bookings:
        s = b.get("source", "autre")
        sources[s] = sources.get(s, 0) + 1
    today_data = {
        "date": today_str,
        "bookings": len(today_bookings),
        "covers": sum(b.get("covers", 0) for b in today_bookings),
        "messages": st.get("messages_today", 0),
        "tables_total": total_tables,
        "tables_occupied": occupied,
        "occ_rate": round(occupied / total_tables * 100) if total_tables else 0,
        "sources": sources,
        "tomorrow_bookings": len(tomorrow_bookings),
        "tomorrow_covers": sum(b.get("covers", 0) for b in tomorrow_bookings),
        "new_contacts": sum(1 for c in rid_contacts.values() if (c.get("first_seen") or "").startswith(today_str)),
        "pending_reviews": sum(1 for r in rq if not r.get("sent")),
        "cancelled": 0,
    }
    date_from = request.query_params.get("from", (today_paris() - timedelta(days=30)).isoformat())
    date_to = request.query_params.get("to", today_str)
    history = []
    d = date.fromisoformat(date_from)
    end_d = date.fromisoformat(date_to)
    total_bk = 0
    total_covers = 0
    all_sources = {}
    all_zones = {}
    client_visits = {}
    while d <= end_d:
        ds = d.isoformat()
        day_bk = [b for b in rid_bookings if (b.get("date") or "").startswith(ds)]
        day_covers = sum(b.get("covers", 0) for b in day_bk)
        src = {}
        for b in day_bk:
            s = b.get("source", "autre")
            src[s] = src.get(s, 0) + 1
            all_sources[s] = all_sources.get(s, 0) + 1
            z = b.get("zone") or "salle"
            all_zones[z] = all_zones.get(z, 0) + 1
            cn = b.get("name", "")
            if cn:
                client_visits[cn] = client_visits.get(cn, 0) + 1
        total_bk += len(day_bk)
        total_covers += day_covers
        if day_bk:
            history.append({"date": ds, "bookings": len(day_bk), "covers": day_covers, "sources": src})
        d += timedelta(days=1)
    noshow_count = sum(1 for b in rid_bookings if b.get("status") == "noshow" and date_from <= (b.get("date") or "") <= date_to)
    top_clients = sorted(client_visits.items(), key=lambda x: -x[1])[:10]
    avg_ticket = float(_state.restaurants_cache.get(rid, {}).get("settings", {}).get("avg_ticket", 25))
    return {
        "history": history[-90:],
        "today": today_data,
        "period": {
            "from": date_from, "to": date_to,
            "total_bookings": total_bk,
            "total_covers": total_covers,
            "avg_covers_per_day": round(total_covers / max(len(history), 1), 1),
            "sources": all_sources,
            "zones": all_zones,
            "noshow_count": noshow_count,
            "noshow_rate": round(noshow_count / max(total_bk, 1) * 100, 1),
            "top_clients": [{"name": n, "visits": v} for n, v in top_clients],
            "estimated_revenue": round(total_covers * avg_ticket),
            "avg_ticket": avg_ticket,
        }
    }

# app/routes/booking_routes.py — /api/bookings/*, /api/waitlist/*, /api/bookings/export

import csv
import io
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Response

import app.state as _state
from app.config import ALL_SLOTS
from app.auth import get_auth
from app.utils.text_utils import sanitize_input, sanitize_dict
from app.utils.date_utils import today_paris
from app.services.db_helpers import (
    db_save_booking, db_update_waitlist_status, bump_version,
    is_active_or_trial_valid, expired_402,
    assign_table, release_table, find_best_table,
    track_contact, track_stats,
)

router = APIRouter()


@router.get("/api/bookings")
async def api_list_bookings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"bookings": _state.bookings.get(rid, [])[-100:]}


@router.get("/api/bookings/export")
async def api_export_bookings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    date_from = request.query_params.get("from", "")
    date_to = request.query_params.get("to", "9999")
    rid_bookings = _state.bookings.get(rid, [])
    filtered = [b for b in rid_bookings if date_from <= (b.get("date") or "") <= date_to]
    filtered.sort(key=lambda b: (b.get("date", ""), b.get("booking_time", "")))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Heure", "Nom", "Telephone", "Couverts", "Table", "Zone", "Source", "Statut", "Occasion"])
    for b in filtered:
        writer.writerow([
            b.get("date", ""), b.get("booking_time", b.get("time", "")),
            b.get("name", ""), b.get("phone", ""),
            b.get("covers", ""), b.get("table", ""), b.get("zone", ""),
            b.get("source", ""), b.get("status", ""), b.get("occasion", ""),
        ])
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=reservations_export.csv"})


@router.post("/api/bookings/add")
@router.post("/api/bookings/manual")
async def api_add_manual_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    sanitize_dict(data, ["name", "phone", "email", "notes", "zone", "source"], 500)
    rid_bookings = _state.bookings.setdefault(rid, [])
    booking_id = f"R{len(rid_bookings)+1}"
    name = data.get("name", "")
    covers = int(data.get("covers", 2))
    booking_time = data.get("time", "")
    zone = data.get("zone", "")
    source = data.get("source", "phone")
    phone = data.get("phone", "")
    email = data.get("email", "")
    notes = data.get("notes", "")
    requested_table = data.get("table", "")
    booking_date = data.get("date", "") or datetime.utcnow().strftime("%Y-%m-%d")
    assigned_table = None
    if requested_table:
        assigned_table = requested_table
        if booking_time and booking_time in ALL_SLOTS:
            assign_table(rid, booking_time, assigned_table, booking_id)
    elif booking_time and booking_time in ALL_SLOTS:
        assigned_table = find_best_table(rid, booking_time, covers, zone or None)
        if assigned_table:
            assign_table(rid, booking_time, assigned_table, booking_id)
    new_booking = {
        "id": booking_id, "phone": phone, "email": email,
        "name": name or phone or "Client", "message": notes,
        "timestamp": datetime.utcnow().isoformat(), "date": booking_date,
        "status": "confirmed" if assigned_table else "pending",
        "booking_time": booking_time, "time": booking_time,
        "covers": covers, "table": assigned_table, "zone": zone, "source": source,
    }
    rid_bookings.append(new_booking)
    if phone:
        track_contact(rid, phone, name)
        rid_contacts = _state.contacts.get(rid, {})
        if email and phone in rid_contacts:
            rid_contacts[phone]["email"] = email
    track_stats(rid, is_booking=True)
    await db_save_booking(rid, new_booking)
    bump_version(rid)
    return {"status": "created", "booking_id": booking_id, "table": assigned_table}


@router.post("/api/bookings/update")
async def api_update_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    sanitize_dict(data, ["name", "phone"], 500)
    bid = data.get("booking_id", "")
    for b in _state.bookings.get(rid, []):
        if b["id"] == bid:
            if "name" in data:
                b["name"] = data["name"]
            if "covers" in data:
                b["covers"] = int(data["covers"])
            if "time" in data:
                b["booking_time"] = data["time"]
                b["time"] = data["time"]
            if "phone" in data:
                b["phone"] = data["phone"]
            if "date" in data:
                b["date"] = data["date"]
            if "table" in data:
                b["table"] = data["table"] or None
                b["status"] = "confirmed" if data["table"] else "pending"
            if "occasion" in data:
                b["occasion"] = data["occasion"] or None
            await db_save_booking(rid, b)
            bump_version(rid)
            return {"status": "updated"}
    return {"error": "Booking not found"}


@router.post("/api/bookings/delete")
async def api_delete_booking(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    bid = data.get("booking_id", "")
    rid_bookings = _state.bookings.get(rid, [])
    for i, b in enumerate(rid_bookings):
        if b["id"] == bid:
            if b.get("table") and b.get("booking_time"):
                release_table(rid, b["booking_time"], b["table"])
            bt = b.get("booking_time") or b.get("time", "")
            bh = int(bt.split(":")[0]) if bt and ":" in bt else 0
            service = "midi" if bh < 17 else "soir"
            booking_date = b.get("date", "")
            covers = b.get("covers", 0)
            rid_bookings.pop(i)
            bump_version(rid)
            if booking_date:
                # Lazy import to avoid circular dep (notify_next_on_waitlist still in main.py)
                import main as _main
                asyncio.create_task(_main.notify_next_on_waitlist(rid, booking_date, service, bt, covers))
            return {"status": "deleted"}
    return {"error": "Booking not found"}


# ==============================================================
# WAITLIST
# ==============================================================

@router.get("/api/waitlist")
async def api_get_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    wl = _state.waitlist.get(rid, [])
    date_filter = request.query_params.get("date", "")
    if date_filter:
        wl = [w for w in wl if w.get("date") == date_filter]
    return {
        "waitlist": wl,
        "total": len(wl),
        "waiting": len([w for w in wl if w["status"] == "waiting"]),
        "notified": len([w for w in wl if w["status"] == "notified"]),
    }


@router.post("/api/waitlist/add")
async def api_add_to_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    sanitize_dict(data, ["phone", "name"], 200)
    phone = data.get("phone", "")
    name = data.get("name", "")
    covers = int(data.get("covers", 2))
    service = data.get("service", "soir")
    booking_date = data.get("date", today_paris().isoformat())
    booking_time = data.get("time", "")
    if not name:
        return {"error": "Nom requis"}
    # Lazy import — add_to_waitlist still in main.py (depends on WhatsApp send)
    import main as _main
    entry = await _main.add_to_waitlist(rid, phone, name, covers, service, booking_date, booking_time)
    if entry:
        return {"status": "ok", "entry": entry}
    return {"error": "Déjà sur la liste d'attente"}


@router.post("/api/waitlist/remove")
async def api_remove_from_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    entry_id = data.get("id", "")
    wl = _state.waitlist.get(rid, [])
    for w in wl:
        if w["id"] == entry_id:
            w["status"] = "declined"
            await db_update_waitlist_status(rid, entry_id, "declined")
            bump_version(rid)
            return {"status": "removed"}
    return {"error": "Entry not found"}


@router.post("/api/waitlist/notify")
async def api_notify_waitlist(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    booking_date = data.get("date", today_paris().isoformat())
    service = data.get("service", "soir")
    freed_time = data.get("time", "")
    freed_covers = int(data.get("covers", 0))
    # Lazy import — notify_next_on_waitlist still in main.py
    import main as _main
    await _main.notify_next_on_waitlist(rid, booking_date, service, freed_time, freed_covers)
    return {"status": "ok"}

# app/routes/floorplan_routes.py — /api/floorplan/*

from fastapi import APIRouter, Request, Response

import app.state as _state
from app.auth import get_auth
from app.services.db_helpers import (
    bump_version, db_save_restaurant, init_daily_slots,
    assign_table, release_table, get_slot_summary,
)

router = APIRouter()


@router.get("/api/floorplan")
async def api_get_floorplan(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {
        "tables": _state.floor_tables.get(rid, []),
        "slots": _state.table_slots.get(rid, {}),
        "bookings": _state.bookings.get(rid, [])[-100:],
        "slot_summary": get_slot_summary(rid),
        "statuses": _state.table_statuses.get(rid, {}),
        "groups": _state.table_groups.get(rid, []),
    }


@router.post("/api/floorplan/assign")
async def api_assign_table(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    booking_id = data.get("booking_id")
    table_id = data.get("table_id")
    slot_time = data.get("slot_time")
    if not all([booking_id, table_id, slot_time]):
        return {"error": "Missing fields"}
    for b in _state.bookings.get(rid, []):
        if b.get("id") == booking_id:
            if b.get("table") and b.get("time"):
                release_table(rid, b["time"], b["table"])
            b["table"] = table_id
            b["status"] = "confirmed"
            break
    assign_table(rid, slot_time, table_id, booking_id)
    bump_version(rid)
    return {"status": "assigned"}


@router.post("/api/floorplan/release")
async def api_release_table(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    booking_id = data.get("booking_id")
    for b in _state.bookings.get(rid, []):
        if b.get("id") == booking_id and b.get("table") and b.get("time"):
            release_table(rid, b["time"], b["table"])
            b["table"] = None
            b["status"] = "pending"
            break
    bump_version(rid)
    return {"status": "released"}


@router.post("/api/floorplan/status")
async def api_table_status(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    table_id = data.get("table_id", "")
    status = data.get("status", "available")
    date_val = data.get("date", "")
    service = data.get("service", "soir")
    if status not in ("available", "reserved", "seated", "dessert", "done", "noshow"):
        return {"error": "Invalid status"}
    key = f"{date_val}:{service}:{table_id}"
    _state.table_statuses.setdefault(rid, {})[key] = status
    bump_version(rid)
    return {"status": "ok"}


@router.post("/api/floorplan/group")
async def api_table_group(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    tables = data.get("tables", [])
    name = data.get("name", "+".join(tables))
    if len(tables) < 2:
        return {"error": "Need at least 2 tables"}
    groups = _state.table_groups.setdefault(rid, [])
    groups.append({"tables": tables, "name": name})
    bump_version(rid)
    return {"status": "ok", "groups": groups}


@router.post("/api/floorplan/ungroup")
async def api_table_ungroup(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    name = data.get("name", "")
    groups = _state.table_groups.get(rid, [])
    _state.table_groups[rid] = [g for g in groups if g.get("name") != name]
    bump_version(rid)
    return {"status": "ok", "groups": _state.table_groups[rid]}


@router.post("/api/floorplan/setup")
async def api_floorplan_setup(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    tables_data = data.get("tables", [])
    zones = data.get("zones", ["Salle"])
    groups_data = data.get("groups", [])
    services = data.get("services", {})

    zone_list = list(set(t.get("zone", "Salle") for t in tables_data)) or zones
    new_tables = []
    for zi, zone in enumerate(zone_list):
        zone_tables = [t for t in tables_data if t.get("zone") == zone]
        cols = max(2, int(len(zone_tables) ** 0.5) + 1)
        x_start = (zi / len(zone_list)) * 80 + 10
        x_range = 80 / len(zone_list) - 5
        for ti, t in enumerate(zone_tables):
            row = ti // cols
            col = ti % cols
            new_tables.append({
                "id": t.get("id", f"T{len(new_tables)+1}"),
                "seats": int(t.get("capacity", t.get("seats", 4))),
                "shape": "round" if int(t.get("capacity", t.get("seats", 4))) <= 4 else "rect",
                "zone": zone.lower(),
                "x": round(x_start + (col / max(cols-1, 1)) * x_range, 1),
                "y": round(15 + (row / max(3, 1)) * 65, 1),
            })

    _state.floor_tables[rid] = new_tables
    rest = _state.restaurants_cache.get(rid)
    if rest:
        rest["floor_tables"] = new_tables
        if services:
            rest.setdefault("settings", {})["services"] = services
    _state.table_groups[rid] = groups_data
    init_daily_slots(rid)
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "ok", "tables": new_tables}

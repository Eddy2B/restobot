# app/routes/config_routes.py — /api/config, /api/menu, /api/settings

import json
import logging

import httpx
from fastapi import APIRouter, Request, Response

import app.state as _state
from app.config import ANTHROPIC_API_KEY
from app.auth import get_auth
from app.utils.text_utils import sanitize_input, sanitize_dict
from app.services.db_helpers import (
    db_save_restaurant, db_save_restaurant_status, bump_version, init_daily_slots,
)

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.get("/api/config")
async def api_get_config(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "No restaurant"}
    ctx = rest.get("settings", {})
    return {
        "name": rest.get("name", ""),
        "description": ctx.get("description", ""),
        "menu": ctx.get("menu", ""),
        "hours": ctx.get("hours", ""),
        "address": ctx.get("address", ""),
        "phone": ctx.get("phone", ""),
        "tone": ctx.get("tone", ""),
        "languages": ctx.get("languages", ""),
        "special_info": ctx.get("special_info", ""),
        "booking_link": ctx.get("booking_link", ""),
        "allergens_policy": ctx.get("allergens_policy", ""),
        "google_review_link": rest.get("google_review_link", ctx.get("google_review_link", "")),
        "tables": _state.floor_tables.get(rid, []),
    }


@router.post("/api/config")
async def api_update_config(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "No restaurant"}
    ctx = rest.setdefault("settings", {})
    text_fields = ["description", "menu", "hours", "address", "phone", "tone", "languages", "special_info", "booking_link", "allergens_policy", "google_review_link", "avg_ticket"]
    sanitize_dict(data, text_fields + ["name"], 2000)
    for field in text_fields:
        if field in data:
            ctx[field] = data[field]
    if "name" in data:
        rest["name"] = data["name"]
    if "tables" in data:
        _state.floor_tables[rid] = data["tables"]
        rest["floor_tables"] = data["tables"]
        init_daily_slots(rid)
    logger.info(f"Config updated for {rest['name']}: {list(data.keys())}")
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "updated"}


@router.get("/api/menu")
async def api_get_menu(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"sections": []}
    ctx = rest.get("settings", {})
    return {"sections": ctx.get("menu_sections", [])}


@router.post("/api/menu")
async def api_save_menu(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    rest = _state.restaurants_cache.get(rid)
    if not rest:
        return {"error": "No restaurant"}
    sections = data.get("sections", [])
    for sec in sections:
        if isinstance(sec.get("title"), str):
            sec["title"] = sanitize_input(sec["title"], 200)
        for item in sec.get("items", []):
            for k in ("name", "description", "price"):
                if isinstance(item.get(k), str):
                    item[k] = sanitize_input(item[k], 500)
    ctx = rest.setdefault("settings", {})
    ctx["menu_sections"] = sections
    text_lines = []
    for sec in sections:
        text_lines.append(f"\n--- {sec.get('title', '')} ---")
        for item in sec.get("items", []):
            line = f"- {item.get('name', '')}"
            if item.get("description"):
                line += f" : {item['description']}"
            if item.get("price"):
                line += f" ({item['price']})"
            text_lines.append(line)
    ctx["menu"] = "\n".join(text_lines)
    await db_save_restaurant(rid, rest)
    bump_version(rid)
    return {"status": "ok"}


@router.post("/api/menu/scan")
async def api_scan_menu(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    data = await request.json()
    image_b64 = data.get("image", "")
    media_type = data.get("media_type", "image/jpeg")
    if not image_b64:
        return {"error": "No image provided"}
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,")[1]
    if not ANTHROPIC_API_KEY:
        return {"error": "No API key"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514", "max_tokens": 4000,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                        {"type": "text", "text": 'Transcris ce menu de restaurant en JSON. Retourne UNIQUEMENT du JSON valide sans backticks. Format: {"sections": [{"title": "Entrees", "items": [{"name": "Salade Cesar", "description": "Romaine, parmesan", "price": "12"}]}]}. Identifie les sections (Entrees, Plats, Desserts, Boissons, Vins etc). Pour chaque plat: nom, description si visible, prix sans symbole euro. Garde l orthographe exacte.'}
                    ]}]
                }
            )
            result = resp.json()
            text = result.get("content", [{}])[0].get("text", "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            sections = json.loads(text).get("sections", [])
            return {"sections": sections}
    except Exception as e:
        logger.error(f"Menu scan error: {e}")
        return {"error": str(e), "sections": []}


@router.get("/api/settings")
async def api_get_settings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    set_key = request.query_params.get("set", "")
    set_val = request.query_params.get("value", "")
    if set_key:
        status = _state.restaurant_status.setdefault(rid, {})
        status[set_key] = set_val
        await db_save_restaurant_status(rid, status)
        return {"status": "ok"}
    status = _state.restaurant_status.get(rid, {})
    rest = _state.restaurants_cache.get(rid, {})
    settings = rest.get("settings") or {}
    return {
        "pages": status.get("dashboard_pages", {
            "floorplan": True, "bookings": True, "conversations": True,
            "reviews": True, "contacts": True, "dashboard": True,
        }),
        "onboarding_done": status.get("onboarding_done", "0"),
        "reminders_enabled": settings.get("reminders_enabled", True),
    }


@router.post("/api/settings")
async def api_update_settings(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    status = _state.restaurant_status.setdefault(rid, {})
    if "pages" in data:
        status["dashboard_pages"] = data.get("pages", {})
    if "reminders_enabled" in data:
        rest = _state.restaurants_cache.get(rid)
        if rest:
            rest.setdefault("settings", {})["reminders_enabled"] = data["reminders_enabled"]
        status["reminders_enabled"] = data["reminders_enabled"]
    await db_save_restaurant_status(rid, status)
    return {"status": "updated"}

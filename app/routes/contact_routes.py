# app/routes/contact_routes.py — /api/contacts/*, /api/contacts/export, GDPR delete

import csv
import io
import logging

from fastapi import APIRouter, Request, Response

import app.state as _state
from app.auth import get_auth
from app.utils.text_utils import sanitize_input
from app.utils.date_utils import now_paris
from app.services.db_helpers import (
    db_save_contact, bump_version, is_active_or_trial_valid, expired_402,
)

logger = logging.getLogger("guestscale")
router = APIRouter()


@router.get("/api/contacts/export")
async def api_export_contacts(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_contacts = _state.contacts.get(rid, {})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Nom", "Telephone", "Email", "Source", "Visites", "Tags", "Preferences", "Notes", "Langue"])
    for c in sorted(rid_contacts.values(), key=lambda c: c.get("name", "")):
        writer.writerow([
            c.get("name", ""), c.get("phone", ""), c.get("email", ""),
            c.get("source", ""), c.get("visits", 0),
            ", ".join(c.get("tags", [])), c.get("preferences", ""),
            c.get("notes", ""), c.get("language", ""),
        ])
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=contacts_export.csv"})


@router.get("/api/contacts/search")
async def api_search_contacts(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    q = (request.query_params.get("q") or "").strip().lower()
    if len(q) < 2:
        return {"results": []}
    rid_contacts = _state.contacts.get(rid, {})
    results = []
    for phone, ct in rid_contacts.items():
        name = (ct.get("name") or "").lower()
        email = (ct.get("email") or "").lower()
        if q in name or q in email or q in phone:
            results.append(ct)
        if len(results) >= 5:
            break
    return {"results": results}


@router.get("/api/contacts")
async def api_get_contacts(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_contacts = _state.contacts.get(rid, {})
    contact_list = sorted(rid_contacts.values(), key=lambda c: c.get("last_seen", ""), reverse=True)
    return {"contacts": contact_list[:200], "total": len(rid_contacts)}


@router.post("/api/contacts/tag")
async def api_tag_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    phone = data.get("phone")
    tag = sanitize_input(data.get("tag", ""), 100)
    tags_list = [sanitize_input(t, 100) for t in data.get("tags", []) if isinstance(t, str)]
    rid_contacts = _state.contacts.get(rid, {})
    if phone in rid_contacts:
        if tags_list:
            rid_contacts[phone]["tags"] = tags_list
        elif tag:
            if tag not in rid_contacts[phone].get("tags", []):
                rid_contacts[phone].setdefault("tags", []).append(tag)
        await db_save_contact(rid, phone, rid_contacts[phone])
    return {"status": "ok"}


@router.post("/api/contacts/note")
async def api_note_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    phone = data.get("phone")
    note_text = sanitize_input(data.get("note", ""), 2000)
    rid_contacts = _state.contacts.get(rid, {})
    if phone in rid_contacts:
        ct = rid_contacts[phone]
        existing = ct.get("notes", "")
        if isinstance(existing, str):
            ct["notes"] = [{"text": existing, "date": now_paris().isoformat()}] if existing else []
        if note_text:
            ct["notes"].append({"text": note_text, "date": now_paris().isoformat()})
        await db_save_contact(rid, phone, ct)
        bump_version(rid)
    return {"status": "ok"}


@router.post("/api/contacts/preferences")
async def api_preferences_contact(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    if not is_active_or_trial_valid(rid):
        return expired_402()
    data = await request.json()
    phone = data.get("phone")
    preferences = sanitize_input(data.get("preferences", ""), 1000)
    rid_contacts = _state.contacts.get(rid, {})
    if phone in rid_contacts:
        rid_contacts[phone]["preferences"] = preferences
        await db_save_contact(rid, phone, rid_contacts[phone])
    return {"status": "ok"}


@router.delete("/api/contacts/{phone}/gdpr")
async def api_gdpr_delete_contact(request: Request, phone: str):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rid_contacts = _state.contacts.get(rid, {})
    rid_contacts.pop(phone, None)
    rid_bookings = _state.bookings.get(rid, [])
    _state.bookings[rid] = [b for b in rid_bookings if b.get("phone") != phone]
    conv_key = f"{rid}:{phone}"
    _state.conversations.pop(conv_key, None)
    if _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                await conn.execute("DELETE FROM mt_contacts WHERE restaurant_id = $1::uuid AND data->>'phone' = $2", rid, phone)
                await conn.execute("DELETE FROM mt_bookings WHERE restaurant_id = $1::uuid AND data->>'phone' = $2", rid, phone)
                await conn.execute("DELETE FROM mt_conversations WHERE restaurant_id = $1::uuid AND phone = $2", rid, phone)
        except Exception as e:
            logger.error(f"GDPR deletion error: {e}")
    bump_version(rid)
    logger.info(f"GDPR deletion: contact {phone} from restaurant {rid[:8]}...")
    return {"status": "ok", "message": "Données du contact supprimées"}

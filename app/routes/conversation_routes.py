# app/routes/conversation_routes.py — /api/conversations, escalations, missed-calls

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

import app.state as _state
from app.auth import get_auth
from app.utils.text_utils import sanitize_input
from app.utils.date_utils import now_paris
from app.services.db_helpers import save_message, bump_version
from app.services.whatsapp_service import send_whatsapp_message

router = APIRouter()


@router.post("/api/conversations/send")
async def api_send_manual_message(request: Request):
    """Send a manual WhatsApp message from the restaurateur to a client."""
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone", "")
    message = sanitize_input(data.get("message", ""), 2000)
    if not phone or not message:
        return JSONResponse(status_code=400, content={"error": "Téléphone et message requis"})
    rest = _state.restaurants_cache.get(rid)
    if not rest or not rest.get("whatsapp_phone_number_id") or not rest.get("whatsapp_access_token"):
        return JSONResponse(status_code=400, content={"error": "WhatsApp non configuré"})
    await send_whatsapp_message(rest["whatsapp_phone_number_id"], rest["whatsapp_access_token"], phone, message)
    save_message(rid, phone, "assistant", message, sender_type="human")
    bump_version(rid)
    return {"status": "ok"}


@router.get("/api/escalations")
async def api_escalations(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"escalations": _state.escalations.get(rid, [])}


@router.post("/api/escalations/resolve")
async def api_resolve_escalation(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    data = await request.json()
    phone = data.get("phone", "")
    for e in _state.escalations.get(rid, []):
        if e["phone"] == phone and e["status"] == "open":
            e["status"] = "resolved"
            e["resolved_at"] = now_paris().isoformat()
    _state.ai_paused_conversations.get(rid, {}).pop(phone, None)
    return {"status": "ok"}


@router.get("/api/missed-calls")
async def api_missed_calls(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    return {"calls": [{"phone": p, **v} for p, v in _state.missed_call_tracker.get(rid, {}).items()]}


@router.get("/api/conversations")
async def api_list_conversations(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    result = []
    for k, msgs in sorted(_state.conversations.items(), key=lambda x: x[1][-1]["timestamp"] if x[1] else "", reverse=True):
        if not k.startswith(rid + ":") or not msgs:
            continue
        phone = k.split(":")[1] if ":" in k else k
        result.append({
            "phone": phone,
            "messages": [{"role": m["role"], "content": m["content"], "time": m.get("timestamp", "")[:16].replace("T", " ")} for m in msgs],
            "last_message": msgs[-1]["content"][:200],
            "last_time": msgs[-1].get("timestamp", "")[:16].replace("T", " "),
            "count": len(msgs),
        })
    return {"conversations": result}

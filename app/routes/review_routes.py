# app/routes/review_routes.py — /api/reviews

from fastapi import APIRouter, Request, Response

import app.state as _state
from app.auth import get_auth

router = APIRouter()


@router.get("/api/reviews")
async def api_get_reviews(request: Request):
    auth = get_auth(request)
    if not auth:
        return Response(status_code=401)
    rid = auth["restaurant_id"]
    rq = _state.review_queue.get(rid, [])
    return {
        "queue": rq[-50:],
        "stats": {
            "total": len(rq),
            "sent": sum(1 for r in rq if r.get("sent")),
            "responded": sum(1 for r in rq if r.get("responded")),
            "positive": sum(1 for r in rq if r.get("sentiment") == "POSITIVE"),
            "negative": sum(1 for r in rq if r.get("sentiment") == "NEGATIVE"),
        }
    }

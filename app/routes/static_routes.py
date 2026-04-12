# app/routes/static_routes.py — SPA pages, admin page, health checks
# Dependencies: templates (extracted), stdlib only. No circular imports.

from pathlib import Path
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse

from app.templates.dashboard_legacy import DASHBOARD_HTML
from app.templates.admin_dashboard import ADMIN_DASHBOARD_HTML

router = APIRouter()

DASHBOARD_DIR = Path(__file__).parents[2] / "guestscale-dashboard" / "dist"


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    if not request.query_params.get("k"):
        return Response(status_code=404, content="Not found")
    return HTMLResponse(ADMIN_DASHBOARD_HTML)


@router.get("/")
async def root():
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/dashboard/{slug}", response_class=HTMLResponse)
async def dashboard_page(request: Request, slug: str = ""):
    index_file = DASHBOARD_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file, media_type="text/html")
    return HTMLResponse(DASHBOARD_HTML)


@router.get("/health")
@router.get("/api/health")
async def health():
    return {"status": "ok"}


@router.get("/api/info")
async def api_info():
    return {"version": "1.0.0", "name": "GuestScale API"}

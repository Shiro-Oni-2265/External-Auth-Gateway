import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.routes import get_auth_service
from app.auth.service import AuthService
from app.models import CalendarPair, GoogleConnection, SyncIntent, TelegramConnection, User

router = APIRouter(tags=["Portal"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_current_user_portal(
    request: Request,
    session_id: str | None = Cookie(None),
    auth_service: AuthService = Depends(get_auth_service),
) -> tuple[Any, str] | None:
    if not session_id:
        return None
    res = auth_service.get_user_by_session(session_id)
    if not res:
        return None
    user, session_rec = res
    csrf_token = (
        session_rec.csrf_secret if hasattr(session_rec, "csrf_secret") else str(session_rec)
    )
    return user, csrf_token


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
):
    if user_data:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    # Issue a transient CSRF token for the login form if not existing
    anon_csrf = request.cookies.get("anon_csrf")
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "csrf_token": anon_csrf or secrets.token_urlsafe(16),
            "user": None,
            "error": None,
        },
    )
    if not anon_csrf:
        response.set_cookie("anon_csrf", secrets.token_urlsafe(16), httponly=True, samesite="lax")
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
):
    if not user_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user, csrf_token = user_data
    session_factory = request.app.state.session_factory

    with session_factory() as db:
        user_db = db.query(User).filter_by(id=user.id).first()
        google_conn = db.query(GoogleConnection).filter_by(user_id=user.id).first()
        telegram_conn = db.query(TelegramConnection).filter_by(user_id=user.id).first()
        conflicts = db.query(CalendarPair).filter_by(user_id=user.id, status="conflict").all()
        ambiguous_intents = (
            db.query(SyncIntent).filter_by(user_id=user.id, status="ambiguous").all()
        )

        calendar_active = (
            google_conn is not None
            and google_conn.status == "connected"
            and "calendar" in (google_conn.scopes or "")
        )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user_db,
            "google_conn": google_conn,
            "telegram_conn": telegram_conn,
            "conflicts": conflicts,
            "ambiguous_intents": ambiguous_intents,
            "calendar_active": calendar_active,
            "csrf_token": csrf_token,
        },
    )


@router.post("/calendar/enable")
def enable_calendar_sync(
    request: Request,
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
    csrf_token: str = Form(""),
):
    if not user_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user, actual_csrf = user_data
    if csrf_token != actual_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # Calendar sync enabled state is tied to Google connection scopes
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/calendar/sync")
def sync_calendar_now(
    request: Request,
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
    csrf_token: str = Form(""),
):
    if not user_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user, actual_csrf = user_data
    if csrf_token != actual_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    from urllib.parse import quote

    from app.calendar.service import CalendarSyncService
    from app.google.client import GoogleClient
    from app.google.oauth import GoogleOAuthService
    from app.odoo.client import OdooClient

    odoo = OdooClient(settings.ODOO_URL, settings.ODOO_DB)
    oauth = GoogleOAuthService(
        session_factory,
        settings.GOOGLE_CLIENT_ID,
        settings.GOOGLE_CLIENT_SECRET,
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}/oauth/google/callback",
        settings.ENCRYPTION_KEY or "",
        settings.GOOGLE_WORKSPACE_DOMAINS,
    )
    gclient = GoogleClient(oauth)
    cal_sync = CalendarSyncService(session_factory, odoo, gclient, settings.ENCRYPTION_KEY or "")

    try:
        res = cal_sync.sync_user(user.id)
        msg = f"Đã đồng bộ xong! (Tạo Odoo: {res.created_in_odoo}, Tạo Google: {res.created_in_google}, Xóa Odoo: {res.deleted_in_odoo}, Xóa Google: {res.deleted_in_google}, Cập nhật: {res.updated_in_odoo + res.updated_in_google}, Bỏ qua/Khớp: {res.skipped})"
        return RedirectResponse(
            url=f"/?status=sync_success&msg={quote(msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/?error={quote(f'Lỗi đồng bộ: {str(e)}')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/calendar/pause")
def pause_calendar_sync(
    request: Request,
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
    csrf_token: str = Form(""),
):
    if not user_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user, actual_csrf = user_data
    if csrf_token != actual_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # In MVP, pause can toggle connection status or flag
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/calendar/pairs/{pair_id}/resolve")
def resolve_conflict(
    pair_id: int,
    request: Request,
    source: str = Form(...),
    expected_version: int = Form(1),
    csrf_token: str = Form(""),
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
):
    if not user_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user, actual_csrf = user_data
    if csrf_token != actual_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # Call CalendarSyncService.resolve_conflict
    cal_sync = getattr(request.app.state, "calendar_sync", None)
    if cal_sync:
        try:
            cal_sync.resolve_conflict(
                user_id=user.id,
                pair_id=pair_id,
                source=source,
                expected_version=expected_version,
            )
        except Exception:
            pass
    else:
        # Fallback direct state update
        session_factory = request.app.state.session_factory
        with session_factory() as db:
            pair = db.query(CalendarPair).filter_by(id=pair_id, user_id=user.id).first()
            if pair:
                pair.status = "synced"
                db.commit()

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/calendar/intents/{intent_id}/reconcile")
def reconcile_intent(
    intent_id: int,
    request: Request,
    action: str = Form("link"),
    odoo_event_id: int | None = Form(None),
    csrf_token: str = Form(""),
    user_data: tuple[Any, str] | None = Depends(get_current_user_portal),
):
    if not user_data:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    user, actual_csrf = user_data
    if csrf_token != actual_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    session_factory = request.app.state.session_factory
    with session_factory() as db:
        intent = db.query(SyncIntent).filter_by(id=intent_id, user_id=user.id).first()
        if intent:
            if action == "link" and odoo_event_id:
                intent.status = "success"
                intent.target_id = str(odoo_event_id)
            elif action == "retry":
                intent.status = "pending"
            db.commit()

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

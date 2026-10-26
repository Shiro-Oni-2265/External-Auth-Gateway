import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.google.client import GoogleClient
from app.google.oauth import GoogleOAuthService
from app.internal.security import verify_agent_secret
from app.models import TelegramConnection, User

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal Integration"],
    dependencies=[Depends(verify_agent_secret)],
)


class IdentityResolveRequest(BaseModel):
    telegram_user_id: int
    chat_id: int


def _get_google_client(request: Request) -> GoogleClient:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    redirect_uri = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/oauth/google/callback"
    oauth_service = GoogleOAuthService(
        session_factory=session_factory,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        redirect_uri=redirect_uri,
        encryption_key=settings.ENCRYPTION_KEY or "",
        allowed_domains=settings.GOOGLE_WORKSPACE_DOMAINS,
    )
    return GoogleClient(oauth_service)


@router.post("/identity/resolve")
def resolve_identity(
    req: IdentityResolveRequest,
    request: Request,
):
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        # Check matching active connection in private chat
        conn = (
            db.query(TelegramConnection)
            .filter_by(
                telegram_user_id=req.telegram_user_id,
                private_chat_id=req.chat_id,
                status="connected",
            )
            .first()
        )
        if not conn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_CONNECTED", "message": "Telegram connection not found"},
            )

        user = db.query(User).filter_by(id=conn.user_id).first()
        if not user or not user.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "INACTIVE_USER", "message": "User account is disabled or missing"},
            )

        return {
            "status": "OK",
            "user_id": user.id,
            "tenant": user.tenant,
            "database": user.database,
            "odoo_uid": user.odoo_uid,
            "name": user.name,
            "login": user.login,
            "tz": user.tz or "Asia/Bangkok",
        }


@router.get("/users/{user_id}/connections")
def get_user_connections(
    user_id: int,
    request: Request,
):
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found or inactive"},
            )

        tele_conn = user.telegram_connection
        google_conn = user.google_connection

        tele_status = "OK" if (tele_conn and tele_conn.status == "connected") else "NOT_CONNECTED"
        if tele_conn and tele_conn.status == "blocked":
            tele_status = "BLOCKED"

        g_status = "NOT_CONNECTED"
        g_scopes: list[str] = []
        if google_conn:
            if google_conn.status == "connected":
                g_status = "OK"
            elif google_conn.status == "reconnect_required":
                g_status = "RECONNECT_REQUIRED"
            else:
                g_status = "NOT_CONNECTED"
            if google_conn.scopes:
                g_scopes = google_conn.scopes.split()

        return {
            "status": "OK",
            "user_id": user.id,
            "odoo": {
                "status": "OK",
                "login": user.login,
                "odoo_uid": user.odoo_uid,
            },
            "telegram": {
                "status": tele_status,
                "telegram_user_id": tele_conn.telegram_user_id if tele_conn else None,
                "username": tele_conn.username if tele_conn else None,
            },
            "google": {
                "status": g_status,
                "email": google_conn.email if google_conn else None,
                "scopes": g_scopes,
            },
        }


@router.get("/users/{user_id}/calendar/events")
def get_calendar_events(
    user_id: int,
    request: Request,
    time_min: str = Query(..., description="Start ISO datetime"),
    time_max: str = Query(..., description="End ISO datetime"),
):
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found or inactive"},
            )
        g_conn = user.google_connection

    if not g_conn or g_conn.status == "disconnected":
        return {"status": "NOT_CONNECTED", "events": []}
    if g_conn.status == "reconnect_required":
        return {"status": "RECONNECT_REQUIRED", "events": []}

    # Check scopes
    scopes = g_conn.scopes.split() if g_conn.scopes else []
    has_cal = any("calendar" in s for s in scopes)
    if not has_cal:
        return {
            "status": "PERMISSION_DENIED",
            "events": [],
            "instruction": "Vui lòng cấp quyền Google Calendar.",
        }

    client = _get_google_client(request)
    try:
        data = client.list_calendar_events(
            user_id=user_id,
            calendar_id="primary",
            time_min=time_min,
            time_max=time_max,
            single_events=True,
        )
        items = data.get("items", [])
        events = []
        for it in items:
            start_info = it.get("start", {})
            end_info = it.get("end", {})
            is_all_day = "date" in start_info and "dateTime" not in start_info
            events.append(
                {
                    "id": it.get("id"),
                    "summary": it.get("summary") or "(Không có tiêu đề)",
                    "description": it.get("description") or "",
                    "start": start_info.get("dateTime") or start_info.get("date"),
                    "end": end_info.get("dateTime") or end_info.get("date"),
                    "is_all_day": is_all_day,
                    "location": it.get("location") or "",
                    "link": it.get("htmlLink") or "",
                }
            )
        return {"status": "OK", "events": events}
    except Exception as e:
        from app.google.oauth import GoogleAuthRevokedError

        if isinstance(e, GoogleAuthRevokedError):
            return {"status": "RECONNECT_REQUIRED", "events": []}
        import httpx

        if isinstance(e, httpx.TimeoutException):
            return {"status": "TIMEOUT", "events": []}
        return {"status": "ERROR", "events": [], "message": str(e)}


@router.get("/users/{user_id}/gmail/unread")
def get_unread_emails(
    user_id: int,
    request: Request,
):
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found or inactive"},
            )

    client = _get_google_client(request)
    return client.list_unread_emails(user_id=user_id, max_results=5)

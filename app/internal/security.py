import secrets

from fastapi import Header, HTTPException, Request, status

from app.config import Settings


def verify_agent_secret(
    request: Request,
    authorization: str | None = Header(None),
) -> bool:
    settings: Settings = request.app.state.settings
    expected_secret = settings.AGENT_TO_GATEWAY_SECRET
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "MISCONFIGURED",
                "message": "AGENT_TO_GATEWAY_SECRET is not configured",
            },
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Missing or invalid Bearer token"},
        )

    token = authorization.split("Bearer ", 1)[1].strip()
    if not secrets.compare_digest(token, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid agent secret"},
        )

    return True

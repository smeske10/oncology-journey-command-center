from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService
from app.config import settings

SESSION_COOKIE_NAME = "ojcc_session"


def get_current_demo_session_service() -> DemoSessionService:
    try:
        return DemoSessionService(
            actor_repository=None,
            secret=settings.demo_session_secret,
            ttl_minutes=settings.demo_session_ttl_minutes,
            organization_id=None,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo sessions are not configured",
        ) from error


def current_actor(
    request: Request,
    session_service: DemoSessionService = Depends(get_current_demo_session_service),
) -> CurrentActor:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    try:
        return session_service.current_actor(token)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired demo session",
        ) from error


def require_role(*allowed: Role) -> Callable[[CurrentActor], CurrentActor]:
    def dependency(actor: CurrentActor = Depends(current_actor)) -> CurrentActor:
        if actor.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role not permitted")
        return actor

    return dependency

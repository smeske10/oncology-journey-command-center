from datetime import datetime
from typing import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.auth.authority import authority_from_row, build_authority_statement
from app.auth.models import CurrentActor, Role
from app.auth.service import (
    MAX_SESSION_LIFETIME_SECONDS,
    DemoSessionService,
    SqlAlchemyActorRepository,
)
from app.config import settings
from app.db.session import get_session

SESSION_COOKIE_NAME = "ojcc_session"


async def resolve_patient_actor(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    at: datetime,
) -> CurrentActor:
    """Resolve patient authority through the shared cardinality-safe envelope."""
    row = (
        await session.execute(
            build_authority_statement(
                organization_id=organization_id,
                user_id=user_id,
                role=Role.SUPPORTING_ACTOR,
                at=at,
            )
        )
    ).one_or_none()
    authority = authority_from_row(
        row,
        organization_id=organization_id,
        user_id=user_id,
        role=Role.SUPPORTING_ACTOR,
    )
    if authority is None:
        raise LookupError("No active patient identity link is available")
    return authority.actor


def get_current_demo_session_service() -> DemoSessionService:
    try:
        ttl_minutes = settings.demo_session_ttl_minutes
        if ttl_minutes is None or not 1 <= ttl_minutes <= MAX_SESSION_LIFETIME_SECONDS // 60:
            raise ValueError("DEMO_SESSION_TTL_MINUTES must be between 1 and 120")
        return DemoSessionService(
            actor_repository=None,
            secret=settings.demo_session_secret,
            ttl_minutes=ttl_minutes,
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
    session: Session = Depends(get_session),
) -> CurrentActor:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    try:
        token_actor = session_service.current_actor(token)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired demo session",
        ) from error
    active_actor = SqlAlchemyActorRepository(session).find_active_actor_for_user(
        organization_id=token_actor.organization_id,
        user_id=token_actor.user_id,
        role=token_actor.role,
    )
    if active_actor is None or active_actor.patient_id != token_actor.patient_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Demo session is no longer authorized",
        )
    return active_actor


def require_role(*allowed: Role) -> Callable[[CurrentActor], CurrentActor]:
    def dependency(actor: CurrentActor = Depends(current_actor)) -> CurrentActor:
        if actor.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role not permitted")
        return actor

    return dependency

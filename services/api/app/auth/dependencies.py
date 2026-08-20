from datetime import datetime
from typing import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService
from app.config import settings
from app.db.models import PatientIdentityLink, RoleAssignment, User

SESSION_COOKIE_NAME = "ojcc_session"


async def resolve_patient_actor(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    at: datetime,
) -> CurrentActor:
    """Resolve the active patient identity link without relying on UUID equality."""
    statement = (
        select(PatientIdentityLink.patient_id)
        .join(User, User.id == PatientIdentityLink.user_id)
        .join(
            RoleAssignment,
            and_(
                RoleAssignment.user_id == User.id,
                RoleAssignment.organization_id == PatientIdentityLink.organization_id,
            ),
        )
        .where(
            PatientIdentityLink.organization_id == organization_id,
            PatientIdentityLink.user_id == user_id,
            PatientIdentityLink.linked_at <= at,
            (PatientIdentityLink.revoked_at.is_(None) | (at < PatientIdentityLink.revoked_at)),
            RoleAssignment.role == Role.SUPPORTING_ACTOR,
            RoleAssignment.granted_at <= at,
            (RoleAssignment.revoked_at.is_(None) | (at < RoleAssignment.revoked_at)),
            User.is_active.is_(True),
        )
    )
    patient_id = (await session.execute(statement)).scalar_one_or_none()
    if patient_id is None:
        raise LookupError("No active patient identity link is available")
    return CurrentActor(
        user_id=user_id,
        organization_id=organization_id,
        role=Role.SUPPORTING_ACTOR,
        patient_id=patient_id,
    )


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

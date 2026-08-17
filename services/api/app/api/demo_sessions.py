from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import SESSION_COOKIE_NAME
from app.auth.models import Role
from app.auth.service import DemoSessionService, SqlAlchemyActorRepository
from app.config import settings
from app.db.session import get_session

router = APIRouter(prefix="/v1/demo", tags=["demo-sessions"])


def get_demo_session_service(
    session: Session = Depends(get_session),
) -> DemoSessionService:
    try:
        return DemoSessionService(
            actor_repository=SqlAlchemyActorRepository(session),
            secret=settings.demo_session_secret,
            ttl_minutes=settings.demo_session_ttl_minutes,
            organization_id=settings.demo_organization_id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo sessions are not configured",
        ) from error


@router.post("/session/{role}", status_code=status.HTTP_204_NO_CONTENT)
def create_demo_session(
    role: Role,
    response: Response,
    session_service: DemoSessionService = Depends(get_demo_session_service),
) -> None:
    try:
        token = session_service.create_session(role)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo actor is unavailable",
        ) from error
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=not settings.is_local_development,
        samesite="lax",
        path="/",
    )

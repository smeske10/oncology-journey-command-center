"""Navigator acknowledgement and resolution command boundaries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.session import get_session
from app.domain.safety import (
    SafetySignalConflict,
    SafetySignalNotFound,
    acknowledge_signal,
    resolve_signal,
)

router = APIRouter(prefix="/v1/navigator/safety-signals", tags=["navigator"])


class AcknowledgementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcknowledgementRead(BaseModel):
    signal_id: UUID
    acknowledged_by_user_id: UUID
    acknowledged_at: datetime
    effective_state: str


class ResolutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution_reason: str = Field(min_length=1, max_length=4000)


class ResolutionRead(BaseModel):
    resolution_id: UUID
    signal_id: UUID
    resolved_by_user_id: UUID
    resolved_at: datetime
    resolution_reason: str
    effective_state: str


@router.post(
    "/{signal_id}/acknowledgements",
    response_model=AcknowledgementRead,
    status_code=status.HTTP_201_CREATED,
)
def post_acknowledgement(
    signal_id: UUID,
    command: AcknowledgementCreate,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> AcknowledgementRead:
    del command
    try:
        result = acknowledge_signal(
            session,
            organization_id=actor.organization_id,
            signal_id=signal_id,
            acknowledged_by_user_id=actor.user_id,
        )
        session.commit()
    except SafetySignalNotFound as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except SafetySignalConflict as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return AcknowledgementRead(**result.__dict__)


@router.post(
    "/{signal_id}/resolutions",
    response_model=ResolutionRead,
    status_code=status.HTTP_201_CREATED,
)
def post_resolution(
    signal_id: UUID,
    command: ResolutionCreate,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> ResolutionRead:
    try:
        result = resolve_signal(
            session,
            organization_id=actor.organization_id,
            signal_id=signal_id,
            resolved_by_user_id=actor.user_id,
            resolution_reason=command.resolution_reason,
        )
        session.commit()
    except SafetySignalNotFound as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except SafetySignalConflict as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return ResolutionRead(**result.__dict__)

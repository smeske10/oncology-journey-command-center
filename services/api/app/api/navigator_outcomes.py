"""Navigator-only outcome preview and closure commands."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.session import get_session
from app.domain.enums import OutcomeDisposition
from app.domain.needs import NeedNotFound
from app.domain.outcomes import OutcomeConflict, preview_outcome, record_outcome

router = APIRouter(prefix="/v1/navigator", tags=["navigator"])


class OutcomePreviewTaskRead(BaseModel):
    id: UUID
    title: str
    status: str


class OutcomePreviewRead(BaseModel):
    need_id: UUID
    tasks: list[OutcomePreviewTaskRead]


class OutcomeCommandCreate(BaseModel):
    disposition: OutcomeDisposition
    note: str | None = Field(default=None, max_length=4000)


class OutcomeCommandRead(BaseModel):
    outcome_id: UUID
    need_id: UUID
    disposition: OutcomeDisposition
    note: str | None
    recorded_by_user_id: UUID
    recorded_at: datetime
    cancelled_task_ids: list[UUID]


@router.get(
    "/needs/{need_id}/outcome-preview",
    response_model=OutcomePreviewRead,
)
def get_outcome_preview(
    need_id: UUID,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> OutcomePreviewRead:
    try:
        preview = preview_outcome(
            session,
            organization_id=actor.organization_id,
            need_id=need_id,
        )
    except NeedNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return OutcomePreviewRead(
        need_id=preview.need_id,
        tasks=[OutcomePreviewTaskRead(**task.__dict__) for task in preview.tasks],
    )


@router.post(
    "/needs/{need_id}/outcomes",
    response_model=OutcomeCommandRead,
    status_code=status.HTTP_201_CREATED,
)
def post_outcome(
    need_id: UUID,
    command: OutcomeCommandCreate,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> OutcomeCommandRead:
    try:
        result = record_outcome(
            session,
            organization_id=actor.organization_id,
            need_id=need_id,
            recorded_by_user_id=actor.user_id,
            disposition=command.disposition,
            note=command.note,
            idempotency_key=idempotency_key,
        )
        session.commit()
    except NeedNotFound as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except OutcomeConflict as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return OutcomeCommandRead(
        outcome_id=result.outcome_id,
        need_id=result.need_id,
        disposition=OutcomeDisposition(result.disposition),
        note=result.note,
        recorded_by_user_id=result.recorded_by_user_id,
        recorded_at=result.recorded_at,
        cancelled_task_ids=list(result.cancelled_task_ids),
    )

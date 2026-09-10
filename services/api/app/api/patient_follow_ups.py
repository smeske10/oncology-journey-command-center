"""Supporting-actor follow-up reads and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.session import get_session
from app.domain.enums import FollowUpResponseValue
from app.domain.follow_ups import (
    FollowUpConflict,
    FollowUpForbidden,
    FollowUpNotFound,
    FollowUpResponseResult,
    PatientFollowUpItem,
    list_patient_follow_ups,
    map_follow_up_database_error,
    record_follow_up_response,
)

router = APIRouter(prefix="/v1/patient/follow-ups", tags=["patient-follow-ups"])


class PatientFollowUpRead(BaseModel):
    request_id: UUID
    need_id: UUID
    task_id: UUID
    requested_at: datetime
    prompt_version: int
    prompt: str
    status: Literal["awaiting_response", "answered", "unavailable_need_closed"]
    response: FollowUpResponseValue | None
    note: str | None
    responded_at: datetime | None


class PatientFollowUpListRead(BaseModel):
    items: list[PatientFollowUpRead]


class FollowUpResponseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response: FollowUpResponseValue
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class FollowUpResponseRead(BaseModel):
    id: UUID
    request_id: UUID
    response: FollowUpResponseValue
    note: str | None
    submitted_at: datetime
    replayed: bool


class FollowUpErrorDetail(BaseModel):
    code: Literal[
        "need_closed",
        "follow_up_already_answered",
        "concurrent_change",
    ]
    message: str


class FollowUpErrorResponse(BaseModel):
    detail: FollowUpErrorDetail


@router.get("", response_model=PatientFollowUpListRead)
def get_patient_follow_ups(
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    session: Session = Depends(get_session),
) -> PatientFollowUpListRead:
    try:
        items = list_patient_follow_ups(
            session,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            patient_id=actor.patient_id,
        )
    except FollowUpForbidden as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return PatientFollowUpListRead(items=[_item_read(item) for item in items])


@router.post(
    "/{request_id}/responses",
    response_model=FollowUpResponseRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": FollowUpResponseRead},
        409: {"model": FollowUpErrorResponse},
    },
)
def post_follow_up_response(
    request_id: UUID,
    command: FollowUpResponseCreate,
    http_response: Response,
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    session: Session = Depends(get_session),
) -> FollowUpResponseRead:
    try:
        result = record_follow_up_response(
            session,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            patient_id=actor.patient_id,
            request_id=request_id,
            response=command.response,
            note=command.note,
        )
        session.commit()
    except FollowUpNotFound as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FollowUpForbidden as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except FollowUpConflict as error:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": str(error)},
        ) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DBAPIError as error:
        session.rollback()
        mapped = map_follow_up_database_error(error)
        if mapped is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": mapped.code, "message": str(mapped)},
            ) from error
        raise
    if result.replayed:
        http_response.status_code = status.HTTP_200_OK
    return _response_read(result)


def _item_read(item: PatientFollowUpItem) -> PatientFollowUpRead:
    return PatientFollowUpRead(**item.__dict__)


def _response_read(result: FollowUpResponseResult) -> FollowUpResponseRead:
    return FollowUpResponseRead(**result.__dict__)

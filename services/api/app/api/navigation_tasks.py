"""Navigator commands for approved task execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Never
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.session import get_session
from app.domain.enums import NavigationTaskStatus
from app.domain.navigation_tasks import (
    TaskCommandResult,
    TaskConflict,
    TaskForbidden,
    TaskNotFound,
    claim_navigation_task,
    complete_navigation_task,
    map_task_database_error,
    start_navigation_task,
)

router = APIRouter(prefix="/v1/navigator/tasks", tags=["navigator"])


class TaskClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposed_change_id: UUID
    due_at: datetime


class TaskCommandRead(BaseModel):
    task_id: UUID
    need_id: UUID
    status: NavigationTaskStatus
    assignee_user_id: UUID | None
    due_at: datetime | None
    authorized_proposed_change_id: UUID | None
    completed_at: datetime | None
    follow_up_request_id: UUID | None
    replayed: bool


class EmptyTaskCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/{task_id}/claim", response_model=TaskCommandRead)
def claim_task(
    task_id: UUID,
    command: TaskClaimCreate,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> TaskCommandRead:
    try:
        result = claim_navigation_task(
            session,
            organization_id=actor.organization_id,
            task_id=task_id,
            actor_user_id=actor.user_id,
            proposed_change_id=command.proposed_change_id,
            due_at=command.due_at,
        )
        session.commit()
    except TaskNotFound as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TaskForbidden as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except TaskConflict as error:
        session.rollback()
        _raise_conflict(error)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DBAPIError as error:
        session.rollback()
        mapped = map_task_database_error(error)
        if isinstance(mapped, TaskForbidden):
            raise HTTPException(status_code=403, detail=str(mapped)) from error
        if isinstance(mapped, TaskConflict):
            _raise_conflict(mapped)
        raise
    return _read(result)


@router.post("/{task_id}/start", response_model=TaskCommandRead)
def start_task(
    task_id: UUID,
    command: EmptyTaskCommand,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> TaskCommandRead:
    del command
    return _run_transition(
        session,
        lambda: start_navigation_task(
            session,
            organization_id=actor.organization_id,
            task_id=task_id,
            actor_user_id=actor.user_id,
        ),
    )


@router.post("/{task_id}/complete", response_model=TaskCommandRead)
def complete_task(
    task_id: UUID,
    command: EmptyTaskCommand,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> TaskCommandRead:
    del command
    return _run_transition(
        session,
        lambda: complete_navigation_task(
            session,
            organization_id=actor.organization_id,
            task_id=task_id,
            actor_user_id=actor.user_id,
        ),
    )


def _run_transition(
    session: Session, command: Callable[[], TaskCommandResult]
) -> TaskCommandRead:
    try:
        result = command()
        session.commit()
    except TaskNotFound as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TaskForbidden as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except TaskConflict as error:
        session.rollback()
        _raise_conflict(error)
    except DBAPIError as error:
        session.rollback()
        mapped = map_task_database_error(error)
        if isinstance(mapped, TaskForbidden):
            raise HTTPException(status_code=403, detail=str(mapped)) from error
        if isinstance(mapped, TaskConflict):
            _raise_conflict(mapped)
        raise
    return _read(result)


def _raise_conflict(error: TaskConflict) -> Never:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": error.code, "message": str(error)},
    ) from error


def _read(result: TaskCommandResult) -> TaskCommandRead:
    return TaskCommandRead(**result.__dict__)

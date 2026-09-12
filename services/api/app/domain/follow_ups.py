"""Patient follow-up reads and immutable one-shot response commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.advisory_locks import acquire_transaction_lock
from app.db.models import (
    FollowUpRequest,
    FollowUpResponse,
    Outcome,
    PatientIdentityLink,
    ReportedNeed,
    RoleAssignment,
    User,
)
from app.db.repositories import SqlAlchemyPatientRepository
from app.domain.enums import FollowUpResponseValue, UserRole
from app.domain.public_demo import validate_public_demo_text

FOLLOW_UP_PROMPT = "Did this navigation support address your reported need?"
FollowUpStatus = Literal["awaiting_response", "answered", "unavailable_need_closed"]


class FollowUpNotFound(LookupError):
    pass


class FollowUpForbidden(PermissionError):
    pass


class FollowUpConflict(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PatientFollowUpItem:
    request_id: UUID
    need_id: UUID
    task_id: UUID
    requested_at: datetime
    prompt_version: int
    prompt: str
    status: FollowUpStatus
    response: FollowUpResponseValue | None
    note: str | None
    responded_at: datetime | None


@dataclass(frozen=True)
class FollowUpResponseResult:
    id: UUID
    request_id: UUID
    response: FollowUpResponseValue
    note: str | None
    submitted_at: datetime
    replayed: bool


def list_patient_follow_ups(
    session: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    patient_id: UUID | None,
) -> tuple[PatientFollowUpItem, ...]:
    patient_id = _require_active_patient(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        patient_id=patient_id,
    )
    repository = SqlAlchemyPatientRepository(session)
    requests = repository.list_follow_up_requests(
        patient_id=patient_id,
        organization_id=organization_id,
    )
    responses = repository.list_follow_up_responses(
        request_ids={request.id for request in requests},
        organization_id=organization_id,
    )
    response_by_request = {
        response.follow_up_request_id: response for response in responses
    }
    outcomes = repository.list_outcomes_for_needs(
        need_ids={request.reported_need_id for request in requests},
        patient_id=patient_id,
        organization_id=organization_id,
    )
    closed_need_ids = {outcome.reported_need_id for outcome in outcomes}
    return tuple(
        _item(
            request,
            response_by_request.get(request.id),
            need_is_closed=request.reported_need_id in closed_need_ids,
        )
        for request in requests
    )


def record_follow_up_response(
    session: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    patient_id: UUID | None,
    request_id: UUID,
    response: str | FollowUpResponseValue,
    note: str | None,
) -> FollowUpResponseResult:
    response_value = FollowUpResponseValue(response)
    normalized_note = _normalize_note(note)
    if patient_id is None:
        raise FollowUpForbidden("Active patient identity link is required")
    preliminary = session.scalar(
        select(FollowUpRequest).where(
            FollowUpRequest.organization_id == organization_id,
            FollowUpRequest.id == request_id,
            FollowUpRequest.patient_id == patient_id,
        )
    )
    if preliminary is None:
        raise FollowUpNotFound("Follow-up request not found")

    acquire_transaction_lock(
        session,
        namespace="reported_need",
        identifier=preliminary.reported_need_id,
    )
    acquire_transaction_lock(
        session,
        namespace="follow_up_request",
        identifier=request_id,
    )
    need = session.scalar(
        select(ReportedNeed)
        .where(
            ReportedNeed.organization_id == organization_id,
            ReportedNeed.patient_id == preliminary.patient_id,
            ReportedNeed.id == preliminary.reported_need_id,
        )
    )
    if need is None:
        raise FollowUpNotFound("Follow-up request not found")
    request = session.scalar(
        select(FollowUpRequest)
        .where(
            FollowUpRequest.organization_id == organization_id,
            FollowUpRequest.id == request_id,
            FollowUpRequest.patient_id == need.patient_id,
        )
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise FollowUpNotFound("Follow-up request not found")

    link = _require_active_patient_link(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        patient_id=patient_id,
    )
    _require_supporting_actor_authority(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
    )
    existing = session.scalar(
        select(FollowUpResponse).where(
            FollowUpResponse.organization_id == organization_id,
            FollowUpResponse.follow_up_request_id == request.id,
        )
    )
    if existing is not None:
        if (
            existing.submitted_by_user_id == actor_user_id
            and existing.response == response_value
            and existing.note == normalized_note
        ):
            return _response_result(existing, replayed=True)
        raise FollowUpConflict(
            "follow_up_already_answered",
            "Follow-up request already has a different response",
        )
    if session.scalar(
        select(Outcome.id).where(
            Outcome.organization_id == organization_id,
            Outcome.patient_id == request.patient_id,
            Outcome.reported_need_id == request.reported_need_id,
        )
    ) is not None:
        raise FollowUpConflict("need_closed", "Reported need is already closed")

    created = FollowUpResponse(
        organization_id=organization_id,
        follow_up_request_id=request.id,
        submitted_by_user_id=actor_user_id,
        patient_identity_link_id=link.id,
        response=response_value,
        note=normalized_note,
        submitted_at=datetime.now(UTC),
    )
    try:
        with session.begin_nested():
            session.add(created)
            session.flush()
    except (IntegrityError, DBAPIError) as error:
        mapped = map_follow_up_database_error(error)
        if mapped is not None:
            raise mapped from error
        raise
    session.refresh(created)
    return _response_result(created, replayed=False)


def _require_active_patient(
    session: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    patient_id: UUID | None,
) -> UUID:
    if patient_id is None:
        raise FollowUpForbidden("Active patient identity link is required")
    link = SqlAlchemyPatientRepository(session).resolve_active_patient_link(
        user_id=actor_user_id,
        patient_id=patient_id,
        organization_id=organization_id,
    )
    if link is None:
        raise FollowUpForbidden("Active patient identity link is required")
    return patient_id


def _require_active_patient_link(
    session: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
    patient_id: UUID | None,
) -> PatientIdentityLink:
    if patient_id is None:
        raise FollowUpForbidden("Active patient identity link is required")
    at = datetime.now(UTC)
    link = session.scalar(
        select(PatientIdentityLink)
        .join(User, User.id == PatientIdentityLink.user_id)
        .where(
            PatientIdentityLink.organization_id == organization_id,
            PatientIdentityLink.user_id == actor_user_id,
            PatientIdentityLink.patient_id == patient_id,
            PatientIdentityLink.linked_at <= at,
            PatientIdentityLink.revoked_at.is_(None)
            | (at < PatientIdentityLink.revoked_at),
            User.is_active.is_(True),
        )
        .order_by(PatientIdentityLink.linked_at.desc(), PatientIdentityLink.id.asc())
        .limit(1)
    )
    if link is None:
        raise FollowUpForbidden("Active patient identity link is required")
    return link


def _require_supporting_actor_authority(
    session: Session,
    *,
    organization_id: UUID,
    actor_user_id: UUID,
) -> RoleAssignment:
    at = datetime.now(UTC)
    assignment = session.scalar(
        select(RoleAssignment)
        .where(
            RoleAssignment.organization_id == organization_id,
            RoleAssignment.user_id == actor_user_id,
            RoleAssignment.role == UserRole.SUPPORTING_ACTOR,
            RoleAssignment.granted_at <= at,
            RoleAssignment.revoked_at.is_(None)
            | (at < RoleAssignment.revoked_at),
        )
        .order_by(RoleAssignment.granted_at.desc(), RoleAssignment.id.asc())
        .limit(1)
    )
    if assignment is None:
        raise FollowUpForbidden("Current supporting actor authority is required")
    return assignment


def _normalize_note(note: str | None) -> str | None:
    normalized = note.strip() if note is not None else None
    normalized = normalized or None
    if normalized is not None and len(normalized) > 2000:
        raise ValueError("Follow-up note must not exceed 2000 characters")
    validate_public_demo_text(normalized)
    return normalized


def _item(
    request: FollowUpRequest,
    response: FollowUpResponse | None,
    *,
    need_is_closed: bool,
) -> PatientFollowUpItem:
    status: FollowUpStatus
    if response is not None:
        status = "answered"
    elif need_is_closed:
        status = "unavailable_need_closed"
    else:
        status = "awaiting_response"
    return PatientFollowUpItem(
        request_id=request.id,
        need_id=request.reported_need_id,
        task_id=request.navigation_task_id,
        requested_at=request.requested_at,
        prompt_version=request.prompt_version,
        prompt=FOLLOW_UP_PROMPT,
        status=status,
        response=response.response if response is not None else None,
        note=response.note if response is not None else None,
        responded_at=response.submitted_at if response is not None else None,
    )


def _response_result(
    response: FollowUpResponse, *, replayed: bool
) -> FollowUpResponseResult:
    return FollowUpResponseResult(
        id=response.id,
        request_id=response.follow_up_request_id,
        response=response.response,
        note=response.note,
        submitted_at=response.submitted_at,
        replayed=replayed,
    )


def map_follow_up_database_error(error: DBAPIError) -> FollowUpConflict | None:
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    if sqlstate in {"40001", "40P01", "55P03"}:
        return FollowUpConflict("concurrent_change", "Concurrent change; retry safely")
    message = str(getattr(error, "orig", error))
    if "after need closure" in message:
        return FollowUpConflict("need_closed", "Reported need is already closed")
    if "organization_request" in message or "already" in message:
        return FollowUpConflict(
            "follow_up_already_answered",
            "Follow-up request already has a response",
        )
    return None

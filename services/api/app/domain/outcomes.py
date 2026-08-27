"""Outcome commands; PostgreSQL remains authoritative for closure side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, NavigationTask, Outcome, ReportedNeed
from app.domain.enums import NavigationTaskStatus, OutcomeDisposition
from app.domain.needs import NeedNotFound

NONTERMINAL_TASK_STATES = (
    NavigationTaskStatus.OPEN,
    NavigationTaskStatus.ASSIGNED,
    NavigationTaskStatus.IN_PROGRESS,
)


class OutcomeConflict(ValueError):
    """The need was closed already or an idempotency key names another command."""


@dataclass(frozen=True)
class OutcomePreviewTask:
    id: UUID
    title: str
    status: str


@dataclass(frozen=True)
class OutcomePreview:
    need_id: UUID
    tasks: tuple[OutcomePreviewTask, ...]


@dataclass(frozen=True)
class OutcomeCommandResult:
    outcome_id: UUID
    need_id: UUID
    disposition: str
    note: str | None
    recorded_by_user_id: UUID
    recorded_at: datetime
    cancelled_task_ids: tuple[UUID, ...]


def preview_outcome(
    session: Session,
    *,
    organization_id: UUID,
    need_id: UUID,
) -> OutcomePreview:
    need = session.scalar(
        select(ReportedNeed.id).where(
            ReportedNeed.organization_id == organization_id,
            ReportedNeed.id == need_id,
        )
    )
    if need is None:
        raise NeedNotFound("Reported need not found")
    tasks = session.scalars(
        select(NavigationTask)
        .where(
            NavigationTask.organization_id == organization_id,
            NavigationTask.reported_need_id == need_id,
            NavigationTask.status.in_(NONTERMINAL_TASK_STATES),
        )
        .order_by(NavigationTask.id)
    ).all()
    return OutcomePreview(
        need_id=need_id,
        tasks=tuple(
            OutcomePreviewTask(id=task.id, title=task.title, status=task.status.value)
            for task in tasks
        ),
    )


def record_outcome(
    session: Session,
    *,
    organization_id: UUID,
    need_id: UUID,
    recorded_by_user_id: UUID,
    disposition: str | OutcomeDisposition,
    note: str | None,
    idempotency_key: str,
) -> OutcomeCommandResult:
    """Insert one Outcome and read trigger-authored cancellation facts for the response."""
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("Idempotency key must not be blank")
    if len(idempotency_key) > 255:
        raise ValueError("Idempotency key must not exceed 255 characters")
    disposition_value = OutcomeDisposition(disposition)

    need = session.scalar(
        select(ReportedNeed)
        .where(
            ReportedNeed.organization_id == organization_id,
            ReportedNeed.id == need_id,
        )
        .with_for_update()
    )
    if need is None:
        raise NeedNotFound("Reported need not found")

    existing_for_key = _outcome_for_key(session, organization_id, idempotency_key)
    if existing_for_key is not None:
        _require_exact_replay(
            existing_for_key,
            need_id=need_id,
            disposition=disposition_value,
            note=note,
        )
        return _command_result(session, existing_for_key)

    existing_for_need = session.scalar(
        select(Outcome).where(
            Outcome.organization_id == organization_id,
            Outcome.reported_need_id == need_id,
        )
    )
    if existing_for_need is not None:
        raise OutcomeConflict("Reported need already has an Outcome")

    outcome = Outcome(
        organization_id=organization_id,
        patient_id=need.patient_id,
        reported_need_id=need.id,
        recorded_by_user_id=recorded_by_user_id,
        recorded_at=datetime.now(UTC),
        disposition=disposition_value,
        note=note,
        idempotency_key=idempotency_key,
    )
    try:
        with session.begin_nested():
            session.add(outcome)
            session.flush()
    except IntegrityError:
        existing_for_key = _outcome_for_key(session, organization_id, idempotency_key)
        if existing_for_key is not None:
            _require_exact_replay(
                existing_for_key,
                need_id=need_id,
                disposition=disposition_value,
                note=note,
            )
            return _command_result(session, existing_for_key)
        if session.scalar(
            select(Outcome.id).where(
                Outcome.organization_id == organization_id,
                Outcome.reported_need_id == need_id,
            )
        ) is not None:
            raise OutcomeConflict("Reported need already has an Outcome") from None
        raise

    return _command_result(session, outcome)


def _outcome_for_key(
    session: Session, organization_id: UUID, idempotency_key: str
) -> Outcome | None:
    return session.scalar(
        select(Outcome).where(
            Outcome.organization_id == organization_id,
            Outcome.idempotency_key == idempotency_key,
        )
    )


def _require_exact_replay(
    outcome: Outcome,
    *,
    need_id: UUID,
    disposition: OutcomeDisposition,
    note: str | None,
) -> None:
    if (
        outcome.reported_need_id != need_id
        or outcome.disposition != disposition
        or outcome.note != note
    ):
        raise OutcomeConflict("Idempotency key is already used by a different Outcome command")


def _command_result(session: Session, outcome: Outcome) -> OutcomeCommandResult:
    events = session.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == outcome.organization_id,
            AuditEvent.entity_type == "navigation_task",
            AuditEvent.event_type == "task_cancelled_by_closure",
        )
    ).all()
    cancelled_task_ids = tuple(
        sorted(
            (
                event.entity_id
                for event in events
                if event.payload.get("outcome_id") == str(outcome.id)
            ),
            key=str,
        )
    )
    return OutcomeCommandResult(
        outcome_id=outcome.id,
        need_id=outcome.reported_need_id,
        disposition=outcome.disposition.value,
        note=outcome.note,
        recorded_by_user_id=outcome.recorded_by_user_id,
        recorded_at=outcome.recorded_at,
        cancelled_task_ids=cancelled_task_ids,
    )

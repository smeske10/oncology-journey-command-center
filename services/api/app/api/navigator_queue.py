"""Navigator-only, organization-scoped read models for the work queue."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.config import settings
from app.db.models import (
    CheckInSubmission,
    NavigationTask,
    ReportedNeed,
    SafetySignal,
    SyntheticPatient,
)
from app.db.session import get_session
from app.domain.enums import NavigationTaskStatus, NeedStatus
from app.domain.needs import NeedKind
from app.domain.prioritization import OperationalPriorityWeights, PriorityResult, rank_need

router = APIRouter(prefix="/v1/navigator", tags=["navigator"])


class EvidenceRead(BaseModel):
    field: str
    text: str


class PriorityResultRead(BaseModel):
    level: Literal["high", "medium", "routine"]
    score: int
    reasons: list[str]


class QueueItemRead(BaseModel):
    need_id: UUID
    patient_id: UUID
    patient_display_name: str
    kind: NeedKind
    priority: PriorityResultRead
    evidence: list[EvidenceRead]
    created_at: datetime
    due_at: datetime | None
    owner_id: UUID | None


class NavigatorQueueRead(BaseModel):
    items: list[QueueItemRead]


class SubmissionRead(BaseModel):
    id: UUID
    submitted_at: datetime | None
    items: list[dict[str, Any]]
    free_text: str | None
    provenance: dict[str, Any]


class SafetySignalRead(BaseModel):
    id: UUID
    rule_code: str
    severity: str
    status: str
    evidence: list[EvidenceRead]
    created_at: datetime


class NavigationTaskRead(BaseModel):
    id: UUID
    reported_need_id: UUID | None
    title: str
    status: str
    due_at: datetime | None
    owner_id: UUID | None
    created_at: datetime


class PatientCaseRead(BaseModel):
    patient: dict[str, Any]
    longitudinal_submissions: list[SubmissionRead]
    open_needs: list[QueueItemRead]
    safety_signals: list[SafetySignalRead]
    navigation_tasks: list[NavigationTaskRead]
    upcoming_synthetic_appointment: dict[str, Any] | None


class NavigatorPriorityPolicyProvider(Protocol):
    """Tenant override seam; a future repository can replace the deployment provider."""

    def for_organization(self, organization_id: UUID) -> OperationalPriorityWeights: ...


class DeploymentNavigatorPriorityPolicyProvider:
    def for_organization(self, organization_id: UUID) -> OperationalPriorityWeights:
        del organization_id
        return settings.navigator_priority_policy


def get_navigator_priority_policy(
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
) -> OperationalPriorityWeights:
    return DeploymentNavigatorPriorityPolicyProvider().for_organization(actor.organization_id)


@router.get("/queue", response_model=NavigatorQueueRead)
def get_navigator_queue(
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
    priority_policy: OperationalPriorityWeights = Depends(get_navigator_priority_policy),
) -> NavigatorQueueRead:
    needs = session.scalars(
        select(ReportedNeed).where(
            ReportedNeed.organization_id == actor.organization_id,
            ReportedNeed.status.in_([NeedStatus.OPEN, NeedStatus.IN_PROGRESS]),
        )
    ).all()
    items = [
        _queue_item_for_need(session, actor.organization_id, need, priority_policy=priority_policy)
        for need in needs
    ]
    return NavigatorQueueRead(items=sorted(items, key=_queue_sort_key))


@router.get("/patients/{patient_id}/case", response_model=PatientCaseRead)
def get_navigator_case(
    patient_id: UUID,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
    priority_policy: OperationalPriorityWeights = Depends(get_navigator_priority_policy),
) -> PatientCaseRead:
    patient = session.scalars(
        select(SyntheticPatient).where(
            SyntheticPatient.id == patient_id,
            SyntheticPatient.organization_id == actor.organization_id,
        )
    ).first()
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient case not found")

    submissions = session.scalars(
        select(CheckInSubmission)
        .where(
            CheckInSubmission.organization_id == actor.organization_id,
            CheckInSubmission.patient_id == patient_id,
        )
        .order_by(CheckInSubmission.submitted_at.desc())
    ).all()
    needs = session.scalars(
        select(ReportedNeed).where(
            ReportedNeed.organization_id == actor.organization_id,
            ReportedNeed.patient_id == patient_id,
            ReportedNeed.status.in_([NeedStatus.OPEN, NeedStatus.IN_PROGRESS]),
        )
    ).all()
    signals = session.scalars(
        select(SafetySignal).where(
            SafetySignal.organization_id == actor.organization_id,
            SafetySignal.patient_id == patient_id,
        )
    ).all()
    tasks = session.scalars(
        select(NavigationTask).where(
            NavigationTask.organization_id == actor.organization_id,
            NavigationTask.patient_id == patient_id,
        )
    ).all()

    demographics = patient.demographics if isinstance(patient.demographics, dict) else {}
    return PatientCaseRead(
        patient={
            "id": patient.id,
            "display_name": patient.display_name,
            "diagnosis": demographics.get("diagnosis"),
            "consent_status": demographics.get("consent_status"),
        },
        longitudinal_submissions=[_submission_read(submission) for submission in submissions],
        open_needs=sorted(
            [
                _queue_item_for_need(
                    session,
                    actor.organization_id,
                    need,
                    patient,
                    priority_policy=priority_policy,
                )
                for need in needs
            ],
            key=_queue_sort_key,
        ),
        safety_signals=[_safety_signal_read(signal) for signal in signals],
        navigation_tasks=[_navigation_task_read(task) for task in tasks],
        upcoming_synthetic_appointment=_appointment_from(demographics),
    )


def _queue_item_for_need(
    session: Session,
    organization_id: UUID,
    need: ReportedNeed,
    patient: SyntheticPatient | None = None,
    *,
    priority_policy: OperationalPriorityWeights,
) -> QueueItemRead:
    patient = patient or _patient_for_need(session, organization_id, need.patient_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient case not found")
    tasks = session.scalars(
        select(NavigationTask).where(
            NavigationTask.organization_id == organization_id,
            NavigationTask.reported_need_id == need.id,
            NavigationTask.patient_id == need.patient_id,
            NavigationTask.status.in_(
                [NavigationTaskStatus.OPEN, NavigationTaskStatus.IN_PROGRESS]
            ),
        )
    ).all()
    task = min(tasks, key=_task_due_sort_key, default=None)
    priority = _priority_for_need(need, task, priority_policy)
    evidence = [_evidence_read(item) for item in need.evidence if isinstance(item, dict)]
    return QueueItemRead(
        need_id=need.id,
        patient_id=need.patient_id,
        patient_display_name=patient.display_name,
        kind=_need_kind(need.kind),
        priority=PriorityResultRead(**priority.__dict__),
        evidence=evidence,
        created_at=_created_at(need.created_at),
        due_at=task.due_at if task else None,
        owner_id=task.assignee_user_id if task else None,
    )


def _patient_for_need(
    session: Session, organization_id: UUID, patient_id: UUID
) -> SyntheticPatient | None:
    return session.scalars(
        select(SyntheticPatient).where(
            SyntheticPatient.id == patient_id,
            SyntheticPatient.organization_id == organization_id,
        )
    ).first()


def _priority_for_need(
    need: ReportedNeed,
    task: NavigationTask | None,
    priority_policy: OperationalPriorityWeights,
) -> PriorityResult:
    now = datetime.now(UTC)
    created_at = _created_at(need.created_at)
    age_hours = max((now - created_at).total_seconds() / 3600, 0)
    due_in_hours = None
    if task and task.due_at:
        due_in_hours = (task.due_at - now).total_seconds() / 3600
    evidence = [item for item in need.evidence if isinstance(item, dict)]
    has_medication_question = any(
        item.get("field") == "medication_question" and _is_affirmative(item.get("text"))
        for item in evidence
    )
    worsening = any(
        isinstance(item.get("field"), str)
        and item["field"].endswith("_change")
        and isinstance(item.get("text"), str)
        and item["text"].casefold() == "worse"
        for item in evidence
    )
    return rank_need(
        kind=need.kind,
        worsening=worsening,
        medication_question=has_medication_question,
        age_hours=age_hours,
        due_in_hours=due_in_hours,
        weights=priority_policy,
    )


def _queue_sort_key(item: QueueItemRead) -> tuple[int, datetime, datetime, str]:
    due_at = item.due_at or datetime.max.replace(tzinfo=UTC)
    return (-item.priority.score, due_at, item.created_at, str(item.need_id))


def _submission_read(submission: CheckInSubmission) -> SubmissionRead:
    answers = submission.answers if isinstance(submission.answers, dict) else {}
    items = answers.get("items", [])
    return SubmissionRead(
        id=submission.id,
        submitted_at=submission.submitted_at,
        items=items if isinstance(items, list) else [],
        free_text=answers.get("free_text") if isinstance(answers.get("free_text"), str) else None,
        provenance=_provenance_from_answers(answers),
    )


def _safety_signal_read(signal: SafetySignal) -> SafetySignalRead:
    return SafetySignalRead(
        id=signal.id,
        rule_code=signal.rule_code,
        severity=signal.severity.value,
        status=signal.status.value,
        evidence=[_evidence_read(item) for item in signal.evidence if isinstance(item, dict)],
        created_at=_created_at(signal.created_at),
    )


def _navigation_task_read(task: NavigationTask) -> NavigationTaskRead:
    return NavigationTaskRead(
        id=task.id,
        reported_need_id=task.reported_need_id,
        title=task.title,
        status=task.status.value,
        due_at=task.due_at,
        owner_id=task.assignee_user_id,
        created_at=_created_at(task.created_at),
    )


def _evidence_read(value: dict[str, Any]) -> EvidenceRead:
    return EvidenceRead(
        field=str(value.get("field", "submitted_data")), text=str(value.get("text", ""))
    )


def _need_kind(value: str) -> NeedKind:
    allowed: set[str] = {
        "symptom_change",
        "medication_question",
        "transportation",
        "financial_support",
        "other",
    }
    return value if value in allowed else "other"  # type: ignore[return-value]


def _is_affirmative(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() in {"yes", "true"})


def _created_at(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _task_due_sort_key(task: NavigationTask) -> tuple[bool, datetime]:
    return (task.due_at is None, task.due_at or datetime.max.replace(tzinfo=UTC))


def _provenance_from_answers(answers: dict[str, Any]) -> dict[str, Any]:
    provenance = answers.get("provenance")
    return provenance if isinstance(provenance, dict) else {}


def _appointment_from(demographics: dict[str, Any]) -> dict[str, Any] | None:
    appointment = demographics.get("upcoming_appointment")
    return appointment if isinstance(appointment, dict) else None

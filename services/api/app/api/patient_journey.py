"""Audience-safe supporting-actor journey timeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.repositories import SqlAlchemyPatientRepository
from app.db.session import get_session
from app.domain.journey_timeline import PatientTimelineEvent, build_patient_timeline

router = APIRouter(prefix="/v1/patient", tags=["patient-journey"])


class PatientTimelineRead(BaseModel):
    events: list[PatientTimelineEvent]


@router.get("/journey-timeline", response_model=PatientTimelineRead)
def get_patient_journey_timeline(
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    session: Session = Depends(get_session),
) -> PatientTimelineRead:
    if actor.patient_id is None:
        raise HTTPException(status_code=403, detail="Active patient identity link is required")
    repository = SqlAlchemyPatientRepository(session)
    if repository.resolve_active_patient_link(
        user_id=actor.user_id,
        patient_id=actor.patient_id,
        organization_id=actor.organization_id,
    ) is None:
        raise HTTPException(status_code=403, detail="Active patient identity link is required")
    submissions = repository.list_submissions(
        patient_id=actor.patient_id,
        organization_id=actor.organization_id,
    )
    needs = repository.list_needs(
        patient_id=actor.patient_id,
        organization_id=actor.organization_id,
    )
    tasks = repository.list_tasks(
        patient_id=actor.patient_id,
        organization_id=actor.organization_id,
    )
    audits = repository.list_task_audits(
        task_ids={task.id for task in tasks},
        organization_id=actor.organization_id,
    )
    requests = repository.list_follow_up_requests(
        patient_id=actor.patient_id,
        organization_id=actor.organization_id,
    )
    responses = repository.list_follow_up_responses(
        request_ids={request.id for request in requests},
        organization_id=actor.organization_id,
    )
    outcomes = repository.list_outcomes_for_needs(
        need_ids={need.id for need in needs},
        patient_id=actor.patient_id,
        organization_id=actor.organization_id,
    )
    return PatientTimelineRead(
        events=build_patient_timeline(
            submissions=submissions,
            needs=needs,
            tasks=tasks,
            audits=audits,
            requests=requests,
            responses=responses,
            outcomes=outcomes,
        )
    )

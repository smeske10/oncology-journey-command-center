"""Need-scoped navigator evidence, governance, and history projection."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.repositories import SqlAlchemyNavigatorRepository
from app.db.session import get_session
from app.domain.navigator_workspace import (
    NavigatorNeedWorkspaceRead,
    build_navigator_workspace,
)

router = APIRouter(prefix="/v1/navigator/needs", tags=["navigator"])


@router.get("/{need_id}/workspace", response_model=NavigatorNeedWorkspaceRead)
def get_navigator_need_workspace(
    need_id: UUID,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> NavigatorNeedWorkspaceRead:
    repository = SqlAlchemyNavigatorRepository(session)
    selected = repository.get_need_with_effective_state(
        need_id=need_id,
        organization_id=actor.organization_id,
    )
    if selected is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Navigator need workspace not found",
        )
    need, effective_state = selected
    patient = repository.get_patient(
        patient_id=need.patient_id,
        organization_id=actor.organization_id,
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Navigator need workspace not found",
        )
    submissions = repository.list_episode_submissions(
        patient_id=need.patient_id,
        care_episode_id=need.care_episode_id,
        organization_id=actor.organization_id,
    )
    definitions = repository.list_check_in_definitions(
        definition_ids={submission.check_in_definition_id for submission in submissions},
        organization_id=actor.organization_id,
    )
    tasks = repository.list_need_tasks(
        need_id=need.id,
        patient_id=need.patient_id,
        organization_id=actor.organization_id,
    )
    need_lineage = repository.list_episode_needs(
        patient_id=need.patient_id,
        care_episode_id=need.care_episode_id,
        organization_id=actor.organization_id,
    )
    proposals = repository.list_task_proposals(
        task_ids={task.id for task in tasks},
        organization_id=actor.organization_id,
    )
    proposal_ids = {record.proposal.id for record in proposals}
    decisions = repository.list_proposal_decisions(
        proposal_ids=proposal_ids,
        organization_id=actor.organization_id,
    )
    resources = repository.list_proposal_resources(
        proposal_ids=proposal_ids,
        organization_id=actor.organization_id,
    )
    audits = repository.list_task_audits(
        task_ids={task.id for task in tasks},
        organization_id=actor.organization_id,
    )
    follow_up_requests = repository.list_need_follow_up_requests(
        need_id=need.id,
        patient_id=need.patient_id,
        organization_id=actor.organization_id,
    )
    follow_up_responses = repository.list_follow_up_responses(
        request_ids={request.id for request in follow_up_requests},
        organization_id=actor.organization_id,
    )
    outcome = repository.get_need_outcome(
        need_id=need.id,
        patient_id=need.patient_id,
        organization_id=actor.organization_id,
    )
    return build_navigator_workspace(
        need=need,
        effective_state=effective_state,
        need_lineage=need_lineage,
        patient=patient,
        submissions=submissions,
        definitions=definitions,
        tasks=tasks,
        proposals=proposals,
        decisions=decisions,
        resources=resources,
        audits=audits,
        follow_up_requests=follow_up_requests,
        follow_up_responses=follow_up_responses,
        outcome=outcome,
    )

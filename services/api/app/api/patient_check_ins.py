from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import current_actor, require_role
from app.auth.models import CurrentActor, Role
from app.db.models import (
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    EpisodePathwayAssignment,
)
from app.db.repositories import SqlAlchemyUnitOfWork, TenantScoped
from app.db.session import get_session
from app.domain.check_ins import (
    CheckInDefinitionMismatchError,
    CheckInSubmissionCreate,
    create_immutable_submission,
    questionnaire_version_for,
)
from app.fhir.check_in_mapper import map_check_in_to_fhir_bundle

router = APIRouter(prefix="/v1/patient/check-ins", tags=["patient-check-ins"])


class CheckInDefinitionResponse(BaseModel):
    id: UUID
    title: str
    questionnaire_version: str
    questions: list[dict[str, Any]]


class CheckInSubmissionResponse(BaseModel):
    id: UUID
    status: str
    questionnaire_version: str
    submitted_at: str


def get_check_in_unit_of_work(
    actor: CurrentActor = Depends(current_actor),
    session: Session = Depends(get_session),
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(actor.organization_id, lambda: session)


@router.get("/current", response_model=CheckInDefinitionResponse)
def get_current_check_in(
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    session: Session = Depends(get_session),
) -> CheckInDefinitionResponse:
    if actor.patient_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Patient identity link is required",
        )
    now = datetime.now(UTC)
    definition = session.scalars(
        select(CheckInDefinition)
        .join(
            EpisodePathwayAssignment,
            and_(
                EpisodePathwayAssignment.organization_id == CheckInDefinition.organization_id,
                EpisodePathwayAssignment.pathway_definition_id
                == CheckInDefinition.pathway_definition_id,
            ),
        )
        .join(
            CareEpisode,
            and_(
                CareEpisode.organization_id == EpisodePathwayAssignment.organization_id,
                CareEpisode.id == EpisodePathwayAssignment.care_episode_id,
            ),
        )
        .where(
            CheckInDefinition.organization_id == actor.organization_id,
            CareEpisode.patient_id == actor.patient_id,
            CareEpisode.status == "active",
            EpisodePathwayAssignment.effective_from <= now,
            EpisodePathwayAssignment.effective_to.is_(None)
            | (now < EpisodePathwayAssignment.effective_to),
        )
        .order_by(CheckInDefinition.created_at.desc())
    ).first()
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No current check-in")
    questions = definition.questionnaire.get("questions", [])
    return CheckInDefinitionResponse(
        id=definition.id,
        title=definition.title,
        questionnaire_version=questionnaire_version_for(definition),
        questions=questions if isinstance(questions, list) else [],
    )


@router.post(
    "/{definition_id}/submissions",
    response_model=CheckInSubmissionResponse,
    status_code=201,
)
def submit_check_in(
    definition_id: UUID,
    payload: CheckInSubmissionCreate,
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    unit_of_work: SqlAlchemyUnitOfWork = Depends(get_check_in_unit_of_work),
) -> CheckInSubmissionResponse:
    try:
        with unit_of_work:
            definition = cast(
                CheckInDefinition | None,
                unit_of_work.get(
                    cast(type[Any], CheckInDefinition),
                    definition_id,
                    organization_id=actor.organization_id,
                ),
            )
            if definition is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Check-in not found",
                )
            if actor.patient_id is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patient identity link is required",
                )
            episode = unit_of_work.find_active_care_episode(patient_id=actor.patient_id)
            if episode is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No active care episode",
                )
            if not unit_of_work.definition_matches_effective_pathway(
                care_episode_id=episode.id,
                check_in_definition_id=definition.id,
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Check-in definition is not active for this care episode",
                )
            submission = create_immutable_submission(
                actor=actor,
                definition=definition,
                care_episode_id=episode.id,
                payload=payload,
            )
            unit_of_work.add(cast(TenantScoped, submission))
            unit_of_work.commit()
    except CheckInDefinitionMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "We could not save your check-in. Your draft is still available; please try again."
            ),
        ) from error

    return _submission_response(submission)


@router.get("/{submission_id}/fhir")
def export_submission_as_synthetic_fhir(
    submission_id: UUID,
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    unit_of_work: SqlAlchemyUnitOfWork = Depends(get_check_in_unit_of_work),
) -> dict[str, Any]:
    with unit_of_work:
        submission = cast(
            CheckInSubmission | None,
            unit_of_work.get(
                cast(type[Any], CheckInSubmission),
                submission_id,
                organization_id=actor.organization_id,
            ),
        )
        if submission is None or submission.patient_id != actor.patient_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Check-in submission not found",
            )
        return map_check_in_to_fhir_bundle(submission)


def _submission_response(submission: CheckInSubmission) -> CheckInSubmissionResponse:
    return CheckInSubmissionResponse(
        id=submission.id,
        status=submission.status.value,
        questionnaire_version=str(submission.answers["questionnaire_version"]),
        submitted_at=submission.submitted_at.isoformat() if submission.submitted_at else "",
    )

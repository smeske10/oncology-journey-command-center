from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
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
from app.db.repositories import (
    SqlAlchemyFhirRepository,
    SqlAlchemyPatientRepository,
    SqlAlchemyUnitOfWork,
    TenantScoped,
)
from app.db.session import get_session
from app.domain.check_ins import (
    CheckInDefinitionMismatchError,
    CheckInSubmissionCreate,
    active_check_in_submission,
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
    active_submission_id: UUID | None


class CheckInSubmissionResponse(BaseModel):
    id: UUID
    status: str
    questionnaire_version: str
    submitted_at: str
    supersedes_submission_id: UUID | None


class CheckInSubmissionErrorDetail(BaseModel):
    code: Literal[
        "answers_invalid",
        "configuration_invalid",
        "correction_stale",
        "definition_inactive",
        "questionnaire_stale",
    ]
    message: str


class CheckInSubmissionErrorResponse(BaseModel):
    detail: CheckInSubmissionErrorDetail


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
    patient_id = SqlAlchemyPatientRepository(session).resolve_active_patient_id(
        organization_id=actor.organization_id,
        user_id=actor.user_id,
    )
    if patient_id is None or actor.patient_id != patient_id:
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
            CareEpisode.patient_id == patient_id,
            CareEpisode.status == "active",
            EpisodePathwayAssignment.effective_from <= now,
            EpisodePathwayAssignment.effective_to.is_(None)
            | (now < EpisodePathwayAssignment.effective_to),
        )
        .order_by(CheckInDefinition.created_at.desc())
    ).first()
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No current check-in")
    active_submission_id = session.scalar(
        select(active_check_in_submission.c.id)
        .where(
            active_check_in_submission.c.organization_id == actor.organization_id,
            active_check_in_submission.c.patient_id == patient_id,
            active_check_in_submission.c.check_in_definition_id == definition.id,
        )
        .order_by(active_check_in_submission.c.submitted_at.desc())
    )
    questions = definition.questionnaire.get("questions", [])
    return CheckInDefinitionResponse(
        id=definition.id,
        title=definition.title,
        questionnaire_version=questionnaire_version_for(definition),
        questions=questions if isinstance(questions, list) else [],
        active_submission_id=active_submission_id,
    )


@router.post(
    "/{definition_id}/submissions",
    response_model=CheckInSubmissionResponse,
    status_code=201,
    responses={422: {"model": CheckInSubmissionErrorResponse}},
)
def submit_check_in(
    definition_id: UUID,
    payload: CheckInSubmissionCreate,
    actor: CurrentActor = Depends(require_role(Role.SUPPORTING_ACTOR)),
    unit_of_work: SqlAlchemyUnitOfWork = Depends(get_check_in_unit_of_work),
) -> CheckInSubmissionResponse:
    try:
        with unit_of_work:
            patient_id = unit_of_work.resolve_active_patient_id(
                organization_id=actor.organization_id,
                user_id=actor.user_id,
            )
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
            if patient_id is None or actor.patient_id != patient_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patient identity link is required",
                )
            episode = unit_of_work.find_active_care_episode(
                patient_id=patient_id,
                organization_id=actor.organization_id,
            )
            if episode is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No active care episode",
                )
            if not unit_of_work.definition_matches_effective_pathway(
                care_episode_id=episode.id,
                check_in_definition_id=definition.id,
                organization_id=actor.organization_id,
            ):
                raise _submission_error(
                    "definition_inactive",
                    "This check-in is no longer active. Reload the current check-in.",
                )
            predecessor = None
            if payload.supersedes_submission_id is not None:
                predecessor = unit_of_work.find_active_submission(
                    submission_id=payload.supersedes_submission_id,
                    patient_id=patient_id,
                    check_in_definition_id=definition.id,
                    organization_id=actor.organization_id,
                )
            submission = create_immutable_submission(
                actor=actor,
                definition=definition,
                care_episode_id=episode.id,
                payload=payload,
                predecessor=predecessor,
            )
            unit_of_work.add(cast(TenantScoped, submission))
            unit_of_work.commit()
    except CheckInDefinitionMismatchError as error:
        raise _submission_error(error.code, str(error)) from error
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
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    with unit_of_work:
        patient_id = unit_of_work.resolve_active_patient_id(
            organization_id=actor.organization_id,
            user_id=actor.user_id,
        )
        if patient_id is None or actor.patient_id != patient_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Check-in submission not found",
            )
        submission = cast(
            CheckInSubmission | None,
            unit_of_work.get(
                cast(type[Any], CheckInSubmission),
                submission_id,
                organization_id=actor.organization_id,
            ),
        )
        if submission is None or submission.patient_id != patient_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Check-in submission not found",
            )
        predecessor = None
        if submission.supersedes_submission_id is not None:
            predecessor = cast(
                CheckInSubmission | None,
                unit_of_work.get(
                    cast(type[Any], CheckInSubmission),
                    submission.supersedes_submission_id,
                    organization_id=actor.organization_id,
                ),
            )
            if predecessor is None or predecessor.patient_id != patient_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Check-in submission predecessor not found",
                )
        successor = unit_of_work.find_submission_successor(
            submission_id=submission.id,
            patient_id=patient_id,
            organization_id=actor.organization_id,
        )
        fhir_repository = SqlAlchemyFhirRepository(session)
        signal_records = fhir_repository.list_submission_safety_signals(
            submission_id=submission.id,
            patient_id=patient_id,
            organization_id=actor.organization_id,
        )
        proposal_records = fhir_repository.list_signal_proposals(
            signal_ids=[record.signal.id for record in signal_records],
            organization_id=actor.organization_id,
        )
        decisions = fhir_repository.list_approval_decisions(
            proposal_ids=[record.proposal.id for record in proposal_records],
            organization_id=actor.organization_id,
        )
        return map_check_in_to_fhir_bundle(
            submission,
            is_superseded=successor is not None,
            predecessor_submission=predecessor,
            safety_signals=[record.signal for record in signal_records],
            effective_signal_states={
                record.signal.id: record.effective_state for record in signal_records
            },
            signal_resolutions={
                record.signal.id: record.resolution
                for record in signal_records
                if record.resolution is not None
            },
            applied_proposals=[record.proposal for record in proposal_records],
            effective_proposal_states={
                record.proposal.id: record.effective_state
                for record in proposal_records
            },
            approval_decisions=decisions,
        )


def _submission_response(submission: CheckInSubmission) -> CheckInSubmissionResponse:
    return CheckInSubmissionResponse(
        id=submission.id,
        status=submission.status.value,
        questionnaire_version=str(submission.answers["questionnaire_version"]),
        submitted_at=submission.submitted_at.isoformat() if submission.submitted_at else "",
        supersedes_submission_id=submission.supersedes_submission_id,
    )


def _submission_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )

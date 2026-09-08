from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar, cast
from uuid import UUID

from sqlalchemy import String, Uuid, and_, column, func, or_, select, table
from sqlalchemy.orm import Session, joinedload

from app.db import models

TenantEntity = TypeVar("TenantEntity", bound="TenantScoped")


class TenantScoped(Protocol):
    organization_id: UUID


effective_safety_signal_state = table(
    "effective_safety_signal_state",
    column("id", Uuid),
    column("organization_id", Uuid),
    column("effective_state", String),
)

effective_proposed_change_state = table(
    "effective_proposed_change_state",
    column("id", Uuid),
    column("organization_id", Uuid),
    column("effective_state", String),
)


@dataclass(frozen=True)
class EffectiveSafetySignalRecord:
    signal: models.SafetySignal
    effective_state: str
    resolution: models.SafetySignalResolution | None = None


@dataclass(frozen=True)
class EffectiveProposedChangeRecord:
    proposal: models.ProposedChange
    effective_state: str


class UnitOfWork(Protocol):
    """Transaction boundary for domain commands."""

    organization_id: UUID

    def add(self, entity: TenantScoped) -> None: ...

    def get(
        self, model: type[TenantEntity], entity_id: UUID, *, organization_id: UUID
    ) -> TenantEntity | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class PatientRepository(Protocol):
    def get_for_actor(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> models.SyntheticPatient | None: ...

    def resolve_active_patient_id(
        self, *, user_id: UUID, organization_id: UUID
    ) -> UUID | None: ...


class SqlAlchemyPatientRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_actor(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> models.SyntheticPatient | None:
        statement = select(models.SyntheticPatient).where(
            models.SyntheticPatient.id == patient_id,
            models.SyntheticPatient.organization_id == organization_id,
        )
        return self._session.scalar(statement)

    def resolve_active_patient_id(
        self, *, user_id: UUID, organization_id: UUID
    ) -> UUID | None:
        statement = select(models.PatientIdentityLink.patient_id).where(
            models.PatientIdentityLink.organization_id == organization_id,
            models.PatientIdentityLink.user_id == user_id,
            models.PatientIdentityLink.revoked_at.is_(None),
        )
        return self._session.scalar(statement)


class SqlAlchemyNavigatorRepository:
    """Organization-scoped read repository backed by canonical lifecycle views."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_patient(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> models.SyntheticPatient | None:
        return self._session.scalars(
            select(models.SyntheticPatient).where(
                models.SyntheticPatient.id == patient_id,
                models.SyntheticPatient.organization_id == organization_id,
            )
        ).first()

    def list_active_submissions(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> list[models.CheckInSubmission]:
        from app.domain.check_ins import active_check_in_submission

        return list(
            self._session.scalars(
                select(models.CheckInSubmission)
                .join(
                    active_check_in_submission,
                    and_(
                        active_check_in_submission.c.organization_id
                        == models.CheckInSubmission.organization_id,
                        active_check_in_submission.c.id == models.CheckInSubmission.id,
                    ),
                )
                .where(
                    models.CheckInSubmission.organization_id == organization_id,
                    models.CheckInSubmission.patient_id == patient_id,
                )
                .order_by(models.CheckInSubmission.submitted_at.desc())
            ).all()
        )

    def list_open_needs(
        self, *, organization_id: UUID, patient_id: UUID | None = None
    ) -> list[models.ReportedNeed]:
        from app.domain.enums import NeedStatus
        from app.domain.needs import effective_need_state

        statement = (
            select(models.ReportedNeed)
            .join(
                effective_need_state,
                and_(
                    effective_need_state.c.organization_id
                    == models.ReportedNeed.organization_id,
                    effective_need_state.c.id == models.ReportedNeed.id,
                ),
            )
            .where(
                models.ReportedNeed.organization_id == organization_id,
                effective_need_state.c.effective_state.in_(
                    [NeedStatus.OPEN.value, NeedStatus.IN_PROGRESS.value]
                ),
            )
        )
        if patient_id is not None:
            statement = statement.where(models.ReportedNeed.patient_id == patient_id)
        return list(self._session.scalars(statement).all())

    def list_effective_safety_signals(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> list[EffectiveSafetySignalRecord]:
        rows = self._session.execute(
            select(models.SafetySignal, effective_safety_signal_state.c.effective_state)
            .join(
                effective_safety_signal_state,
                and_(
                    effective_safety_signal_state.c.organization_id
                    == models.SafetySignal.organization_id,
                    effective_safety_signal_state.c.id == models.SafetySignal.id,
                ),
            )
            .where(
                models.SafetySignal.organization_id == organization_id,
                models.SafetySignal.patient_id == patient_id,
            )
        ).all()
        return [
            EffectiveSafetySignalRecord(signal=row[0], effective_state=str(row[1]))
            for row in rows
        ]

    def list_navigation_tasks(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> list[models.NavigationTask]:
        from app.domain.enums import TaskCancellationReason

        return list(
            self._session.scalars(
                select(models.NavigationTask).where(
                    models.NavigationTask.organization_id == organization_id,
                    models.NavigationTask.patient_id == patient_id,
                    or_(
                        models.NavigationTask.cancellation_reason.is_(None),
                        models.NavigationTask.cancellation_reason
                        != TaskCancellationReason.NEED_CLOSED,
                    ),
                )
            ).all()
        )


class SqlAlchemyFhirRepository:
    """FHIR export facts selected through canonical effective-state contracts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_submission_safety_signals(
        self,
        *,
        submission_id: UUID,
        patient_id: UUID,
        organization_id: UUID,
    ) -> list[EffectiveSafetySignalRecord]:
        rows = self._session.execute(
            select(
                models.SafetySignal,
                effective_safety_signal_state.c.effective_state,
                models.SafetySignalResolution,
            )
            .options(joinedload(models.SafetySignal.rule))
            .join(
                effective_safety_signal_state,
                and_(
                    effective_safety_signal_state.c.organization_id
                    == models.SafetySignal.organization_id,
                    effective_safety_signal_state.c.id == models.SafetySignal.id,
                ),
            )
            .outerjoin(
                models.SafetySignalResolution,
                and_(
                    models.SafetySignalResolution.organization_id
                    == models.SafetySignal.organization_id,
                    models.SafetySignalResolution.safety_signal_id
                    == models.SafetySignal.id,
                ),
            )
            .where(
                models.SafetySignal.organization_id == organization_id,
                models.SafetySignal.patient_id == patient_id,
                models.SafetySignal.source_submission_id == submission_id,
            )
        ).all()
        return [
            EffectiveSafetySignalRecord(
                signal=row[0], effective_state=str(row[1]), resolution=row[2]
            )
            for row in rows
        ]

    def list_signal_proposals(
        self, *, signal_ids: list[UUID], organization_id: UUID
    ) -> list[EffectiveProposedChangeRecord]:
        if not signal_ids:
            return []
        rows = self._session.execute(
            select(models.ProposedChange, effective_proposed_change_state.c.effective_state)
            .join(
                effective_proposed_change_state,
                and_(
                    effective_proposed_change_state.c.organization_id
                    == models.ProposedChange.organization_id,
                    effective_proposed_change_state.c.id == models.ProposedChange.id,
                ),
            )
            .where(
                models.ProposedChange.organization_id == organization_id,
                models.ProposedChange.safety_signal_id.in_(signal_ids),
            )
        ).all()
        return [
            EffectiveProposedChangeRecord(proposal=row[0], effective_state=str(row[1]))
            for row in rows
        ]

    def list_approval_decisions(
        self, *, proposal_ids: list[UUID], organization_id: UUID
    ) -> dict[UUID, list[models.ApprovalDecision]]:
        from app.domain.enums import ApprovalChangeType, ApprovalDecisionValue

        if not proposal_ids:
            return {}
        decisions = self._session.scalars(
            select(models.ApprovalDecision)
            .join(
                models.ProposedChange,
                and_(
                    models.ProposedChange.organization_id
                    == models.ApprovalDecision.organization_id,
                    models.ProposedChange.id
                    == models.ApprovalDecision.proposed_change_id,
                ),
            )
            .join(
                models.RoleAssignment,
                and_(
                    models.RoleAssignment.organization_id
                    == models.ApprovalDecision.organization_id,
                    models.RoleAssignment.user_id
                    == models.ApprovalDecision.authorized_by_user_id,
                    models.RoleAssignment.id
                    == models.ApprovalDecision.qualifying_role_assignment_id,
                    models.RoleAssignment.role
                    == models.ProposedChange.required_approver_role_snapshot,
                    models.ApprovalDecision.qualifying_role_snapshot
                    == models.ProposedChange.required_approver_role_snapshot,
                    models.ApprovalDecision.authorized_at
                    >= models.RoleAssignment.granted_at,
                    or_(
                        models.RoleAssignment.revoked_at.is_(None),
                        models.ApprovalDecision.authorized_at
                        < models.RoleAssignment.revoked_at,
                    ),
                ),
            )
            .outerjoin(
                models.SafetySignal,
                and_(
                    models.SafetySignal.organization_id
                    == models.ProposedChange.organization_id,
                    models.SafetySignal.id == models.ProposedChange.safety_signal_id,
                ),
            )
            .where(
                models.ApprovalDecision.organization_id == organization_id,
                models.ApprovalDecision.proposed_change_id.in_(proposal_ids),
                models.ApprovalDecision.decision == ApprovalDecisionValue.APPROVED,
                or_(
                    models.ProposedChange.proposed_by_user_id.is_(None),
                    models.ApprovalDecision.authorized_by_user_id
                    != models.ProposedChange.proposed_by_user_id,
                    and_(
                        models.ProposedChange.allow_self_approval_snapshot.is_(True),
                        or_(
                            models.ProposedChange.change_type
                            != ApprovalChangeType.DISMISS_SIGNAL,
                            and_(
                                models.ProposedChange.deterministic_severity_threshold_snapshot.is_not(
                                    None
                                ),
                                func.safety_severity_rank(
                                    models.SafetySignal.deterministic_level
                                )
                                < func.safety_severity_rank(
                                    models.ProposedChange.deterministic_severity_threshold_snapshot
                                ),
                            ),
                        ),
                    ),
                ),
            )
        ).all()
        grouped: dict[UUID, list[models.ApprovalDecision]] = {}
        for decision in decisions:
            grouped.setdefault(decision.proposed_change_id, []).append(decision)
        return grouped

class SqlAlchemyUnitOfWork:
    def __init__(self, organization_id: UUID, session_factory: Callable[[], Session]) -> None:
        self.organization_id = organization_id
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self._session.rollback()
        self._session.close()
        self._session = None

    def add(self, entity: TenantScoped) -> None:
        if entity.organization_id != self.organization_id:
            raise ValueError(
                "Entity organization_id does not match the unit of work organization scope"
            )
        self._require_session().add(entity)

    def get(
        self, model: type[TenantEntity], entity_id: UUID, *, organization_id: UUID
    ) -> TenantEntity | None:
        if organization_id != self.organization_id:
            raise ValueError(
                "Lookup organization_id does not match the unit of work organization scope"
            )
        statement = select(model).where(
            getattr(model, "id") == entity_id,
            getattr(model, "organization_id") == organization_id,
        )
        return cast(TenantEntity | None, self._require_session().scalar(statement))

    def commit(self) -> None:
        self._require_session().commit()

    def resolve_active_patient_id(
        self, *, user_id: UUID, organization_id: UUID
    ) -> UUID | None:
        self._validate_organization(organization_id)
        statement = select(models.PatientIdentityLink.patient_id).where(
            models.PatientIdentityLink.organization_id == organization_id,
            models.PatientIdentityLink.user_id == user_id,
            models.PatientIdentityLink.revoked_at.is_(None),
        )
        return self._require_session().scalar(statement)

    def find_active_care_episode(
        self, *, patient_id: UUID, organization_id: UUID
    ) -> models.CareEpisode | None:
        self._validate_organization(organization_id)
        statement = select(models.CareEpisode).where(
            models.CareEpisode.organization_id == organization_id,
            models.CareEpisode.patient_id == patient_id,
            models.CareEpisode.status == "active",
        )
        return self._require_session().scalar(statement)

    def definition_matches_effective_pathway(
        self,
        *,
        care_episode_id: UUID,
        check_in_definition_id: UUID,
        organization_id: UUID,
        at: datetime | None = None,
    ) -> bool:
        self._validate_organization(organization_id)
        at = datetime.now(UTC) if at is None else at
        statement = (
            select(models.EpisodePathwayAssignment.id)
            .join(
                models.CheckInDefinition,
                and_(
                    models.CheckInDefinition.organization_id
                    == models.EpisodePathwayAssignment.organization_id,
                    models.CheckInDefinition.pathway_definition_id
                    == models.EpisodePathwayAssignment.pathway_definition_id,
                ),
            )
            .where(
                models.EpisodePathwayAssignment.organization_id == organization_id,
                models.EpisodePathwayAssignment.care_episode_id == care_episode_id,
                models.CheckInDefinition.id == check_in_definition_id,
                models.EpisodePathwayAssignment.effective_from <= at,
                models.EpisodePathwayAssignment.effective_to.is_(None)
                | (at < models.EpisodePathwayAssignment.effective_to),
            )
        )
        return self._require_session().scalar(statement) is not None

    def find_active_submission(
        self,
        *,
        submission_id: UUID,
        patient_id: UUID,
        check_in_definition_id: UUID,
        organization_id: UUID,
    ) -> models.CheckInSubmission | None:
        from app.domain.check_ins import active_check_in_submission

        self._validate_organization(organization_id)
        statement = (
            select(models.CheckInSubmission)
            .join(
                active_check_in_submission,
                and_(
                    active_check_in_submission.c.organization_id
                    == models.CheckInSubmission.organization_id,
                    active_check_in_submission.c.id == models.CheckInSubmission.id,
                ),
            )
            .where(
                models.CheckInSubmission.organization_id == organization_id,
                models.CheckInSubmission.patient_id == patient_id,
                models.CheckInSubmission.check_in_definition_id == check_in_definition_id,
                models.CheckInSubmission.id == submission_id,
            )
        )
        return self._require_session().scalar(statement)

    def find_submission_successor(
        self,
        *,
        submission_id: UUID,
        patient_id: UUID,
        organization_id: UUID,
    ) -> models.CheckInSubmission | None:
        self._validate_organization(organization_id)
        statement = select(models.CheckInSubmission).where(
            models.CheckInSubmission.organization_id == organization_id,
            models.CheckInSubmission.patient_id == patient_id,
            models.CheckInSubmission.supersedes_submission_id == submission_id,
        )
        return self._require_session().scalar(statement)

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work has not been entered")
        return self._session

    def _validate_organization(self, organization_id: UUID) -> None:
        if organization_id != self.organization_id:
            raise ValueError(
                "Lookup organization_id does not match the unit of work organization scope"
            )

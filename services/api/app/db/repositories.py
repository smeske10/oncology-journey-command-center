from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar, cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import models

TenantEntity = TypeVar("TenantEntity", bound="TenantScoped")


class TenantScoped(Protocol):
    organization_id: UUID


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

    def find_active_care_episode(self, *, patient_id: UUID) -> models.CareEpisode | None:
        statement = select(models.CareEpisode).where(
            models.CareEpisode.organization_id == self.organization_id,
            models.CareEpisode.patient_id == patient_id,
            models.CareEpisode.status == "active",
        )
        return self._require_session().scalar(statement)

    def definition_matches_effective_pathway(
        self, *, care_episode_id: UUID, check_in_definition_id: UUID, at: datetime | None = None
    ) -> bool:
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
                models.EpisodePathwayAssignment.organization_id == self.organization_id,
                models.EpisodePathwayAssignment.care_episode_id == care_episode_id,
                models.CheckInDefinition.id == check_in_definition_id,
                models.EpisodePathwayAssignment.effective_from <= at,
                models.EpisodePathwayAssignment.effective_to.is_(None)
                | (at < models.EpisodePathwayAssignment.effective_to),
            )
        )
        return self._require_session().scalar(statement) is not None

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work has not been entered")
        return self._session

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

TenantEntity = TypeVar("TenantEntity", bound="TenantScoped")


class TenantScoped(Protocol):
    organization_id: UUID


class UnitOfWork(Protocol):
    """Transaction boundary for domain commands."""

    organization_id: UUID

    def add(self, entity: TenantScoped) -> None: ...

    def get(self, model: type[TenantEntity], entity_id: UUID) -> TenantEntity | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


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

    def get(self, model: type[TenantEntity], entity_id: UUID) -> TenantEntity | None:
        statement = select(model).where(
            getattr(model, "id") == entity_id,
            getattr(model, "organization_id") == self.organization_id,
        )
        return cast(TenantEntity | None, self._require_session().scalar(statement))

    def commit(self) -> None:
        self._require_session().commit()

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work has not been entered")
        return self._session

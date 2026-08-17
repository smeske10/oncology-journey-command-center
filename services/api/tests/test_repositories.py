from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.db.models import ReportedNeed
from app.db.repositories import SqlAlchemyUnitOfWork


class CapturingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.statement: object | None = None
        self.committed = False
        self.closed = False

    def add(self, entity: object) -> None:
        self.added.append(entity)

    def scalar(self, statement: object) -> None:
        self.statement = statement
        return None

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_unit_of_work_requires_scope_and_rejects_cross_tenant_operations() -> None:
    organization_id = uuid4()
    other_organization_id = uuid4()
    session = CapturingSession()
    unit_of_work = SqlAlchemyUnitOfWork(organization_id, lambda: session)  # type: ignore[arg-type]

    assert not hasattr(unit_of_work, "session")
    with unit_of_work:
        unit_of_work.add(SimpleNamespace(organization_id=organization_id))
        with pytest.raises(ValueError, match="organization scope"):
            unit_of_work.add(SimpleNamespace(organization_id=other_organization_id))
        assert unit_of_work.get(ReportedNeed, UUID(int=1)) is None

    assert session.added == [SimpleNamespace(organization_id=organization_id)]
    assert session.statement is not None
    assert "organization_id" in str(session.statement)

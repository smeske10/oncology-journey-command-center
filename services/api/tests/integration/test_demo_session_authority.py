from __future__ import annotations

import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.authority import (
    AmbiguousAuthorityError,
    AuthorityDatabaseUnavailableError,
    authority_from_row,
    build_authority_statement,
)
from app.auth.models import CurrentActor, ResolvedAuthority, Role
from app.auth.service import SqlAlchemyActorRepository
from app.db.models import (
    Organization,
    PatientIdentityLink,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from tests.database_support import DisposableDatabase, disposable_database

CHECKED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class AuthorityDatabase:
    database: DisposableDatabase

    @contextmanager
    def owner_session(self) -> Iterator[Session]:
        engine = create_engine(self.database.migration_url)
        try:
            with Session(engine) as session:
                yield session
        finally:
            engine.dispose()

    @contextmanager
    def runtime_session(self) -> Iterator[Session]:
        engine = create_engine(self.database.application_url)
        try:
            with Session(engine) as session:
                yield session
        finally:
            engine.dispose()


@pytest.fixture(scope="module")
def authority_database() -> Iterator[AuthorityDatabase]:
    with disposable_database(prefix="ojcc_migration_test_", migrate_to="head") as database:
        yield AuthorityDatabase(database=database)


def _commit_identity_and_roles(
    authority_database: AuthorityDatabase,
    *,
    organization_ids: set[UUID],
    user_id: UUID,
    roles: list[RoleAssignment],
    is_active: bool = True,
    primary_organization_id: UUID | None = None,
) -> None:
    with authority_database.owner_session() as owner:
        owner.add_all(
            [
                Organization(id=organization_id, name=f"Authority org {uuid4()}")
                for organization_id in organization_ids
            ]
        )
        owner.flush()
        owner.add(
            User(
                id=user_id,
                email=f"authority-{uuid4()}@example.test",
                display_name="Authority actor",
                is_active=is_active,
                primary_organization_id=primary_organization_id,
            )
        )
        owner.flush()
        owner.add_all(roles)
        owner.commit()


def _resolve_authority(
    authority_database: AuthorityDatabase,
    *,
    organization_id: UUID,
    user_id: UUID,
    role: Role = Role.NAVIGATOR,
) -> ResolvedAuthority | None:
    with authority_database.runtime_session() as runtime:
        return SqlAlchemyActorRepository(runtime).resolve_authority(
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            at=CHECKED_AT,
        )


def test_authority_fixture_commits_owner_setup_for_runtime_select(
    authority_database: AuthorityDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    with authority_database.owner_session() as owner:
        owner.add_all(
            [
                Organization(id=organization_id, name=f"Authority org {uuid4()}"),
                User(
                    id=user_id,
                    email=f"authority-{uuid4()}@example.test",
                    display_name="Authority actor",
                    primary_organization_id=None,
                ),
            ]
        )
        owner.commit()

    with authority_database.runtime_session() as runtime:
        observed_id = runtime.execute(
            select(User.id).where(User.id == user_id)
        ).scalar_one()

    assert observed_id == UUID(str(user_id))


def test_overlapping_roles_refuse_authority(
    authority_database: AuthorityDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    with authority_database.owner_session() as owner:
        owner.add_all(
            [
                Organization(id=organization_id, name=f"Overlap org {uuid4()}"),
                User(
                    id=user_id,
                    email=f"overlap-{uuid4()}@example.test",
                    display_name="Overlapping actor",
                    primary_organization_id=None,
                ),
            ]
        )
        owner.flush()
        owner.add_all(
            [
                RoleAssignment(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.SUPPORTING_ACTOR,
                    granted_at=CHECKED_AT - timedelta(hours=2),
                    revoked_at=CHECKED_AT + timedelta(hours=1),
                ),
                RoleAssignment(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.SUPPORTING_ACTOR,
                    granted_at=CHECKED_AT - timedelta(hours=1),
                ),
            ]
        )
        owner.commit()

    with authority_database.runtime_session() as runtime:
        repository = SqlAlchemyActorRepository(runtime)
        with pytest.raises(AmbiguousAuthorityError):
            repository.resolve_authority(
                organization_id=organization_id,
                user_id=user_id,
                role=Role.SUPPORTING_ACTOR,
                at=CHECKED_AT,
            )


def test_authority_statement_uses_half_open_role_intervals_without_row_hiding() -> None:
    organization_id = UUID("00000000-0000-0000-0000-000000000101")
    user_id = UUID("00000000-0000-0000-0000-000000000102")

    statement = build_authority_statement(
        organization_id=organization_id,
        user_id=user_id,
        role=Role.NAVIGATOR,
        at=CHECKED_AT,
    )
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "role_assignment.organization_id = '00000000-0000-0000-0000-000000000101'" in compiled
    assert "role_assignment.user_id = '00000000-0000-0000-0000-000000000102'" in compiled
    assert "role_assignment.granted_at <= '2026-09-13 12:00:00+00:00'" in compiled
    assert "role_assignment.revoked_at is null" in compiled
    assert "role_assignment.revoked_at > '2026-09-13 12:00:00+00:00'" in compiled
    assert "distinct" not in compiled
    assert " limit " not in compiled


@pytest.mark.parametrize("role_ids", [None, []])
def test_authority_interpreter_role_zero_is_unavailable(
    role_ids: list[UUID] | None,
) -> None:
    result = authority_from_row(
        {
            "organization_exists": True,
            "user_exists": True,
            "user_is_active": True,
            "role_assignment_ids": role_ids,
        },
        organization_id=UUID("00000000-0000-0000-0000-000000000201"),
        user_id=UUID("00000000-0000-0000-0000-000000000202"),
        role=Role.NAVIGATOR,
    )

    assert result is None


def test_authority_interpreter_role_one_extracts_exact_grant() -> None:
    organization_id = UUID("00000000-0000-0000-0000-000000000211")
    user_id = UUID("00000000-0000-0000-0000-000000000212")
    role_assignment_id = UUID("00000000-0000-0000-0000-000000000213")

    result = authority_from_row(
        {
            "organization_exists": True,
            "user_exists": True,
            "user_is_active": True,
            "role_assignment_ids": [role_assignment_id],
        },
        organization_id=organization_id,
        user_id=user_id,
        role=Role.NAVIGATOR,
    )

    assert result == ResolvedAuthority(
        actor=CurrentActor(
            user_id=user_id,
            organization_id=organization_id,
            role=Role.NAVIGATOR,
        ),
        role_assignment_id=role_assignment_id,
    )


def test_authority_interpreter_role_many_is_ambiguous() -> None:
    with pytest.raises(AmbiguousAuthorityError):
        authority_from_row(
            {
                "organization_exists": True,
                "user_exists": True,
                "user_is_active": True,
                "role_assignment_ids": [
                    UUID("00000000-0000-0000-0000-000000000221"),
                    UUID("00000000-0000-0000-0000-000000000222"),
                ],
            },
            organization_id=UUID("00000000-0000-0000-0000-000000000223"),
            user_id=UUID("00000000-0000-0000-0000-000000000224"),
            role=Role.NAVIGATOR,
        )


@pytest.mark.parametrize(
    "anchor_update",
    [
        {"organization_exists": False},
        {"user_exists": False},
        {"user_is_active": False},
        {"user_is_active": None},
    ],
)
def test_authority_interpreter_rejects_invalid_anchor(
    anchor_update: dict[str, bool | None],
) -> None:
    row: dict[str, object] = {
        "organization_exists": True,
        "user_exists": True,
        "user_is_active": True,
        "role_assignment_ids": [UUID("00000000-0000-0000-0000-000000000231")],
    }
    row.update(anchor_update)

    assert (
        authority_from_row(
            row,
            organization_id=UUID("00000000-0000-0000-0000-000000000232"),
            user_id=UUID("00000000-0000-0000-0000-000000000233"),
            role=Role.NAVIGATOR,
        )
        is None
    )


@pytest.mark.parametrize(
    ("granted_at", "revoked_at", "is_available"),
    [
        (CHECKED_AT, None, True),
        (CHECKED_AT - timedelta(hours=2), CHECKED_AT, False),
        (CHECKED_AT + timedelta(seconds=1), None, False),
        (CHECKED_AT - timedelta(hours=1), CHECKED_AT + timedelta(seconds=1), True),
        (CHECKED_AT - timedelta(hours=2), CHECKED_AT - timedelta(hours=1), False),
        (CHECKED_AT - timedelta(hours=1), CHECKED_AT - timedelta(hours=1), False),
    ],
    ids=[
        "starts-at-t",
        "ends-at-t",
        "future-grant",
        "future-revocation",
        "expired",
        "zero-length",
    ],
)
def test_temporal_role_uses_half_open_intervals(
    authority_database: AuthorityDatabase,
    granted_at: datetime,
    revoked_at: datetime | None,
    is_available: bool,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    role_assignment_id = uuid4()
    _commit_identity_and_roles(
        authority_database,
        organization_ids={organization_id},
        user_id=user_id,
        roles=[
            RoleAssignment(
                id=role_assignment_id,
                organization_id=organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=granted_at,
                revoked_at=revoked_at,
            )
        ],
    )

    authority = _resolve_authority(
        authority_database,
        organization_id=organization_id,
        user_id=user_id,
    )

    if is_available:
        assert authority is not None
        assert authority.role_assignment_id == role_assignment_id
    else:
        assert authority is None


def test_temporal_role_adjacent_grants_select_only_the_new_interval(
    authority_database: AuthorityDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    current_role_id = uuid4()
    _commit_identity_and_roles(
        authority_database,
        organization_ids={organization_id},
        user_id=user_id,
        roles=[
            RoleAssignment(
                id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
                revoked_at=CHECKED_AT,
            ),
            RoleAssignment(
                id=current_role_id,
                organization_id=organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=CHECKED_AT,
            ),
        ],
    )

    authority = _resolve_authority(
        authority_database,
        organization_id=organization_id,
        user_id=user_id,
    )

    assert authority is not None
    assert authority.role_assignment_id == current_role_id


def test_temporal_role_ignores_disjoint_role_and_organization(
    authority_database: AuthorityDatabase,
) -> None:
    target_organization_id = uuid4()
    other_organization_id = uuid4()
    user_id = uuid4()
    _commit_identity_and_roles(
        authority_database,
        organization_ids={target_organization_id, other_organization_id},
        user_id=user_id,
        roles=[
            RoleAssignment(
                id=uuid4(),
                organization_id=target_organization_id,
                user_id=user_id,
                role=Role.ADMINISTRATOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
            ),
            RoleAssignment(
                id=uuid4(),
                organization_id=other_organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
            ),
        ],
    )

    assert (
        _resolve_authority(
            authority_database,
            organization_id=target_organization_id,
            user_id=user_id,
        )
        is None
    )


def test_temporal_role_rejects_inactive_user(
    authority_database: AuthorityDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    _commit_identity_and_roles(
        authority_database,
        organization_ids={organization_id},
        user_id=user_id,
        is_active=False,
        roles=[
            RoleAssignment(
                id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
            )
        ],
    )

    assert (
        _resolve_authority(
            authority_database,
            organization_id=organization_id,
            user_id=user_id,
        )
        is None
    )


@pytest.mark.parametrize("missing", ["organization", "user"])
def test_temporal_role_rejects_missing_user_or_organization(
    authority_database: AuthorityDatabase,
    missing: str,
) -> None:
    existing_organization_id = uuid4()
    existing_user_id = uuid4()
    _commit_identity_and_roles(
        authority_database,
        organization_ids={existing_organization_id},
        user_id=existing_user_id,
        roles=[],
    )

    assert (
        _resolve_authority(
            authority_database,
            organization_id=(uuid4() if missing == "organization" else existing_organization_id),
            user_id=(uuid4() if missing == "user" else existing_user_id),
        )
        is None
    )


@pytest.mark.parametrize("primary_kind", ["null", "different"])
def test_temporal_role_does_not_require_primary_organization_match(
    authority_database: AuthorityDatabase,
    primary_kind: str,
) -> None:
    organization_id = uuid4()
    other_organization_id = uuid4()
    user_id = uuid4()
    role_assignment_id = uuid4()
    primary_organization_id = None if primary_kind == "null" else other_organization_id
    _commit_identity_and_roles(
        authority_database,
        organization_ids={organization_id, other_organization_id},
        user_id=user_id,
        primary_organization_id=primary_organization_id,
        roles=[
            RoleAssignment(
                id=role_assignment_id,
                organization_id=organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
            )
        ],
    )

    authority = _resolve_authority(
        authority_database,
        organization_id=organization_id,
        user_id=user_id,
    )

    assert authority is not None
    assert authority.role_assignment_id == role_assignment_id


def test_authority_database_error_is_sanitized() -> None:
    sentinel = "authority-driver-secret-sentinel"

    class FailingSession:
        def execute(self, _statement: object) -> None:
            raise SQLAlchemyError(sentinel)

    repository = SqlAlchemyActorRepository(FailingSession())  # type: ignore[arg-type]

    with pytest.raises(AuthorityDatabaseUnavailableError) as exc_info:
        repository.resolve_authority(
            organization_id=UUID("00000000-0000-0000-0000-000000000301"),
            user_id=UUID("00000000-0000-0000-0000-000000000302"),
            role=Role.NAVIGATOR,
            at=CHECKED_AT,
        )

    rendered = "".join(traceback.format_exception(exc_info.value))
    assert str(exc_info.value) == "Demo session authority database is unavailable"
    assert sentinel not in rendered


@pytest.mark.parametrize("link_shape", ["same-patient", "two-patients"])
def test_multiple_effective_links_refuse_authority(
    authority_database: AuthorityDatabase,
    link_shape: str,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    first_patient_id = uuid4()
    second_patient_id = first_patient_id if link_shape == "same-patient" else uuid4()
    patient_ids = {first_patient_id, second_patient_id}
    with authority_database.owner_session() as owner:
        owner.add(Organization(id=organization_id, name=f"Link org {uuid4()}"))
        owner.flush()
        owner.add(
            User(
                id=user_id,
                email=f"links-{uuid4()}@example.test",
                display_name="Linked actor",
                primary_organization_id=None,
            )
        )
        owner.add_all(
            [
                SyntheticPatient(
                    id=patient_id,
                    organization_id=organization_id,
                    external_ref=f"authority-patient-{uuid4()}",
                    display_name="Authority patient",
                )
                for patient_id in patient_ids
            ]
        )
        owner.flush()
        owner.add(
            RoleAssignment(
                id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                role=Role.SUPPORTING_ACTOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
            )
        )
        owner.add_all(
            [
                PatientIdentityLink(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    patient_id=patient_id,
                    linked_at=CHECKED_AT - timedelta(hours=1),
                    revoked_at=CHECKED_AT + timedelta(hours=1),
                )
                for patient_id in (first_patient_id, second_patient_id)
            ]
        )
        owner.commit()

    with authority_database.runtime_session() as runtime:
        with pytest.raises(AmbiguousAuthorityError):
            SqlAlchemyActorRepository(runtime).resolve_authority(
                organization_id=organization_id,
                user_id=user_id,
                role=Role.SUPPORTING_ACTOR,
                at=CHECKED_AT,
            )


def test_reverse_patient_link_conflict_refuses_authority(
    authority_database: AuthorityDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    conflicting_user_id = uuid4()
    patient_id = uuid4()
    with authority_database.owner_session() as owner:
        owner.add(Organization(id=organization_id, name=f"Reverse link org {uuid4()}"))
        owner.flush()
        owner.add_all(
            [
                User(
                    id=user_id,
                    email=f"selected-{uuid4()}@example.test",
                    display_name="Selected actor",
                    primary_organization_id=None,
                ),
                User(
                    id=conflicting_user_id,
                    email=f"inactive-conflict-{uuid4()}@example.test",
                    display_name="Inactive conflicting actor",
                    is_active=False,
                    primary_organization_id=None,
                ),
                SyntheticPatient(
                    id=patient_id,
                    organization_id=organization_id,
                    external_ref=f"reverse-link-patient-{uuid4()}",
                    display_name="Reverse-link patient",
                ),
            ]
        )
        owner.flush()
        owner.add_all(
            [
                RoleAssignment(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.SUPPORTING_ACTOR,
                    granted_at=CHECKED_AT - timedelta(hours=1),
                ),
                PatientIdentityLink(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    patient_id=patient_id,
                    linked_at=CHECKED_AT - timedelta(hours=1),
                ),
                PatientIdentityLink(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=conflicting_user_id,
                    patient_id=patient_id,
                    linked_at=CHECKED_AT - timedelta(hours=1),
                    revoked_at=CHECKED_AT + timedelta(hours=1),
                ),
            ]
        )
        owner.commit()

    with authority_database.runtime_session() as runtime:
        with pytest.raises(AmbiguousAuthorityError):
            SqlAlchemyActorRepository(runtime).resolve_authority(
                organization_id=organization_id,
                user_id=user_id,
                role=Role.SUPPORTING_ACTOR,
                at=CHECKED_AT,
            )


def test_staff_role_ignores_patient_links(
    authority_database: AuthorityDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    patient_ids = [uuid4(), uuid4()]
    role_assignment_id = uuid4()
    with authority_database.owner_session() as owner:
        owner.add(Organization(id=organization_id, name=f"Staff link org {uuid4()}"))
        owner.flush()
        owner.add(
            User(
                id=user_id,
                email=f"staff-links-{uuid4()}@example.test",
                display_name="Staff actor",
                primary_organization_id=None,
            )
        )
        owner.add_all(
            [
                SyntheticPatient(
                    id=patient_id,
                    organization_id=organization_id,
                    external_ref=f"staff-link-patient-{uuid4()}",
                    display_name="Unrelated patient",
                )
                for patient_id in patient_ids
            ]
        )
        owner.flush()
        owner.add(
            RoleAssignment(
                id=role_assignment_id,
                organization_id=organization_id,
                user_id=user_id,
                role=Role.NAVIGATOR,
                granted_at=CHECKED_AT - timedelta(hours=1),
            )
        )
        owner.add_all(
            [
                PatientIdentityLink(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    patient_id=patient_id,
                    linked_at=CHECKED_AT - timedelta(hours=1),
                    revoked_at=CHECKED_AT + timedelta(hours=1),
                )
                for patient_id in patient_ids
            ]
        )
        owner.commit()

    authority = _resolve_authority(
        authority_database,
        organization_id=organization_id,
        user_id=user_id,
        role=Role.NAVIGATOR,
    )

    assert authority is not None
    assert authority.role_assignment_id == role_assignment_id
    assert authority.actor.patient_id is None
    assert authority.patient_identity_link_id is None

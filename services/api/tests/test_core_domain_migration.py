# ruff: noqa: E501

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from app.config import settings
from app.db.base import Base
from app.db.models import EpisodePathwayAssignment

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_core_domain.py"
)
EXPECTED_INITIAL_MIGRATION_SHA256 = (
    "a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391"
)
DISPOSABLE_MIGRATION_DATABASE_PREFIX = "ojcc_migration_test_"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _run_alembic(database_url: str, revision: str) -> subprocess.CompletedProcess[str]:
    project_root = MIGRATION_PATH.parents[4]
    environment = os.environ | {"DATABASE_URL": database_url}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _run_alembic_without_checking(
    database_url: str, revision: str
) -> subprocess.CompletedProcess[str]:
    project_root = MIGRATION_PATH.parents[4]
    environment = os.environ | {"DATABASE_URL": database_url}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _validate_loopback_postgres_url(url: URL) -> None:
    if url.get_backend_name() != "postgresql" or url.host not in LOOPBACK_HOSTS:
        raise ValueError("Migration test databases must use a loopback PostgreSQL URL")
    if url.port not in (None, 5432):
        raise ValueError("Migration test databases must use the local PostgreSQL port")


def _validate_disposable_database_url(url: URL) -> None:
    _validate_loopback_postgres_url(url)
    database = url.database
    if database is None or not database.startswith(DISPOSABLE_MIGRATION_DATABASE_PREFIX):
        raise ValueError("Refusing to operate on a non-disposable migration test database")
    suffix = database.removeprefix(DISPOSABLE_MIGRATION_DATABASE_PREFIX)
    if len(suffix) != 32 or any(character not in "0123456789abcdef" for character in suffix):
        raise ValueError("Refusing to operate on an invalid disposable migration test database")


def _build_disposable_database_url() -> URL:
    configured_url = make_url(settings.database_url)
    _validate_loopback_postgres_url(configured_url)
    disposable_url = configured_url.set(
        database=f"{DISPOSABLE_MIGRATION_DATABASE_PREFIX}{uuid4().hex}"
    )
    _validate_disposable_database_url(disposable_url)
    return disposable_url


@contextmanager
def _disposable_migration_database() -> Iterator[str]:
    disposable_url = _build_disposable_database_url()
    _validate_disposable_database_url(disposable_url)
    database = disposable_url.database
    assert database is not None
    admin_url = disposable_url.set(database="postgres")
    _validate_loopback_postgres_url(admin_url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    created = False

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        yield disposable_url.render_as_string(hide_password=False)
    finally:
        if created:
            _validate_disposable_database_url(disposable_url)
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f'DROP DATABASE "{database}"'))
        admin_engine.dispose()


def test_initial_migration_is_an_immutable_explicit_schema_snapshot() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert (
        hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest() == EXPECTED_INITIAL_MIGRATION_SHA256
    )
    assert "app.db.models" not in source
    assert "app.db.base" not in source
    assert "Base.metadata" not in source
    assert "op.create_table" in source
    assert "op.drop_table" in source

    project_root = MIGRATION_PATH.parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    sql = result.stdout
    migration_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATION_PATH.parent.glob("*.py"))
    )
    for table in Base.metadata.tables.values():
        assert f'"{table.name}"' in migration_sources
        assert f"CREATE TABLE {table.name}" in sql
        for constraint in table.constraints:
            if constraint.name and len(constraint.name) <= 63:
                assert constraint.name in sql
        for index in table.indexes:
            assert index.name in sql


def test_populated_upgrade_uses_a_disposable_database_not_the_configured_application_database() -> (
    None
):
    disposable_url = _build_disposable_database_url()

    assert disposable_url.database != make_url(settings.database_url).database
    assert disposable_url.database is not None
    assert disposable_url.database.startswith("ojcc_migration_test_")
    with pytest.raises(
        ValueError, match="Refusing to operate on a non-disposable migration test database"
    ):
        _validate_disposable_database_url(make_url(settings.database_url))
    with pytest.raises(
        ValueError, match="Migration test databases must use a loopback PostgreSQL URL"
    ):
        _validate_disposable_database_url(disposable_url.set(host="database.example.test"))


def test_0002_installed_episode_pathway_constraint_name_matches_orm_metadata() -> None:
    expected_name = next(
        constraint.name
        for constraint in EpisodePathwayAssignment.__table__.constraints
        if getattr(constraint, "sqltext", None) is not None
    )
    assert expected_name is not None

    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "head")
        engine = create_engine(database_url)
        try:
            installed_names = {
                constraint["name"]
                for constraint in inspect(engine).get_check_constraints(
                    "episode_pathway_assignment"
                )
            }
        finally:
            engine.dispose()

    assert expected_name in installed_names


def test_0003_installs_separate_reported_need_insert_and_update_guards() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                installed_guards = {
                    (row.trigger_name, row.event_manipulation)
                    for row in connection.execute(
                        text(
                            "SELECT trigger_name, event_manipulation "
                            "FROM information_schema.triggers "
                            "WHERE event_object_schema = current_schema() "
                            "AND event_object_table = 'reported_need'"
                        )
                    )
                }
        finally:
            engine.dispose()

    assert installed_guards == {
        ("trg_reported_need_identity_update_guard", "UPDATE"),
        ("trg_reported_need_reopening_guard", "INSERT"),
    }


def test_upgrade_from_representative_0001_rows_preserves_unambiguous_history() -> None:
    """This fails if reconciliation rejects legacy rows whose identity and provenance are explicit."""
    organization_id = uuid4()
    patient_user_id = uuid4()
    pathway_id = uuid4()
    episode_id = uuid4()
    definition_id = uuid4()
    submission_id = uuid4()
    need_id = uuid4()
    task_id = uuid4()
    created_at = datetime(2026, 8, 18, tzinfo=UTC)
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0001_core_domain")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                {"id": organization_id, "name": "Representative synthetic organization"},
            )
            connection.execute(
                text(
                    "INSERT INTO user_account "
                    "(id, organization_id, email, display_name, is_active, created_at) "
                    "VALUES (:id, :organization_id, :email, :display_name, true, :created_at)"
                ),
                {
                    "id": patient_user_id,
                    "organization_id": organization_id,
                    "email": "representative.patient@example.test",
                    "display_name": "Representative Patient",
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO synthetic_patient "
                    "(id, organization_id, external_ref, display_name, demographics, created_at) "
                    "VALUES (:id, :organization_id, :external_ref, :display_name, '{}'::jsonb, :created_at)"
                ),
                {
                    "id": patient_user_id,
                    "organization_id": organization_id,
                    "external_ref": "representative-patient",
                    "display_name": "Representative Patient",
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO role_assignment (id, organization_id, user_id, role, created_at) "
                    "VALUES (:id, :organization_id, :user_id, 'supporting_actor', :created_at)"
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "user_id": patient_user_id,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO pathway_definition "
                    "(id, organization_id, slug, version, name, configuration, is_active, created_at) "
                    "VALUES (:id, :organization_id, 'representative', 1, 'Representative', '{}'::jsonb, true, :created_at)"
                ),
                {"id": pathway_id, "organization_id": organization_id, "created_at": created_at},
            )
            connection.execute(
                text(
                    "INSERT INTO care_episode "
                    "(id, organization_id, patient_id, pathway_definition_id, status, started_at) "
                    "VALUES (:id, :organization_id, :patient_id, :pathway_id, 'active', :started_at)"
                ),
                {
                    "id": episode_id,
                    "organization_id": organization_id,
                    "patient_id": patient_user_id,
                    "pathway_id": pathway_id,
                    "started_at": created_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO check_in_definition "
                    "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire, created_at) "
                    "VALUES (:id, :organization_id, :pathway_id, 'weekly', 1, 'Weekly', '{}'::jsonb, :created_at)"
                ),
                {
                    "id": definition_id,
                    "organization_id": organization_id,
                    "pathway_id": pathway_id,
                    "created_at": created_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO check_in_submission "
                    "(id, organization_id, patient_id, check_in_definition_id, status, answers, submitted_at, created_at) "
                    "VALUES (:id, :organization_id, :patient_id, :definition_id, 'submitted', '{}'::jsonb, :submitted_at, :created_at)"
                ),
                {
                    "id": submission_id,
                    "organization_id": organization_id,
                    "patient_id": patient_user_id,
                    "definition_id": definition_id,
                    "submitted_at": created_at,
                    "created_at": created_at,
                },
            )
        engine.dispose()

        _run_alembic(database_url, "0002_identity_pathway_submission")

        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO reported_need "
                    "(id, organization_id, patient_id, source_submission_id, kind, status, evidence) "
                    "VALUES (:id, :organization_id, :patient_id, :submission_id, "
                    "'transportation', 'open', '[]'::jsonb)"
                ),
                {
                    "id": need_id,
                    "organization_id": organization_id,
                    "patient_id": patient_user_id,
                    "submission_id": submission_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO navigation_task "
                    "(id, organization_id, patient_id, reported_need_id, assignee_user_id, "
                    "title, status) VALUES (:id, :organization_id, :patient_id, :need_id, "
                    ":assignee_user_id, 'Representative assigned task', 'open')"
                ),
                {
                    "id": task_id,
                    "organization_id": organization_id,
                    "patient_id": patient_user_id,
                    "need_id": need_id,
                    "assignee_user_id": patient_user_id,
                },
            )
        engine.dispose()

        _run_alembic(database_url, "head")

        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT patient_id FROM patient_identity_link")
                ).scalar_one()
                == patient_user_id
            )
            assert (
                connection.execute(
                    text("SELECT care_episode_id FROM check_in_submission")
                ).scalar_one()
                == episode_id
            )
            assert (
                connection.execute(
                    text("SELECT submitted_by_user_id FROM check_in_submission")
                ).scalar_one()
                == patient_user_id
            )
            migrated_need = connection.execute(
                text(
                    "SELECT care_episode_id, status, effective_state "
                    "FROM effective_need_state WHERE id = :need_id"
                ),
                {"need_id": need_id},
            ).mappings().one()
            assert migrated_need.care_episode_id == episode_id
            assert migrated_need.status == "in_progress"
            assert migrated_need.effective_state == "in_progress"
            migrated_task = connection.execute(
                text(
                    "SELECT reported_need_id, status FROM navigation_task WHERE id = :task_id"
                ),
                {"task_id": task_id},
            ).mappings().one()
            assert migrated_task.reported_need_id == need_id
            assert migrated_task.status == "assigned"
        engine.dispose()


@pytest.mark.parametrize(
    ("legacy_terminal_kind", "expected_diagnostic"),
    [
        (
            "closed_need",
            (
                "legacy terminal need/outcome data",
                "without a provable Outcome recorder",
            ),
        ),
        (
            "cancelled_task",
            (
                "legacy cancelled navigation task",
                "without provable cancellation actor, timestamp, and reason provenance",
            ),
        ),
    ],
)
def test_0003_fails_precisely_when_legacy_terminal_authorship_is_ambiguous(
    legacy_terminal_kind: str,
    expected_diagnostic: tuple[str, str],
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    patient_id = uuid4()
    pathway_id = uuid4()
    episode_id = uuid4()
    definition_id = uuid4()
    submission_id = uuid4()
    need_id = uuid4()
    task_id = uuid4()
    now = datetime(2026, 8, 18, tzinfo=UTC)

    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0002_identity_pathway_submission")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                {"id": organization_id, "name": "Terminal migration diagnostic"},
            )
            connection.execute(
                text(
                    "INSERT INTO user_account "
                    "(id, primary_organization_id, email, display_name, is_active) "
                    "VALUES (:id, :organization_id, :email, 'Recorder unknown', true)"
                ),
                {
                    "id": user_id,
                    "organization_id": organization_id,
                    "email": f"terminal-{uuid4()}@example.test",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO synthetic_patient "
                    "(id, organization_id, external_ref, display_name, demographics) "
                    "VALUES (:id, :organization_id, :external_ref, 'Synthetic patient', '{}'::jsonb)"
                ),
                {
                    "id": patient_id,
                    "organization_id": organization_id,
                    "external_ref": f"terminal-{uuid4()}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO pathway_definition "
                    "(id, organization_id, slug, version, name, configuration, is_active) "
                    "VALUES (:id, :organization_id, :slug, 1, 'Pathway', '{}'::jsonb, true)"
                ),
                {
                    "id": pathway_id,
                    "organization_id": organization_id,
                    "slug": f"terminal-{uuid4()}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO care_episode "
                    "(id, organization_id, patient_id, status, started_at) "
                    "VALUES (:id, :organization_id, :patient_id, 'active', :started_at)"
                ),
                {
                    "id": episode_id,
                    "organization_id": organization_id,
                    "patient_id": patient_id,
                    "started_at": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO check_in_definition "
                    "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire) "
                    "VALUES (:id, :organization_id, :pathway_id, :slug, 1, 'Check-in', '{}'::jsonb)"
                ),
                {
                    "id": definition_id,
                    "organization_id": organization_id,
                    "pathway_id": pathway_id,
                    "slug": f"terminal-check-in-{uuid4()}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO check_in_submission "
                    "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, "
                    "status, answers, submission_source, submitted_by_user_id, submitted_at) "
                    "VALUES (:id, :organization_id, :patient_id, :episode_id, :definition_id, "
                    "'submitted', '{}'::jsonb, 'patient', :user_id, :submitted_at)"
                ),
                {
                    "id": submission_id,
                    "organization_id": organization_id,
                    "patient_id": patient_id,
                    "episode_id": episode_id,
                    "definition_id": definition_id,
                    "user_id": user_id,
                    "submitted_at": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO reported_need "
                    "(id, organization_id, patient_id, source_submission_id, kind, status, evidence, resolved_at) "
                    "VALUES (:id, :organization_id, :patient_id, :submission_id, "
                    "'symptom_change', :status, '[]'::jsonb, :resolved_at)"
                ),
                {
                    "id": need_id,
                    "organization_id": organization_id,
                    "patient_id": patient_id,
                    "submission_id": submission_id,
                    "status": "closed" if legacy_terminal_kind == "closed_need" else "open",
                    "resolved_at": now if legacy_terminal_kind == "closed_need" else None,
                },
            )
            if legacy_terminal_kind == "cancelled_task":
                connection.execute(
                    text(
                        "INSERT INTO navigation_task "
                        "(id, organization_id, patient_id, reported_need_id, title, status) "
                        "VALUES (:id, :organization_id, :patient_id, :need_id, "
                        "'Legacy cancelled task', 'cancelled')"
                    ),
                    {
                        "id": task_id,
                        "organization_id": organization_id,
                        "patient_id": patient_id,
                        "need_id": need_id,
                    },
                )
        engine.dispose()

        result = _run_alembic_without_checking(database_url, "0003_need_task_outcome_lifecycle")

    assert result.returncode != 0
    diagnostic = result.stdout + result.stderr
    assert expected_diagnostic[0] in diagnostic
    assert expected_diagnostic[1] in diagnostic
    assert "reset the synthetic demo database" in diagnostic

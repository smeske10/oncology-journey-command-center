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

import psycopg
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, make_url

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


def _render_alembic_sql(
    database_url: str,
    command: str,
    revision_range: str,
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
            command,
            revision_range,
            "--sql",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _run_alembic_check(database_url: str) -> subprocess.CompletedProcess[str]:
    project_root = MIGRATION_PATH.parents[4]
    environment = os.environ | {"DATABASE_URL": database_url}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "check",
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


def test_0004_executes_value_schema_seed_with_json_literals() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0003_need_task_outcome_lifecycle")

        result = _run_alembic_without_checking(
            database_url, "0004_safety_approval_lifecycle"
        )

        assert result.returncode == 0, result.stdout + result.stderr
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                schema_document = connection.scalar(
                    text(
                        "SELECT schema_document FROM proposed_value_schema "
                        "WHERE value_schema_id = 'ojcc.authorize-navigation-task'"
                    )
                )
        finally:
            engine.dispose()

    assert schema_document == {
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 255}
        },
    }


def test_0004_upgrade_matches_current_orm_metadata() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "head")

        result = _run_alembic_check(database_url)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new upgrade operations detected." in result.stdout


def test_0004_upgrades_empty_database_and_installs_safety_approval_contract() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0004_safety_approval_lifecycle")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert {
                "signal_rule",
                "safety_signal_resolution",
                "approval_policy",
                "proposed_value_schema",
                "proposed_change",
                "patient_message",
            } <= set(inspector.get_table_names())
            assert {
                "effective_safety_signal_state",
                "effective_proposed_change_state",
            } <= set(inspector.get_view_names())
            with engine.connect() as connection:
                triggers = {
                    row.tgname
                    for row in connection.execute(
                        text(
                            "SELECT tgname FROM pg_trigger "
                            "WHERE NOT tgisinternal AND tgrelid IN "
                            "('safety_signal'::regclass, 'safety_signal_resolution'::regclass, "
                            "'signal_rule'::regclass, 'approval_policy'::regclass, "
                            "'proposed_value_schema'::regclass, 'role_assignment'::regclass, "
                            "'proposed_change'::regclass, 'approval_decision'::regclass)"
                        )
                    )
                }
                value_schema_keys = set(
                    connection.execute(
                        text(
                            "SELECT change_type::text, value_schema_id, value_schema_version "
                            "FROM proposed_value_schema"
                        )
                    ).tuples()
                )
            assert {
                "trg_safety_signal_lifecycle_guard",
                "trg_safety_signal_resolution_guard",
                "trg_signal_rule_immutable",
                "trg_approval_policy_immutable",
                "trg_proposed_value_schema_immutable",
                "trg_role_assignment_approval_history_guard",
                "trg_proposed_change_revision_guard",
                "trg_approval_decision_guard",
                "trg_approval_decision_apply",
            } <= triggers
            assert value_schema_keys == {
                ("dismiss_signal", "ojcc.dismiss-signal", 1),
                ("override_signal_severity", "ojcc.override-signal-severity", 1),
                ("authorize_navigation_task", "ojcc.authorize-navigation-task", 1),
                ("authorize_patient_message", "ojcc.authorize-patient-message", 1),
            }
        finally:
            engine.dispose()


def test_0004_migrates_derivable_active_signal_with_rule_and_episode_provenance() -> None:
    organization_id = uuid4()
    user_id = uuid4()
    patient_id = uuid4()
    pathway_id = uuid4()
    episode_id = uuid4()
    definition_id = uuid4()
    submission_id = uuid4()
    signal_id = uuid4()
    now = datetime(2026, 8, 18, tzinfo=UTC)
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0003_need_task_outcome_lifecycle")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (id, name) VALUES (:id, '0004 populated')"),
                {"id": organization_id},
            )
            connection.execute(
                text(
                    "INSERT INTO user_account (id, email, display_name, is_active) "
                    "VALUES (:id, :email, 'Patient', true)"
                ),
                {"id": user_id, "email": f"0004-{uuid4()}@example.test"},
            )
            connection.execute(
                text(
                    "INSERT INTO synthetic_patient "
                    "(id, organization_id, external_ref, display_name, demographics) "
                    "VALUES (:id, :organization_id, :ref, 'Patient', '{}'::jsonb)"
                ),
                {"id": patient_id, "organization_id": organization_id, "ref": str(uuid4())},
            )
            connection.execute(
                text(
                    "INSERT INTO pathway_definition "
                    "(id, organization_id, slug, version, name, configuration, is_active) "
                    "VALUES (:id, :organization_id, :slug, 1, 'Pathway', '{}'::jsonb, true)"
                ),
                {"id": pathway_id, "organization_id": organization_id, "slug": str(uuid4())},
            )
            connection.execute(
                text(
                    "INSERT INTO care_episode "
                    "(id, organization_id, patient_id, status, started_at) "
                    "VALUES (:id, :organization_id, :patient_id, 'active', :now)"
                ),
                {"id": episode_id, "organization_id": organization_id,
                 "patient_id": patient_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO check_in_definition "
                    "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire) "
                    "VALUES (:id, :organization_id, :pathway, :slug, 1, 'Check-in', '{}'::jsonb)"
                ),
                {"id": definition_id, "organization_id": organization_id,
                 "pathway": pathway_id, "slug": str(uuid4())},
            )
            connection.execute(
                text(
                    "INSERT INTO check_in_submission "
                    "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, "
                    "status, answers, submission_source, submitted_by_user_id, submitted_at) "
                    "VALUES (:id, :organization_id, :patient_id, :episode_id, :definition, "
                    "'submitted', '{}'::jsonb, 'patient', :user_id, :now)"
                ),
                {"id": submission_id, "organization_id": organization_id,
                 "patient_id": patient_id, "episode_id": episode_id,
                 "definition": definition_id, "user_id": user_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO safety_signal "
                    "(id, organization_id, patient_id, source_submission_id, rule_code, severity, "
                    "status, evidence) VALUES (:id, :organization_id, :patient_id, :submission_id, "
                    "'urgent-language', 'urgent', 'active', '[]'::jsonb)"
                ),
                {"id": signal_id, "organization_id": organization_id,
                 "patient_id": patient_id, "submission_id": submission_id},
            )
        engine.dispose()
        _run_alembic(database_url, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT signal.care_episode_id, signal.deterministic_level, "
                        "signal.effective_level, signal.status, rule.rule_code, rule.version, "
                        "rule.rule_kind FROM safety_signal signal JOIN signal_rule rule "
                        "ON rule.id = signal.signal_rule_id WHERE signal.id = :signal_id"
                    ),
                    {"signal_id": signal_id},
                ).mappings().one()
            assert row == {
                "care_episode_id": episode_id,
                "deterministic_level": "urgent",
                "effective_level": "urgent",
                "status": "open",
                "rule_code": "urgent-language",
                "version": 1,
                "rule_kind": "deterministic",
            }
        finally:
            engine.dispose()


@pytest.mark.parametrize("legacy_kind", ["terminal_signal", "approval"])
def test_0004_fails_precisely_when_authorization_provenance_would_be_invented(
    legacy_kind: str,
) -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0003_need_task_outcome_lifecycle")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            if legacy_kind == "terminal_signal":
                ids = {name: uuid4() for name in (
                    "organization", "user", "patient", "pathway", "episode", "definition",
                    "submission", "signal",
                )}
                now = datetime(2026, 8, 18, tzinfo=UTC)
                connection.execute(text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                                   {"id": ids["organization"], "name": str(uuid4())})
                connection.execute(text(
                    "INSERT INTO user_account (id, email, display_name, is_active) "
                    "VALUES (:id, :email, 'Patient', true)"),
                    {"id": ids["user"], "email": f"{uuid4()}@example.test"})
                connection.execute(text(
                    "INSERT INTO synthetic_patient (id, organization_id, external_ref, "
                    "display_name, demographics) VALUES (:id, :organization_id, :ref, "
                    "'Patient', '{}'::jsonb)"), {"id": ids["patient"],
                    "organization_id": ids["organization"], "ref": str(uuid4())})
                connection.execute(text(
                    "INSERT INTO pathway_definition (id, organization_id, slug, version, name, "
                    "configuration, is_active) VALUES (:id, :organization_id, :slug, 1, "
                    "'Path', '{}'::jsonb, true)"), {"id": ids["pathway"],
                    "organization_id": ids["organization"], "slug": str(uuid4())})
                connection.execute(text(
                    "INSERT INTO care_episode (id, organization_id, patient_id, status, started_at) "
                    "VALUES (:id, :organization_id, :patient_id, 'active', :now)"),
                    {"id": ids["episode"], "organization_id": ids["organization"],
                    "patient_id": ids["patient"], "now": now})
                connection.execute(text(
                    "INSERT INTO check_in_definition (id, organization_id, pathway_definition_id, "
                    "slug, version, title, questionnaire) VALUES (:id, :organization_id, "
                    ":pathway, :slug, 1, 'Check-in', '{}'::jsonb)"),
                    {"id": ids["definition"], "organization_id": ids["organization"],
                    "pathway": ids["pathway"], "slug": str(uuid4())})
                connection.execute(text(
                    "INSERT INTO check_in_submission (id, organization_id, patient_id, "
                    "care_episode_id, check_in_definition_id, status, answers, submission_source, "
                    "submitted_by_user_id, submitted_at) VALUES (:id, :organization_id, "
                    ":patient_id, :episode_id, :definition, 'submitted', '{}'::jsonb, 'patient', "
                    ":user_id, :now)"), {"id": ids["submission"],
                    "organization_id": ids["organization"], "patient_id": ids["patient"],
                    "episode_id": ids["episode"], "definition": ids["definition"],
                    "user_id": ids["user"], "now": now})
                connection.execute(text(
                    "INSERT INTO safety_signal (id, organization_id, patient_id, "
                    "source_submission_id, rule_code, severity, status, evidence) VALUES "
                    "(:id, :organization_id, :patient_id, :submission_id, 'legacy', 'urgent', "
                    "'acknowledged', '[]'::jsonb)"), {"id": ids["signal"],
                    "organization_id": ids["organization"], "patient_id": ids["patient"],
                    "submission_id": ids["submission"]})
            else:
                ids = {name: uuid4() for name in (
                    "organization", "user", "patient", "need", "task",
                )}
                connection.execute(text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                                   {"id": ids["organization"], "name": str(uuid4())})
                connection.execute(text(
                    "INSERT INTO user_account (id, email, display_name, is_active) VALUES "
                    "(:id, :email, 'Approver', true)"),
                    {"id": ids["user"], "email": f"{uuid4()}@example.test"})
                # A legacy approval row can be inserted only with a complete legacy task graph;
                # disabling triggers/constraints here models imported synthetic legacy data.
                connection.execute(text("SET session_replication_role = replica"))
                connection.execute(text(
                    "INSERT INTO approval_decision (id, organization_id, navigation_task_id, "
                    "authorized_user_id, status, proposed_value, final_value) VALUES "
                    "(:id, :organization_id, :task_id, :user_id, 'approved', '{}'::jsonb, "
                    "'{}'::jsonb)"), {"id": uuid4(), "organization_id": ids["organization"],
                    "task_id": ids["task"], "user_id": ids["user"]})
                connection.execute(text("SET session_replication_role = origin"))
        engine.dispose()
        result = _run_alembic_without_checking(database_url, "head")

    assert result.returncode != 0
    diagnostic = result.stdout + result.stderr
    if legacy_kind == "terminal_signal":
        assert "resolver or acknowledgement provenance" in diagnostic
    else:
        assert "proposal, policy, or qualifying-role provenance" in diagnostic
    assert "reset the synthetic demo database" in diagnostic


def _seed_offline_0004_ambiguity(connection: Connection, legacy_kind: str):
    execute = connection.execute
    organization_id = uuid4()
    offending_id = uuid4()
    execute(
        text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
        {"id": organization_id, "name": f"Offline 0004 {uuid4()}"},
    )
    execute(text("SET session_replication_role = replica"))
    if legacy_kind == "approval":
        execute(
            text(
                "INSERT INTO approval_decision "
                "(id, organization_id, navigation_task_id, authorized_user_id, status, "
                "proposed_value, final_value) VALUES "
                "(:id, :organization_id, :task_id, :user_id, 'approved', '{}'::jsonb, "
                "'{}'::jsonb)"
            ),
            {
                "id": offending_id,
                "organization_id": organization_id,
                "task_id": uuid4(),
                "user_id": uuid4(),
            },
        )
    elif legacy_kind == "terminal_signal":
        execute(
            text(
                "INSERT INTO safety_signal "
                "(id, organization_id, patient_id, source_submission_id, rule_code, severity, "
                "status, evidence) VALUES (:id, :organization_id, :patient_id, :submission_id, "
                "'legacy-terminal', 'urgent', 'acknowledged', '[]'::jsonb)"
            ),
            {
                "id": offending_id,
                "organization_id": organization_id,
                "patient_id": uuid4(),
                "submission_id": uuid4(),
            },
        )
    else:
        signal_patient_id = uuid4()
        submission_patient_id = uuid4()
        submission_id = uuid4()
        execute(
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
                "patient_id": submission_patient_id,
                "episode_id": uuid4(),
                "definition_id": uuid4(),
                "user_id": uuid4(),
                "submitted_at": datetime(2026, 8, 18, tzinfo=UTC),
            },
        )
        execute(
            text(
                "INSERT INTO safety_signal "
                "(id, organization_id, patient_id, source_submission_id, rule_code, severity, "
                "status, evidence) VALUES (:id, :organization_id, :patient_id, :submission_id, "
                "'legacy-mismatch', 'urgent', 'active', '[]'::jsonb)"
            ),
            {
                "id": offending_id,
                "organization_id": organization_id,
                "patient_id": signal_patient_id,
                "submission_id": submission_id,
            },
        )
    execute(text("SET session_replication_role = origin"))
    return offending_id


def test_0004_fails_precisely_for_non_derivable_origin_provenance() -> None:
    """A patient/source mismatch must name the signal and require synthetic reset."""
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0003_need_task_outcome_lifecycle")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            offending_id = _seed_offline_0004_ambiguity(connection, "non_derivable_signal")
        engine.dispose()
        result = _run_alembic_without_checking(database_url, "head")

    assert result.returncode != 0
    diagnostic = result.stdout + result.stderr
    assert str(offending_id) in diagnostic
    assert "without derivable tenant-, patient-, episode-, severity-, and rule provenance" in diagnostic
    assert "reset the synthetic demo database" in diagnostic


@pytest.mark.parametrize(
    ("legacy_kind", "expected_diagnostic"),
    [
        ("approval", "proposal, policy, or qualifying-role provenance"),
        ("terminal_signal", "resolver or acknowledgement provenance"),
        (
            "non_derivable_signal",
            "without derivable tenant-, patient-, episode-, severity-, and rule provenance",
        ),
    ],
)
def test_0004_offline_upgrade_artifact_guards_ambiguous_rows_before_destructive_ddl(
    legacy_kind: str,
    expected_diagnostic: str,
) -> None:
    """Executing offline SQL must fail precisely before legacy rows or schema are discarded."""
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "0003_need_task_outcome_lifecycle")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            offending_id = _seed_offline_0004_ambiguity(connection, legacy_kind)
        artifact = _render_alembic_sql(
            database_url,
            "upgrade",
            "0003_need_task_outcome_lifecycle:0004_safety_approval_lifecycle",
        )
        assert artifact.returncode == 0, artifact.stderr
        sql = artifact.stdout
        assert expected_diagnostic in sql
        first_destructive_ddl = (
            "ALTER TABLE safety_signal "
            "DROP CONSTRAINT fk_safety_signal_organization_submission;"
        )
        assert sql.index(expected_diagnostic) < sql.index(first_destructive_ddl)
        assert sql.index(expected_diagnostic) < sql.index("DROP TABLE approval_decision")

        raw_connection = engine.raw_connection()
        try:
            with pytest.raises(psycopg.errors.RaiseException) as raised:
                raw_connection.cursor().execute(sql)
            raw_connection.rollback()
        finally:
            raw_connection.close()
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT to_regclass('public.approval_decision') IS NOT NULL")
            ) is True
        engine.dispose()

    assert str(offending_id) in str(raised.value)
    assert expected_diagnostic in str(raised.value)
    assert "reset the synthetic demo database" in str(raised.value)


def test_0004_offline_downgrade_refuses_before_emitting_partial_teardown() -> None:
    """An irreversible downgrade must emit no trigger, view, or function teardown."""
    result = _render_alembic_sql(
        settings.database_url,
        "downgrade",
        "0004_safety_approval_lifecycle:0003_need_task_outcome_lifecycle",
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "reset the synthetic demo database instead" in output
    assert "DROP TRIGGER" not in result.stdout
    assert "DROP VIEW" not in result.stdout
    assert "DROP FUNCTION" not in result.stdout

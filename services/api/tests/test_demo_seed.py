# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.integrity import inspect_integrity
from app.db.models import FollowUpRequest
from app.domain.approvals import record_decision
from app.domain.follow_ups import record_follow_up_response
from app.domain.navigation_tasks import (
    claim_navigation_task,
    complete_navigation_task,
    start_navigation_task,
)
from app.domain.outcomes import record_outcome
from scripts import seed_demo as seed_demo_script
from scripts.seed_demo import DEMO_IDS, seed_demo
from tests.database_support import disposable_database

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DISPOSABLE_PREFIX = "ojcc_task7_"
SCOPED_TABLES = (
    "agent_run_citation",
    "manual_review_task",
    "follow_up_response",
    "follow_up_request",
    "audit_event",
    "agent_run",
    "workflow_transition_event",
    "workflow_run",
    "organization_knowledge_approval",
    "knowledge_document",
    "navigation_task_resource",
    "resource",
    "approval_decision",
    "proposed_change",
    "approval_policy",
    "patient_message",
    "safety_signal_resolution",
    "safety_signal",
    "signal_rule",
    "outcome",
    "navigation_task",
    "reported_need",
    "check_in_submission",
    "check_in_definition",
    "episode_pathway_assignment",
    "pathway_definition",
    "care_episode",
    "patient_identity_link",
    "role_assignment",
    "synthetic_patient",
    "user_account",
    "organization",
)


@contextmanager
def _disposable_database() -> Iterator[str]:
    with disposable_database(prefix=DISPOSABLE_PREFIX, migrate_to="head") as database:
        yield database.migration_url


@contextmanager
def _disposable_session() -> Iterator[Session]:
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        try:
            with Session(engine) as session:
                yield session
        finally:
            engine.dispose()


def _seed_digest(session: Session) -> str:
    rows: list[str] = []
    organization_id = DEMO_IDS["organization"]
    for table in SCOPED_TABLES:
        if table == "organization":
            predicate = "id = :organization_id"
        elif table == "user_account":
            predicate = "primary_organization_id = :organization_id"
        else:
            predicate = "organization_id = :organization_id"
        rows.extend(
            session.execute(
                text(
                    f"SELECT row_to_json(scoped)::text FROM "
                    f"(SELECT * FROM {table} WHERE {predicate} ORDER BY id) AS scoped"
                ),
                {"organization_id": organization_id},
            ).scalars()
        )
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _database_digest(session: Session) -> str:
    rows: list[str] = []
    for table in SCOPED_TABLES:
        rows.extend(
            session.execute(
                text(
                    f"SELECT row_to_json(all_rows)::text FROM "
                    f"(SELECT * FROM {table} ORDER BY id) AS all_rows"
                )
            ).scalars()
        )
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _insert_identity_prerequisites(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    patient_id: UUID | None = None,
) -> None:
    created_at = datetime.now(UTC)
    session.execute(
        text(
            "INSERT INTO organization (id, name, created_at) "
            "VALUES (:organization_id, :name, :created_at)"
        ),
        {
            "organization_id": organization_id,
            "name": f"Synthetic fixture {organization_id}",
            "created_at": created_at,
        },
    )
    session.execute(
        text(
            "INSERT INTO user_account "
            "(id, primary_organization_id, email, display_name, is_active, created_at) "
            "VALUES (:user_id, :organization_id, :email, 'Fixture user', true, :created_at)"
        ),
        {
            "user_id": user_id,
            "organization_id": organization_id,
            "email": f"fixture-{user_id}@example.test",
            "created_at": created_at,
        },
    )
    if patient_id is not None:
        session.execute(
            text(
                "INSERT INTO synthetic_patient "
                "(id, organization_id, external_ref, display_name, birth_date, demographics, created_at) "
                "VALUES (:patient_id, :organization_id, :external_ref, "
                "'Fixture patient', NULL, '{}'::jsonb, :created_at)"
            ),
            {
                "patient_id": patient_id,
                "organization_id": organization_id,
                "external_ref": f"fixture-{patient_id}",
                "created_at": created_at,
            },
        )


def _assert_seed_refuses_identity_conflict(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.commit()
    before_digest = _database_digest(session)
    trigger_changes: list[bool] = []
    original_set_seed_user_triggers = seed_demo_script._set_seed_user_triggers

    def record_trigger_change(changed_session: Session, *, enabled: bool) -> None:
        trigger_changes.append(enabled)
        original_set_seed_user_triggers(changed_session, enabled=enabled)

    monkeypatch.setattr(seed_demo_script, "_set_seed_user_triggers", record_trigger_change)

    with pytest.raises(
        RuntimeError, match="Existing demo identity conflicts with synthetic seed"
    ):
        seed_demo(session)

    assert trigger_changes == []
    assert _database_digest(session) == before_digest


def _resolve_powershell_executable(
    find_executable: Callable[[str], str | None] = shutil.which,
) -> str:
    for candidate in ("pwsh", "powershell"):
        if executable := find_executable(candidate):
            return executable
    raise RuntimeError("Demo reset execution requires pwsh or Windows PowerShell")


def _run_reset(
    target_url: str,
    confirmation: str,
    *,
    bootstrap_database_url: str | None = None,
    migration_database_url: str | None = None,
    database_url: str | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bootstrap_url, migration_url, application_url = _database_triple(target_url)
    return subprocess.run(
        [
            _resolve_powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/reset_demo.ps1",
            "-BootstrapDatabaseUrl",
            bootstrap_database_url or bootstrap_url,
            "-MigrationDatabaseUrl",
            migration_database_url or migration_url,
            "-DatabaseUrl",
            database_url or application_url,
            "-ConfirmDatabaseName",
            confirmation,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _database_triple(target_url: str) -> tuple[str, str, str]:
    target = make_url(target_url)

    def with_configured_credentials(variable_name: str) -> str:
        configured = make_url(os.environ[variable_name])
        return (
            target.set(
                username=configured.username,
                password=configured.password,
            ).render_as_string(hide_password=False)
        )

    return (
        with_configured_credentials("BOOTSTRAP_DATABASE_URL"),
        with_configured_credentials("MIGRATION_DATABASE_URL"),
        with_configured_credentials("DATABASE_URL"),
    )


def _seed_arguments(target_url: str) -> list[str]:
    _, migration_url, application_url = _database_triple(target_url)
    return [
        "--migration-database-url",
        migration_url,
        "--database-url",
        application_url,
    ]


def test_print_demo_actors_is_connection_free_and_emits_exact_sorted_ids(
    tmp_path: Path,
) -> None:
    """Production break: operators cannot configure the exact synthetic actor roster."""
    sentinel = "database engine creation was attempted"
    (tmp_path / "sitecustomize.py").write_text(
        "import sqlalchemy\n"
        "def blocked_create_engine(*args, **kwargs):\n"
        f"    raise AssertionError({sentinel!r})\n"
        "sqlalchemy.create_engine = blocked_create_engine\n",
        encoding="utf-8",
    )
    python_path = str(tmp_path)
    if existing_python_path := os.environ.get("PYTHONPATH"):
        python_path += os.pathsep + existing_python_path

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.seed_demo",
            "--print-demo-actors",
        ],
        cwd=PROJECT_ROOT / "services" / "api",
        env=os.environ | {"PYTHONPATH": python_path},
        capture_output=True,
        text=True,
        check=False,
    )

    expected = {
        "administrator": {"user_id": "0a71cbf4-43a7-52b6-aee4-d88cdfc4fb16"},
        "navigator": {"user_id": "47c99329-0479-5fdd-97c4-dbfd97490dff"},
        "supporting_actor": {
            "patient_id": "bca3e068-4b0f-5434-a6b7-879e3a9e2d89",
            "user_id": "5c12bf9d-c570-52b2-8a7f-4b55f66c7c30",
        },
    }
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == json.dumps(expected, sort_keys=True) + "\n"
    assert result.stderr == ""
    assert sentinel not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("available", "expected", "expected_lookups"),
    (
        (
            {"pwsh": "/opt/microsoft/powershell/7/pwsh", "powershell": None},
            "/opt/microsoft/powershell/7/pwsh",
            ["pwsh"],
        ),
        (
            {"pwsh": None, "powershell": r"C:\Windows\System32\WindowsPowerShell\powershell.exe"},
            r"C:\Windows\System32\WindowsPowerShell\powershell.exe",
            ["pwsh", "powershell"],
        ),
    ),
)
def test_resolve_powershell_prefers_pwsh_with_windows_fallback(
    available: dict[str, str | None],
    expected: str,
    expected_lookups: list[str],
) -> None:
    """Hosted Linux must use pwsh while Windows retains its legacy fallback."""
    lookups: list[str] = []

    def find_executable(name: str) -> str | None:
        lookups.append(name)
        return available[name]

    assert _resolve_powershell_executable(find_executable) == expected
    assert lookups == expected_lookups


def test_resolve_powershell_fails_clearly_when_no_executable_exists() -> None:
    with pytest.raises(RuntimeError, match="requires pwsh or Windows PowerShell"):
        _resolve_powershell_executable(lambda _name: None)


def test_seed_rejects_shared_or_mismatched_credentials_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_engine_creation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("database engine creation was attempted")

    monkeypatch.setattr(seed_demo_script, "create_engine", unexpected_engine_creation)
    _, migration_url, application_url = _database_triple(
        "postgresql+psycopg://ignored:ignored@127.0.0.1:5432/ojcc_demo_deadbeef"
    )

    with pytest.raises(ValueError, match="distinct usernames"):
        seed_demo_script.main(
            [
                "--migration-database-url",
                migration_url,
                "--database-url",
                migration_url,
            ]
        )
    with pytest.raises(ValueError, match="same database name"):
        seed_demo_script.main(
            [
                "--migration-database-url",
                migration_url,
                "--database-url",
                make_url(application_url)
                .set(database="ojcc_demo_feedface")
                .render_as_string(hide_password=False),
            ]
        )


def test_reset_rejects_shared_credentials_before_engine_creation() -> None:
    target = (
        "postgresql+psycopg://ignored:ignored@127.0.0.1:5432/"
        "ojcc_demo_deadbeef"
    )
    bootstrap_url, _, application_url = _database_triple(target)
    result = _run_reset(
        target,
        "ojcc_demo_deadbeef",
        migration_database_url=bootstrap_url,
        database_url=application_url,
    )

    assert result.returncode != 0
    assert "distinct usernames" in (result.stdout + result.stderr).lower()


def test_seed_is_synthetic_complete_deterministic_and_idempotent() -> None:
    """Production break: rerunning the public seed duplicates or changes reconciled history."""
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        with Session(engine) as session:
            first_summary = seed_demo(session)
            session.commit()
            first_digest = _seed_digest(session)

            assert first_summary.organization_id == DEMO_IDS["organization"]
            assert first_summary.row_counts == {
                "agent_run": 2,
                "agent_run_citation": 1,
                "approval_decision": 5,
                "approval_policy": 4,
                "audit_event": 4,
                "care_episode": 1,
                "check_in_definition": 2,
                "check_in_submission": 2,
                "episode_pathway_assignment": 2,
                "follow_up_request": 0,
                "follow_up_response": 0,
                "knowledge_document": 1,
                "manual_review_task": 1,
                "navigation_task": 3,
                "navigation_task_resource": 2,
                "organization": 1,
                "organization_knowledge_approval": 1,
                "outcome": 1,
                "patient_identity_link": 1,
                "patient_message": 1,
                "pathway_definition": 2,
                "proposed_change": 7,
                "reported_need": 3,
                "resource": 2,
                "role_assignment": 4,
                "safety_signal": 4,
                "safety_signal_resolution": 1,
                "signal_rule": 2,
                "synthetic_patient": 1,
                "user_account": 3,
                "workflow_run": 1,
                "workflow_transition_event": 2,
            }
            assert session.scalar(
                text(
                    "SELECT user_id <> patient_id FROM patient_identity_link "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            )
            assert session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE revoked_at IS NULL), "
                    "count(*) FILTER (WHERE revoked_at IS NOT NULL) "
                    "FROM role_assignment WHERE organization_id = :organization_id"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).one() == (3, 1)
            assert session.scalar(
                text(
                    "SELECT count(*) FROM episode_pathway_assignment "
                    "WHERE organization_id = :organization_id AND effective_to IS NULL"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ) == 1
            assert session.scalar(
                text(
                    "SELECT count(*) FROM active_check_in_submission "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ) == 1
            assert session.execute(
                text(
                    "SELECT effective_state::text FROM effective_need_state "
                    "WHERE organization_id = :organization_id ORDER BY effective_state::text"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).scalars().all() == ["closed", "open", "open"]
            assert session.execute(
                text(
                    "SELECT status::text FROM navigation_task "
                    "WHERE organization_id = :organization_id ORDER BY status::text"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).scalars().all() == ["cancelled", "open", "open"]
            assert session.execute(
                text(
                    "SELECT effective_state::text FROM effective_safety_signal_state "
                    "WHERE organization_id = :organization_id ORDER BY effective_state::text"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).scalars().all() == ["acknowledged", "dismissed", "open", "resolved"]
            assert session.execute(
                text(
                    "SELECT effective_state::text, count(*) FROM effective_proposed_change_state "
                    "WHERE organization_id = :organization_id GROUP BY effective_state "
                    "ORDER BY effective_state::text"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).all() == [("approved", 4), ("pending", 2), ("superseded", 1)]
            assert session.execute(
                text(
                    "SELECT need.kind, need.source_submission_id, task.status::text, "
                    "task.authorized_proposed_change_id, proposal.value_schema_id, "
                    "proposal.value_schema_version, proposal.proposed_value, "
                    "snapshot.resource_name_snapshot, snapshot.approved_at "
                    "FROM reported_need AS need "
                    "JOIN navigation_task AS task ON task.organization_id = need.organization_id "
                    "AND task.reported_need_id = need.id "
                    "JOIN proposed_change AS proposal "
                    "ON proposal.organization_id = task.organization_id "
                    "AND proposal.navigation_task_id = task.id "
                    "JOIN navigation_task_resource AS snapshot "
                    "ON snapshot.organization_id = proposal.organization_id "
                    "AND snapshot.proposed_change_id = proposal.id "
                    "WHERE need.id = :need_id AND task.id = :task_id "
                    "AND proposal.id = :proposal_id AND snapshot.id = :snapshot_id"
                ),
                {
                    "need_id": DEMO_IDS["transportation_need"],
                    "task_id": DEMO_IDS["transportation_task"],
                    "proposal_id": DEMO_IDS["transportation_task_proposal"],
                    "snapshot_id": DEMO_IDS["transportation_task_resource"],
                },
            ).one() == (
                "transportation",
                DEMO_IDS["submission_v2"],
                "open",
                None,
                "ojcc.authorize-navigation-task",
                2,
                {
                    "title": "Arrange transportation for oncology follow-up",
                    "resources": [
                        {
                            "resource_id": str(DEMO_IDS["transportation_resource"]),
                            "name": "Synthetic community ride network",
                            "category": "transportation",
                            "url": "https://example.test/community-rides",
                            "metadata": {"synthetic": True, "service_area": "demo"},
                            "match_rationale": "Serves the synthetic patient's upcoming oncology visit.",
                        }
                    ],
                },
                "Synthetic community ride network",
                None,
            )
            assert session.execute(
                text(
                    "SELECT initial_state, current_state, count(event.id) "
                    "FROM workflow_run AS run "
                    "JOIN workflow_transition_event AS event "
                    "ON event.organization_id = run.organization_id "
                    "AND event.workflow_run_id = run.id "
                    "WHERE run.organization_id = :organization_id "
                    "GROUP BY run.id"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).one() == ("received", "complete", 2)
            assert session.scalar(
                text(
                    "SELECT count(*) FROM agent_run_citation AS citation "
                    "JOIN agent_run AS run ON run.organization_id = citation.organization_id "
                    "AND run.id = citation.agent_run_id "
                    "JOIN organization_knowledge_approval AS approval "
                    "ON approval.organization_id = citation.organization_id "
                    "AND approval.knowledge_document_id = citation.knowledge_document_id "
                    "AND approval.knowledge_document_version = citation.knowledge_document_version "
                    "WHERE citation.organization_id = :organization_id "
                    "AND approval.effective_from <= run.created_at "
                    "AND (approval.withdrawn_at IS NULL OR run.created_at < approval.withdrawn_at)"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ) == 1
            assert session.execute(
                text(
                    "SELECT actor_type::text, count(*) FROM audit_event "
                    "WHERE organization_id = :organization_id GROUP BY actor_type "
                    "ORDER BY actor_type::text"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).all() == [("agent", 1), ("policy", 1), ("system", 1), ("user", 1)]
            assert inspect_integrity(session) == []

            second_summary = seed_demo(session)
            session.commit()
            second_digest = _seed_digest(session)

            assert second_summary == first_summary
            assert second_digest == first_digest
            assert inspect_integrity(session) == []
        engine.dispose()


def test_seed_refuses_wrong_user_for_intended_role_id_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production break: deterministic role identity drift is silently accepted."""
    with _disposable_session() as session:
        _insert_identity_prerequisites(
            session,
            organization_id=DEMO_IDS["organization"],
            user_id=DEMO_IDS["administrator_user"],
        )
        session.execute(
            text(
                "INSERT INTO role_assignment "
                "(id, organization_id, user_id, role, granted_at, revoked_at, created_at) "
                "VALUES (:role_assignment_id, :organization_id, :wrong_user_id, "
                "'navigator', :created_at, NULL, :created_at)"
            ),
            {
                "role_assignment_id": DEMO_IDS["navigator_role"],
                "organization_id": DEMO_IDS["organization"],
                "wrong_user_id": DEMO_IDS["administrator_user"],
                "created_at": datetime.now(UTC),
            },
        )
        _assert_seed_refuses_identity_conflict(session, monkeypatch)


def test_seed_refuses_wrong_organization_for_intended_role_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_organization_id = uuid4()
    with _disposable_session() as session:
        _insert_identity_prerequisites(
            session,
            organization_id=wrong_organization_id,
            user_id=DEMO_IDS["navigator_user"],
        )
        session.execute(
            text(
                "INSERT INTO role_assignment "
                "(id, organization_id, user_id, role, granted_at, revoked_at, created_at) "
                "VALUES (:role_assignment_id, :organization_id, :user_id, "
                "'navigator', :created_at, NULL, :created_at)"
            ),
            {
                "role_assignment_id": DEMO_IDS["navigator_role"],
                "organization_id": wrong_organization_id,
                "user_id": DEMO_IDS["navigator_user"],
                "created_at": datetime.now(UTC),
            },
        )
        _assert_seed_refuses_identity_conflict(session, monkeypatch)


def test_seed_refuses_wrong_role_for_intended_role_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _disposable_session() as session:
        _insert_identity_prerequisites(
            session,
            organization_id=DEMO_IDS["organization"],
            user_id=DEMO_IDS["navigator_user"],
        )
        session.execute(
            text(
                "INSERT INTO role_assignment "
                "(id, organization_id, user_id, role, granted_at, revoked_at, created_at) "
                "VALUES (:role_assignment_id, :organization_id, :user_id, "
                "'administrator', :created_at, NULL, :created_at)"
            ),
            {
                "role_assignment_id": DEMO_IDS["navigator_role"],
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["navigator_user"],
                "created_at": datetime.now(UTC),
            },
        )
        _assert_seed_refuses_identity_conflict(session, monkeypatch)


def test_seed_refuses_wrong_user_for_intended_patient_link_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _disposable_session() as session:
        _insert_identity_prerequisites(
            session,
            organization_id=DEMO_IDS["organization"],
            user_id=DEMO_IDS["administrator_user"],
            patient_id=DEMO_IDS["patient"],
        )
        session.execute(
            text(
                "INSERT INTO patient_identity_link "
                "(id, organization_id, user_id, patient_id, linked_at, revoked_at, created_at) "
                "VALUES (:link_id, :organization_id, :wrong_user_id, :patient_id, "
                ":created_at, NULL, :created_at)"
            ),
            {
                "link_id": DEMO_IDS["patient_identity_link"],
                "organization_id": DEMO_IDS["organization"],
                "wrong_user_id": DEMO_IDS["administrator_user"],
                "patient_id": DEMO_IDS["patient"],
                "created_at": datetime.now(UTC),
            },
        )
        _assert_seed_refuses_identity_conflict(session, monkeypatch)


def test_seed_refuses_wrong_patient_for_intended_patient_link_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_patient_id = uuid4()
    with _disposable_session() as session:
        _insert_identity_prerequisites(
            session,
            organization_id=DEMO_IDS["organization"],
            user_id=DEMO_IDS["patient_user"],
            patient_id=wrong_patient_id,
        )
        session.execute(
            text(
                "INSERT INTO patient_identity_link "
                "(id, organization_id, user_id, patient_id, linked_at, revoked_at, created_at) "
                "VALUES (:link_id, :organization_id, :user_id, :wrong_patient_id, "
                ":created_at, NULL, :created_at)"
            ),
            {
                "link_id": DEMO_IDS["patient_identity_link"],
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["patient_user"],
                "wrong_patient_id": wrong_patient_id,
                "created_at": datetime.now(UTC),
            },
        )
        _assert_seed_refuses_identity_conflict(session, monkeypatch)


def test_seed_refuses_wrong_organization_for_intended_patient_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production break: an unlinked deterministic patient reaches inserts and raw FK failure."""
    with _disposable_session() as session:
        organization_id = uuid4()
        session.execute(
            text("INSERT INTO organization (id, name) VALUES (:id, 'Patient conflict fixture')"),
            {"id": organization_id},
        )
        session.execute(
            text(
                "INSERT INTO synthetic_patient (id, organization_id, external_ref, display_name, demographics) "
                "VALUES (:id, :organization_id, 'conflicting-patient', 'Fixture patient', '{}'::jsonb)"
            ),
            {"id": DEMO_IDS["patient"], "organization_id": organization_id},
        )
        session.commit()
        assert session.scalar(text("SELECT count(*) FROM role_assignment")) == 0
        assert session.scalar(text("SELECT count(*) FROM patient_identity_link")) == 0
        before_digest = _database_digest(session)
        trigger_changes: list[bool] = []
        original_toggle = seed_demo_script._set_seed_user_triggers

        def observe_toggle(changed_session: Session, *, enabled: bool) -> None:
            trigger_changes.append(enabled)
            original_toggle(changed_session, enabled=enabled)

        monkeypatch.setattr(seed_demo_script, "_set_seed_user_triggers", observe_toggle)
        refusal = False
        database_error = False
        try:
            seed_demo(session)
        except RuntimeError as error:
            refusal = str(error) == "Existing demo identity conflicts with synthetic seed"
        except SQLAlchemyError:
            # Do not format a database exception: RED must expose only safe observations.
            database_error = True
            session.rollback()
        after_digest = _database_digest(session)
        print(f"PATIENT_PREFLIGHT digest_unchanged={before_digest == after_digest} "
              f"trigger_changes={trigger_changes} database_error={database_error}")
        assert refusal, "Expected sanitized preflight refusal before mutation"
        assert not database_error
        assert trigger_changes == []
        assert after_digest == before_digest


def test_seed_never_reactivates_an_inactive_intended_user() -> None:
    with _disposable_session() as session:
        seed_demo(session)
        session.commit()
        session.execute(
            text("UPDATE user_account SET is_active = false WHERE id = :user_id"),
            {"user_id": DEMO_IDS["patient_user"]},
        )
        session.commit()
        before_digest = _database_digest(session)

        seed_demo(session)
        session.commit()

        assert _database_digest(session) == before_digest
        assert session.scalar(
            text("SELECT is_active FROM user_account WHERE id = :user_id"),
            {"user_id": DEMO_IDS["patient_user"]},
        ) is False
        assert session.scalar(
            text(
                "SELECT count(*) FROM role_assignment "
                "WHERE organization_id = :organization_id AND user_id = :user_id "
                "AND role = 'supporting_actor'"
            ),
            {
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["patient_user"],
            },
        ) == 1


def test_seed_never_clears_revocation_or_replaces_an_intended_grant() -> None:
    with _disposable_session() as session:
        seed_demo(session)
        session.commit()
        revoked_at = datetime.now(UTC)
        session.execute(
            text(
                "UPDATE role_assignment SET revoked_at = :revoked_at "
                "WHERE id = :role_assignment_id"
            ),
            {
                "revoked_at": revoked_at,
                "role_assignment_id": DEMO_IDS["navigator_role"],
            },
        )
        session.commit()
        before_digest = _database_digest(session)

        seed_demo(session)
        session.commit()

        assert _database_digest(session) == before_digest
        assert session.scalar(
            text("SELECT revoked_at FROM role_assignment WHERE id = :id"),
            {"id": DEMO_IDS["navigator_role"]},
        ) == revoked_at
        assert session.scalar(
            text(
                "SELECT count(*) FROM role_assignment "
                "WHERE organization_id = :organization_id AND user_id = :user_id "
                "AND role = 'navigator'"
            ),
            {
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["navigator_user"],
            },
        ) == 2
        assert session.scalar(
            text(
                "SELECT count(*) FROM role_assignment "
                "WHERE organization_id = :organization_id AND user_id = :user_id "
                "AND role = 'navigator' AND granted_at <= :at "
                "AND (revoked_at IS NULL OR :at < revoked_at)"
            ),
            {
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["navigator_user"],
                "at": revoked_at,
            },
        ) == 0


def test_seed_never_clears_revocation_or_replaces_an_intended_patient_link() -> None:
    with _disposable_session() as session:
        seed_demo(session)
        session.commit()
        revoked_at = datetime.now(UTC)
        session.execute(
            text(
                "UPDATE patient_identity_link SET revoked_at = :revoked_at "
                "WHERE id = :link_id"
            ),
            {
                "revoked_at": revoked_at,
                "link_id": DEMO_IDS["patient_identity_link"],
            },
        )
        session.commit()
        before_digest = _database_digest(session)

        seed_demo(session)
        session.commit()

        assert _database_digest(session) == before_digest
        assert session.scalar(
            text("SELECT revoked_at FROM patient_identity_link WHERE id = :id"),
            {"id": DEMO_IDS["patient_identity_link"]},
        ) == revoked_at
        assert session.scalar(
            text(
                "SELECT count(*) FROM patient_identity_link "
                "WHERE organization_id = :organization_id AND user_id = :user_id"
            ),
            {
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["patient_user"],
            },
        ) == 1
        assert session.scalar(
            text(
                "SELECT count(*) FROM patient_identity_link "
                "WHERE organization_id = :organization_id AND user_id = :user_id "
                "AND linked_at <= :at AND (revoked_at IS NULL OR :at < revoked_at)"
            ),
            {
                "organization_id": DEMO_IDS["organization"],
                "user_id": DEMO_IDS["patient_user"],
                "at": revoked_at,
            },
        ) == 0


def test_reseeding_after_closed_loop_activity_preserves_the_completed_story() -> None:
    """A demo reseed must never reopen, erase, or duplicate an exercised journey."""
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        with Session(engine) as session:
            seed_demo(session)
            session.commit()

            record_decision(
                session,
                organization_id=DEMO_IDS["organization"],
                proposed_change_id=DEMO_IDS["transportation_task_proposal"],
                authorized_by_user_id=DEMO_IDS["navigator_user"],
                qualifying_role_assignment_id=None,
                decision="approved",
                reason=None,
            )
            claim_navigation_task(
                session,
                organization_id=DEMO_IDS["organization"],
                task_id=DEMO_IDS["transportation_task"],
                actor_user_id=DEMO_IDS["navigator_user"],
                proposed_change_id=DEMO_IDS["transportation_task_proposal"],
                due_at=datetime.now(UTC) + timedelta(days=7),
            )
            start_navigation_task(
                session,
                organization_id=DEMO_IDS["organization"],
                task_id=DEMO_IDS["transportation_task"],
                actor_user_id=DEMO_IDS["navigator_user"],
            )
            complete_navigation_task(
                session,
                organization_id=DEMO_IDS["organization"],
                task_id=DEMO_IDS["transportation_task"],
                actor_user_id=DEMO_IDS["navigator_user"],
            )
            request_id = session.scalar(
                select(FollowUpRequest.id).where(
                    FollowUpRequest.organization_id == DEMO_IDS["organization"],
                    FollowUpRequest.navigation_task_id == DEMO_IDS["transportation_task"],
                )
            )
            assert request_id is not None
            record_follow_up_response(
                session,
                organization_id=DEMO_IDS["organization"],
                actor_user_id=DEMO_IDS["patient_user"],
                patient_id=DEMO_IDS["patient"],
                request_id=request_id,
                response="resolved",
                note="Synthetic ride support worked.",
            )
            record_outcome(
                session,
                organization_id=DEMO_IDS["organization"],
                need_id=DEMO_IDS["transportation_need"],
                recorded_by_user_id=DEMO_IDS["navigator_user"],
                disposition="resolved",
                note="Synthetic follow-up confirmed resolution.",
                idempotency_key="synthetic-transportation-closed-loop-v1",
            )
            session.commit()
            completed_digest = _seed_digest(session)

            reseed_summary = seed_demo(session)
            session.commit()

            assert _seed_digest(session) == completed_digest
            assert reseed_summary.row_counts["follow_up_request"] == 1
            assert reseed_summary.row_counts["follow_up_response"] == 1
            assert session.execute(
                text(
                    "SELECT need.effective_state::text, task.status::text, proposal.effective_state::text "
                    "FROM effective_need_state AS need "
                    "JOIN navigation_task AS task ON task.reported_need_id = need.id "
                    "JOIN effective_proposed_change_state AS proposal "
                    "ON proposal.id = task.authorized_proposed_change_id "
                    "WHERE need.id = :need_id AND task.id = :task_id"
                ),
                {
                    "need_id": DEMO_IDS["transportation_need"],
                    "task_id": DEMO_IDS["transportation_task"],
                },
            ).one() == ("closed", "completed", "approved")
            assert session.scalar(
                text(
                    "SELECT count(*) FROM outcome WHERE organization_id = :organization_id "
                    "AND reported_need_id = :need_id"
                ),
                {
                    "organization_id": DEMO_IDS["organization"],
                    "need_id": DEMO_IDS["transportation_need"],
                },
            ) == 1
            assert inspect_integrity(session) == []
        engine.dispose()


def test_seed_restores_trigger_enforcement_after_a_python_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caught helper error must not leave triggers disabled in the caller's transaction."""
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        with Session(engine) as session:
            transaction = session.begin()

            class ExpectedSeedError(RuntimeError):
                pass

            def fail_check_in_seed(
                _session: Session,
                _ids: dict[str, object],
                _values: dict[str, object],
            ) -> None:
                raise ExpectedSeedError

            monkeypatch.setattr(seed_demo_script, "_seed_check_ins", fail_check_in_seed)

            with pytest.raises(ExpectedSeedError):
                seed_demo(session)

            assert transaction.is_active
            assert session.scalar(text("SHOW session_replication_role")) == "origin"
            transaction.rollback()
        engine.dispose()


def test_seed_preserves_database_error_until_the_caller_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed SQL transaction must remain owned by the caller and reset on rollback."""
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        with Session(engine) as session:
            transaction = session.begin()

            def fail_with_database_error(
                failed_session: Session,
                _ids: dict[str, object],
                _values: dict[str, object],
            ) -> None:
                failed_session.execute(text("SELECT * FROM table_that_does_not_exist"))

            monkeypatch.setattr(
                seed_demo_script, "_seed_check_ins", fail_with_database_error
            )

            with pytest.raises(SQLAlchemyError) as caught:
                seed_demo(session)

            assert "table_that_does_not_exist" in str(
                getattr(caught.value, "statement", "")
            )
            assert transaction.is_active
            transaction.rollback()
            assert session.scalar(text("SHOW session_replication_role")) == "origin"
        engine.dispose()


def test_reset_requires_explicit_safe_target_then_seeds_twice_and_audits() -> None:
    """Production break: demo reset can erase an unconfirmed target or leave a dirty seed."""
    with _disposable_database() as database_url:
        database_name = make_url(database_url).database
        assert database_name is not None
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE reset_marker (id integer PRIMARY KEY)"))
            connection.execute(text("INSERT INTO reset_marker (id) VALUES (1)"))

        mismatch = _run_reset(database_url, f"{database_name}_wrong")
        assert mismatch.returncode != 0
        assert "confirmation does not match" in (mismatch.stdout + mismatch.stderr).lower()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM reset_marker")) == 1

        reset = _run_reset(database_url, database_name)
        assert reset.returncode == 0, reset.stdout + reset.stderr
        assert reset.stdout.count('"status": "seeded"') == 2
        assert '"status": "ok"' in reset.stdout
        with Session(engine) as session:
            assert session.scalar(text("SELECT to_regclass('public.reset_marker')")) is None
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0007_database_least_privilege"
            )
            assert inspect_integrity(session) == []
            assert session.scalar(
                text(
                    "SELECT count(*) FROM organization "
                    "WHERE id = :organization_id"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ) == 1
        engine.dispose()


def test_reset_rejects_persistent_and_remote_database_urls_before_connecting() -> None:
    """Production break: reset accepts a persistent name or non-loopback PostgreSQL host."""
    persistent = _run_reset(
        "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/ojcc",
        "ojcc",
    )
    assert persistent.returncode != 0
    assert "refusing to seed a non-disposable database" in (
        persistent.stdout + persistent.stderr
    ).lower()

    remote = _run_reset(
        "postgresql+psycopg://ojcc:local-synthetic-only@database.example.test:5432/"
        "ojcc_demo_deadbeef",
        "ojcc_demo_deadbeef",
    )
    assert remote.returncode != 0
    assert "requires a loopback postgresql url" in (remote.stdout + remote.stderr).lower()


def test_reset_rejects_query_routing_before_engine_creation(tmp_path: Path) -> None:
    """Production break: reset validation can be bypassed by a libpq query override."""
    sentinel = "database engine creation was attempted"
    (tmp_path / "sitecustomize.py").write_text(
        "import sqlalchemy\n"
        "def blocked_create_engine(*args, **kwargs):\n"
        f"    raise AssertionError({sentinel!r})\n"
        "sqlalchemy.create_engine = blocked_create_engine\n",
        encoding="utf-8",
    )
    python_path = str(tmp_path)
    if existing_python_path := os.environ.get("PYTHONPATH"):
        python_path += os.pathsep + existing_python_path

    result = _run_reset(
        "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
        "ojcc_demo_deadbeef?host=remote.example.test",
        "ojcc_demo_deadbeef",
        environment=os.environ | {"PYTHONPATH": python_path},
    )
    output = (result.stdout + result.stderr).lower()

    assert result.returncode != 0, output
    assert "must not include query parameters" in output
    assert sentinel not in output


@pytest.mark.parametrize(
    "query",
    (
        "host=remote.example.test",
        "port=6543",
        "dbname=ojcc",
        "hostaddr=192.0.2.10",
        "service=production",
        "servicefile=C%3A%5Csynthetic%5Cpg_service.conf",
        "options=-csearch_path%3Dpublic",
        "target_session_attrs=read-write",
        "load_balance_hosts=random",
        "host=127.0.0.1&host=remote.example.test",
    ),
)
def test_disposable_target_rejects_connection_query_overrides_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    """Production break: URL query options can reroute a validated destructive target."""

    def unexpected_engine_creation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("database engine creation was attempted")

    monkeypatch.setattr(seed_demo_script, "create_engine", unexpected_engine_creation)
    database_url = (
        "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
        f"ojcc_demo_deadbeef?{query}"
    )

    with pytest.raises(ValueError, match="must not include query parameters"):
        seed_demo_script.main(_seed_arguments(database_url))


def test_disposable_target_requires_explicit_local_port_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production break: an omitted URL port can inherit an unsafe libpq route."""

    def unexpected_engine_creation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("database engine creation was attempted")

    monkeypatch.setattr(seed_demo_script, "create_engine", unexpected_engine_creation)

    with pytest.raises(ValueError, match="requires the explicit local PostgreSQL port"):
        seed_demo_script.main(
            _seed_arguments(
                "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1/"
                "ojcc_demo_deadbeef"
            )
        )


LIBPQ_ENVIRONMENT_NAMES = (
    "PGHOSTADDR",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGOPTIONS",
    "PGTARGETSESSIONATTRS",
    "PGLOADBALANCEHOSTS",
    "pghost",
    "PgSyntheticProbe",
)


@pytest.mark.parametrize("variable_name", LIBPQ_ENVIRONMENT_NAMES)
def test_standalone_seed_removes_libpq_environment_before_engine_and_restores_it(
    monkeypatch: pytest.MonkeyPatch,
    variable_name: str,
) -> None:
    """Production break: inherited libpq settings can reroute a validated seed URL."""
    original_value = f"unsafe-{variable_name}"
    monkeypatch.setenv(variable_name, original_value)

    class ExpectedEngineStop(RuntimeError):
        pass

    def inspect_environment_then_stop(*_args: object, **_kwargs: object) -> None:
        inherited = sorted(key for key in os.environ if key.upper().startswith("PG"))
        assert inherited == []
        raise ExpectedEngineStop

    monkeypatch.setattr(seed_demo_script, "create_engine", inspect_environment_then_stop)

    with pytest.raises(ExpectedEngineStop):
        seed_demo_script.main(
            _seed_arguments(
                "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
                "ojcc_demo_deadbeef"
            )
        )

    assert os.environ[variable_name] == original_value


def test_reset_removes_all_libpq_environment_before_engine_creation(tmp_path: Path) -> None:
    """Production break: reset child processes inherit libpq connection overrides."""
    dirty_sentinel = "libpq environment reached engine creation"
    clean_sentinel = "engine reached with clean libpq environment"
    (tmp_path / "sitecustomize.py").write_text(
        "import os\n"
        "import sqlalchemy\n"
        "def blocked_create_engine(*args, **kwargs):\n"
        "    inherited = sorted(key for key in os.environ "
        "if key.upper().startswith('PG'))\n"
        "    if inherited:\n"
        f"        raise AssertionError({dirty_sentinel!r} + ': ' + ','.join(inherited))\n"
        f"    raise AssertionError({clean_sentinel!r})\n"
        "sqlalchemy.create_engine = blocked_create_engine\n",
        encoding="utf-8",
    )
    python_path = str(tmp_path)
    if existing_python_path := os.environ.get("PYTHONPATH"):
        python_path += os.pathsep + existing_python_path
    unsafe_environment = os.environ | {
        name: f"unsafe-{name}" for name in LIBPQ_ENVIRONMENT_NAMES
    }
    unsafe_environment["PYTHONPATH"] = python_path

    result = _run_reset(
        "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
        "ojcc_demo_deadbeef",
        "ojcc_demo_deadbeef",
        environment=unsafe_environment,
    )
    output = (result.stdout + result.stderr).lower()

    assert result.returncode != 0
    assert clean_sentinel in output
    assert dirty_sentinel not in output


def test_reset_restores_all_database_and_libpq_environment_after_child_failure(
    tmp_path: Path,
) -> None:
    fake_python = tmp_path / ("python.cmd" if os.name == "nt" else "python")
    if os.name == "nt":
        fake_python.write_text("@echo off\nexit /b 41\n", encoding="utf-8")
    else:
        fake_python.write_text("#!/bin/sh\nexit 41\n", encoding="utf-8")
        fake_python.chmod(0o755)
    bootstrap_url, migration_url, application_url = _database_triple(
        "postgresql+psycopg://ignored:ignored@127.0.0.1:5432/ojcc_demo_deadbeef"
    )
    reset_path = str(PROJECT_ROOT / "scripts" / "reset_demo.ps1").replace("'", "''")
    wrapper = tmp_path / "reset-environment-probe.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:BOOTSTRAP_DATABASE_URL = 'prior-bootstrap'\n"
        "$env:MIGRATION_DATABASE_URL = 'prior-migration'\n"
        "$env:DATABASE_URL = 'prior-application'\n"
        "$env:PGHOST = 'prior-host'\n"
        "$caught = $null\n"
        "try {\n"
        f"  . '{reset_path}' "
        f"-BootstrapDatabaseUrl '{bootstrap_url}' "
        f"-MigrationDatabaseUrl '{migration_url}' "
        f"-DatabaseUrl '{application_url}' "
        "-ConfirmDatabaseName 'ojcc_demo_deadbeef'\n"
        "}\n"
        "catch { $caught = $_.Exception.Message }\n"
        "if ($caught -notmatch '41') { throw \"Unexpected failure: $caught\" }\n"
        "if ($env:BOOTSTRAP_DATABASE_URL -ne 'prior-bootstrap' -or "
        "$env:MIGRATION_DATABASE_URL -ne 'prior-migration' -or "
        "$env:DATABASE_URL -ne 'prior-application' -or $env:PGHOST -ne 'prior-host') {\n"
        "  throw 'Database or PG environment was not restored'\n"
        "}\n"
        "Write-Output 'RESET_ENVIRONMENT_RESTORED'\n",
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PG")
    }
    environment["PATH"] = str(tmp_path) + os.pathsep + environment.get("PATH", "")
    result = subprocess.run(
        [
            _resolve_powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESET_ENVIRONMENT_RESTORED" in result.stdout


def test_standalone_seed_restores_libpq_environment_after_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected target must not leak the temporary process-environment cleanup."""
    prior_values = {
        "PGHOST": "synthetic-remote.example.test",
        "pgport": "6543",
        "PgOptions": "-c search_path=synthetic",
    }
    for key, value in prior_values.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match="non-disposable database"):
        seed_demo_script.main(
            _seed_arguments(
                "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/ojcc"
            )
        )

    for key, value in prior_values.items():
        assert os.environ[key] == value

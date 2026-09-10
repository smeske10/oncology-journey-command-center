# ruff: noqa: E501

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
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

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
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


def _validate_local_url(url: URL) -> None:
    if url.get_backend_name() != "postgresql" or url.host not in LOOPBACK_HOSTS:
        raise ValueError("Demo-seed tests require loopback PostgreSQL")


@contextmanager
def _disposable_database() -> Iterator[str]:
    configured = make_url(settings.database_url)
    _validate_local_url(configured)
    disposable = configured.set(database=f"{DISPOSABLE_PREFIX}{uuid4().hex}")
    database = disposable.database
    assert database is not None and database.startswith(DISPOSABLE_PREFIX)
    admin = disposable.set(database="postgres")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "services/api/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=PROJECT_ROOT,
            env=os.environ | {"DATABASE_URL": disposable.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yield disposable.render_as_string(hide_password=False)
    finally:
        if created:
            assert database.startswith(DISPOSABLE_PREFIX)
            with engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f'DROP DATABASE "{database}"'))
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


def _resolve_powershell_executable(
    find_executable: Callable[[str], str | None] = shutil.which,
) -> str:
    for candidate in ("pwsh", "powershell"):
        if executable := find_executable(candidate):
            return executable
    raise RuntimeError("Demo reset execution requires pwsh or Windows PowerShell")


def _run_reset(
    database_url: str,
    confirmation: str,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _resolve_powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/reset_demo.ps1",
            "-DatabaseUrl",
            database_url,
            "-ConfirmDatabaseName",
            confirmation,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


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
                "0006_navigator_closed_loop"
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

    assert result.returncode == 2, output
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
        seed_demo_script.main(["--database-url", database_url])


def test_disposable_target_requires_explicit_local_port_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production break: an omitted URL port can inherit an unsafe libpq route."""

    def unexpected_engine_creation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("database engine creation was attempted")

    monkeypatch.setattr(seed_demo_script, "create_engine", unexpected_engine_creation)

    with pytest.raises(ValueError, match="requires the explicit local PostgreSQL port"):
        seed_demo_script.main(
            [
                "--database-url",
                "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1/"
                "ojcc_demo_deadbeef",
            ]
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
            [
                "--database-url",
                "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
                "ojcc_demo_deadbeef",
            ]
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
            [
                "--database-url",
                "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/ojcc",
            ]
        )

    for key, value in prior_values.items():
        assert os.environ[key] == value

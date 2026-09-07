# ruff: noqa: E501

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.db.integrity import inspect_integrity
from scripts import seed_demo as seed_demo_script
from scripts.seed_demo import DEMO_IDS, seed_demo

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
DISPOSABLE_PREFIX = "ojcc_task7_"
SCOPED_TABLES = (
    "agent_run_citation",
    "manual_review_task",
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


def _run_reset(
    database_url: str,
    confirmation: str,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
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
                "knowledge_document": 1,
                "manual_review_task": 1,
                "navigation_task": 2,
                "navigation_task_resource": 1,
                "organization": 1,
                "organization_knowledge_approval": 1,
                "outcome": 1,
                "patient_identity_link": 1,
                "patient_message": 1,
                "pathway_definition": 2,
                "proposed_change": 6,
                "reported_need": 2,
                "resource": 1,
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
            ).scalars().all() == ["closed", "open"]
            assert session.execute(
                text(
                    "SELECT status::text FROM navigation_task "
                    "WHERE organization_id = :organization_id ORDER BY status::text"
                ),
                {"organization_id": DEMO_IDS["organization"]},
            ).scalars().all() == ["cancelled", "open"]
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
            ).all() == [("approved", 4), ("pending", 1), ("superseded", 1)]
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
                "0005_workflow_knowledge_audit"
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

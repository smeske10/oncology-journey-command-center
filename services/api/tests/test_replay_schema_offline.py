from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.privilege_attestation import attest_runtime_database
from app.db.targets import parse_database_target
from scripts.replay_schema import render_offline_bundle
from tests.database_support import bootstrap_database_url, disposable_database

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _psycopg_url(url_text: str) -> str:
    return make_url(url_text).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _execute_artifact(url_text: str, artifact: Path) -> None:
    with psycopg.connect(_psycopg_url(url_text), autocommit=True) as connection:
        connection.execute(artifact.read_text())


def test_offline_replay_bundle_renders_without_connecting(tmp_path: Path) -> None:
    database_name = f"ojcc_task7_{uuid4().hex}"
    output_directory = tmp_path / "offline-bundle"
    bootstrap_password = "bootstrap-bundle-secret-sentinel"
    owner_password = "owner-bundle-secret-sentinel"
    runtime_password = "runtime-bundle-secret-sentinel"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {"BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"}
        and not key.upper().startswith("PG")
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.replay_schema",
            "--bootstrap-database-url",
            f"postgresql+psycopg://bundle_bootstrap:{bootstrap_password}@127.0.0.1:5432/{database_name}",
            "--migration-database-url",
            f"postgresql+psycopg://bundle_owner:{owner_password}@127.0.0.1:5432/{database_name}",
            "--database-url",
            f"postgresql+psycopg://bundle_runtime:{runtime_password}@127.0.0.1:5432/{database_name}",
            "--revision",
            "head",
            "--sql-output-directory",
            str(output_directory),
        ],
        cwd=PROJECT_ROOT / "services" / "api",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((output_directory / "manifest.json").read_text())
    assert manifest == {
        "database": database_name,
        "format_version": 1,
        "roles": {
            "application": "bundle_runtime",
            "bootstrap": "bundle_bootstrap",
            "migration": "bundle_owner",
        },
        "stages": [
            {
                "credential": "migration",
                "end_revision": "0004_safety_approval_lifecycle",
                "file": "01-owner.sql",
                "start_revision": "base",
            },
            {
                "credential": "bootstrap",
                "end_revision": "0005_workflow_knowledge_audit",
                "file": "02-bootstrap.sql",
                "start_revision": "0004_safety_approval_lifecycle",
            },
            {
                "credential": "migration",
                "end_revision": "0007_database_least_privilege",
                "file": "03-owner.sql",
                "start_revision": "0005_workflow_knowledge_audit",
            },
        ],
    }
    combined = f"{result.stdout}\n{result.stderr}"
    for file_name in ("manifest.json", "01-owner.sql", "02-bootstrap.sql", "03-owner.sql"):
        combined += (output_directory / file_name).read_text()
    for secret in (bootstrap_password, owner_password, runtime_password, "postgresql"):
        assert secret not in combined.casefold()

    stage_one = (output_directory / "01-owner.sql").read_text()
    stage_two = (output_directory / "02-bootstrap.sql").read_text()
    stage_three = (output_directory / "03-owner.sql").read_text()
    assert "OFFLINE REPLAY STAGE: migration base -> 0004_safety_approval_lifecycle" in stage_one
    assert (
        "OFFLINE REPLAY STAGE: bootstrap 0004_safety_approval_lifecycle "
        "-> 0005_workflow_knowledge_audit"
    ) in stage_two
    assert 'ALTER TABLE public."agent_run_citation" OWNER TO "bundle_owner"' in stage_two
    assert stage_two.index('ALTER TABLE public."agent_run_citation"') < stage_two.rindex(
        "COMMIT;"
    )
    assert (
        "OFFLINE REPLAY STAGE: migration 0005_workflow_knowledge_audit "
        "-> 0007_database_least_privilege"
    ) in stage_three


def test_offline_replay_bundle_executes_all_three_credential_stages(
    tmp_path: Path,
) -> None:
    with disposable_database(prefix="ojcc_task7_") as database:
        bootstrap_url = bootstrap_database_url(database)
        output_directory = tmp_path / "executing-bundle"
        render_offline_bundle(
            bootstrap_database_url=bootstrap_url,
            migration_database_url=database.migration_url,
            database_url=database.application_url,
            revision="head",
            output_directory=output_directory,
        )

        _execute_artifact(database.migration_url, output_directory / "01-owner.sql")
        _execute_artifact(bootstrap_url, output_directory / "02-bootstrap.sql")

        owner_engine = create_engine(database.migration_url)
        try:
            with owner_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0005_workflow_knowledge_audit"
                transferred_owners = {
                    row.owner
                    for row in connection.execute(
                        text(
                            "SELECT owner.rolname AS owner FROM pg_class class "
                            "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                            "JOIN pg_roles owner ON owner.oid = class.relowner "
                            "WHERE namespace.nspname = 'public' "
                            "AND class.relname = 'agent_run_citation'"
                        )
                    )
                }
                assert transferred_owners == {"ojcc_migrator"}
        finally:
            owner_engine.dispose()

        _execute_artifact(database.migration_url, output_directory / "03-owner.sql")

        target = parse_database_target(database.application_url, label="DATABASE_URL")
        runtime_engine = create_engine(database.application_url)
        try:
            with runtime_engine.connect() as connection:
                attest_runtime_database(connection, target=target)
        finally:
            runtime_engine.dispose()


def test_offline_replay_later_stage_refusal_keeps_earlier_stages(
    tmp_path: Path,
) -> None:
    with disposable_database(prefix="ojcc_task7_") as database:
        bootstrap_url = bootstrap_database_url(database)
        output_directory = tmp_path / "refused-bundle"
        render_offline_bundle(
            bootstrap_database_url=bootstrap_url,
            migration_database_url=database.migration_url,
            database_url=database.application_url,
            revision="head",
            output_directory=output_directory,
        )
        _execute_artifact(database.migration_url, output_directory / "01-owner.sql")
        _execute_artifact(bootstrap_url, output_directory / "02-bootstrap.sql")

        object_name = f"unexpected_replay_{uuid4().hex[:16]}"
        with psycopg.connect(
            _psycopg_url(database.migration_url), autocommit=True
        ) as connection:
            connection.execute(
                sql.SQL("CREATE SEQUENCE public.{}").format(sql.Identifier(object_name))
            )
        with pytest.raises(psycopg.errors.RaiseException, match=object_name):
            _execute_artifact(database.migration_url, output_directory / "03-owner.sql")

        owner_engine = create_engine(database.migration_url)
        try:
            with owner_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0005_workflow_knowledge_audit"
                assert connection.scalar(
                    text("SELECT to_regclass('public.agent_run_citation')")
                ) == "agent_run_citation"
        finally:
            owner_engine.dispose()


def test_offline_replay_base_requires_empty_application_schema(
    tmp_path: Path,
) -> None:
    with disposable_database(prefix="ojcc_task7_") as database:
        output_directory = tmp_path / "nonempty-base-bundle"
        render_offline_bundle(
            bootstrap_database_url=bootstrap_database_url(database),
            migration_database_url=database.migration_url,
            database_url=database.application_url,
            revision="head",
            output_directory=output_directory,
        )
        with psycopg.connect(
            _psycopg_url(database.migration_url), autocommit=True
        ) as connection:
            connection.execute(
                "CREATE FUNCTION public.unexpected_preexisting() RETURNS integer "
                "LANGUAGE sql AS 'SELECT 1'"
            )

        with pytest.raises(
            psycopg.errors.RaiseException,
            match="empty application schema",
        ):
            _execute_artifact(database.migration_url, output_directory / "01-owner.sql")

        with psycopg.connect(_psycopg_url(database.migration_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('public.alembic_version')"
            ).fetchone() == (None,)


def test_offline_replay_rejects_an_empty_existing_revision_table(
    tmp_path: Path,
) -> None:
    with disposable_database(prefix="ojcc_task7_") as database:
        bootstrap_url = bootstrap_database_url(database)
        output_directory = tmp_path / "empty-revision-bundle"
        render_offline_bundle(
            bootstrap_database_url=bootstrap_url,
            migration_database_url=database.migration_url,
            database_url=database.application_url,
            revision="head",
            output_directory=output_directory,
        )
        _execute_artifact(database.migration_url, output_directory / "01-owner.sql")
        with psycopg.connect(_psycopg_url(bootstrap_url), autocommit=True) as connection:
            connection.execute("DELETE FROM public.alembic_version")

        with pytest.raises(
            psycopg.errors.RaiseException,
            match="requires revision 0004_safety_approval_lifecycle",
        ):
            _execute_artifact(bootstrap_url, output_directory / "02-bootstrap.sql")

        with psycopg.connect(_psycopg_url(database.migration_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM public.alembic_version"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT to_regclass('public.agent_run_citation')"
            ).fetchone() == (None,)

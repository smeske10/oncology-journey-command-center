from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from app.config import settings
from app.db.targets import parse_database_target, validate_database_target_pair

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_DATABASE_PREFIXES = frozenset(
    {"ojcc_migration_test_", "ojcc_task5_migration_", "ojcc_task7_"}
)
DatabasePrefix = Literal[
    "ojcc_migration_test_",
    "ojcc_task5_migration_",
    "ojcc_task7_",
]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

REVISION_ORDER = (
    "base",
    "0001_core_domain",
    "0002_identity_pathway_submission",
    "0003_need_task_outcome_lifecycle",
    "0004_safety_approval_lifecycle",
    "0005_workflow_knowledge_audit",
    "0006_navigator_closed_loop",
    "0007_database_least_privilege",
)

MIGRATION_0005_TABLES = (
    "agent_run_citation",
    "manual_review_task",
    "navigation_task_resource",
    "organization_knowledge_approval",
    "workflow_run",
    "workflow_transition_event",
)
MIGRATION_0005_FUNCTIONS = (
    "append_workflow_transition_event",
    "apply_navigation_resource_approval",
    "guard_agent_run_citation",
    "guard_agent_run_citation_immutable",
    "guard_agent_run_created_at",
    "guard_knowledge_approval_history",
    "guard_knowledge_document_immutable",
    "guard_manual_review_task",
    "guard_navigation_task_resource",
    "guard_navigation_task_resource_proposal",
    "guard_role_assignment_knowledge_history",
    "guard_workflow_run_lineage",
    "reject_append_only_mutation",
)
MIGRATION_0005_ENUMS = ("audit_actor_type", "manual_review_task_state")


@dataclass(frozen=True)
class DisposableDatabase:
    name: str
    migration_url: str
    application_url: str


@contextmanager
def user_triggers_disabled(executor: Any, *table_names: str) -> Iterator[None]:
    """Temporarily disable user triggers using only table-owner authority."""
    if not table_names:
        raise ValueError("at least one table name is required")
    if any(
        not name
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in name
        )
        for name in table_names
    ):
        raise ValueError("invalid table name")
    for table_name in table_names:
        executor.execute(text(f'ALTER TABLE public."{table_name}" DISABLE TRIGGER USER'))
    try:
        yield
    finally:
        for table_name in reversed(table_names):
            executor.execute(text(f'ALTER TABLE public."{table_name}" ENABLE TRIGGER USER'))


def validate_disposable_url(url: URL, *, prefix: str) -> None:
    if prefix not in ALLOWED_DATABASE_PREFIXES:
        raise ValueError("unsupported disposable database prefix")
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in LOOPBACK_HOSTS
        or url.port not in (None, 5432)
        or url.query
    ):
        raise ValueError(
            "disposable database URL must use loopback PostgreSQL on port 5432 "
            "without query parameters"
        )
    database_name = url.database or ""
    suffix = database_name.removeprefix(prefix)
    if (
        not database_name.startswith(prefix)
        or len(suffix) != 32
        or any(character not in "0123456789abcdef" for character in suffix)
    ):
        raise ValueError("invalid disposable database name")


def alembic_environment(database: DisposableDatabase) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "BOOTSTRAP_DATABASE_URL"}
        and not key.upper().startswith("PG")
    }
    environment["DATABASE_URL"] = database.application_url
    environment["MIGRATION_DATABASE_URL"] = database.migration_url
    return environment


def _alembic_environment_for(
    database: DisposableDatabase, *, migration_url: str
) -> dict[str, str]:
    environment = alembic_environment(database)
    environment["MIGRATION_DATABASE_URL"] = migration_url
    return environment


def run_alembic(
    database: DisposableDatabase,
    arguments: list[str],
    *,
    migration_url: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=_alembic_environment_for(
            database,
            migration_url=migration_url or database.migration_url,
        ),
        check=check,
        capture_output=True,
        text=True,
    )


def _psycopg_url(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def bootstrap_database_url(database: DisposableDatabase) -> str:
    bootstrap_text = os.getenv("BOOTSTRAP_DATABASE_URL")
    if bootstrap_text is None or not bootstrap_text.strip():
        raise ValueError("BOOTSTRAP_DATABASE_URL is required for migration 0005 replay")
    target_bootstrap_url = make_url(bootstrap_text).set(database=database.name).render_as_string(
        hide_password=False
    )
    validate_database_target_pair(
        application_url=database.application_url,
        migration_url=target_bootstrap_url,
    )
    validate_database_target_pair(
        application_url=database.migration_url,
        migration_url=target_bootstrap_url,
    )
    return target_bootstrap_url


def _revision_index(revision: str) -> int:
    normalized = "0007_database_least_privilege" if revision == "head" else revision
    try:
        return REVISION_ORDER.index(normalized)
    except ValueError as error:
        raise ValueError(f"unsupported migration revision: {revision}") from error


def _current_revision(database: DisposableDatabase) -> str:
    with psycopg.connect(_psycopg_url(make_url(database.migration_url))) as connection:
        with connection.cursor() as cursor:
            exists = cursor.execute(
                "SELECT to_regclass('public.alembic_version')"
            ).fetchone()
            if exists is None or exists[0] is None:
                return "base"
            row = cursor.execute("SELECT version_num FROM alembic_version").fetchone()
            return "base" if row is None else str(row[0])


def _transfer_0005_application_ownership(
    database: DisposableDatabase, *, bootstrap_url: str
) -> None:
    migration_role = parse_database_target(
        database.migration_url, label="MIGRATION_DATABASE_URL"
    ).username
    with psycopg.connect(_psycopg_url(make_url(bootstrap_url))) as connection:
        with connection.cursor() as cursor:
            for table_name in MIGRATION_0005_TABLES:
                cursor.execute(
                    sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
                        sql.Identifier(table_name), sql.Identifier(migration_role)
                    )
                )
            for function_name in MIGRATION_0005_FUNCTIONS:
                cursor.execute(
                    sql.SQL("ALTER FUNCTION public.{}() OWNER TO {}").format(
                        sql.Identifier(function_name), sql.Identifier(migration_role)
                    )
                )
            for enum_name in MIGRATION_0005_ENUMS:
                cursor.execute(
                    sql.SQL("ALTER TYPE public.{} OWNER TO {}").format(
                        sql.Identifier(enum_name), sql.Identifier(migration_role)
                    )
                )


def upgrade_database(
    database: DisposableDatabase,
    revision: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    target_index = _revision_index(revision)
    current_index = _revision_index(_current_revision(database))
    if target_index < current_index:
        return run_alembic(database, ["upgrade", revision], check=check)

    last_result: subprocess.CompletedProcess[str] | None = None
    bridge_index = _revision_index("0005_workflow_knowledge_audit")
    if current_index < bridge_index <= target_index:
        if current_index < _revision_index("0004_safety_approval_lifecycle"):
            last_result = run_alembic(
                database,
                ["upgrade", "0004_safety_approval_lifecycle"],
                check=check,
            )
            if last_result.returncode != 0:
                return last_result
        bootstrap_url = bootstrap_database_url(database)
        last_result = run_alembic(
            database,
            ["upgrade", "0005_workflow_knowledge_audit"],
            migration_url=bootstrap_url,
            check=check,
        )
        if last_result.returncode != 0:
            return last_result
        _transfer_0005_application_ownership(
            database,
            bootstrap_url=bootstrap_url,
        )

    if _revision_index(_current_revision(database)) < target_index:
        last_result = run_alembic(database, ["upgrade", revision], check=check)
    if last_result is None:
        last_result = run_alembic(database, ["current"], check=check)
    return last_result


@contextmanager
def disposable_database(
    *, prefix: DatabasePrefix, migrate_to: str | None = None
) -> Iterator[DisposableDatabase]:
    migration_base = make_url(settings.require_migration_database_url())
    application_base = make_url(settings.database_url)
    validate_database_target_pair(
        application_url=settings.database_url,
        migration_url=settings.require_migration_database_url(),
    )
    name = f"{prefix}{uuid4().hex}"
    migration_url = migration_base.set(database=name)
    application_url = application_base.set(database=name)
    validate_disposable_url(migration_url, prefix=prefix)
    validate_disposable_url(application_url, prefix=prefix)
    database = DisposableDatabase(
        name=name,
        migration_url=migration_url.render_as_string(hide_password=False),
        application_url=application_url.render_as_string(hide_password=False),
    )
    validate_database_target_pair(
        application_url=database.application_url,
        migration_url=database.migration_url,
    )

    maintenance_url = migration_base.set(database="postgres")
    migration_role = parse_database_target(
        database.migration_url, label="MIGRATION_DATABASE_URL"
    ).username
    created = False
    with psycopg.connect(_psycopg_url(maintenance_url), autocommit=True) as maintenance:
        with maintenance.cursor() as cursor:
            if cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone() is not None:
                raise RuntimeError("refusing to reuse an existing disposable database")
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(migration_role)
                )
            )
    created = True
    print(f"CREATED {name}", flush=True)

    try:
        with psycopg.connect(
            _psycopg_url(make_url(database.migration_url))
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                        sql.Identifier(migration_role)
                    )
                )
        if migrate_to is not None:
            upgrade_database(database, migrate_to)
        yield database
    finally:
        if created:
            validate_disposable_url(make_url(database.migration_url), prefix=prefix)
            try:
                with psycopg.connect(
                    _psycopg_url(maintenance_url), autocommit=True
                ) as maintenance:
                    with maintenance.cursor() as cursor:
                        cursor.execute(
                            sql.SQL("DROP DATABASE {}").format(sql.Identifier(name))
                        )
            except Exception as error:
                print(f"LEFTOVER {name}: {type(error).__name__}", flush=True)
            else:
                print(f"DROPPED {name}", flush=True)

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.db.targets import validate_database_target_pair
from scripts.seed_demo import validate_disposable_database_url

API_ROOT = Path(__file__).resolve().parents[1]
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay the immutable schema through the bounded 0005 bootstrap bridge."
    )
    parser.add_argument("--bootstrap-database-url", required=True)
    parser.add_argument("--migration-database-url", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--sql-output-directory", type=Path)
    return parser


def _revision_index(revision: str) -> int:
    normalized = "0007_database_least_privilege" if revision == "head" else revision
    try:
        return REVISION_ORDER.index(normalized)
    except ValueError as error:
        raise ValueError(f"Unsupported replay revision: {revision}") from error


def _child_environment(*, application_url: str, migration_url: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {"BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"}
        and not key.upper().startswith("PG")
    }
    environment["DATABASE_URL"] = application_url
    environment["MIGRATION_DATABASE_URL"] = migration_url
    return environment


def _upgrade(*, application_url: str, migration_url: str, revision: str) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=API_ROOT,
        env=_child_environment(
            application_url=application_url,
            migration_url=migration_url,
        ),
        check=True,
    )


def _render_upgrade(
    *, application_url: str, migration_url: str, revision_range: str
) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "--sql",
            revision_range,
        ],
        cwd=API_ROOT,
        env=_child_environment(
            application_url=application_url,
            migration_url=migration_url,
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _psycopg_url(url_text: str) -> str:
    return make_url(url_text).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _stage_preflight(
    *, credential: str, role: str, database: str, start_revision: str, end_revision: str
) -> str:
    revision_check = (
        "IF to_regclass('public.alembic_version') IS NOT NULL OR EXISTS ("
        "SELECT 1 FROM pg_class class JOIN pg_namespace namespace "
        "ON namespace.oid = class.relnamespace WHERE namespace.nspname = 'public' "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend dependency "
        "WHERE dependency.classid = 'pg_class'::regclass "
        "AND dependency.objid = class.oid AND dependency.deptype = 'e')) "
        "OR EXISTS (SELECT 1 FROM pg_proc procedure JOIN pg_namespace namespace "
        "ON namespace.oid = procedure.pronamespace "
        "WHERE namespace.nspname = 'public' "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend dependency "
        "WHERE dependency.classid = 'pg_proc'::regclass "
        "AND dependency.objid = procedure.oid AND dependency.deptype = 'e')) "
        "OR EXISTS (SELECT 1 FROM pg_type type JOIN pg_namespace namespace "
        "ON namespace.oid = type.typnamespace WHERE namespace.nspname = 'public' "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend dependency "
        "WHERE dependency.classid = 'pg_type'::regclass "
        "AND dependency.objid = type.oid AND dependency.deptype = 'e')) THEN\n"
        "        RAISE EXCEPTION 'offline replay base stage requires an empty "
        "application schema';\n"
        "    END IF;"
        if start_revision == "base"
        else (
            "IF to_regclass('public.alembic_version') IS NULL OR "
            "(SELECT count(*) FROM public.alembic_version) <> 1 OR "
            "NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = "
            f"{_sql_literal(start_revision)}) THEN\n"
            f"        RAISE EXCEPTION 'offline replay stage requires revision {start_revision}';\n"
            "    END IF;"
        )
    )
    return f"""
-- OFFLINE REPLAY STAGE: {credential} {start_revision} -> {end_revision}
DO $ojcc_replay_stage$
BEGIN
    IF current_user <> {_sql_literal(role)}
       OR current_database() <> {_sql_literal(database)} THEN
        RAISE EXCEPTION 'offline replay stage connected identity or database mismatch';
    END IF;
    {revision_check}
END
$ojcc_replay_stage$;
"""


def _inject_after_begin(artifact: str, preflight: str) -> str:
    marker = "BEGIN;"
    if marker not in artifact:
        raise RuntimeError("Alembic offline artifact has no transaction boundary")
    return artifact.replace(marker, f"{marker}\n{preflight}", 1)


def _inject_before_commit(artifact: str, sql_text: str) -> str:
    marker = "COMMIT;"
    position = artifact.rfind(marker)
    if position < 0:
        raise RuntimeError("Alembic offline artifact has no commit boundary")
    return f"{artifact[:position]}{sql_text}\n{artifact[position:]}"


def _ownership_transfer_sql(*, migration_role: str) -> str:
    role = _sql_identifier(migration_role)
    statements = [
        f"ALTER TABLE public.{_sql_identifier(name)} OWNER TO {role};"
        for name in MIGRATION_0005_TABLES
    ]
    statements.extend(
        f"ALTER FUNCTION public.{_sql_identifier(name)}() OWNER TO {role};"
        for name in MIGRATION_0005_FUNCTIONS
    )
    statements.extend(
        f"ALTER TYPE public.{_sql_identifier(name)} OWNER TO {role};"
        for name in MIGRATION_0005_ENUMS
    )
    return "\n-- Exact 0005 ownership transfer\n" + "\n".join(statements) + "\n"


def render_offline_bundle(
    *,
    bootstrap_database_url: str,
    migration_database_url: str,
    database_url: str,
    revision: str,
    output_directory: Path,
) -> None:
    if _revision_index(revision) != _revision_index("head"):
        raise ValueError("offline replay bundles currently require revision head")
    bootstrap_url = validate_disposable_database_url(bootstrap_database_url)
    migration_url = validate_disposable_database_url(migration_database_url)
    application_url = validate_disposable_database_url(database_url)
    bootstrap_text = bootstrap_url.render_as_string(hide_password=False)
    migration_text = migration_url.render_as_string(hide_password=False)
    application_text = application_url.render_as_string(hide_password=False)
    application_target, bootstrap_target = validate_database_target_pair(
        application_url=application_text,
        migration_url=bootstrap_text,
    )
    _, migration_target = validate_database_target_pair(
        application_url=application_text,
        migration_url=migration_text,
    )
    if bootstrap_target.username == migration_target.username:
        raise ValueError("Bootstrap, migration, and application require distinct usernames")

    stages = (
        (
            "01-owner.sql",
            "migration",
            "base",
            "0004_safety_approval_lifecycle",
            migration_text,
            migration_target.username,
        ),
        (
            "02-bootstrap.sql",
            "bootstrap",
            "0004_safety_approval_lifecycle",
            "0005_workflow_knowledge_audit",
            bootstrap_text,
            bootstrap_target.username,
        ),
        (
            "03-owner.sql",
            "migration",
            "0005_workflow_knowledge_audit",
            "0007_database_least_privilege",
            migration_text,
            migration_target.username,
        ),
    )
    rendered: dict[str, str] = {}
    manifest_stages: list[dict[str, str]] = []
    for file_name, credential, start, end, credential_url, role in stages:
        artifact = _render_upgrade(
            application_url=application_text,
            migration_url=credential_url,
            revision_range=f"{start}:{end}",
        )
        artifact = _inject_after_begin(
            artifact,
            _stage_preflight(
                credential=credential,
                role=role,
                database=application_target.database,
                start_revision=start,
                end_revision=end,
            ),
        )
        if credential == "bootstrap":
            artifact = _inject_before_commit(
                artifact,
                _ownership_transfer_sql(migration_role=migration_target.username),
            )
        rendered[file_name] = artifact
        manifest_stages.append(
            {
                "credential": credential,
                "end_revision": end,
                "file": file_name,
                "start_revision": start,
            }
        )

    output_directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "database": application_target.database,
        "format_version": 1,
        "roles": {
            "application": application_target.username,
            "bootstrap": bootstrap_target.username,
            "migration": migration_target.username,
        },
        "stages": manifest_stages,
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for file_name, artifact in rendered.items():
        (output_directory / file_name).write_text(artifact, encoding="utf-8")


def _transfer_0005_ownership(*, bootstrap_url: str, migration_role: str) -> None:
    with psycopg.connect(_psycopg_url(bootstrap_url)) as connection:
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


def replay_schema(
    *,
    bootstrap_database_url: str,
    migration_database_url: str,
    database_url: str,
    revision: str,
) -> None:
    bootstrap_url = validate_disposable_database_url(bootstrap_database_url)
    migration_url = validate_disposable_database_url(migration_database_url)
    application_url = validate_disposable_database_url(database_url)
    bootstrap_text = bootstrap_url.render_as_string(hide_password=False)
    migration_text = migration_url.render_as_string(hide_password=False)
    application_text = application_url.render_as_string(hide_password=False)
    _, bootstrap_target = validate_database_target_pair(
        application_url=application_text,
        migration_url=bootstrap_text,
    )
    _, migration_target = validate_database_target_pair(
        application_url=application_text,
        migration_url=migration_text,
    )
    if bootstrap_target.username == migration_target.username:
        raise ValueError("Bootstrap, migration, and application require distinct usernames")

    target_index = _revision_index(revision)
    if target_index == 0:
        return
    owner_prefix_index = min(
        target_index, _revision_index("0004_safety_approval_lifecycle")
    )
    _upgrade(
        application_url=application_text,
        migration_url=migration_text,
        revision=REVISION_ORDER[owner_prefix_index],
    )
    if target_index < _revision_index("0005_workflow_knowledge_audit"):
        return

    _upgrade(
        application_url=application_text,
        migration_url=bootstrap_text,
        revision="0005_workflow_knowledge_audit",
    )
    _transfer_0005_ownership(
        bootstrap_url=bootstrap_text,
        migration_role=migration_target.username,
    )
    if target_index > _revision_index("0005_workflow_knowledge_audit"):
        _upgrade(
            application_url=application_text,
            migration_url=migration_text,
            revision=revision,
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.sql_output_directory is not None:
        render_offline_bundle(
            bootstrap_database_url=arguments.bootstrap_database_url,
            migration_database_url=arguments.migration_database_url,
            database_url=arguments.database_url,
            revision=arguments.revision,
            output_directory=arguments.sql_output_directory,
        )
        return 0
    replay_schema(
        bootstrap_database_url=arguments.bootstrap_database_url,
        migration_database_url=arguments.migration_database_url,
        database_url=arguments.database_url,
        revision=arguments.revision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import ast
import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from tests.database_support import (
    ALLOWED_DATABASE_PREFIXES,
    DisposableDatabase,
    alembic_environment,
    disposable_database,
    run_alembic,
    validate_disposable_url,
)


@pytest.mark.parametrize(
    "prefix",
    ["ojcc_migration_test_", "ojcc_task5_migration_", "ojcc_task7_"],
)
def test_disposable_url_accepts_only_exact_supported_uuid_names(prefix: str) -> None:
    url = make_url(
        f"postgresql+psycopg://owner:secret@127.0.0.1:5432/{prefix}{'a' * 32}"
    )
    validate_disposable_url(url, prefix=prefix)

    for invalid_name in (
        f"{prefix}{'a' * 31}",
        f"{prefix}{'a' * 33}",
        f"{prefix}{'A' * 32}",
        f"{prefix}{'g' * 32}",
        f"wrong_{'a' * 32}",
    ):
        with pytest.raises(ValueError, match="disposable database"):
            validate_disposable_url(url.set(database=invalid_name), prefix=prefix)


def test_disposable_url_rejects_remote_port_query_and_unknown_prefix() -> None:
    prefix = "ojcc_task7_"
    url = make_url(
        f"postgresql+psycopg://owner:secret@127.0.0.1:5432/{prefix}{'b' * 32}"
    )
    invalid_urls = (
        url.set(host="database.example.test"),
        url.set(port=5433),
        url.update_query_dict({"sslmode": "require"}),
        url.set(drivername="sqlite"),
    )
    for invalid_url in invalid_urls:
        with pytest.raises(ValueError):
            validate_disposable_url(invalid_url, prefix=prefix)

    assert prefix in ALLOWED_DATABASE_PREFIXES
    with pytest.raises(ValueError, match="prefix"):
        validate_disposable_url(url, prefix="ojcc_unknown_")


def test_alembic_environment_overwrites_dirty_inherited_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://dirty-app/other")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://dirty-owner/other")
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", "postgresql://dirty-bootstrap/other")
    monkeypatch.setenv("PGDATABASE", "other")
    monkeypatch.setenv("pgHOST", "remote")
    monkeypatch.setenv("KEEP_ME", "preserved")
    database = DisposableDatabase(
        name=f"ojcc_task7_{'c' * 32}",
        migration_url=(
            f"postgresql+psycopg://owner:secret@127.0.0.1:5432/"
            f"ojcc_task7_{'c' * 32}"
        ),
        application_url=(
            f"postgresql+psycopg://app:secret@127.0.0.1:5432/"
            f"ojcc_task7_{'c' * 32}"
        ),
    )

    environment = alembic_environment(database)

    assert environment["DATABASE_URL"] == database.application_url
    assert environment["MIGRATION_DATABASE_URL"] == database.migration_url
    assert "BOOTSTRAP_DATABASE_URL" not in environment
    assert all(not key.upper().startswith("PG") for key in environment)
    assert environment["KEEP_ME"] == "preserved"
    assert os.environ["DATABASE_URL"] == "postgresql://dirty-app/other"


def test_database_lifecycle_source_uses_quoted_identifiers_without_termination() -> None:
    source = (
        Path(__file__).resolve().parent / "database_support.py"
    ).read_text()
    assert "sql.Identifier" in source
    assert "pg_terminate_backend" not in source
    assert 'f"CREATE DATABASE' not in source
    assert 'f"DROP DATABASE' not in source


def test_alembic_test_children_use_an_explicit_two_target_environment() -> None:
    """Repository guard: real Alembic children cannot inherit or set one target."""
    tests_root = Path(__file__).resolve().parent
    violations: list[str] = []
    for path in tests_root.rglob("*.py"):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "subprocess"
                and function.attr == "run"
            ):
                continue
            literals = {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            if "alembic" not in literals:
                continue
            if path.name == "test_database_targets.py":
                # This module deliberately launches invalid target combinations.
                continue
            environment = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "env"),
                None,
            )
            environment_source = (
                ast.get_source_segment(source, environment)
                if environment is not None
                else None
            )
            if environment_source is None or "alembic_environment" not in environment_source:
                violations.append(str(path.relative_to(tests_root)))

    assert violations == []


def test_disposable_database_replays_head_and_drops_normally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with disposable_database(prefix="ojcc_migration_test_", migrate_to="head") as database:
        current = run_alembic(database, ["current"])
        assert "0007_database_least_privilege (head)" in current.stdout
        name = database.name

    output = capsys.readouterr().out
    assert f"CREATED {name}" in output
    assert f"DROPPED {name}" in output
    print(output, end="")


def test_disposable_database_leaves_unknown_live_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application_connection = None
    database = None
    try:
        with disposable_database(
            prefix="ojcc_migration_test_", migrate_to=None
        ) as created:
            database = created
            application_connection = psycopg.connect(
                make_url(created.application_url)
                .set(drivername="postgresql")
                .render_as_string(hide_password=False)
            )

        assert database is not None
        output = capsys.readouterr().out
        assert f"LEFTOVER {database.name}: ObjectInUse" in output
        print(output, end="")
    finally:
        if application_connection is not None:
            application_connection.close()
        if database is not None:
            migration_url = make_url(database.migration_url)
            validate_disposable_url(migration_url, prefix="ojcc_migration_test_")
            maintenance_url = migration_url.set(
                drivername="postgresql", database="postgres"
            ).render_as_string(hide_password=False)
            with psycopg.connect(maintenance_url, autocommit=True) as maintenance:
                with maintenance.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("DROP DATABASE {}").format(
                            sql.Identifier(database.name)
                        )
                    )
            print(f"DROPPED {database.name} after deliberate live-session test")

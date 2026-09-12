from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _targets_module() -> ModuleType:
    try:
        return importlib.import_module("app.db.targets")
    except ModuleNotFoundError:
        pytest.fail("app.db.targets is missing")


def _config_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://runtime:runtime-secret@localhost/ojcc_test"
    )
    return importlib.import_module("app.config")


def _run_offline_alembic(
    *, application_url: str | None, migration_url: str | None, revision: str = "head"
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"BOOTSTRAP_DATABASE_URL", "DATABASE_URL", "MIGRATION_DATABASE_URL"}
    }
    if application_url is not None:
        environment["DATABASE_URL"] = application_url
    if migration_url is not None:
        environment["MIGRATION_DATABASE_URL"] = migration_url
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            revision,
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


@pytest.mark.parametrize("url_text", [None, "", "   "])
def test_parser_rejects_missing_or_blank_url(url_text: str | None) -> None:
    targets = _targets_module()

    with pytest.raises(targets.DatabaseTargetConfigurationError, match="DATABASE_URL"):
        targets.parse_database_target(url_text, label="DATABASE_URL")


def test_parser_rejects_malformed_url_without_echoing_it() -> None:
    targets = _targets_module()
    malformed = "not-a-url-with-secret"

    with pytest.raises(targets.DatabaseTargetConfigurationError) as error:
        targets.parse_database_target(malformed, label="DATABASE_URL")

    assert "DATABASE_URL" in str(error.value)
    assert malformed not in str(error.value)


@pytest.mark.parametrize(
    ("url_text", "message"),
    [
        ("sqlite:///tmp/example.db", "PostgreSQL"),
        ("postgresql://runtime:secret@/ojcc_test", "host"),
        ("postgresql://runtime:secret@localhost", "database name"),
        ("postgresql://localhost/ojcc_test", "username"),
        ("postgresql://%20:secret@localhost/ojcc_test", "username"),
    ],
)
def test_parser_rejects_incomplete_or_non_postgresql_targets(
    url_text: str, message: str
) -> None:
    targets = _targets_module()

    with pytest.raises(targets.DatabaseTargetConfigurationError, match=message):
        targets.parse_database_target(url_text, label="DATABASE_URL")


def test_parser_normalizes_backend_host_and_default_postgresql_port() -> None:
    targets = _targets_module()

    target = targets.parse_database_target(
        "postgresql+psycopg://runtime:secret@LOCALHOST/ojcc_test",
        label="DATABASE_URL",
    )

    assert target == targets.DatabaseTarget(
        backend="postgresql",
        host="localhost",
        port=5432,
        database="ojcc_test",
        username="runtime",
    )


def test_pair_accepts_same_target_with_distinct_usernames_and_passwords() -> None:
    targets = _targets_module()

    application, migration = targets.validate_database_target_pair(
        application_url="postgresql+psycopg://runtime:runtime-secret@localhost/ojcc_test?sslmode=require",
        migration_url="postgresql://owner:owner-secret@LOCALHOST:5432/ojcc_test?application_name=migrate",
    )

    assert application.database == migration.database == "ojcc_test"
    assert application.username == "runtime"
    assert migration.username == "owner"


def test_pair_rejects_the_same_username_without_disclosing_it() -> None:
    targets = _targets_module()
    shared_username = "credential-name-must-stay-redacted"

    with pytest.raises(targets.DatabaseCredentialConflict, match="distinct usernames") as error:
        targets.validate_database_target_pair(
            application_url=f"postgresql://{shared_username}:runtime-secret@localhost/ojcc_test",
            migration_url=f"postgresql://{shared_username}:owner-secret@localhost/ojcc_test",
        )

    assert shared_username not in str(error.value)


@pytest.mark.parametrize(
    ("application_url", "migration_url", "message"),
    [
        (
            "postgresql://runtime:secret@localhost:5432/ojcc_test",
            "postgresql://owner:secret@127.0.0.1:5432/ojcc_test",
            "host",
        ),
        (
            "postgresql://runtime:secret@localhost:5432/ojcc_test",
            "postgresql://owner:secret@localhost:5433/ojcc_test",
            "port",
        ),
        (
            "postgresql://runtime:secret@localhost:5432/ojcc_test_a",
            "postgresql://owner:secret@localhost:5432/ojcc_test_b",
            "database name",
        ),
    ],
)
def test_pair_rejects_target_mismatches(
    application_url: str, migration_url: str, message: str
) -> None:
    targets = _targets_module()

    with pytest.raises(targets.DatabaseTargetMismatch, match=message):
        targets.validate_database_target_pair(
            application_url=application_url,
            migration_url=migration_url,
        )


def test_pair_rejects_inherited_migration_url_for_another_database(monkeypatch) -> None:
    targets = _targets_module()
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://runtime:runtime-secret@localhost/ojcc_test_a"
    )
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", "postgresql://owner:owner-secret@localhost/ojcc_test_b"
    )

    with pytest.raises(targets.DatabaseTargetMismatch, match="database name"):
        targets.validate_database_target_pair(
            application_url=os.environ["DATABASE_URL"],
            migration_url=os.environ["MIGRATION_DATABASE_URL"],
        )


def test_settings_rejects_missing_application_url(monkeypatch) -> None:
    config = _config_module(monkeypatch)
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(config.DatabaseTargetConfigurationError, match="DATABASE_URL"):
        config.Settings()


def test_api_settings_need_only_the_application_url(monkeypatch) -> None:
    config = _config_module(monkeypatch)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    application_url = "postgresql://runtime:runtime-secret@localhost/ojcc_test"
    monkeypatch.setenv("DATABASE_URL", application_url)

    configured = config.Settings()

    assert configured.database_url == application_url
    assert configured.migration_database_url is None


def test_settings_requires_migration_url_only_for_owner_workflows(monkeypatch) -> None:
    config = _config_module(monkeypatch)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    configured = config.Settings()

    with pytest.raises(
        config.DatabaseTargetConfigurationError, match="MIGRATION_DATABASE_URL"
    ):
        configured.require_migration_database_url()


def test_settings_returns_explicit_migration_url_without_replacing_runtime_url(
    monkeypatch,
) -> None:
    config = _config_module(monkeypatch)
    application_url = "postgresql://runtime:runtime-secret@localhost/ojcc_test"
    migration_url = "postgresql://owner:owner-secret@localhost/ojcc_test"
    monkeypatch.setenv("DATABASE_URL", application_url)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_url)

    configured = config.Settings()

    assert configured.database_url == application_url
    assert configured.require_migration_database_url() == migration_url


def test_alembic_rejects_missing_migration_url_before_rendering_sql() -> None:
    result = _run_offline_alembic(
        application_url="postgresql://runtime:runtime-secret@target.invalid/ojcc_target_a",
        migration_url=None,
    )

    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL is required" in result.stderr
    assert "CREATE TABLE" not in result.stdout


def test_alembic_rejects_an_inherited_migration_target_mismatch() -> None:
    result = _run_offline_alembic(
        application_url="postgresql://runtime:runtime-secret@target.invalid/ojcc_target_a",
        migration_url="postgresql://owner:owner-secret@target.invalid/ojcc_target_b",
    )

    assert result.returncode != 0
    assert "same database name" in result.stderr
    assert "CREATE TABLE" not in result.stdout


def test_alembic_rejects_shared_runtime_and_migration_username() -> None:
    result = _run_offline_alembic(
        application_url="postgresql://shared:runtime-secret@target.invalid/ojcc_target_a",
        migration_url="postgresql://shared:owner-secret@target.invalid/ojcc_target_a",
    )

    assert result.returncode != 0
    assert "distinct usernames" in result.stderr
    assert "CREATE TABLE" not in result.stdout


def test_alembic_accepts_a_matching_target_pair_for_offline_sql() -> None:
    result = _run_offline_alembic(
        application_url="postgresql+psycopg://runtime:runtime-secret@target.invalid/ojcc_target_a",
        migration_url="postgresql://owner:owner-secret@TARGET.INVALID:5432/ojcc_target_a",
        revision="0006_navigator_closed_loop",
    )

    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE organization" in result.stdout

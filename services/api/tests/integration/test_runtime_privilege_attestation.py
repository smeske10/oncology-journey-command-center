from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from urllib.request import urlopen
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.db.privilege_attestation import (
    RuntimePrivilegeBoundaryError,
    attest_runtime_database,
)
from app.db.targets import parse_database_target
from tests.database_support import (
    DisposableDatabase,
    bootstrap_database_url,
    disposable_database,
)

APPLICATION_ROLE = "ojcc_api"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _uvicorn_environment(database_url: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {"BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"}
        and not key.upper().startswith("PG")
    }
    environment["DATABASE_URL"] = database_url
    return environment


def _failed_uvicorn_output(database_url: str) -> str:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(_free_port()),
        ],
        env=_uvicorn_environment(database_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.communicate(timeout=5)
        pytest.fail("Uvicorn served despite a runtime database boundary failure")
    assert process.returncode != 0
    return f"{stdout}\n{stderr}"


@pytest.fixture
def runtime_database() -> Iterator[DisposableDatabase]:
    with disposable_database(
        prefix="ojcc_migration_test_", migrate_to="head"
    ) as database:
        yield database


def test_valid_runtime_database_passes_attestation(
    runtime_database: DisposableDatabase,
) -> None:
    target = parse_database_target(
        runtime_database.application_url, label="DATABASE_URL"
    )
    engine = create_engine(runtime_database.application_url)
    try:
        with engine.connect() as connection:
            attest_runtime_database(connection, target=target)
    finally:
        engine.dispose()


def test_owner_credentials_fail_runtime_identity_attestation(
    runtime_database: DisposableDatabase,
) -> None:
    target = parse_database_target(
        runtime_database.migration_url, label="DATABASE_URL"
    )
    engine = create_engine(runtime_database.migration_url)
    try:
        with engine.connect() as connection:
            with pytest.raises(
                RuntimePrivilegeBoundaryError,
                match="runtime_database_boundary.role_profile",
            ):
                attest_runtime_database(connection, target=target)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("grant_sql", "boundary"),
    [
        (
            "GRANT DELETE ON TABLE public.organization TO ojcc_api",
            "runtime_database_boundary.relation_privileges",
        ),
        (
            "GRANT UPDATE (name) ON TABLE public.organization TO ojcc_api",
            "runtime_database_boundary.column_privileges",
        ),
        (
            "GRANT EXECUTE ON FUNCTION public.guard_agent_run_citation() TO ojcc_api",
            "runtime_database_boundary.function_privileges",
        ),
    ],
)
def test_direct_runtime_grant_fails_attestation(
    runtime_database: DisposableDatabase,
    grant_sql: str,
    boundary: str,
) -> None:
    owner_engine = create_engine(runtime_database.migration_url)
    try:
        with owner_engine.begin() as connection:
            connection.exec_driver_sql(grant_sql)
    finally:
        owner_engine.dispose()

    target = parse_database_target(
        runtime_database.application_url, label="DATABASE_URL"
    )
    runtime_engine = create_engine(runtime_database.application_url)
    try:
        with runtime_engine.connect() as connection:
            with pytest.raises(RuntimePrivilegeBoundaryError, match=boundary):
                attest_runtime_database(connection, target=target)
    finally:
        runtime_engine.dispose()

    output = _failed_uvicorn_output(runtime_database.application_url)
    assert boundary in output
    assert "api-local-synthetic-only" not in output
    assert "postgresql" not in output.casefold()


def test_extra_runtime_membership_fails_attestation(
    runtime_database: DisposableDatabase,
) -> None:
    extra_role = f"ojcc_runtime_extra_{uuid4().hex}"
    bootstrap_url = bootstrap_database_url(runtime_database)
    bootstrap_dsn = make_url(bootstrap_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    try:
        with psycopg.connect(bootstrap_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(extra_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT {} TO {} WITH INHERIT FALSE, SET FALSE, ADMIN FALSE"
                ).format(sql.Identifier(extra_role), sql.Identifier(APPLICATION_ROLE))
            )

        target = parse_database_target(
            runtime_database.application_url, label="DATABASE_URL"
        )
        engine = create_engine(runtime_database.application_url)
        try:
            with engine.connect() as runtime_connection:
                with pytest.raises(
                    RuntimePrivilegeBoundaryError,
                    match="runtime_database_boundary.membership",
                ):
                    attest_runtime_database(runtime_connection, target=target)
        finally:
            engine.dispose()
        output = _failed_uvicorn_output(runtime_database.application_url)
        assert "runtime_database_boundary.membership" in output
        assert "api-local-synthetic-only" not in output
        assert "postgresql" not in output.casefold()
    finally:
        with psycopg.connect(bootstrap_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(extra_role), sql.Identifier(APPLICATION_ROLE)
                )
            )
            connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(extra_role))
            )


def test_uvicorn_serves_health_only_after_valid_attestation(
    runtime_database: DisposableDatabase,
) -> None:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=_uvicorn_environment(runtime_database.application_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        response_body = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"Uvicorn exited before health was served: {stdout}\n{stderr}")
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                    response_body = response.read().decode("utf-8")
                break
            except OSError:
                time.sleep(0.1)
        assert response_body == '{"status":"ok"}'
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("credential_kind", ["owner", "bootstrap"])
def test_uvicorn_exits_nonzero_on_privileged_runtime_credentials(
    runtime_database: DisposableDatabase, credential_kind: str
) -> None:
    database_url = (
        runtime_database.migration_url
        if credential_kind == "owner"
        else bootstrap_database_url(runtime_database)
    )
    combined = _failed_uvicorn_output(database_url)
    assert "runtime_database_boundary.role_profile" in combined
    assert "local-synthetic-only" not in combined
    assert "postgresql" not in combined.casefold()

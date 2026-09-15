from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth.models import Role
from app.db.models import Organization, RoleAssignment, User
from tests.database_support import DisposableDatabase, disposable_database

API_ROOT = Path(__file__).resolve().parents[2]
SESSION_SECRET = "process-demo-session-secret-sentinel"
APPLICATION_GROUP = "ojcc_app"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _uvicorn_environment(
    database: DisposableDatabase, *, organization_id: object, user_id: object
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {
            "BOOTSTRAP_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "DATABASE_URL",
            "DEMO_SESSION_SECRET",
            "DEMO_ORGANIZATION_ID",
            "DEMO_ACTORS_JSON",
        }
        and not key.upper().startswith("PG")
    }
    environment.update(
        {
            "APP_ENV": "local",
            "DATABASE_URL": database.application_url,
            "DEMO_SESSION_SECRET": SESSION_SECRET,
            "DEMO_ORGANIZATION_ID": str(organization_id),
            "DEMO_ACTORS_JSON": json.dumps(
                {Role.NAVIGATOR.value: {"user_id": str(user_id)}}
            ),
        }
    )
    return environment


def _wait_for_health(process: subprocess.Popen[str], port: int) -> httpx.Response:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"Uvicorn exited before health was served: {stdout}\n{stderr}")
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
        except httpx.TransportError:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            return response
        time.sleep(0.1)
    pytest.fail("Uvicorn did not serve health before the bounded deadline")


@pytest.fixture
def demo_session_process_database() -> Iterator[DisposableDatabase]:
    with disposable_database(prefix="ojcc_task7_", migrate_to="head") as database:
        yield database


def test_uvicorn_sanitizes_auth_query_failure_after_valid_startup_attestation(
    demo_session_process_database: DisposableDatabase,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    owner_engine = create_engine(demo_session_process_database.migration_url)
    process: subprocess.Popen[str] | None = None
    privilege_revoked = False
    stdout = ""
    stderr = ""
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"Process auth {uuid4()}"))
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"process-{uuid4()}@example.test",
                    display_name="Process auth actor",
                    primary_organization_id=None,
                )
            )
            owner.flush()
            owner.add(
                RoleAssignment(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.NAVIGATOR,
                    granted_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            owner.commit()

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
            cwd=API_ROOT,
            env=_uvicorn_environment(
                demo_session_process_database,
                organization_id=organization_id,
                user_id=user_id,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        health = _wait_for_health(process, port)
        assert health.json() == {"status": "ok"}

        with owner_engine.begin() as owner:
            owner.execute(
                text(
                    "REVOKE SELECT ON TABLE public.role_assignment "
                    f"FROM {APPLICATION_GROUP}"
                )
            )
        privilege_revoked = True
        try:
            response = httpx.post(
                f"http://127.0.0.1:{port}/v1/demo/session/navigator",
                timeout=5,
            )
        finally:
            with owner_engine.begin() as owner:
                owner.execute(
                    text(
                        "GRANT SELECT ON TABLE public.role_assignment "
                        f"TO {APPLICATION_GROUP}"
                    )
                )
            privilege_revoked = False

        assert response.status_code == 503
        assert response.json() == {"detail": "Demo authentication is unavailable"}
        assert "set-cookie" not in response.headers
    finally:
        restore_error: Exception | None = None
        try:
            if privilege_revoked:
                with owner_engine.begin() as owner:
                    owner.execute(
                        text(
                            "GRANT SELECT ON TABLE public.role_assignment "
                            f"TO {APPLICATION_GROUP}"
                        )
                    )
        except Exception as error:
            restore_error = error
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    stdout, stderr = process.communicate()
            finally:
                owner_engine.dispose()
        if restore_error is not None:
            raise restore_error

    process_output = f"{stdout}\n{stderr}"
    assert "503 Service Unavailable" in process_output
    assert "Traceback" not in process_output
    assert "permission denied" not in process_output.casefold()
    assert SESSION_SECRET not in process_output
    assert demo_session_process_database.name not in process_output

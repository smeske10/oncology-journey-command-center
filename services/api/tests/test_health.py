import asyncio
import os
import subprocess
import sys
import traceback

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.db.privilege_attestation import attest_runtime_database
from app.db.targets import parse_database_target
from app.main import app


def test_health_returns_ok() -> None:
    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_application_exposes_health_and_demo_session_routes() -> None:
    from app.api.demo_sessions import router

    assert {route.path for route in app.routes if hasattr(route, "path")} == {"/health"}
    assert "/v1/demo/session/{role}" in {route.path for route in router.routes}
    assert any(getattr(route, "original_router", None) is router for route in app.routes)


def test_application_import_and_openapi_export_do_not_connect() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {"BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"}
        and not key.upper().startswith("PG")
    }
    environment["DATABASE_URL"] = (
        "postgresql+psycopg://runtime:import-secret-sentinel@target.invalid/ojcc_import"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.main import app; print(app.title); print(len(app.openapi()['paths']))",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "Oncology Journey Command Center API" in result.stdout
    assert "import-secret-sentinel" not in f"{result.stdout}\n{result.stderr}"


def test_runtime_attestation_suppresses_driver_exception_context() -> None:
    sentinel = "query-driver-secret-sentinel"

    class FailingConnection:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise SQLAlchemyError(sentinel)

    target = parse_database_target(
        "postgresql://runtime:synthetic@localhost/ojcc_test",
        label="DATABASE_URL",
    )
    with pytest.raises(RuntimeError) as caught:
        attest_runtime_database(FailingConnection(), target=target)  # type: ignore[arg-type]

    rendered = "".join(traceback.format_exception(caught.value))
    assert "runtime_database_boundary.unavailable" in rendered
    assert sentinel not in rendered

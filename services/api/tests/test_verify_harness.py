from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_DATABASE_NAME = "ojcc_demo_11111111111111111111111111111111"
LIVE_DATABASE_NAME = "ojcc_demo_22222222222222222222222222222222"
API_DATABASE_URL = (
    "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
    f"{API_DATABASE_NAME}"
)
LIVE_DATABASE_URL = (
    "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
    f"{LIVE_DATABASE_NAME}"
)
CHILD_SENTINEL = "VERIFY_CHILD_WAS_INVOKED"


def _powershell() -> str:
    for candidate in ("pwsh", "powershell"):
        if executable := shutil.which(candidate):
            return executable
    raise RuntimeError("Verification harness tests require pwsh or Windows PowerShell")


def _write_fake_command(
    directory: Path,
    name: str,
    *,
    exit_code: int,
    output: str = CHILD_SENTINEL,
    require_clean_pg: bool = False,
    log_arguments: bool = False,
) -> None:
    if os.name == "nt":
        pg_check = (
            "for /f \"delims==\" %%V in ('set PG 2^>nul') do (\n"
            "  echo PG_DIRTY\n"
            "  exit /b 42\n"
            ")\n"
            "echo PG_CLEAN\n"
            if require_clean_pg
            else ""
        )
        argument_log = (
            f'echo {name} %*>>"%VERIFY_FAKE_LOG%"\n' if log_arguments else ""
        )
        (directory / f"{name}.cmd").write_text(
            f"@echo off\n{pg_check}{argument_log}echo {output}\nexit /b {exit_code}\n",
            encoding="utf-8",
        )
        return

    pg_check = (
        "if env | grep -i '^pg' >/dev/null; then echo PG_DIRTY; exit 42; fi\n"
        "echo PG_CLEAN\n"
        if require_clean_pg
        else ""
    )
    argument_log = (
        f"printf '%s %s\\n' '{name}' \"$*\" >> \"$VERIFY_FAKE_LOG\"\n"
        if log_arguments
        else ""
    )
    command = directory / name
    command.write_text(
        f"#!/bin/sh\n{pg_check}{argument_log}echo {output}\nexit {exit_code}\n",
        encoding="utf-8",
    )
    command.chmod(0o755)


def _verification_environment(fake_bin: Path, *, database_url: str | None) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("PG")
    }
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    if database_url is None:
        environment.pop("DATABASE_URL", None)
    else:
        environment["DATABASE_URL"] = database_url
    return environment


def _run_verify(
    live_database_url: str,
    live_confirmation: str,
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/verify.ps1",
            "-LiveDatabaseUrl",
            live_database_url,
            "-LiveConfirmDatabaseName",
            live_confirmation,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _run_live_wrapper(*, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/verify_live_journey.ps1",
            "-DatabaseUrl",
            LIVE_DATABASE_URL,
            "-ConfirmDatabaseName",
            LIVE_DATABASE_NAME,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize(
    ("live_url", "confirmation", "expected"),
    (
        (
            "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/ojcc",
            "ojcc",
            "disposable",
        ),
        (
            "postgresql+psycopg://ojcc:local-synthetic-only@database.example.test:5432/"
            "ojcc_demo_deadbeef",
            "ojcc_demo_deadbeef",
            "loopback",
        ),
        (
            "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
            "ojcc_demo_deadbeef?host=database.example.test",
            "ojcc_demo_deadbeef",
            "query",
        ),
    ),
)
def test_verify_refuses_unsafe_live_targets_before_any_child_process(
    tmp_path: Path,
    live_url: str,
    confirmation: str,
    expected: str,
) -> None:
    """Production break: the full verifier reaches children before rejecting a live target."""
    _write_fake_command(tmp_path, "python", exit_code=97)
    result = _run_verify(
        live_url,
        confirmation,
        environment=_verification_environment(tmp_path, database_url=API_DATABASE_URL),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert expected in output.lower()
    assert CHILD_SENTINEL not in output


def test_verify_refuses_one_database_for_both_api_and_live_stages(tmp_path: Path) -> None:
    """Production break: the live reset can erase data under active API tests."""
    _write_fake_command(tmp_path, "python", exit_code=97)
    result = _run_verify(
        API_DATABASE_URL,
        API_DATABASE_NAME,
        environment=_verification_environment(tmp_path, database_url=API_DATABASE_URL),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "different" in output.lower()
    assert CHILD_SENTINEL not in output


@pytest.mark.parametrize(
    ("api_url", "expected"),
    (
        (None, "database_url"),
        (
            "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/ojcc",
            "disposable",
        ),
        (
            "postgresql+psycopg://ojcc:local-synthetic-only@127.0.0.1:5432/"
            "ojcc_demo_deadbeef?host=database.example.test",
            "query",
        ),
    ),
)
def test_verify_requires_an_explicit_safe_api_target_before_any_child_process(
    tmp_path: Path,
    api_url: str | None,
    expected: str,
) -> None:
    """Production break: application imports or tests can silently use persistent ojcc."""
    _write_fake_command(tmp_path, "python", exit_code=97)
    result = _run_verify(
        LIVE_DATABASE_URL,
        LIVE_DATABASE_NAME,
        environment=_verification_environment(tmp_path, database_url=api_url),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert expected in output.lower()
    assert CHILD_SENTINEL not in output


def test_verify_returns_nonzero_when_a_required_child_fails(tmp_path: Path) -> None:
    """Production break: a failed required gate is treated as successful verification."""
    _write_fake_command(tmp_path, "python", exit_code=41)
    result = _run_verify(
        LIVE_DATABASE_URL,
        LIVE_DATABASE_NAME,
        environment=_verification_environment(tmp_path, database_url=API_DATABASE_URL),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "41" in output
    assert CHILD_SENTINEL in output


def test_verify_removes_and_restores_inherited_pg_environment_on_failure(
    tmp_path: Path,
) -> None:
    """Production break: libpq routing reaches a child or leaks after verifier failure."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "python", exit_code=41, require_clean_pg=True)
    wrapper = tmp_path / "probe.ps1"
    verify_path = PROJECT_ROOT / "scripts" / "verify.ps1"
    escaped_verify_path = str(verify_path).replace("'", "''")
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:PGHOST = 'unsafe.example.test'\n"
        "$env:pgport = '6543'\n"
        "$env:PgSyntheticProbe = 'preserve-me'\n"
        "$caught = $null\n"
        "try {\n"
        f"  . '{escaped_verify_path}' "
        f"-LiveDatabaseUrl '{LIVE_DATABASE_URL}' "
        f"-LiveConfirmDatabaseName '{LIVE_DATABASE_NAME}'\n"
        "}\n"
        "catch { $caught = $_.Exception.Message }\n"
        "if ($caught -notmatch '41') { throw \"Missing child failure: $caught\" }\n"
        "if ($env:PGHOST -ne 'unsafe.example.test' -or "
        "$env:pgport -ne '6543' -or $env:PgSyntheticProbe -ne 'preserve-me') {\n"
        "  throw 'PG environment was not restored'\n"
        "}\n"
        "Write-Output 'PG_RESTORED'\n",
        encoding="utf-8",
    )
    environment = _verification_environment(fake_bin, database_url=API_DATABASE_URL)
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "PG_CLEAN" in output
    assert "PG_DIRTY" not in output
    assert "PG_RESTORED" in output


def test_verify_scopes_tool_noise_environment_and_restores_prior_values(
    tmp_path: Path,
) -> None:
    """Production break: locked tools emit avoidable notices or leak environment changes."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    if os.name == "nt":
        (fake_bin / "python.cmd").write_text(
            "@echo off\n"
            "if not \"%1 %2\"==\"-m pyright\" goto ok\n"
            "if not \"%PYRIGHT_PYTHON_IGNORE_WARNINGS%\"==\"1\" goto notice\n"
            "if defined PYRIGHT_PYTHON_FORCE_VERSION goto unpinned\n"
            ":ok\n"
            "echo FAKE_PYTHON_OK\n"
            "exit /b 0\n"
            ":notice\n"
            "echo PYRIGHT_VERSION_NOTICE\n"
            "exit /b 43\n"
            ":unpinned\n"
            "echo PYRIGHT_UNPINNED\n"
            "exit /b 45\n",
            encoding="utf-8",
        )
        (fake_bin / "npm.cmd").write_text(
            "@echo off\n"
            "if defined NO_COLOR (echo NO_COLOR_DIRTY & exit /b 44)\n"
            "echo TOOL_ENV_CLEAN\n"
            "exit /b 41\n",
            encoding="utf-8",
        )
    else:
        python_command = fake_bin / "python"
        python_command.write_text(
            "#!/bin/sh\n"
            "if [ \"$1 $2\" = '-m pyright' ]; then\n"
            "  if [ \"$PYRIGHT_PYTHON_IGNORE_WARNINGS\" != '1' ]; then\n"
            "    echo PYRIGHT_VERSION_NOTICE\n"
            "    exit 43\n"
            "  fi\n"
            "  if [ -n \"$PYRIGHT_PYTHON_FORCE_VERSION\" ]; then\n"
            "    echo PYRIGHT_UNPINNED\n"
            "    exit 45\n"
            "  fi\n"
            "fi\n"
            "echo FAKE_PYTHON_OK\n"
            "exit 0\n",
            encoding="utf-8",
        )
        python_command.chmod(0o755)
        npm_command = fake_bin / "npm"
        npm_command.write_text(
            "#!/bin/sh\n"
            "if [ -n \"$NO_COLOR\" ]; then echo NO_COLOR_DIRTY; exit 44; fi\n"
            "echo TOOL_ENV_CLEAN\n"
            "exit 41\n",
            encoding="utf-8",
        )
        npm_command.chmod(0o755)

    wrapper = tmp_path / "tool-environment-probe.ps1"
    escaped_verify_path = str(PROJECT_ROOT / "scripts" / "verify.ps1").replace("'", "''")
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:NO_COLOR = 'preserve-no-color'\n"
        "$env:PYRIGHT_PYTHON_FORCE_VERSION = 'preserve-version-setting'\n"
        "$env:PYRIGHT_PYTHON_IGNORE_WARNINGS = 'preserve-ignore-setting'\n"
        "$caught = $null\n"
        "try {\n"
        f"  . '{escaped_verify_path}' "
        f"-LiveDatabaseUrl '{LIVE_DATABASE_URL}' "
        f"-LiveConfirmDatabaseName '{LIVE_DATABASE_NAME}'\n"
        "}\n"
        "catch { $caught = $_.Exception.Message }\n"
        "if ($caught -notmatch '41') { throw \"Unexpected child failure: $caught\" }\n"
        "if ($env:NO_COLOR -ne 'preserve-no-color' -or "
        "$env:PYRIGHT_PYTHON_FORCE_VERSION -ne 'preserve-version-setting' -or "
        "$env:PYRIGHT_PYTHON_IGNORE_WARNINGS -ne 'preserve-ignore-setting') {\n"
        "  throw 'Tool environment was not restored'\n"
        "}\n"
        "Write-Output 'TOOL_ENV_RESTORED'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        cwd=PROJECT_ROOT,
        env=_verification_environment(fake_bin, database_url=API_DATABASE_URL),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "TOOL_ENV_CLEAN" in output
    assert "PYRIGHT_VERSION_NOTICE" not in output
    assert "PYRIGHT_UNPINNED" not in output
    assert "NO_COLOR_DIRTY" not in output
    assert "TOOL_ENV_RESTORED" in output


def test_verify_refuses_busy_live_ports_before_resetting_the_live_database(
    tmp_path: Path,
) -> None:
    """Production break: verification kills or reuses an unknown live-test server."""
    _write_fake_command(tmp_path, "python", exit_code=0, output="FAKE_PYTHON_OK")
    _write_fake_command(tmp_path, "npm", exit_code=0, output="FAKE_NPM_OK")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 8011))
        listener.listen()
        result = _run_verify(
            LIVE_DATABASE_URL,
            LIVE_DATABASE_NAME,
            environment=_verification_environment(tmp_path, database_url=API_DATABASE_URL),
        )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "port 8011 is already in use" in output.lower()
    assert "status\": \"seeded" not in output.lower()


def test_live_wrapper_audits_integrity_after_each_browser_journey(tmp_path: Path) -> None:
    """Production break: browser mutations can leave invalid history while the gate passes."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    _write_fake_command(
        fake_bin,
        "python",
        exit_code=0,
        output="FAKE_PYTHON_OK",
        log_arguments=True,
    )
    _write_fake_command(
        fake_bin,
        "npm",
        exit_code=0,
        output="FAKE_NPM_OK",
        log_arguments=True,
    )
    environment = _verification_environment(fake_bin, database_url=API_DATABASE_URL)
    environment["VERIFY_FAKE_LOG"] = str(log_path)
    result = _run_live_wrapper(environment=environment)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output

    commands = log_path.read_text(encoding="utf-8").splitlines()
    live_runs = [index for index, command in enumerate(commands) if "test:e2e:live" in command]
    audits = [
        index
        for index, command in enumerate(commands)
        if "scripts/check_integrity.py --database-url" in command
    ]
    assert len(live_runs) == 2
    assert len(audits) == 4
    assert live_runs[0] < audits[1] < live_runs[1] < audits[3]

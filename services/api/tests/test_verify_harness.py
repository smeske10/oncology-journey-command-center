from __future__ import annotations

import json
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
    "postgresql+psycopg://ojcc_api:api-local-synthetic-only@127.0.0.1:5432/"
    f"{API_DATABASE_NAME}"
)
LIVE_DATABASE_URL = (
    "postgresql+psycopg://ojcc_api:api-local-synthetic-only@127.0.0.1:5432/"
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
        argument_log = ""
        if log_arguments:
            argument_log = (
                "setlocal EnableDelayedExpansion\n"
                f"set \"SAFE_LOG={name}\"\n"
                ":log_arguments\n"
                "if \"%~1\"==\"\" goto arguments_logged\n"
                "set \"ARGUMENT=%~1\"\n"
                "echo(!ARGUMENT!| findstr /c:\"://\" >nul || "
                "set \"SAFE_LOG=!SAFE_LOG! !ARGUMENT!\"\n"
                "shift\n"
                "goto log_arguments\n"
                ":arguments_logged\n"
                "echo !SAFE_LOG!>>\"%VERIFY_FAKE_LOG%\"\n"
                "endlocal\n"
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
    argument_log = ""
    if log_arguments:
        argument_log = (
            f"printf '%s' '{name}' >> \"$VERIFY_FAKE_LOG\"\n"
            "for argument in \"$@\"; do\n"
            "  case \"$argument\" in *://*) ;; *) "
            "printf ' %s' \"$argument\" >> \"$VERIFY_FAKE_LOG\" ;; esac\n"
            "done\n"
            "printf '\\n' >> \"$VERIFY_FAKE_LOG\"\n"
        )
    command = directory / name
    command.write_text(
        f"#!/bin/sh\n{pg_check}{argument_log}echo {output}\nexit {exit_code}\n",
        encoding="utf-8",
    )
    command.chmod(0o755)


def _database_triple(target_url: str) -> tuple[str, str, str]:
    from sqlalchemy.engine import make_url

    target = make_url(target_url)
    return (
        target.set(username="ojcc", password="local-synthetic-only").render_as_string(
            hide_password=False
        ),
        target.set(
            username="ojcc_migrator", password="migrator-local-synthetic-only"
        ).render_as_string(hide_password=False),
        target.set(username="ojcc_api", password="api-local-synthetic-only").render_as_string(
            hide_password=False
        ),
    )


def _verification_environment(
    fake_bin: Path,
    *,
    database_url: str | None,
    include_bootstrap: bool = True,
    include_migration: bool = True,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PG")
        and key.upper()
        not in {"BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"}
    }
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    if database_url is None:
        return environment
    bootstrap_url, migration_url, application_url = _database_triple(database_url)
    if include_bootstrap:
        environment["BOOTSTRAP_DATABASE_URL"] = bootstrap_url
    if include_migration:
        environment["MIGRATION_DATABASE_URL"] = migration_url
    environment["DATABASE_URL"] = application_url
    return environment


def _run_verify(
    live_database_url: str,
    live_confirmation: str,
    *,
    environment: dict[str, str],
    live_bootstrap_database_url: str | None = None,
    live_migration_database_url: str | None = None,
) -> subprocess.CompletedProcess[str]:
    live_bootstrap_url, live_migration_url, live_application_url = _database_triple(
        live_database_url
    )
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/verify.ps1",
            "-LiveBootstrapDatabaseUrl",
            live_bootstrap_database_url or live_bootstrap_url,
            "-LiveMigrationDatabaseUrl",
            live_migration_database_url or live_migration_url,
            "-LiveDatabaseUrl",
            live_application_url,
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
    bootstrap_url, migration_url, application_url = _database_triple(LIVE_DATABASE_URL)
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/verify_live_journey.ps1",
            "-BootstrapDatabaseUrl",
            bootstrap_url,
            "-MigrationDatabaseUrl",
            migration_url,
            "-DatabaseUrl",
            application_url,
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


def _live_web_server_environment_keys() -> list[list[str]]:
    config_path = PROJECT_ROOT / "apps" / "web" / "playwright.live.config.ts"
    script = """
const fs = require("fs");
const path = require("path");
const Module = require("module");
const ts = require("typescript");
const filename = process.argv[1];
const source = fs.readFileSync(filename, "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
}).outputText;
const compiled = new Module(filename, module);
compiled.filename = filename;
compiled.paths = Module._nodeModulePaths(path.dirname(filename));
compiled._compile(output, filename);
const config = compiled.exports.default;
process.stdout.write(JSON.stringify(config.webServer.map((server) =>
  Object.entries(server.env || {})
    .filter(([, value]) => value !== undefined)
    .map(([name]) => name)
    .sort()
)));
"""
    environment = os.environ | {
        "DATABASE_URL": API_DATABASE_URL,
        "DEMO_SESSION_SECRET": "synthetic-live-session-secret-with-32-characters",
        "DEMO_ORGANIZATION_ID": "aeb456d4-3728-5f64-ac05-afed26cd0edc",
        "DEMO_ACTORS_JSON": "{}",
    }
    result = subprocess.run(
        ["node", "-e", script, str(config_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    return parsed


def _probe_live_child_environments(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Launch harmless children through Playwright's real web-server plugin."""
    config_path = PROJECT_ROOT / "apps" / "web" / "playwright.live.config.ts"
    plugin_path = (
        PROJECT_ROOT
        / "node_modules"
        / "playwright"
        / "lib"
        / "plugins"
        / "webServerPlugin.js"
    )
    probe_path = tmp_path / "environment-probe.cjs"
    launcher_path = tmp_path / "playwright-web-server-probe.cjs"
    probe_path.write_text(
        """
const fs = require("fs");
const role = process.argv[2];
const outputPath = process.argv[3];
const exact = (name, expected) => process.env[name] === expected;
const absent = (name) => process.env[name] === undefined;
const commonIsolation = {
  bootstrapAbsent: absent("BOOTSTRAP_DATABASE_URL"),
  migrationAbsent: absent("MIGRATION_DATABASE_URL"),
  baseUrlAbsent: absent("PLAYWRIGHT_BASE_URL"),
  deviceAbsent: absent("OJCC_LIVE_DEVICE"),
  migrationUsernameAbsent: absent("OJCC_MIGRATION_USERNAME"),
};
const result = role === "api" ? {
  ...commonIsolation,
  databaseExact: exact("DATABASE_URL", "synthetic-application-marker"),
  appEnvironmentExact: exact("APP_ENV", "local"),
  sessionSecretExact: exact("DEMO_SESSION_SECRET", "synthetic-secret-marker"),
  organizationExact: exact("DEMO_ORGANIZATION_ID", "synthetic-organization-marker"),
  actorsExact: exact("DEMO_ACTORS_JSON", "synthetic-roster-marker"),
  apiOriginAbsent: absent("OJCC_API_ORIGIN"),
} : {
  ...commonIsolation,
  databaseAbsent: absent("DATABASE_URL"),
  appEnvironmentAbsent: absent("APP_ENV"),
  sessionSecretAbsent: absent("DEMO_SESSION_SECRET"),
  organizationAbsent: absent("DEMO_ORGANIZATION_ID"),
  actorsAbsent: absent("DEMO_ACTORS_JSON"),
  apiOriginExact: exact("OJCC_API_ORIGIN", "http://127.0.0.1:8011"),
};
fs.writeFileSync(outputPath, JSON.stringify({ pid: process.pid, environment: result }));
setTimeout(() => process.exit(0), 10000);
""",
        encoding="utf-8",
    )
    launcher_path.write_text(
        """
const fs = require("fs");
const path = require("path");
const Module = require("module");
const configPath = process.argv[2];
const pluginPath = process.argv[3];
const probePath = process.argv[4];
const outputDirectory = process.argv[5];
const projectRequire = Module.createRequire(configPath);
const ts = projectRequire("typescript");
const source = fs.readFileSync(configPath, "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
}).outputText;
const compiled = new Module(configPath, module);
compiled.filename = configPath;
compiled.paths = Module._nodeModulePaths(path.dirname(configPath));
compiled._compile(output, configPath);
const servers = compiled.exports.default.webServer;
const { WebServerPlugin } = require(pluginPath);
const quote = (value) => `"${value.replaceAll('"', '""')}"`;

async function waitForFile(filename) {
  const deadline = Date.now() + 5000;
  while (!fs.existsSync(filename)) {
    if (Date.now() >= deadline) throw new Error(`Probe did not write ${path.basename(filename)}`);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

async function requireStopped(pid) {
  const deadline = Date.now() + 5000;
  while (true) {
    try {
      process.kill(pid, 0);
    } catch {
      return;
    }
    if (Date.now() >= deadline) throw new Error("Owned probe child was not stopped");
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

async function launch(server, role) {
  const outputPath = path.join(outputDirectory, `${role}.json`);
  const command = [process.execPath, probePath, role, outputPath].map(quote).join(" ");
  const plugin = new WebServerPlugin({
    ...server,
    command,
    cwd: outputDirectory,
    port: undefined,
    url: undefined,
    stdout: "ignore",
    stderr: "ignore",
  }, false);
  let result;
  try {
    await plugin.setup({}, path.dirname(configPath), {});
    await waitForFile(outputPath);
    result = JSON.parse(fs.readFileSync(outputPath, "utf8"));
  } finally {
    await plugin.teardown();
  }
  await requireStopped(result.pid);
  return result.environment;
}

(async () => {
  const api = await launch(servers[0], "api");
  const next = await launch(servers[1], "next");
  process.stdout.write(JSON.stringify({ api, next }));
})().catch((error) => {
  process.stderr.write(String(error && error.message ? error.message : error));
  process.exitCode = 1;
});
""",
        encoding="utf-8",
    )
    markers = {
        "DATABASE_URL": "synthetic-application-marker",
        "BOOTSTRAP_DATABASE_URL": "synthetic-bootstrap-marker",
        "MIGRATION_DATABASE_URL": "synthetic-migration-marker",
        "DEMO_SESSION_SECRET": "synthetic-secret-marker",
        "DEMO_ORGANIZATION_ID": "synthetic-organization-marker",
        "DEMO_ACTORS_JSON": "synthetic-roster-marker",
        "OJCC_API_ORIGIN": "synthetic-parent-origin-marker",
        "PLAYWRIGHT_BASE_URL": "synthetic-base-url-marker",
        "OJCC_LIVE_DEVICE": "synthetic-device-marker",
        "OJCC_MIGRATION_USERNAME": "synthetic-migration-username-marker",
    }
    result = subprocess.run(
        [
            "node",
            str(launcher_path),
            str(config_path),
            str(plugin_path),
            str(probe_path),
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        env=os.environ | markers,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    combined_output = result.stdout + result.stderr
    for marker in markers.values():
        assert marker not in combined_output
    return result


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


@pytest.mark.parametrize("stage", ("api", "live"))
def test_verify_rejects_shared_database_usernames_before_children(
    tmp_path: Path, stage: str
) -> None:
    _write_fake_command(tmp_path, "python", exit_code=97)
    environment = _verification_environment(tmp_path, database_url=API_DATABASE_URL)
    live_migration_override = None
    if stage == "api":
        environment["MIGRATION_DATABASE_URL"] = environment["DATABASE_URL"]
    else:
        live_bootstrap_url, _, _ = _database_triple(LIVE_DATABASE_URL)
        live_migration_override = live_bootstrap_url
    result = _run_verify(
        LIVE_DATABASE_URL,
        LIVE_DATABASE_NAME,
        environment=environment,
        live_migration_database_url=live_migration_override,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "distinct" in output.lower()
    assert "usernames" in output.lower()
    assert CHILD_SENTINEL not in output


@pytest.mark.parametrize(
    ("include_bootstrap", "include_migration", "expected"),
    ((False, True, "bootstrap_database_url"), (True, False, "migration_database_url")),
)
def test_verify_requires_all_api_credentials_before_any_child_process(
    tmp_path: Path,
    include_bootstrap: bool,
    include_migration: bool,
    expected: str,
) -> None:
    _write_fake_command(tmp_path, "python", exit_code=97)
    result = _run_verify(
        LIVE_DATABASE_URL,
        LIVE_DATABASE_NAME,
        environment=_verification_environment(
            tmp_path,
            database_url=API_DATABASE_URL,
            include_bootstrap=include_bootstrap,
            include_migration=include_migration,
        ),
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
    live_bootstrap_url, live_migration_url, live_application_url = _database_triple(
        LIVE_DATABASE_URL
    )
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:PGHOST = 'unsafe.example.test'\n"
        "$env:pgport = '6543'\n"
        "$env:PgSyntheticProbe = 'preserve-me'\n"
        "$caught = $null\n"
        "try {\n"
        f"  . '{escaped_verify_path}' "
        f"-LiveBootstrapDatabaseUrl '{live_bootstrap_url}' "
        f"-LiveMigrationDatabaseUrl '{live_migration_url}' "
        f"-LiveDatabaseUrl '{live_application_url}' "
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
    live_bootstrap_url, live_migration_url, live_application_url = _database_triple(
        LIVE_DATABASE_URL
    )
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:NO_COLOR = 'preserve-no-color'\n"
        "$env:PYRIGHT_PYTHON_FORCE_VERSION = 'preserve-version-setting'\n"
        "$env:PYRIGHT_PYTHON_IGNORE_WARNINGS = 'preserve-ignore-setting'\n"
        "$caught = $null\n"
        "try {\n"
        f"  . '{escaped_verify_path}' "
        f"-LiveBootstrapDatabaseUrl '{live_bootstrap_url}' "
        f"-LiveMigrationDatabaseUrl '{live_migration_url}' "
        f"-LiveDatabaseUrl '{live_application_url}' "
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
        if "-m scripts.check_integrity --database-url" in command
    ]
    assert len(live_runs) == 2
    assert len(audits) == 6
    assert live_runs[0] < audits[2] < live_runs[1] < audits[5]


@pytest.mark.parametrize("npm_exit_code", (0, 41), ids=("success", "forced-failure"))
def test_live_wrapper_restores_prior_demo_actor_configuration(
    tmp_path: Path,
    npm_exit_code: int,
) -> None:
    """Production break: live verification leaks its synthetic actor roster."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "python", exit_code=0, output="FAKE_PYTHON_OK")
    _write_fake_command(fake_bin, "npm", exit_code=npm_exit_code, output="FAKE_NPM")
    wrapper = tmp_path / "live-roster-environment-probe.ps1"
    escaped_live_path = str(
        PROJECT_ROOT / "scripts" / "verify_live_journey.ps1"
    ).replace("'", "''")
    live_bootstrap_url, live_migration_url, live_application_url = _database_triple(
        LIVE_DATABASE_URL
    )
    expected_failure = (
        "if ($caught -notmatch '41') { throw \"Unexpected failure: $caught\" }\n"
        if npm_exit_code
        else "if ($null -ne $caught) { throw \"Unexpected failure: $caught\" }\n"
    )
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:DEMO_ACTORS_JSON = 'prior-demo-actors-json'\n"
        "$caught = $null\n"
        "try {\n"
        f"  . '{escaped_live_path}' "
        f"-BootstrapDatabaseUrl '{live_bootstrap_url}' "
        f"-MigrationDatabaseUrl '{live_migration_url}' "
        f"-DatabaseUrl '{live_application_url}' "
        f"-ConfirmDatabaseName '{LIVE_DATABASE_NAME}'\n"
        "}\n"
        "catch { $caught = $_.Exception.Message }\n"
        + expected_failure
        + "if ($env:DEMO_ACTORS_JSON -ne 'prior-demo-actors-json') {\n"
        "  throw 'Demo actor configuration was not restored'\n"
        "}\n"
        "Write-Output 'DEMO_ACTORS_JSON_RESTORED'\n",
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

    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEMO_ACTORS_JSON_RESTORED" in result.stdout


def test_live_child_processes_use_allowlisted_runtime_environments() -> None:
    config = (PROJECT_ROOT / "apps" / "web" / "playwright.live.config.ts").read_text()
    journey = (
        PROJECT_ROOT
        / "apps"
        / "web"
        / "e2e-live"
        / "closed-loop-transportation.spec.ts"
    ).read_text()

    assert "...process.env" not in config
    assert "MIGRATION_DATABASE_URL" not in config
    assert "BOOTSTRAP_DATABASE_URL" not in config
    api_environment, next_environment = map(set, _live_web_server_environment_keys())
    assert {
        "DATABASE_URL",
        "APP_ENV",
        "DEMO_SESSION_SECRET",
        "DEMO_ORGANIZATION_ID",
        "DEMO_ACTORS_JSON",
    } <= api_environment
    assert {
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "BOOTSTRAP_DATABASE_URL",
        "DEMO_SESSION_SECRET",
        "DEMO_ACTORS_JSON",
    }.isdisjoint(next_environment)
    assert "current_user" in journey
    assert "session_user" in journey
    assert "OJCC_MIGRATION_USERNAME" in journey


def test_live_child_processes_receive_isolated_effective_environments(
    tmp_path: Path,
) -> None:
    """Production break: Playwright merges the parent environment into both children."""
    result = _probe_live_child_environments(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    environments = json.loads(result.stdout)

    assert set(environments) == {"api", "next"}
    assert all(
        type(value) is bool
        for environment in environments.values()
        for value in environment.values()
    )
    assert all(environments["api"].values()), environments["api"]
    assert all(environments["next"].values()), environments["next"]


def test_ci_provisions_uuid_databases_and_distinct_roles_without_cache_regression() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    readme = (PROJECT_ROOT / "README.md").read_text()

    assert 'tuple(f"ojcc_demo_{uuid4().hex}" for _ in range(2))' in workflow
    assert "sql.Identifier(name)" in workflow
    assert "ojcc_migrator" in workflow
    assert "ojcc_api" in workflow
    assert "python -m scripts.provision_database_roles" in workflow
    assert "expected_roles =" not in workflow
    assert "python -m scripts.provision_database_roles" in readme
    for field in (
        "BOOTSTRAP_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "DATABASE_URL",
        "LIVE_BOOTSTRAP_DATABASE_URL",
        "LIVE_MIGRATION_DATABASE_URL",
        "LIVE_DATABASE_URL",
    ):
        assert field in workflow
    assert "cache: npm" in workflow
    assert "cache: pip" in workflow
    assert "Restore Next.js build cache" in workflow

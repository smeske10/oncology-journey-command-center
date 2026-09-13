param(
    [Parameter(Mandatory = $true)]
    [string]$BootstrapDatabaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$MigrationDatabaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$DatabaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$ConfirmDatabaseName
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $projectRoot "services/api"
$databaseEnvironmentNames = @(
    "BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"
)
$priorDatabaseEnvironment = @{}
$priorLibpqEnvironment = @()

foreach ($name in $databaseEnvironmentNames) {
    $priorDatabaseEnvironment[$name] = [System.Environment]::GetEnvironmentVariable(
        $name, "Process"
    )
}

Get-ChildItem Env: | Where-Object {
    $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object {
    $priorLibpqEnvironment += [PSCustomObject]@{
        Name = $_.Name
        Value = $_.Value
    }
}

function Invoke-CheckedPython {
    param([scriptblock]$Command)

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Demo reset child process failed with exit code $LASTEXITCODE."
    }
}

$locationPushed = $false
try {
    foreach ($entry in $priorLibpqEnvironment) {
        Remove-Item -LiteralPath ("Env:{0}" -f $entry.Name)
    }
    foreach ($name in $databaseEnvironmentNames) {
        Remove-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
    }
    Push-Location $apiRoot
    $locationPushed = $true
    $resetCode = @'
import sys
from sqlalchemy import create_engine, text
from app.db.targets import validate_database_target_pair
from scripts.seed_demo import validate_disposable_database_url

try:
    bootstrap_url = validate_disposable_database_url(sys.argv[1])
    migration_url = validate_disposable_database_url(sys.argv[2])
    application_url = validate_disposable_database_url(sys.argv[3])
    application_text = application_url.render_as_string(hide_password=False)
    _, bootstrap = validate_database_target_pair(
        application_url=application_text,
        migration_url=bootstrap_url.render_as_string(hide_password=False),
    )
    _, migration = validate_database_target_pair(
        application_url=application_text,
        migration_url=migration_url.render_as_string(hide_password=False),
    )
    if bootstrap.username == migration.username:
        raise ValueError(
            "Bootstrap, migration, and application require distinct usernames"
        )
    database_name = application_url.database
    if database_name != sys.argv[4]:
        raise ValueError(
            f"Database confirmation does not match: expected {database_name!r}, "
            f"received {sys.argv[4]!r}"
        )
except ValueError as error:
    print(error, file=sys.stderr)
    raise SystemExit(2)

engine = create_engine(migration_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
try:
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
finally:
    engine.dispose()
'@
    $resetCode | python - `
        $BootstrapDatabaseUrl $MigrationDatabaseUrl $DatabaseUrl $ConfirmDatabaseName
    if ($LASTEXITCODE -ne 0) {
        throw "Demo reset validation or schema reset failed with exit code $LASTEXITCODE."
    }

    Invoke-CheckedPython {
        python -m scripts.replay_schema `
            --bootstrap-database-url $BootstrapDatabaseUrl `
            --migration-database-url $MigrationDatabaseUrl `
            --database-url $DatabaseUrl `
            --revision head
    }
    Invoke-CheckedPython {
        python -m scripts.seed_demo `
            --migration-database-url $MigrationDatabaseUrl `
            --database-url $DatabaseUrl
    }
    Invoke-CheckedPython {
        python -m scripts.seed_demo `
            --migration-database-url $MigrationDatabaseUrl `
            --database-url $DatabaseUrl
    }
    [System.Environment]::SetEnvironmentVariable(
        "DATABASE_URL", $MigrationDatabaseUrl, "Process"
    )
    Invoke-CheckedPython {
        python -m scripts.check_integrity --database-url $MigrationDatabaseUrl
    }
    [System.Environment]::SetEnvironmentVariable("DATABASE_URL", $DatabaseUrl, "Process")
    Invoke-CheckedPython { python -m scripts.check_integrity --database-url $DatabaseUrl }
}
finally {
    Get-ChildItem Env: | Where-Object {
        $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object {
        Remove-Item -LiteralPath ("Env:{0}" -f $_.Name)
    }
    foreach ($entry in $priorLibpqEnvironment) {
        [System.Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, "Process")
    }
    foreach ($name in $databaseEnvironmentNames) {
        [System.Environment]::SetEnvironmentVariable(
            $name, $priorDatabaseEnvironment[$name], "Process"
        )
    }
    if ($locationPushed) {
        Pop-Location
    }
}

param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$ConfirmDatabaseName
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $projectRoot "services/api"
$priorDatabaseUrl = [System.Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
$priorLibpqEnvironment = @{}

Get-ChildItem Env: | Where-Object {
    $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object {
    $priorLibpqEnvironment[$_.Name] = $_.Value
}

function Invoke-CheckedPython {
    param([scriptblock]$Command)

    & $Command
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$locationPushed = $false
try {
    foreach ($name in $priorLibpqEnvironment.Keys) {
        Remove-Item -LiteralPath ("Env:{0}" -f $name)
    }
    Push-Location $apiRoot
    $locationPushed = $true
    $resetCode = @'
import sys
from sqlalchemy import create_engine, text
from scripts.seed_demo import validate_disposable_database_url

try:
    validated_url = validate_disposable_database_url(sys.argv[1])
    database_name = validated_url.database
    if database_name != sys.argv[2]:
        raise ValueError(
            f"Database confirmation does not match: expected {database_name!r}, "
            f"received {sys.argv[2]!r}"
        )
except ValueError as error:
    print(error, file=sys.stderr)
    raise SystemExit(2)

engine = create_engine(validated_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
try:
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
finally:
    engine.dispose()
'@
    $resetCode | python - $DatabaseUrl $ConfirmDatabaseName
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    [System.Environment]::SetEnvironmentVariable("DATABASE_URL", $DatabaseUrl, "Process")
    Invoke-CheckedPython { python -m alembic -c alembic.ini upgrade head }
    Invoke-CheckedPython { python scripts/seed_demo.py --database-url $DatabaseUrl }
    Invoke-CheckedPython { python scripts/seed_demo.py --database-url $DatabaseUrl }
    Invoke-CheckedPython { python scripts/check_integrity.py --database-url $DatabaseUrl }
}
finally {
    Get-ChildItem Env: | Where-Object {
        $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object {
        Remove-Item -LiteralPath ("Env:{0}" -f $_.Name)
    }
    foreach ($entry in $priorLibpqEnvironment.GetEnumerator()) {
        [System.Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
    if ($null -eq $priorDatabaseUrl) {
        [System.Environment]::SetEnvironmentVariable("DATABASE_URL", $null, "Process")
    }
    else {
        [System.Environment]::SetEnvironmentVariable("DATABASE_URL", $priorDatabaseUrl, "Process")
    }
    if ($locationPushed) {
        Pop-Location
    }
}

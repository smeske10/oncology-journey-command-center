param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$ConfirmDatabaseName
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot "apps/web"
$apiRoot = Join-Path $projectRoot "services/api"
$taskEnvironmentNames = @(
    "DATABASE_URL", "APP_ENV", "DEMO_SESSION_SECRET", "DEMO_ORGANIZATION_ID",
    "OJCC_API_ORIGIN", "PLAYWRIGHT_BASE_URL", "OJCC_LIVE_DEVICE"
)
$priorTaskEnvironment = @{}
$priorLibpqEnvironment = @()

foreach ($name in $taskEnvironmentNames) {
    $priorTaskEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}
Get-ChildItem Env: | Where-Object {
    $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object {
    $priorLibpqEnvironment += [PSCustomObject]@{ Name = $_.Name; Value = $_.Value }
}

function Assert-SafeTarget {
    param([string]$UrlText, [string]$Confirmation)
    $parsed = [System.Uri]$UrlText
    $databaseName = $parsed.AbsolutePath.TrimStart("/")
    $allowedHost = $parsed.Host -in @("127.0.0.1", "::1", "localhost")
    $allowedName = $databaseName -match '^ojcc_(demo|task7)_[0-9a-f]{8,32}$'
    if ($parsed.Scheme -notin @("postgresql", "postgresql+psycopg") -or
        -not $allowedHost -or $parsed.Port -ne 5432 -or
        $parsed.Query -or $parsed.Fragment -or -not $allowedName) {
        throw "Live journey requires an explicit loopback disposable PostgreSQL URL."
    }
    if ($databaseName -ne $Confirmation) {
        throw "Database confirmation does not match the validated disposable database name."
    }
    return $databaseName
}

function Assert-PortAvailable {
    param([int]$Port)
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Parse("127.0.0.1"), $Port
    )
    try { $listener.Start() }
    catch { throw "Required live-test port $Port is already in use." }
    finally { $listener.Stop() }
}

function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Live journey child process failed with exit code $LASTEXITCODE." }
}

function Assert-DatabaseUnused {
    param([string]$UrlText, [string]$Name)
    $usageCheck = @'
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
url = make_url(sys.argv[1])
engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
try:
    with engine.connect() as connection:
        count = connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=:name"), {"name": sys.argv[2]})
        if count:
            raise SystemExit(f"Owned live database is in use by {count} process(es); refusing reset.")
finally:
    engine.dispose()
'@
    Push-Location $apiRoot
    try {
        $usageCheck | python - $UrlText $Name
        if ($LASTEXITCODE -ne 0) { throw "Owned live database usage check failed." }
    }
    finally {
        Pop-Location
    }
}

$databaseName = Assert-SafeTarget $DatabaseUrl $ConfirmDatabaseName
try {
    foreach ($entry in $priorLibpqEnvironment) { Remove-Item -LiteralPath ("Env:{0}" -f $entry.Name) }

    foreach ($device in @("desktop", "mobile")) {
        Assert-PortAvailable 8011
        Assert-PortAvailable 3011
        Assert-DatabaseUnused $DatabaseUrl $databaseName
        Invoke-Checked { & (Join-Path $PSScriptRoot "reset_demo.ps1") -DatabaseUrl $DatabaseUrl -ConfirmDatabaseName $databaseName }
        [System.Environment]::SetEnvironmentVariable("DATABASE_URL", $DatabaseUrl, "Process")
        [System.Environment]::SetEnvironmentVariable("APP_ENV", "local", "Process")
        [System.Environment]::SetEnvironmentVariable("DEMO_SESSION_SECRET", "synthetic-live-session-secret-with-32-characters", "Process")
        [System.Environment]::SetEnvironmentVariable("DEMO_ORGANIZATION_ID", "aeb456d4-3728-5f64-ac05-afed26cd0edc", "Process")
        [System.Environment]::SetEnvironmentVariable("OJCC_API_ORIGIN", "http://127.0.0.1:8011", "Process")
        [System.Environment]::SetEnvironmentVariable("PLAYWRIGHT_BASE_URL", "http://127.0.0.1:3011", "Process")
        [System.Environment]::SetEnvironmentVariable("OJCC_LIVE_DEVICE", $device, "Process")
        Push-Location $webRoot
        try {
            Invoke-Checked { npm run test:e2e:live }
        }
        finally {
            Pop-Location
        }
        Push-Location $apiRoot
        try {
            Invoke-Checked { python scripts/check_integrity.py --database-url $DatabaseUrl }
        }
        finally {
            Pop-Location
        }
        Assert-PortAvailable 8011
        Assert-PortAvailable 3011
    }
}
finally {
    Get-ChildItem Env: | Where-Object {
        $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object { Remove-Item -LiteralPath ("Env:{0}" -f $_.Name) }
    foreach ($entry in $priorLibpqEnvironment) {
        [System.Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, "Process")
    }
    foreach ($name in $taskEnvironmentNames) {
        [System.Environment]::SetEnvironmentVariable($name, $priorTaskEnvironment[$name], "Process")
    }
}

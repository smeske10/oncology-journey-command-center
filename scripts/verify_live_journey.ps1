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
$webRoot = Join-Path $projectRoot "apps/web"
$apiRoot = Join-Path $projectRoot "services/api"
$taskEnvironmentNames = @(
    "BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL", "APP_ENV",
    "DEMO_SESSION_SECRET", "DEMO_ORGANIZATION_ID", "DEMO_ACTORS_JSON",
    "OJCC_API_ORIGIN", "PLAYWRIGHT_BASE_URL", "OJCC_LIVE_DEVICE",
    "OJCC_MIGRATION_USERNAME"
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
    param([string]$UrlText, [string]$Purpose, [string]$Confirmation = "")
    if ([string]::IsNullOrWhiteSpace($UrlText)) {
        throw "$Purpose requires an explicit database URL."
    }
    $parsed = [System.Uri]$UrlText
    $databaseName = $parsed.AbsolutePath.TrimStart("/")
    $allowedHost = $parsed.Host -in @("127.0.0.1", "::1", "localhost")
    $allowedName = $databaseName -match '^ojcc_(demo|task7)_[0-9a-f]{8,32}$'
    if ($parsed.Scheme -notin @("postgresql", "postgresql+psycopg") -or
        -not $allowedHost -or $parsed.Port -ne 5432 -or
        $parsed.Query -or $parsed.Fragment -or -not $allowedName) {
        throw "$Purpose requires an explicit loopback disposable PostgreSQL URL."
    }
    if ($Confirmation -and $databaseName -ne $Confirmation) {
        throw "$Purpose confirmation does not match the validated disposable database name."
    }
    return [PSCustomObject]@{
        Name = $databaseName
        Host = $parsed.Host.ToLowerInvariant()
        Port = $parsed.Port
        Username = [System.Uri]::UnescapeDataString($parsed.UserInfo.Split(":")[0])
    }
}

function Assert-TargetTriple {
    param(
        [string]$BootstrapUrl,
        [string]$MigrationUrl,
        [string]$ApplicationUrl,
        [string]$Confirmation
    )
    $bootstrap = Assert-SafeTarget $BootstrapUrl "BOOTSTRAP_DATABASE_URL"
    $migration = Assert-SafeTarget $MigrationUrl "MIGRATION_DATABASE_URL"
    $application = Assert-SafeTarget $ApplicationUrl "DATABASE_URL" $Confirmation
    foreach ($candidate in @($bootstrap, $migration)) {
        if ($candidate.Name -cne $application.Name -or
            $candidate.Host -cne $application.Host -or
            $candidate.Port -ne $application.Port) {
            throw "Live database targets must use the same host, port, and database name."
        }
    }
    $distinctCount = @(
        $bootstrap.Username, $migration.Username, $application.Username
    ) | Sort-Object -Unique | Measure-Object | Select-Object -ExpandProperty Count
    if ($distinctCount -ne 3) {
        throw "Bootstrap, migration, and application require distinct usernames."
    }
    return $application.Name
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

$databaseName = Assert-TargetTriple `
    $BootstrapDatabaseUrl $MigrationDatabaseUrl $DatabaseUrl $ConfirmDatabaseName
$migrationUsername = [System.Uri]::UnescapeDataString(
    ([System.Uri]$MigrationDatabaseUrl).UserInfo.Split(":")[0]
)
try {
    foreach ($entry in $priorLibpqEnvironment) { Remove-Item -LiteralPath ("Env:{0}" -f $entry.Name) }
    foreach ($name in @("BOOTSTRAP_DATABASE_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL")) {
        Remove-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
    }

    Push-Location $apiRoot
    try {
        $demoActorsJson = & python -m scripts.seed_demo --print-demo-actors
        if ($LASTEXITCODE -ne 0) {
            throw "Synthetic demo actor configuration failed with exit code $LASTEXITCODE."
        }
        if ([string]::IsNullOrWhiteSpace($demoActorsJson)) {
            throw "Synthetic demo actor configuration was empty."
        }
    }
    finally {
        Pop-Location
    }

    foreach ($device in @("desktop", "mobile")) {
        Assert-PortAvailable 8011
        Assert-PortAvailable 3011
        Assert-DatabaseUnused $MigrationDatabaseUrl $databaseName
        Invoke-Checked {
            & (Join-Path $PSScriptRoot "reset_demo.ps1") `
                -BootstrapDatabaseUrl $BootstrapDatabaseUrl `
                -MigrationDatabaseUrl $MigrationDatabaseUrl `
                -DatabaseUrl $DatabaseUrl `
                -ConfirmDatabaseName $databaseName
        }
        [System.Environment]::SetEnvironmentVariable("DATABASE_URL", $DatabaseUrl, "Process")
        [System.Environment]::SetEnvironmentVariable("APP_ENV", "local", "Process")
        [System.Environment]::SetEnvironmentVariable("DEMO_SESSION_SECRET", "synthetic-live-session-secret-with-32-characters", "Process")
        [System.Environment]::SetEnvironmentVariable("DEMO_ORGANIZATION_ID", "aeb456d4-3728-5f64-ac05-afed26cd0edc", "Process")
        [System.Environment]::SetEnvironmentVariable("DEMO_ACTORS_JSON", $demoActorsJson, "Process")
        [System.Environment]::SetEnvironmentVariable("OJCC_API_ORIGIN", "http://127.0.0.1:8011", "Process")
        [System.Environment]::SetEnvironmentVariable("PLAYWRIGHT_BASE_URL", "http://127.0.0.1:3011", "Process")
        [System.Environment]::SetEnvironmentVariable("OJCC_LIVE_DEVICE", $device, "Process")
        [System.Environment]::SetEnvironmentVariable(
            "OJCC_MIGRATION_USERNAME", $migrationUsername, "Process"
        )
        Push-Location $webRoot
        try {
            Invoke-Checked { npm run test:e2e:live }
        }
        finally {
            Pop-Location
        }
        Push-Location $apiRoot
        try {
            Invoke-Checked {
                python -m scripts.check_integrity --database-url $DatabaseUrl
            }
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
        if ($null -eq $priorTaskEnvironment[$name]) {
            Remove-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
        }
        else {
            [System.Environment]::SetEnvironmentVariable(
                $name, $priorTaskEnvironment[$name], "Process"
            )
        }
    }
}

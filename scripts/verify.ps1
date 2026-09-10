param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LiveDatabaseUrl,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LiveConfirmDatabaseName
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$npmExecutable = if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
    "npm.cmd"
}
else {
    "npm"
}
$priorLibpqEnvironment = @()
$toolEnvironmentNames = @(
    "NO_COLOR", "PYRIGHT_PYTHON_FORCE_VERSION", "PYRIGHT_PYTHON_IGNORE_WARNINGS"
)
$priorToolEnvironment = @{}

Get-ChildItem Env: | Where-Object {
    $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object {
    $priorLibpqEnvironment += [PSCustomObject]@{ Name = $_.Name; Value = $_.Value }
}
foreach ($name in $toolEnvironmentNames) {
    $priorToolEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}

function Assert-SafeDatabaseTarget {
    param(
        [string]$UrlText,
        [string]$Purpose,
        [string]$Confirmation = ""
    )

    if ([string]::IsNullOrWhiteSpace($UrlText)) {
        throw "$Purpose requires an explicit DATABASE_URL."
    }
    try {
        $parsed = [System.Uri]$UrlText
    }
    catch {
        throw "$Purpose requires a valid PostgreSQL URL."
    }
    if ($parsed.Query -or $parsed.Fragment) {
        throw "$Purpose URL must not include query parameters or fragments."
    }
    if ($parsed.Scheme -notin @("postgresql", "postgresql+psycopg")) {
        throw "$Purpose requires a PostgreSQL driver URL."
    }
    if ($parsed.Host -notin @("127.0.0.1", "::1", "localhost")) {
        throw "$Purpose requires a loopback PostgreSQL URL."
    }
    if ($parsed.Port -ne 5432) {
        throw "$Purpose requires the explicit local PostgreSQL port 5432."
    }
    $databaseName = [System.Uri]::UnescapeDataString($parsed.AbsolutePath.TrimStart("/"))
    if ($databaseName -cnotmatch '^ojcc_(demo|task7)_[0-9a-f]{8,32}$') {
        throw "$Purpose requires an explicit disposable database name."
    }
    if ($Confirmation -and $databaseName -cne $Confirmation) {
        throw "$Purpose database confirmation does not match its validated name."
    }
    return $databaseName
}

function Invoke-VerificationCommand {
    param([scriptblock]$Command)

    & $Command
    $childExitCode = $LASTEXITCODE
    if ($childExitCode -ne 0) {
        throw "Verification child process failed with exit code $childExitCode."
    }
}

$apiDatabaseUrl = [System.Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
$apiDatabaseName = Assert-SafeDatabaseTarget $apiDatabaseUrl "API verification"
$liveDatabaseName = Assert-SafeDatabaseTarget (
    $LiveDatabaseUrl
) "Live verification" $LiveConfirmDatabaseName
if ($apiDatabaseName -ceq $liveDatabaseName) {
    throw "API and live verification require different disposable databases."
}

$locationPushed = $false
try {
    foreach ($entry in $priorLibpqEnvironment) {
        Remove-Item -LiteralPath ("Env:{0}" -f $entry.Name)
    }
    Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue
    Remove-Item Env:PYRIGHT_PYTHON_FORCE_VERSION -ErrorAction SilentlyContinue
    [System.Environment]::SetEnvironmentVariable(
        "PYRIGHT_PYTHON_IGNORE_WARNINGS", "1", "Process"
    )
    Push-Location $projectRoot
    $locationPushed = $true
    Push-Location "services/api"
    try {
        Invoke-VerificationCommand { python -m pip install --require-hashes -r requirements.lock }
        Invoke-VerificationCommand { python -m pip install --no-deps --no-build-isolation -e . }
        Invoke-VerificationCommand { python -m ruff check . }
        Invoke-VerificationCommand { python -m pyright }
        Invoke-VerificationCommand { python scripts/check_integrity.py }
        Invoke-VerificationCommand { python -m pytest -q }
    }
    finally {
        Pop-Location
    }

    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web run lint }
    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web test -- --run }
    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web run build }
    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web run test:e2e }
    Invoke-VerificationCommand {
        & (Join-Path $PSScriptRoot "verify_live_journey.ps1") `
            -DatabaseUrl $LiveDatabaseUrl `
            -ConfirmDatabaseName $LiveConfirmDatabaseName
    }
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    Get-ChildItem Env: | Where-Object {
        $_.Name.StartsWith("PG", [System.StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object {
        Remove-Item -LiteralPath ("Env:{0}" -f $_.Name)
    }
    foreach ($entry in $priorLibpqEnvironment) {
        [System.Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, "Process")
    }
    foreach ($name in $toolEnvironmentNames) {
        [System.Environment]::SetEnvironmentVariable(
            $name, $priorToolEnvironment[$name], "Process"
        )
    }
}

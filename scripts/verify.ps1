$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$npmExecutable = if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
    "npm.cmd"
}
else {
    "npm"
}

function Invoke-VerificationCommand {
    param([scriptblock]$Command)

    & $Command
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Push-Location $projectRoot
try {
    Push-Location "services/api"
    try {
        Invoke-VerificationCommand { python -m pip install --require-hashes -r requirements.lock }
        Invoke-VerificationCommand { python -m pip install --no-deps --no-build-isolation -e . }
        Invoke-VerificationCommand { python -m ruff check . }
        Invoke-VerificationCommand { python -m pyright }
        Invoke-VerificationCommand { python -m pytest -q }
    }
    finally {
        Pop-Location
    }

    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web run lint }
    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web test -- --run }
    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web run build }
    Invoke-VerificationCommand { & $npmExecutable --workspace apps/web run test:e2e }
}
finally {
    Pop-Location
}

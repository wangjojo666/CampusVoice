[CmdletBinding()]
param(
    [string]$ProjectName = $(if ($env:CAMPUSVOICE_SMOKE_PROJECT) { $env:CAMPUSVOICE_SMOKE_PROJECT } else { "campusvoice-smoke" }),
    [string]$ComposeFile = $(if ($env:CAMPUSVOICE_COMPOSE_FILE) { $env:CAMPUSVOICE_COMPOSE_FILE } else { "docker-compose.yml" }),
    [string]$SmokeFile = $(if ($env:CAMPUSVOICE_SMOKE_COMPOSE_FILE) { $env:CAMPUSVOICE_SMOKE_COMPOSE_FILE } else { "docker-compose.smoke.yml" }),
    [string]$ExtraComposeFile = $(if ($env:CAMPUSVOICE_EXTRA_COMPOSE_FILE) { $env:CAMPUSVOICE_EXTRA_COMPOSE_FILE } else { "docker-compose.multi-worker.yml" }),
    [string]$ComposeProfile = $(if ($env:CAMPUSVOICE_SMOKE_PROFILE) { $env:CAMPUSVOICE_SMOKE_PROFILE } else { "multi-worker" })
)

$ErrorActionPreference = "Stop"
$composeArgs = @(
    "compose",
    "--project-name", $ProjectName,
    "--file", $ComposeFile,
    "--file", $SmokeFile
)
if ($ExtraComposeFile) {
    $composeArgs += @("--file", $ExtraComposeFile)
}
if ($ComposeProfile) {
    $composeArgs += @("--profile", $ComposeProfile)
}
$completed = $false

function Invoke-SmokeCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)

    & docker @composeArgs @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($CommandArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

try {
    Invoke-SmokeCompose config --quiet
    Invoke-SmokeCompose up --detach --build --wait --wait-timeout 300

    $previousWebUrl = $env:CAMPUSVOICE_SMOKE_WEB_URL
    $previousApiUrl = $env:CAMPUSVOICE_SMOKE_API_URL
    try {
        if (-not $env:CAMPUSVOICE_SMOKE_WEB_URL) {
            $env:CAMPUSVOICE_SMOKE_WEB_URL = "http://127.0.0.1:3000"
        }
        if (-not $env:CAMPUSVOICE_SMOKE_API_URL) {
            $env:CAMPUSVOICE_SMOKE_API_URL = "http://127.0.0.1:8000"
        }
        & pnpm --filter '@campusvoice/web' test:e2e:smoke
        if ($LASTEXITCODE -ne 0) {
            throw "Playwright Compose smoke failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        $env:CAMPUSVOICE_SMOKE_WEB_URL = $previousWebUrl
        $env:CAMPUSVOICE_SMOKE_API_URL = $previousApiUrl
    }
    $completed = $true
}
catch {
    & docker @composeArgs ps
    & docker @composeArgs logs --no-color
    throw
}
finally {
    & docker @composeArgs down --volumes --remove-orphans
    if (-not $completed) {
        Write-Error "Compose smoke did not complete. Diagnostic output is shown above." -ErrorAction Continue
    }
}

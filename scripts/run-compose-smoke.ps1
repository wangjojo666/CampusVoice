[CmdletBinding()]
param(
    [string]$ProjectName = $(if ($env:CAMPUSVOICE_SMOKE_PROJECT) { $env:CAMPUSVOICE_SMOKE_PROJECT } elseif ($env:GITHUB_RUN_ID) { "campusvoice-smoke-$($env:GITHUB_RUN_ID)-$($env:GITHUB_RUN_ATTEMPT)" } else { "campusvoice-smoke-$PID" }),
    [string]$ComposeFile = $(if ($env:CAMPUSVOICE_COMPOSE_FILE) { $env:CAMPUSVOICE_COMPOSE_FILE } else { "docker-compose.yml" }),
    [string]$SmokeFile = $(if ($env:CAMPUSVOICE_SMOKE_COMPOSE_FILE) { $env:CAMPUSVOICE_SMOKE_COMPOSE_FILE } else { "docker-compose.smoke.yml" }),
    [AllowEmptyString()][string]$ExtraComposeFile = $(if ($null -ne $env:CAMPUSVOICE_EXTRA_COMPOSE_FILE) { $env:CAMPUSVOICE_EXTRA_COMPOSE_FILE } else { "docker-compose.multi-worker.yml" }),
    [AllowEmptyString()][string]$ComposeProfile = $(if ($null -ne $env:CAMPUSVOICE_SMOKE_PROFILE) { $env:CAMPUSVOICE_SMOKE_PROFILE } else { "multi-worker" })
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PreviousLocation = Get-Location
$WebUrl = if ($env:CAMPUSVOICE_SMOKE_WEB_URL) { $env:CAMPUSVOICE_SMOKE_WEB_URL } else { "http://127.0.0.1:3000" }
$ApiUrl = if ($env:CAMPUSVOICE_SMOKE_API_URL) { $env:CAMPUSVOICE_SMOKE_API_URL } else { "http://127.0.0.1:8000" }

function Resolve-RepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $RepoRoot $Path))
}

$composeArgs = @(
    "compose",
    "--project-name", $ProjectName,
    "--file", (Resolve-RepoPath $ComposeFile),
    "--file", (Resolve-RepoPath $SmokeFile)
)
if ($ExtraComposeFile) {
    $composeArgs += @("--file", (Resolve-RepoPath $ExtraComposeFile))
}
if ($ComposeProfile) {
    $composeArgs += @("--profile", $ComposeProfile)
}

$preservedEnvironment = @{}
Get-ChildItem Env: | Where-Object {
    $_.Name -match '^(CAMPUSVOICE_|NEXT_PUBLIC_|COMPOSE_)'
} | ForEach-Object {
    $preservedEnvironment[$_.Name] = $_.Value
    Remove-Item -Path "Env:$($_.Name)"
}
$env:COMPOSE_DISABLE_ENV_FILE = "1"
$env:CAMPUSVOICE_BIND_HOST = "127.0.0.1"
$env:CAMPUSVOICE_DATABASE_URL = "sqlite+aiosqlite:///./campusvoice.db"
$env:CAMPUSVOICE_LOG_LEVEL = "INFO"
$env:CAMPUSVOICE_ASR_WORKER_COUNT = if ($ExtraComposeFile) { "2" } else { "1" }
$env:CAMPUSVOICE_ASR_REDIS_URL = "redis://redis:6379/0"
$env:CAMPUSVOICE_ASR_REDIS_KEY_PREFIX = "campusvoice:smoke:$($ProjectName):asr:quota"
$env:CAMPUSVOICE_SMOKE_WEB_URL = $WebUrl
$env:CAMPUSVOICE_SMOKE_API_URL = $ApiUrl

$completed = $false

function Invoke-SmokeCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)

    & docker @composeArgs @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($CommandArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

try {
    Set-Location $RepoRoot
    Invoke-SmokeCompose config --quiet
    Invoke-SmokeCompose up --detach --build --wait --wait-timeout 300

    $beforeContainerId = ((Invoke-SmokeCompose ps -q api) | Out-String).Trim()
    if (-not $beforeContainerId) {
        throw "Compose smoke could not resolve the initial API container id."
    }
    $beforeMount = (& docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}}|{{.Name}}{{end}}{{end}}' $beforeContainerId).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $beforeMount.StartsWith("volume|")) {
        throw "API /data mount is not a Docker volume: $beforeMount"
    }
    $beforeVolumeName = $beforeMount.Substring("volume|".Length)
    $beforeVolumeLabels = (& docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.volume"}}' $beforeVolumeName).Trim()
    if ($LASTEXITCODE -ne 0 -or $beforeVolumeLabels -ne "$ProjectName|campusvoice_smoke_data") {
        throw "API /data volume is not the Compose-managed smoke volume: $beforeVolumeLabels"
    }

    $sentinelActionId = (& node (Join-Path $RepoRoot "scripts/check_compose_persistence.mjs") create $ApiUrl).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $sentinelActionId) {
        throw "Compose persistence sentinel creation failed."
    }
    Invoke-SmokeCompose exec -T api sh -c 'mkdir -p /data/app && printf ''%s\n'' ''raise RuntimeError("untrusted /data/app imported")'' > /data/app/__init__.py && printf ''%s\n'' ''CAMPUSVOICE_API_PREFIX=/untrusted'' > /data/.env'

    Invoke-SmokeCompose up --detach --force-recreate --no-deps --wait --wait-timeout 300 api
    $afterContainerId = ((Invoke-SmokeCompose ps -q api) | Out-String).Trim()
    if (-not $afterContainerId -or $afterContainerId -eq $beforeContainerId) {
        throw "API container was not replaced during the persistence check."
    }
    $afterMount = (& docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}}|{{.Name}}{{end}}{{end}}' $afterContainerId).Trim()
    if ($LASTEXITCODE -ne 0 -or $afterMount -ne $beforeMount) {
        throw "API /data volume changed during container recreation."
    }

    & node (Join-Path $RepoRoot "scripts/check_compose_persistence.mjs") verify $ApiUrl $sentinelActionId
    if ($LASTEXITCODE -ne 0) {
        throw "Compose persistence sentinel verification failed."
    }
    & pnpm --filter '@campusvoice/web' test:e2e:smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright Compose smoke failed with exit code $LASTEXITCODE"
    }
    $completed = $true
}
catch {
    & docker @composeArgs ps
    & docker @composeArgs logs --no-color
    throw
}
finally {
    try {
        & docker @composeArgs down --volumes --remove-orphans
    }
    catch {
        Write-Warning "Compose smoke cleanup failed: $($_.Exception.GetType().Name)"
    }
    finally {
        Set-Location $PreviousLocation
        Get-ChildItem Env: | Where-Object {
            $_.Name -match '^(CAMPUSVOICE_|NEXT_PUBLIC_|COMPOSE_)'
        } | ForEach-Object {
            Remove-Item -Path "Env:$($_.Name)"
        }
        foreach ($entry in $preservedEnvironment.GetEnumerator()) {
            Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
        }
    }
    if (-not $completed) {
        Write-Error "Compose smoke did not complete. Diagnostic output is shown above." -ErrorAction Continue
    }
}

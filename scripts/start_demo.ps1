[CmdletBinding()]
param(
    [string]$PythonExecutable = $env:CAMPUSVOICE_PYTHON,
    [ValidateRange(10, 45)]
    [int]$TimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ApiRoot = Join-Path $RepoRoot "services/api"
$RuntimeRoot = Join-Path $ApiRoot "data/runtime"
$StateFile = Join-Path $RuntimeRoot "demo-processes.json"
$ApiStdout = Join-Path $RuntimeRoot "api.stdout.log"
$ApiStderr = Join-Path $RuntimeRoot "api.stderr.log"
$WebStdout = Join-Path $RuntimeRoot "web.stdout.log"
$WebStderr = Join-Path $RuntimeRoot "web.stderr.log"
$DemoDataRoot = [IO.Path]::GetFullPath((Join-Path $ApiRoot "data"))
$DemoDatabasePath = [IO.Path]::GetFullPath((Join-Path $DemoDataRoot "campusvoice.db"))
$ManagedEnvironmentVariables = @(
    "CAMPUSVOICE_ENV",
    "CAMPUSVOICE_DATABASE_URL",
    "CAMPUSVOICE_DATABASE_AUTO_CREATE",
    "CAMPUSVOICE_AUTH_MODE",
    "CAMPUSVOICE_ASR_PROVIDER",
    "CAMPUSVOICE_ASR_QUOTA_BACKEND",
    "CAMPUSVOICE_ASR_WORKER_COUNT",
    "CAMPUSVOICE_STORE_RAW_AUDIO",
    "CAMPUSVOICE_CORS_ORIGINS",
    "NEXT_PUBLIC_API_BASE_URL",
    "NEXT_PUBLIC_AUTH_MODE",
    "NEXT_PUBLIC_ASR_WS_URL"
)
$Started = New-Object System.Collections.ArrayList
$OwnedLogFiles = New-Object System.Collections.ArrayList
$OwnsStateFile = $false
$StartupDeadline = (Get-Date).AddSeconds($TimeoutSeconds)

function Write-Step([string]$Message) {
    Write-Host "[CampusVoice] $Message" -ForegroundColor Cyan
}

function Get-ProcessEnvironmentSnapshot([string[]]$VariableNames) {
    $Snapshot = [ordered]@{}
    foreach ($VariableName in $VariableNames) {
        $Snapshot[$VariableName] = [pscustomobject]@{
            exists = Test-Path -LiteralPath "Env:$VariableName"
            value = [Environment]::GetEnvironmentVariable(
                $VariableName,
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }
    return $Snapshot
}

function Restore-ProcessEnvironment($Snapshot) {
    foreach ($VariableName in $Snapshot.Keys) {
        $Saved = $Snapshot[$VariableName]
        if ($Saved.exists) {
            [Environment]::SetEnvironmentVariable(
                $VariableName,
                $Saved.value,
                [System.EnvironmentVariableTarget]::Process
            )
        }
        else {
            Remove-Item -LiteralPath "Env:$VariableName" -ErrorAction SilentlyContinue
        }
    }
}

function Get-IsolatedDemoConfiguration([string]$DatabasePath = $DemoDatabasePath) {
    $AllowedDataRoot = [IO.Path]::GetFullPath($DemoDataRoot)
    $ResolvedDatabasePath = [IO.Path]::GetFullPath($DatabasePath)
    $DatabaseParent = [IO.Path]::GetDirectoryName($ResolvedDatabasePath)
    $PathComparison = if ($env:OS -eq "Windows_NT") {
        [StringComparison]::OrdinalIgnoreCase
    }
    else {
        [StringComparison]::Ordinal
    }
    if (-not [string]::Equals(
            $DatabaseParent,
            $AllowedDataRoot,
            $PathComparison
        )) {
        throw "The demo database must stay directly inside this worktree's services/api/data directory."
    }
    foreach ($PathToInspect in @($AllowedDataRoot, $ResolvedDatabasePath)) {
        if (Test-Path -LiteralPath $PathToInspect) {
            $PathItem = Get-Item -Force -LiteralPath $PathToInspect
            if (($PathItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "The demo database path must not use a symbolic link or junction."
            }
        }
    }

    $DatabaseUrlPath = $ResolvedDatabasePath.Replace("\", "/")
    return [pscustomobject]@{
        DatabasePath = $ResolvedDatabasePath
        DatabaseUrl = "sqlite+aiosqlite:///$DatabaseUrlPath"
        Environment = "development"
        AuthMode = "demo"
        AsrProvider = "disabled"
        ApiBaseUrl = "http://localhost:8000"
        AsrWebSocketUrl = "ws://localhost:8000/ws/asr"
    }
}

function Set-IsolatedDemoEnvironment($Configuration) {
    $Values = [ordered]@{
        CAMPUSVOICE_ENV = $Configuration.Environment
        CAMPUSVOICE_DATABASE_URL = $Configuration.DatabaseUrl
        CAMPUSVOICE_DATABASE_AUTO_CREATE = "false"
        CAMPUSVOICE_AUTH_MODE = $Configuration.AuthMode
        CAMPUSVOICE_ASR_PROVIDER = $Configuration.AsrProvider
        CAMPUSVOICE_ASR_QUOTA_BACKEND = "local"
        CAMPUSVOICE_ASR_WORKER_COUNT = "1"
        CAMPUSVOICE_STORE_RAW_AUDIO = "false"
        CAMPUSVOICE_CORS_ORIGINS = '["http://localhost:3000","http://127.0.0.1:3000"]'
        NEXT_PUBLIC_API_BASE_URL = $Configuration.ApiBaseUrl
        NEXT_PUBLIC_AUTH_MODE = $Configuration.AuthMode
        NEXT_PUBLIC_ASR_WS_URL = $Configuration.AsrWebSocketUrl
    }
    foreach ($VariableName in $Values.Keys) {
        [Environment]::SetEnvironmentVariable(
            $VariableName,
            [string]$Values[$VariableName],
            [System.EnvironmentVariableTarget]::Process
        )
    }
}

function Invoke-WithDemoEnvironment(
    [scriptblock]$Action,
    [string]$DatabasePath = $DemoDatabasePath
) {
    $Snapshot = Get-ProcessEnvironmentSnapshot $ManagedEnvironmentVariables
    try {
        $Configuration = Get-IsolatedDemoConfiguration $DatabasePath
        Set-IsolatedDemoEnvironment $Configuration
        & $Action $Configuration
    }
    finally {
        Restore-ProcessEnvironment $Snapshot
    }
}

function Get-RecordValue($Record, [string]$Key) {
    if ($Record -is [Collections.IDictionary] -and $Record.Contains($Key)) {
        return [string]$Record[$Key]
    }
    $Property = $Record.PSObject.Properties[$Key]
    if ($Property) {
        return [string]$Property.Value
    }
    return ""
}

function Get-RemainingStartupSeconds([string]$Label) {
    $Remaining = [int][Math]::Ceiling(($StartupDeadline - (Get-Date)).TotalSeconds)
    if ($Remaining -le 0) {
        throw "Startup exceeded its $TimeoutSeconds-second deadline during $Label."
    }
    return $Remaining
}

function Test-Python311([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    try {
        $version = & $Candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        return $LASTEXITCODE -eq 0 -and $version.Trim() -eq "3.11"
    }
    catch {
        return $false
    }
}

function Resolve-Python311 {
    $Candidates = New-Object System.Collections.Generic.List[string]
    if ($PythonExecutable) {
        $Candidates.Add($PythonExecutable)
    }
    $Candidates.Add((Join-Path $RepoRoot ".venv/Scripts/python.exe"))
    $Candidates.Add((Join-Path $ApiRoot ".venv/Scripts/python.exe"))
    if ($env:CONDA_PREFIX) {
        $Candidates.Add((Join-Path $env:CONDA_PREFIX "python.exe"))
    }
    if ($env:USERPROFILE) {
        $Candidates.Add((Join-Path $env:USERPROFILE "miniconda3/envs/campusvoice/python.exe"))
        $Candidates.Add((Join-Path $env:USERPROFILE "anaconda3/envs/campusvoice/python.exe"))
    }
    $Candidates.Add("D:\minconda\envs\campusvoice\python.exe")
    $pathPython = Get-Command python -ErrorAction SilentlyContinue
    if ($pathPython) {
        $Candidates.Add($pathPython.Source)
    }
    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (Test-Python311 $Candidate) {
            return [IO.Path]::GetFullPath($Candidate)
        }
    }
    throw "Python 3.11 was not found. Activate the campusvoice environment or pass -PythonExecutable."
}

function Assert-PortFree([int]$Port) {
    $Listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($Listener) {
        $Owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction SilentlyContinue
        $ProcessName = if ($Owner -and $Owner.Name) { $Owner.Name } else { "unknown process" }
        throw "Port $Port is already used by PID $($Listener.OwningProcess) ($ProcessName)."
    }
}

function Invoke-Checked(
    [string]$Label,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory
) {
    $Remaining = Get-RemainingStartupSeconds $Label
    Write-Step $Label
    $CommandPath = "'" + $FilePath.Replace("'", "''") + "'"
    $WorkingPath = "'" + $WorkingDirectory.Replace("'", "''") + "'"
    $Token = [Guid]::NewGuid().ToString("N")
    $Stdout = Join-Path $RuntimeRoot "check-$Token.stdout.log"
    $Stderr = Join-Path $RuntimeRoot "check-$Token.stderr.log"
    $ExitStatus = Join-Path $RuntimeRoot "check-$Token.exit.txt"
    $ExitStatusPath = "'" + $ExitStatus.Replace("'", "''") + "'"
    $QuotedArguments = @($Arguments | ForEach-Object {
        "'" + ([string]$_).Replace("'", "''") + "'"
    })
    $Command = "`$ProgressPreference = 'SilentlyContinue'; Set-Location -LiteralPath $WorkingPath; & $CommandPath $($QuotedArguments -join ' '); `$CampusVoiceExitCode = `$LASTEXITCODE; [IO.File]::WriteAllText($ExitStatusPath, [string]`$CampusVoiceExitCode); exit `$CampusVoiceExitCode"
    $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    $Process = Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $EncodedCommand
    ) -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
    try {
        Wait-Process -Id $Process.Id -Timeout $Remaining -ErrorAction SilentlyContinue
        if (Get-Process -Id $Process.Id -ErrorAction SilentlyContinue) {
            $Descendants = @(Get-DescendantProcessIds $Process.Id)
            [array]::Reverse($Descendants)
            foreach ($ProcessId in $Descendants) {
                Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
            }
            Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
            throw "$Label exceeded the shared startup deadline."
        }
        if (Test-Path -LiteralPath $Stdout) {
            Get-Content -LiteralPath $Stdout | ForEach-Object { Write-Host $_ }
        }
        if (Test-Path -LiteralPath $Stderr) {
            Get-Content -LiteralPath $Stderr | ForEach-Object { Write-Host $_ }
        }
        if (-not (Test-Path -LiteralPath $ExitStatus)) {
            throw "$Label did not report an exit code."
        }
        $ExitCode = [int](Get-Content -LiteralPath $ExitStatus -Raw)
        if ($ExitCode -ne 0) {
            throw "$Label failed with exit code $ExitCode."
        }
        [void](Get-RemainingStartupSeconds $Label)
    }
    finally {
        Remove-Item -LiteralPath $Stdout, $Stderr, $ExitStatus -Force -ErrorAction SilentlyContinue
    }
}

function Save-State {
    param([object[]]$Processes = @($Started))
    $Payload = [ordered]@{
        version = 1
        repo_root = $RepoRoot
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        processes = @($Processes)
    }
    $Payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StateFile -Encoding UTF8
    $script:OwnsStateFile = $true
}

function Start-TrackedProcess(
    [string]$Name,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [string]$Stdout,
    [string]$Stderr,
    [string]$Marker
) {
    $Process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
    foreach ($LogPath in @($Stdout, $Stderr)) {
        if (-not $OwnedLogFiles.Contains($LogPath)) {
            [void]$OwnedLogFiles.Add($LogPath)
        }
    }
    $Record = [ordered]@{
        name = $Name
        pid = $Process.Id
        marker = $Marker
        stdout = $Stdout
        stderr = $Stderr
    }
    [void]$Started.Add($Record)
    Save-State
    return $Record
}

function Get-DescendantProcessIds([int]$RootPid) {
    $All = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $Result = New-Object System.Collections.ArrayList
    $Queue = New-Object System.Collections.Queue
    $Queue.Enqueue($RootPid)
    while ($Queue.Count -gt 0) {
        $Parent = [int]$Queue.Dequeue()
        foreach ($Child in @($All | Where-Object { $_.ParentProcessId -eq $Parent })) {
            [void]$Result.Add([int]$Child.ProcessId)
            $Queue.Enqueue([int]$Child.ProcessId)
        }
    }
    return @($Result)
}

function Stop-TrackedProcess($Record) {
    $RecordPid = 0
    $Marker = [string]$Record.marker
    $Name = [string]$Record.name
    if ($Name -notin @("api", "web") -or
        -not [int]::TryParse([string]$Record.pid, [ref]$RecordPid) -or
        $RecordPid -le 0 -or
        [string]::IsNullOrWhiteSpace($Marker)) {
        Write-Warning "Skipped an invalid process record during cleanup."
        return [pscustomobject]@{ success = $false; records = @($Record) }
    }
    $Root = Get-CimInstance Win32_Process -Filter "ProcessId=$RecordPid" -ErrorAction SilentlyContinue
    if (-not $Root) {
        return [pscustomobject]@{ success = $true; records = @() }
    }
    if (-not $Root.CommandLine -or -not $Root.CommandLine.Contains($Marker)) {
        Write-Warning "Skipped PID ${RecordPid}: its command line does not match this run."
        return [pscustomobject]@{ success = $false; records = @($Record) }
    }
    $Descendants = @(Get-DescendantProcessIds $RecordPid)
    $Snapshots = @{}
    foreach ($ProcessId in (@($RecordPid) + @($Descendants))) {
        $Snapshot = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if ($Snapshot) {
            $Snapshots[[string]$ProcessId] = $Snapshot
        }
    }
    [array]::Reverse($Descendants)
    foreach ($ProcessId in $Descendants) {
        Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $RecordPid -ErrorAction SilentlyContinue
    Wait-Process -Id $RecordPid -Timeout 5 -ErrorAction SilentlyContinue
    $CandidateIds = @($RecordPid) + @($Descendants)
    $Survivors = @()
    foreach ($Attempt in 1..4) {
        $Survivors = @($CandidateIds | Where-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        })
        if ($Survivors.Count -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if ($Survivors.Count -gt 0) {
        Write-Warning "$Name cleanup left PIDs running: $($Survivors -join ', ')."
        $SurvivorRecords = @($Survivors | ForEach-Object {
            $SurvivorPid = [int]$_
            $Snapshot = $Snapshots[[string]$SurvivorPid]
            $SurvivorMarker = if ($SurvivorPid -eq $RecordPid) {
                $Marker
            }
            elseif ($Snapshot -and $Snapshot.CommandLine) {
                [string]$Snapshot.CommandLine
            }
            else {
                ""
            }
            [ordered]@{
                name = $Name
                pid = $SurvivorPid
                marker = $SurvivorMarker
                stdout = Get-RecordValue $Record "stdout"
                stderr = Get-RecordValue $Record "stderr"
            }
        })
        return [pscustomobject]@{ success = $false; records = $SurvivorRecords }
    }
    return [pscustomobject]@{ success = $true; records = @() }
}

function Wait-Http(
    [string]$Label,
    [string]$Url,
    [int]$ProcessId
) {
    Write-Step "$Label (shared startup deadline)"
    $LastError = $null
    while ((Get-Date) -lt $StartupDeadline) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "$Label failed because PID $ProcessId exited."
        }
        try {
            $Remaining = Get-RemainingStartupSeconds $Label
            $RequestTimeout = [Math]::Max(1, [Math]::Min(3, $Remaining))
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $RequestTimeout
            if ($Response.StatusCode -eq 200) {
                return
            }
            $LastError = "HTTP $($Response.StatusCode)"
        }
        catch {
            $LastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Label exceeded the shared startup deadline at $Url. Last error: $LastError"
}

function Show-LogTail([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        Write-Host "--- $Path"
        Get-Content -LiteralPath $Path -Tail 30
    }
}

function Invoke-CampusVoiceDemo {
    $script:Started = New-Object System.Collections.ArrayList
    $script:OwnedLogFiles = New-Object System.Collections.ArrayList
    $script:OwnsStateFile = $false
    $script:StartupDeadline = (Get-Date).AddSeconds($TimeoutSeconds)

    try {
        Invoke-WithDemoEnvironment -Action {
            param($DemoConfiguration)

            [IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null
            Write-Step "Using isolated local demo configuration"
            Write-Host "Database target: $($DemoConfiguration.DatabasePath)"
            Write-Host "Environment: development; auth: demo; ASR: disabled (local quota)"

            if (Test-Path -LiteralPath $StateFile) {
                $Existing = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
                $Running = @($Existing.processes | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue })
                if ($Running.Count -gt 0) {
                    throw "This worktree already has recorded demo processes. Run scripts/stop_demo.ps1 first."
                }
                Remove-Item -LiteralPath $StateFile -Force
            }

            Assert-PortFree 8000
            Assert-PortFree 3000
            $Python = Resolve-Python311
            $Pnpm = (Get-Command pnpm -ErrorAction Stop).Source
            $Node = (Get-Command node -ErrorAction Stop).Source

            Write-Step "Checking Python, Node, pnpm, and project dependencies"
            Write-Host "Python: $(& $Python --version 2>&1)"
            Write-Host "Node: $(& $Node --version 2>&1)"
            Write-Host "pnpm: $(& $Pnpm --version 2>&1)"
            Invoke-Checked "Checking API dependencies" $Python @(
                "-c",
                "import alembic, fastapi, httpx, sqlalchemy, uvicorn"
            ) $ApiRoot
            Invoke-Checked "Checking Next.js dependencies" $Pnpm @(
                "--filter", "@campusvoice/web", "exec", "next", "--version"
            ) $RepoRoot

            Invoke-Checked "Applying Alembic migrations" $Python @("-m", "alembic", "upgrade", "head") $ApiRoot

            Write-Step "Starting API"
            $ApiMarker = [IO.Path]::GetFullPath($ApiRoot)
            $Api = Start-TrackedProcess "api" $Python @(
                "-m", "uvicorn", "app.main:app",
                "--app-dir", $ApiMarker,
                "--host", "127.0.0.1",
                "--port", "8000"
            ) $ApiRoot $ApiStdout $ApiStderr $ApiMarker
            Wait-Http "API liveness check" "http://localhost:8000/health/live" ([int]$Api.pid)
            Wait-Http "API readiness check" "http://localhost:8000/health/ready" ([int]$Api.pid)

            Invoke-Checked "Loading idempotent synthetic demo data" $Python @(
                (Join-Path $RepoRoot "scripts/seed_demo.py"),
                "--base-url", "http://localhost:8000",
                "--request-timeout-seconds", "5"
            ) $RepoRoot

            Write-Step "Starting Web"
            $EscapedRepo = $RepoRoot.Replace("'", "''")
            $EscapedPnpm = $Pnpm.Replace("'", "''")
            $WebCommand = "Set-Location -LiteralPath '$EscapedRepo'; & '$EscapedPnpm' --filter '@campusvoice/web' exec next dev -H localhost -p 3000"
            $EncodedWebCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($WebCommand))
            $Web = Start-TrackedProcess "web" "powershell.exe" @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $EncodedWebCommand
            ) $RepoRoot $WebStdout $WebStderr $EncodedWebCommand
            Wait-Http "Web homepage check" "http://localhost:3000/" ([int]$Web.pid)

            Write-Host ""
            Write-Host "CampusVoice demo is ready" -ForegroundColor Green
            Write-Host "Home:          http://localhost:3000/"
            Write-Host "Voice:         http://localhost:3000/voice"
            Write-Host "Recognition:   http://localhost:3000/settings"
            Write-Host "API docs:      http://localhost:8000/docs"
            Write-Host "Process state: $StateFile"
            Write-Host "Stop command:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop_demo.ps1"
        }
    }
    catch {
        $CleanupRecords = @($Started)
        [array]::Reverse($CleanupRecords)
        $CleanupComplete = $true
        $RemainingRecords = New-Object System.Collections.ArrayList
        foreach ($Record in $CleanupRecords) {
            $CleanupResult = Stop-TrackedProcess $Record
            if (-not $CleanupResult.success) {
                $CleanupComplete = $false
            }
            foreach ($RemainingRecord in @($CleanupResult.records)) {
                [void]$RemainingRecords.Add($RemainingRecord)
            }
        }
        foreach ($LogPath in @($OwnedLogFiles | Where-Object { $_ -like "*.stderr.log" })) {
            Show-LogTail $LogPath
        }
        if ($OwnsStateFile -and $CleanupComplete -and (Test-Path -LiteralPath $StateFile)) {
            Remove-Item -LiteralPath $StateFile -Force
        }
        elseif ($OwnsStateFile -and -not $CleanupComplete) {
            Save-State -Processes ([object[]]@($RemainingRecords))
            Write-Warning "Cleanup was incomplete; the state file and logs were retained for review."
        }
        if ($CleanupComplete) {
            foreach ($LogPath in @($OwnedLogFiles)) {
                Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
                if (Test-Path -LiteralPath $LogPath) {
                    Write-Warning "Failed to remove owned runtime log: $LogPath"
                }
            }
        }
        throw
    }
}

if ($MyInvocation.InvocationName -ne ".") {
    Invoke-CampusVoiceDemo
}

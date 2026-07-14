[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $RepoRoot "services/api/data/runtime"
$StateFile = Join-Path $RuntimeRoot "demo-processes.json"

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

function Get-TrackedProcessRecordStatus($Record) {
    $RecordPid = 0
    $Marker = Get-RecordValue $Record "marker"
    $Name = Get-RecordValue $Record "name"
    if ($Name -notin @("api", "web") -or
        -not [int]::TryParse((Get-RecordValue $Record "pid"), [ref]$RecordPid) -or
        $RecordPid -le 0 -or
        [string]::IsNullOrWhiteSpace($Marker)) {
        return [pscustomobject]@{
            status = "invalid"
            name = $Name
            pid = $RecordPid
            marker = $Marker
            process = $null
        }
    }

    $Root = Get-CimInstance Win32_Process -Filter "ProcessId=$RecordPid" -ErrorAction SilentlyContinue
    if (-not $Root) {
        $Status = "exited"
    }
    elseif (-not $Root.CommandLine) {
        $Status = "unverifiable"
    }
    elseif ($Root.CommandLine.Contains($Marker)) {
        $Status = "matching"
    }
    else {
        $Status = "stale"
    }
    return [pscustomobject]@{
        status = $Status
        name = $Name
        pid = $RecordPid
        marker = $Marker
        process = $Root
    }
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

if (-not (Test-Path -LiteralPath $StateFile)) {
    Write-Host "No demo process state exists for this worktree; no process was stopped."
    exit 0
}

$State = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$State.repo_root) -ne $RepoRoot) {
    throw "The process state belongs to another worktree; no process was stopped."
}

$RemainingRecords = New-Object System.Collections.ArrayList
$Records = @($State.processes)
[array]::Reverse($Records)
foreach ($Record in $Records) {
    $RecordStatus = Get-TrackedProcessRecordStatus $Record
    $RecordPid = [int]$RecordStatus.pid
    $Marker = [string]$RecordStatus.marker
    $Name = [string]$RecordStatus.name
    if ($RecordStatus.status -eq "invalid") {
        [void]$RemainingRecords.Add($Record)
        Write-Warning "Skipped an invalid process record."
        continue
    }
    if ($RecordStatus.status -eq "exited") {
        Write-Host "${Name}: PID $RecordPid already exited."
        continue
    }
    if ($RecordStatus.status -eq "stale") {
        Write-Warning "${Name}: PID $RecordPid belongs to another process; stale state was removed without stopping it."
        continue
    }
    if ($RecordStatus.status -eq "unverifiable") {
        [void]$RemainingRecords.Add($Record)
        Write-Warning "${Name}: PID $RecordPid command line could not be verified and was not stopped."
        continue
    }

    $Root = $RecordStatus.process
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
    Wait-Process -Id $RecordPid -Timeout 10 -ErrorAction SilentlyContinue
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
        foreach ($Survivor in $Survivors) {
            $SurvivorPid = [int]$Survivor
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
            [void]$RemainingRecords.Add([ordered]@{
                name = $Name
                pid = $SurvivorPid
                marker = $SurvivorMarker
                stdout = Get-RecordValue $Record "stdout"
                stderr = Get-RecordValue $Record "stderr"
            })
        }
        Write-Warning "${Name}: verified cleanup left PIDs running: $($Survivors -join ', ')."
        continue
    }
    Write-Host "${Name}: stopped recorded PID $RecordPid and its descendants."
}

if ($RemainingRecords.Count -eq 0) {
    Remove-Item -LiteralPath $StateFile -Force
}
else {
    $State.processes = @($RemainingRecords)
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StateFile -Encoding UTF8
    Write-Warning "Cleanup was incomplete; the state file was retained for review."
}

foreach ($Port in @(3000, 8000)) {
    $Listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($Listener) {
        Write-Warning "Port $Port is still used by PID $($Listener.OwningProcess); it was outside the verified stop scope."
    }
    else {
        Write-Host "Port $Port is free."
    }
}

if ($RemainingRecords.Count -gt 0) {
    exit 1
}

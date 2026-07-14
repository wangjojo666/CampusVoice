from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
START_DEMO = REPO_ROOT / "scripts" / "start_demo.ps1"
STOP_DEMO = REPO_ROOT / "scripts" / "stop_demo.ps1"


def _powershell_executable() -> str:
    candidates = ("powershell", "pwsh") if os.name == "nt" else ("pwsh", "powershell")
    executable = next((path for name in candidates if (path := shutil.which(name))), None)
    if executable is None:
        pytest.fail("PowerShell is required to test scripts/start_demo.ps1")
    return executable


def _copy_start_script(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "campusvoice"
    script_path = repo_root / "scripts" / "start_demo.ps1"
    script_path.parent.mkdir(parents=True)
    shutil.copy2(START_DEMO, script_path)
    shutil.copy2(STOP_DEMO, script_path.with_name("stop_demo.ps1"))
    return repo_root, script_path


def _run_powershell_json(repo_root: Path, script_path: Path, command: str) -> dict[str, Any]:
    escaped_script = str(script_path).replace("'", "''")
    escaped_stop_script = str(script_path.with_name("stop_demo.ps1")).replace("'", "''")
    source = (
        textwrap.dedent(command)
        .replace("__START_DEMO__", escaped_script)
        .replace("__STOP_DEMO__", escaped_stop_script)
    )
    environment = os.environ.copy()
    environment["CAMPUSVOICE_TEST_PYTHON"] = sys.executable
    completed = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            source,
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, (
        f"PowerShell regression harness failed ({completed.returncode}).\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    for line in reversed(completed.stdout.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{"):
            return json.loads(candidate)
    pytest.fail(
        "PowerShell regression harness did not emit JSON.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def _assert_restored(
    after: dict[str, dict[str, Any]], sentinels: dict[str, str], managed: list[str]
) -> None:
    for variable_name in managed:
        state = after[variable_name]
        if variable_name in sentinels:
            assert state == {"Exists": True, "Value": sentinels[variable_name]}
        else:
            assert state["Exists"] is False
            assert state["Value"] is None


@pytest.mark.skipif(os.name != "nt", reason="start_demo.ps1 launches Windows processes")
def test_tracked_process_preserves_windows_arguments_with_spaces_quotes_and_backslashes(
    tmp_path: Path,
) -> None:
    repo_root, script_path = _copy_start_script(tmp_path / "repository with spaces")
    payload = _run_powershell_json(
        repo_root,
        script_path,
        r"""
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        . '__START_DEMO__'

        [IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null
        $Stdout = Join-Path $RuntimeRoot 'argv.stdout.log'
        $Stderr = Join-Path $RuntimeRoot 'argv.stderr.log'
        $Expected = @(
            'argument with spaces'
            'quote"inside'
            'trailing path\'
            ''
        )
        $PythonCode = 'import json, sys; print(json.dumps(sys.argv[1:]))'
        $ChildArguments = @('-c', $PythonCode) + $Expected
        $Record = Start-TrackedProcess `
            -Name 'api' `
            -FilePath $env:CAMPUSVOICE_TEST_PYTHON `
            -Arguments $ChildArguments `
            -WorkingDirectory $RepoRoot `
            -Stdout $Stdout `
            -Stderr $Stderr `
            -Marker 'argv-regression-marker'
        Wait-Process -Id ([int]$Record.pid) -Timeout 10 -ErrorAction SilentlyContinue
        $StillRunning = [bool](Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue)
        if ($StillRunning) {
            Stop-Process -Id ([int]$Record.pid) -Force -ErrorAction SilentlyContinue
        }
        [pscustomobject]@{
            StillRunning = $StillRunning
            Stdout = if (Test-Path -LiteralPath $Stdout) {
                ([string]::Join("", [string[]]@(Get-Content -LiteralPath $Stdout))).Trim()
            }
            else { '' }
            Stderr = if (Test-Path -LiteralPath $Stderr) {
                ([string]::Join("", [string[]]@(Get-Content -LiteralPath $Stderr))).Trim()
            }
            else { '' }
        } | ConvertTo-Json -Compress
        """,
    )

    assert payload["StillRunning"] is False
    assert payload["Stderr"] == ""
    assert json.loads(payload["Stdout"]) == [
        "argument with spaces",
        'quote"inside',
        "trailing path\\",
        "",
    ]


def test_stale_pid_marker_state_is_removed_without_stopping_the_process(tmp_path: Path) -> None:
    repo_root, script_path = _copy_start_script(tmp_path)
    payload = _run_powershell_json(
        repo_root,
        script_path,
        r"""
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        . '__START_DEMO__'

        [IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null
        $Marker = 'expected-campusvoice-marker'
        $script:MockCommandLine = 'unrelated-process --serve'
        function Get-CimInstance {
            [CmdletBinding()]
            param(
                [Parameter(Position = 0)][string]$ClassName,
                [string]$Filter
            )
            return [pscustomobject]@{
                ProcessId = 4242
                ParentProcessId = 1
                CommandLine = $script:MockCommandLine
                Name = 'unrelated.exe'
            }
        }
        function Get-NetTCPConnection {
            [CmdletBinding()]
            param([string]$State, [int]$LocalPort)
            return $null
        }
        function Stop-Process {
            throw 'stale marker must never stop the unrelated process'
        }
        function Write-TestState {
            [ordered]@{
                version = 1
                repo_root = $RepoRoot
                processes = @(
                    [ordered]@{
                        name = 'api'
                        pid = 4242
                        marker = $Marker
                        stdout = 'unused.stdout.log'
                        stderr = 'unused.stderr.log'
                    }
                )
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StateFile -Encoding UTF8
        }

        Write-TestState
        $script:MockCommandLine = "python -m uvicorn --app-dir $Marker"
        $MatchingFailure = $null
        try {
            Assert-NoRecordedDemoProcesses
        }
        catch {
            $MatchingFailure = $_.Exception.Message
        }
        $MatchingStateExists = Test-Path -LiteralPath $StateFile
        Remove-Item -LiteralPath $StateFile -Force

        Write-TestState
        $script:MockCommandLine = 'unrelated-process --serve'
        Assert-NoRecordedDemoProcesses
        $StartRemovedStaleState = -not (Test-Path -LiteralPath $StateFile)

        Write-TestState
        & '__STOP_DEMO__'
        $StopRemovedStaleState = -not (Test-Path -LiteralPath $StateFile)

        [pscustomobject]@{
            MatchingFailure = $MatchingFailure
            MatchingStateExists = $MatchingStateExists
            StartRemovedStaleState = $StartRemovedStaleState
            StopRemovedStaleState = $StopRemovedStaleState
        } | ConvertTo-Json -Compress
        """,
    )

    assert "already has recorded demo processes" in payload["MatchingFailure"]
    assert payload["MatchingStateExists"] is True
    assert payload["StartRemovedStaleState"] is True
    assert payload["StopRemovedStaleState"] is True


def test_demo_main_isolates_ambient_configuration_and_restores_it(tmp_path: Path) -> None:
    repo_root, script_path = _copy_start_script(tmp_path)
    payload = _run_powershell_json(
        repo_root,
        script_path,
        r"""
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        . '__START_DEMO__'

        $Sentinels = [ordered]@{
            CAMPUSVOICE_ENV = 'production'
            CAMPUSVOICE_DATABASE_URL = 'campusvoice-invalid://ambient-user:ambient-secret@invalid/never-use'
            CAMPUSVOICE_DATABASE_AUTO_CREATE = 'true'
            CAMPUSVOICE_AUTH_MODE = 'oidc'
            CAMPUSVOICE_ASR_PROVIDER = 'whisper'
            CAMPUSVOICE_ASR_QUOTA_BACKEND = 'redis'
            CAMPUSVOICE_ASR_WORKER_COUNT = '9'
            CAMPUSVOICE_STORE_RAW_AUDIO = 'true'
            CAMPUSVOICE_CORS_ORIGINS = '["https://ambient.invalid"]'
            NEXT_PUBLIC_API_BASE_URL = 'https://ambient.invalid'
            NEXT_PUBLIC_AUTH_MODE = 'oidc'
            NEXT_PUBLIC_ASR_WS_URL = 'wss://ambient.invalid/ws/asr'
        }
        $OuterSnapshot = Get-ProcessEnvironmentSnapshot $ManagedEnvironmentVariables
        try {
            foreach ($VariableName in $ManagedEnvironmentVariables) {
                Remove-Item -LiteralPath "Env:$VariableName" -ErrorAction SilentlyContinue
            }
            foreach ($Entry in $Sentinels.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($Entry.Key, $Entry.Value, 'Process')
            }

            $Events = New-Object System.Collections.ArrayList
            $Calls = New-Object System.Collections.ArrayList
            function Capture-Stage([string]$StageName) {
                [void]$Events.Add([ordered]@{
                    Name = $StageName
                    Environment = $env:CAMPUSVOICE_ENV
                    DatabaseUrl = $env:CAMPUSVOICE_DATABASE_URL
                    DatabaseAutoCreate = $env:CAMPUSVOICE_DATABASE_AUTO_CREATE
                    AuthMode = $env:CAMPUSVOICE_AUTH_MODE
                    AsrProvider = $env:CAMPUSVOICE_ASR_PROVIDER
                    AsrQuotaBackend = $env:CAMPUSVOICE_ASR_QUOTA_BACKEND
                    AsrWorkerCount = $env:CAMPUSVOICE_ASR_WORKER_COUNT
                    StoreRawAudio = $env:CAMPUSVOICE_STORE_RAW_AUDIO
                    CorsOrigins = $env:CAMPUSVOICE_CORS_ORIGINS
                    ApiBaseUrl = $env:NEXT_PUBLIC_API_BASE_URL
                    WebAuthMode = $env:NEXT_PUBLIC_AUTH_MODE
                    AsrWebSocketUrl = $env:NEXT_PUBLIC_ASR_WS_URL
                })
            }
            function Assert-PortFree([int]$Port) { }
            function Resolve-Python311 { return $env:CAMPUSVOICE_TEST_PYTHON }
            function Get-Command {
                [CmdletBinding()]
                param([Parameter(Position = 0)][string]$Name)
                return [pscustomobject]@{ Source = $env:CAMPUSVOICE_TEST_PYTHON }
            }
            function Invoke-Checked(
                [string]$Label,
                [string]$FilePath,
                [string[]]$Arguments,
                [string]$WorkingDirectory
            ) {
                [void]$Calls.Add([ordered]@{
                    Kind = 'checked'
                    Label = $Label
                    FilePath = $FilePath
                    Arguments = @($Arguments)
                    WorkingDirectory = $WorkingDirectory
                })
                if ($Label -eq 'Applying Alembic migrations') {
                    Capture-Stage 'migrate'
                }
                elseif ($Label -eq 'Loading idempotent synthetic demo data') {
                    Capture-Stage 'seed'
                }
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
                Capture-Stage $Name
                $FakePid = if ($Name -eq 'api') { 101 } else { 102 }
                [void]$Calls.Add([ordered]@{
                    Kind = 'process'
                    Name = $Name
                    FilePath = $FilePath
                    Arguments = @($Arguments)
                    WorkingDirectory = $WorkingDirectory
                    Stdout = $Stdout
                    Stderr = $Stderr
                    Marker = $Marker
                    Pid = $FakePid
                })
                return [pscustomobject]@{ name = $Name; pid = $FakePid; marker = $Marker }
            }
            function Wait-Http([string]$Label, [string]$Url, [int]$ProcessId) {
                Capture-Stage $Label
                [void]$Calls.Add([ordered]@{
                    Kind = 'http'
                    Label = $Label
                    Url = $Url
                    ProcessId = $ProcessId
                })
            }

            $Transcript = @(
                Invoke-CampusVoiceDemo *>&1 | ForEach-Object { [string]$_ }
            )
            $After = [ordered]@{}
            foreach ($VariableName in $ManagedEnvironmentVariables) {
                $After[$VariableName] = [ordered]@{
                    Exists = Test-Path -LiteralPath "Env:$VariableName"
                    Value = [Environment]::GetEnvironmentVariable($VariableName, 'Process')
                }
            }
            [pscustomobject]@{
                Managed = @($ManagedEnvironmentVariables)
                Sentinels = $Sentinels
                Events = @($Events)
                Calls = @($Calls)
                Transcript = @($Transcript)
                After = $After
                Config = Get-IsolatedDemoConfiguration
            } | ConvertTo-Json -Depth 8 -Compress
        }
        finally {
            Restore-ProcessEnvironment $OuterSnapshot
        }
        """,
    )

    managed = payload["Managed"]
    sentinels = payload["Sentinels"]
    _assert_restored(payload["After"], sentinels, managed)

    expected_database_path = (repo_root / "services" / "api" / "data" / "campusvoice.db").resolve()
    expected_database_url = f"sqlite+aiosqlite:///{expected_database_path.as_posix()}"
    assert Path(payload["Config"]["DatabasePath"]).resolve() == expected_database_path
    assert payload["Config"]["DatabaseUrl"] == expected_database_url

    expected_order = [
        "migrate",
        "api",
        "API liveness check",
        "API readiness check",
        "seed",
        "web",
        "Web homepage check",
    ]
    assert [event["Name"] for event in payload["Events"]] == expected_order

    calls = payload["Calls"]
    migrate = next(call for call in calls if call.get("Label") == "Applying Alembic migrations")
    assert Path(migrate["FilePath"]).resolve() == Path(sys.executable).resolve()
    assert migrate["Arguments"] == ["-m", "alembic", "upgrade", "head"]
    assert Path(migrate["WorkingDirectory"]).resolve() == (repo_root / "services" / "api").resolve()

    seed = next(
        call for call in calls if call.get("Label") == "Loading idempotent synthetic demo data"
    )
    assert Path(seed["FilePath"]).resolve() == Path(sys.executable).resolve()
    assert (
        Path(seed["Arguments"][0]).resolve() == (repo_root / "scripts" / "seed_demo.py").resolve()
    )
    assert seed["Arguments"][1:] == [
        "--base-url",
        "http://localhost:8000",
        "--request-timeout-seconds",
        "5",
    ]
    assert Path(seed["WorkingDirectory"]).resolve() == repo_root.resolve()

    process_calls = {call["Name"]: call for call in calls if call["Kind"] == "process"}
    api_process = process_calls["api"]
    api_root = (repo_root / "services" / "api").resolve()
    assert Path(api_process["FilePath"]).resolve() == Path(sys.executable).resolve()
    assert api_process["Arguments"] == [
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        str(api_root),
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    assert Path(api_process["WorkingDirectory"]).resolve() == api_root
    assert Path(api_process["Marker"]).resolve() == api_root

    web_process = process_calls["web"]
    assert web_process["FilePath"] == "powershell.exe"
    assert web_process["Arguments"][:4] == [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
    ]
    encoded_web_command = web_process["Arguments"][4]
    assert web_process["Marker"] == encoded_web_command
    web_command = base64.b64decode(encoded_web_command).decode("utf-16-le")
    assert f"Set-Location -LiteralPath '{repo_root}'" in web_command
    assert "--filter '@campusvoice/web' exec next dev -H localhost -p 3000" in web_command
    assert sys.executable in web_command
    assert Path(web_process["WorkingDirectory"]).resolve() == repo_root.resolve()

    http_calls = [call for call in calls if call["Kind"] == "http"]
    assert [(call["Url"], call["ProcessId"]) for call in http_calls] == [
        ("http://localhost:8000/health/live", 101),
        ("http://localhost:8000/health/ready", 101),
        ("http://localhost:3000/", 102),
    ]
    transcript = "\n".join(payload["Transcript"])
    assert str(expected_database_path) in transcript
    assert "ambient-secret" not in transcript
    assert "campusvoice-invalid" not in transcript

    for event in payload["Events"]:
        settings = Settings(
            env=event["Environment"],
            database_url=event["DatabaseUrl"],
            database_auto_create=event["DatabaseAutoCreate"],
            auth_mode=event["AuthMode"],
            asr_provider=event["AsrProvider"],
            asr_quota_backend=event["AsrQuotaBackend"],
            asr_worker_count=event["AsrWorkerCount"],
            store_raw_audio=event["StoreRawAudio"],
            cors_origins=json.loads(event["CorsOrigins"]),
        )
        assert settings.env == "development"
        assert settings.database_url == expected_database_url
        assert settings.database_auto_create is False
        assert settings.auth_mode == event["WebAuthMode"] == "demo"
        assert settings.asr_provider == "disabled"
        assert settings.asr_quota_backend == "local"
        assert settings.asr_worker_count == 1
        assert settings.store_raw_audio is False
        assert settings.cors_origins == [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
        assert event["ApiBaseUrl"] == "http://localhost:8000"
        assert event["AsrWebSocketUrl"] == "ws://localhost:8000/ws/asr"
        assert sentinels["CAMPUSVOICE_DATABASE_URL"] not in event["DatabaseUrl"]


def test_demo_failure_restores_environment_and_removes_owned_runtime_files(
    tmp_path: Path,
) -> None:
    repo_root, script_path = _copy_start_script(tmp_path)
    payload = _run_powershell_json(
        repo_root,
        script_path,
        r"""
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        . '__START_DEMO__'

        $Sentinels = [ordered]@{
            CAMPUSVOICE_ENV = 'production'
            CAMPUSVOICE_DATABASE_URL = 'campusvoice-invalid://ambient/never-use'
            CAMPUSVOICE_AUTH_MODE = 'jwt'
            CAMPUSVOICE_ASR_PROVIDER = 'funasr'
        }
        $OuterSnapshot = Get-ProcessEnvironmentSnapshot $ManagedEnvironmentVariables
        try {
            foreach ($VariableName in $ManagedEnvironmentVariables) {
                Remove-Item -LiteralPath "Env:$VariableName" -ErrorAction SilentlyContinue
            }
            foreach ($Entry in $Sentinels.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($Entry.Key, $Entry.Value, 'Process')
            }

            $Stopped = New-Object System.Collections.ArrayList
            function Assert-PortFree([int]$Port) { }
            function Resolve-Python311 { return $env:CAMPUSVOICE_TEST_PYTHON }
            function Get-Command {
                [CmdletBinding()]
                param([Parameter(Position = 0)][string]$Name)
                return [pscustomobject]@{ Source = $env:CAMPUSVOICE_TEST_PYTHON }
            }
            function Invoke-Checked(
                [string]$Label,
                [string]$FilePath,
                [string[]]$Arguments,
                [string]$WorkingDirectory
            ) {
                if ($Label -eq 'Loading idempotent synthetic demo data') {
                    throw 'forced seed failure'
                }
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
                $Record = [ordered]@{ name = $Name; pid = 201; marker = $Marker }
                [void]$script:Started.Add($Record)
                foreach ($LogPath in @($Stdout, $Stderr)) {
                    [IO.File]::WriteAllText($LogPath, 'owned test log')
                    [void]$script:OwnedLogFiles.Add($LogPath)
                }
                Save-State
                return $Record
            }
            function Wait-Http([string]$Label, [string]$Url, [int]$ProcessId) { }
            function Stop-TrackedProcess($Record) {
                [void]$Stopped.Add([int]$Record.pid)
                return [pscustomobject]@{ success = $true; records = @() }
            }

            $Failure = $null
            try {
                Invoke-CampusVoiceDemo *> $null
            }
            catch {
                $Failure = $_.Exception.Message
            }
            $After = [ordered]@{}
            foreach ($VariableName in $ManagedEnvironmentVariables) {
                $After[$VariableName] = [ordered]@{
                    Exists = Test-Path -LiteralPath "Env:$VariableName"
                    Value = [Environment]::GetEnvironmentVariable($VariableName, 'Process')
                }
            }
            $RuntimeFiles = if (Test-Path -LiteralPath $RuntimeRoot) {
                @(
                    Get-ChildItem -Force -LiteralPath $RuntimeRoot |
                        Select-Object -ExpandProperty Name
                )
            }
            else {
                @()
            }
            [pscustomobject]@{
                Managed = @($ManagedEnvironmentVariables)
                Sentinels = $Sentinels
                Failure = $Failure
                After = $After
                StateExists = Test-Path -LiteralPath $StateFile
                RuntimeFiles = @($RuntimeFiles)
                Stopped = @($Stopped)
            } | ConvertTo-Json -Depth 8 -Compress
        }
        finally {
            Restore-ProcessEnvironment $OuterSnapshot
        }
        """,
    )

    assert payload["Failure"] == "forced seed failure"
    _assert_restored(payload["After"], payload["Sentinels"], payload["Managed"])
    assert payload["StateExists"] is False
    assert payload["RuntimeFiles"] == []
    assert payload["Stopped"] == [201]


def test_database_guard_rejects_out_of_tree_target_before_action(tmp_path: Path) -> None:
    repo_root, script_path = _copy_start_script(tmp_path)
    payload = _run_powershell_json(
        repo_root,
        script_path,
        r"""
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        . '__START_DEMO__'

        $Sentinel = 'campusvoice-invalid://ambient/never-use'
        $OuterSnapshot = Get-ProcessEnvironmentSnapshot $ManagedEnvironmentVariables
        try {
            [Environment]::SetEnvironmentVariable('CAMPUSVOICE_DATABASE_URL', $Sentinel, 'Process')
            $Reached = New-Object System.Collections.ArrayList
            $Failure = $null
            $OutsidePath = Join-Path (Split-Path -Parent $RepoRoot) 'outside.db'
            try {
                Invoke-WithDemoEnvironment -DatabasePath $OutsidePath -Action {
                    param($Configuration)
                    [void]$Reached.Add($Configuration.DatabasePath)
                }
            }
            catch {
                $Failure = $_.Exception.Message
            }
            [pscustomobject]@{
                Failure = $Failure
                Reached = @($Reached)
                DatabaseUrl = [Environment]::GetEnvironmentVariable(
                    'CAMPUSVOICE_DATABASE_URL',
                    'Process'
                )
            } | ConvertTo-Json -Depth 5 -Compress
        }
        finally {
            Restore-ProcessEnvironment $OuterSnapshot
        }
        """,
    )

    assert "must stay directly inside" in payload["Failure"]
    assert "ambient/never-use" not in payload["Failure"]
    assert payload["Reached"] == []
    assert payload["DatabaseUrl"] == "campusvoice-invalid://ambient/never-use"

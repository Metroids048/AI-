param(
    [int]$ApiPort = 8016,
    [int]$FrontendPort = 5173,
    [string]$DatabasePath = ".local_paper_console.db",
    [bool]$OpenBrowser = $true,
    [ValidateSet("v2_shadow", "v2_active")]
    [string]$AutomatedTradingEngine = "v2_shadow",
    [switch]$EnableNaturalTestnet,
    [switch]$PreserveExternalTestnetBaseline
)

# One-click launcher - same pattern as 辅助面试/scripts/launch-experience.ps1
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

$ApiHealthUrl = "http://127.0.0.1:$ApiPort/health"
$FrontendUrl = "http://127.0.0.1:$FrontendPort/trading"
$LogsDir = Join-Path $Root "logs"
$ApiLog = Join-Path $LogsDir "api.log"
$FrontendLog = Join-Path $LogsDir "frontend.log"
$StartupLog = Join-Path $LogsDir "startup-last.log"
$ApiPidFile = Join-Path $LogsDir "api.pid"
$SchedulerPidFile = Join-Path $LogsDir "scheduler.pid"
$SchedulerStateFile = Join-Path $LogsDir "scheduler-state.json"
$SchedulerLog = Join-Path $LogsDir "scheduler.log"
$SchedulerErrorLog = Join-Path $LogsDir "scheduler-error.log"
$MicrostructurePidFile = Join-Path $LogsDir "microstructure.pid"
$MicrostructureLog = Join-Path $LogsDir "microstructure.log"
$MicrostructureErrorLog = Join-Path $LogsDir "microstructure-error.log"
$FrontendPidFile = Join-Path $LogsDir "frontend.pid"
$DbPath = Join-Path $Root $DatabasePath
$SqliteUrl = "sqlite:///$($DbPath.Replace('\', '/'))"
$StartupResultPath = Join-Path $LogsDir "startup-result.json"
$script:StartupStage = "INITIALIZING"
$script:ProjectionRecoveryPending = $false
$script:ProjectionRecoveryGap = ""
$script:ProjectionRecoveryBootstrap = $false
$script:StartupRecoveryResult = "NOT_REQUIRED"
$script:StartupSafetyStop = $false

$env:NO_PROXY = "127.0.0.1,localhost"
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""

function Test-EndpointReady([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -Proxy $null
        return $resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Write-Step([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [paper-console] $Message"
    Write-Host $line
    Add-Content -LiteralPath $StartupLog -Value $line -Encoding utf8
}

function Reset-LogFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType File -Path $Path | Out-Null
        return
    }
    try {
        [System.IO.File]::WriteAllText($Path, "")
    }
    catch {
        Write-Host "Log file is in use, continuing without reset: $Path"
    }
}

function Stop-RecordedProcess([string]$PidFile, [int]$Port) {
    if (Test-Path -LiteralPath $PidFile) {
        $recordedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
        if ($recordedPid -match '^\d+$') {
            $processInfo = Get-Process -Id ([int]$recordedPid) -ErrorAction SilentlyContinue
            if ($processInfo) {
                Write-Step "stopping prior launcher process on port $Port (pid $recordedPid)"
                Stop-ProcessTree -RootPid ([int]$recordedPid)
                Start-Sleep -Milliseconds 500
            }
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }

    $listeners = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $owner = $listeners | Select-Object -First 1 -ExpandProperty OwningProcess
        $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
        $commandLine = [string]$ownerInfo.CommandLine
        if ($commandLine -match "apps\.api\.(main|local_server)" -or $commandLine -match "vite\.js") {
            Write-Step "removing legacy project listener on port $Port (pid $owner)"
            Stop-ProcessTree -RootPid ([int]$owner)
            Start-Sleep -Milliseconds 500
        }
        else {
            throw "Port $Port is already in use by pid $owner. The launcher will not stop an unrecorded process."
        }
    }
}

function Stop-ProcessTree([int]$RootPid) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootPid" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootPid $child.ProcessId
    }
    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Save-ListenerPid([int]$Port, [string]$PidFile) {
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) {
        throw "No listener was found on port $Port after startup."
    }
    Set-Content -LiteralPath $PidFile -Value $listener.OwningProcess -Encoding ascii
}

function Test-ProjectListener([int]$Port) {
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) { return $false }
    $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $commandLine = [string]$ownerInfo.CommandLine
    if ($commandLine -match "apps\.api\.(main|local_server)") {
        return $commandLine -match "--local-console"
    }
    # Frontend vite: accept this repo path (AI--main). Old hardcode "量化项目" never matched and
    # forced a full DB re-prep (~50s) on every re-launch even when the console was already up.
    $rootEscaped = [regex]::Escape($Root)
    return ($commandLine -match "vite\.js") -and (
        $commandLine -match $rootEscaped -or
        $commandLine -match 'frontend[/\\]+admin' -or
        $commandLine -match "量化项目"
    )
}

function Stop-RecordedScheduler {
    if (Test-Path -LiteralPath $SchedulerPidFile) {
        $recordedPid = (Get-Content -LiteralPath $SchedulerPidFile -Raw).Trim()
        if ($recordedPid -match '^\d+$') {
            $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $recordedPid" -ErrorAction SilentlyContinue
            if ([string]$ownerInfo.CommandLine -match "run-local-paper-scheduler\.py") {
                Write-Step "stopping prior local scheduler (pid $recordedPid)"
                Stop-ProcessTree -RootPid ([int]$recordedPid)
            }
        }
        Remove-Item -LiteralPath $SchedulerPidFile -Force -ErrorAction SilentlyContinue
    }
    # A previous launcher can leave a child scheduler alive after its recorded
    # parent exits. Reclaim every process for this exact command so only one
    # writer can publish scheduler-state.json.
    $orphanSchedulers = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            [string]$_.CommandLine -match "run-local-paper-scheduler\.py" -and
            [string]$_.CommandLine -match [regex]::Escape($Root)
        }
    foreach ($orphan in $orphanSchedulers) {
        Write-Step "stopping orphan local scheduler (pid $($orphan.ProcessId))"
        Stop-ProcessTree -RootPid ([int]$orphan.ProcessId)
    }
    # Expire same-host leases/claims owned by dead PIDs so the next launch is not
    # stuck in standby_not_leader / duplicate_slot_skipped until TTL elapses.
    try {
        & $env:AGENT_PYTHON (Join-Path $Root "scripts\reclaim_stale_scheduler_locks.py") $DbPath | ForEach-Object {
            Write-Step $_
        }
    }
    catch {
        Write-Step "warning: could not reclaim stale scheduler locks ($($_.Exception.Message))"
    }
}

function Write-StartupResult {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$ReasonCode = "",
        [string]$Detail = ""
    )
    if (-not (Test-Path -LiteralPath $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
    }
    $payload = [ordered]@{
        schema_version = 1
        status = $Status
        stage = $script:StartupStage
        reason_code = $ReasonCode
        detail = $Detail
        symbol = $script:ProjectionRecoveryGap
        safety_stop = [bool]$script:StartupSafetyStop
        automatic_recovery = $script:StartupRecoveryResult
        log_paths = [ordered]@{
            startup = $StartupLog
            api = $ApiLog
            scheduler = $SchedulerLog
            scheduler_error = $SchedulerErrorLog
        }
        recorded_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StartupResultPath -Encoding utf8
}

trap {
    $script:StartupSafetyStop = $true
    $message = $_.Exception.Message
    $reason = if ($message -match "SYSTEM_POSITION_PROJECTION_GAP") { "SYSTEM_POSITION_PROJECTION_GAP" } else { "STARTUP_FAILED" }
    $script:StartupRecoveryResult = if ($script:ProjectionRecoveryPending) { "FAILED" } else { $script:StartupRecoveryResult }
    $cleanup = Invoke-StartupSafetyStop -Reason $reason
    if ($cleanup) { $message = "$message; safety_cleanup=$cleanup" }
    Write-StartupResult -Status "FAILED" -ReasonCode $reason -Detail $message
    Write-Host "STARTUP_FAILED stage=$($script:StartupStage) reason_code=$reason safety_stop=true recovery=$($script:StartupRecoveryResult)"
    Write-Host "startup_result=$StartupResultPath"
    exit 1
}

function Stop-RecordedMicrostructure {
    if (Test-Path -LiteralPath $MicrostructurePidFile) {
        $recordedPid = (Get-Content -LiteralPath $MicrostructurePidFile -Raw).Trim()
        if ($recordedPid -match '^\d+$') {
            $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $recordedPid" -ErrorAction SilentlyContinue
            if ([string]$ownerInfo.CommandLine -match "run_microstructure_collector\.py") {
                Write-Step "stopping prior microstructure collector (pid $recordedPid)"
                Stop-ProcessTree -RootPid ([int]$recordedPid)
            }
        }
        Remove-Item -LiteralPath $MicrostructurePidFile -Force -ErrorAction SilentlyContinue
    }
    $orphans = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and [string]$_.CommandLine -match "run_microstructure_collector\.py" -and [string]$_.CommandLine -match [regex]::Escape($Root) }
    foreach ($orphan in $orphans) { Stop-ProcessTree -RootPid ([int]$orphan.ProcessId) }
}

function Invoke-StartupSafetyStop {
    param([string]$Reason = "STARTUP_FAILED")
    $cleanupErrors = [System.Collections.Generic.List[string]]::new()
    try {
        if ($AutomatedTradingEngine -eq "v2_active" -and (Test-EndpointReady $ApiHealthUrl)) {
            $body = @{ reason = "STARTUP_SAFETY_STOP:$Reason" } | ConvertTo-Json -Compress
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v2/automated-trading/controls/entry-disable" `
                -Method Post -ContentType "application/json" -Body $body -UseBasicParsing -TimeoutSec 5 -Proxy $null
        }
    }
    catch { $cleanupErrors.Add("entry_disable:$($_.Exception.Message)") }
    foreach ($action in @(
        { Stop-RecordedScheduler },
        { Stop-RecordedMicrostructure },
        { Stop-RecordedProcess $ApiPidFile $ApiPort },
        { Stop-RecordedProcess $FrontendPidFile $FrontendPort }
    )) {
        try { & $action }
        catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    $script:StartupSafetyStop = $true
    if ($cleanupErrors.Count) { return ($cleanupErrors -join " | ") }
    return "completed"
}

function Start-MicrostructureCollector {
    Stop-RecordedMicrostructure
    Reset-LogFile $MicrostructureLog
    Reset-LogFile $MicrostructureErrorLog
    $collectorScript = Join-Path $PSScriptRoot "run_microstructure_collector.py"
    $pythonExecutable = $env:AGENT_PYTHON
    $collectorProcess = Start-Process -FilePath $pythonExecutable `
        -ArgumentList @($collectorScript, "--database-url", $SqliteUrl, "--interval-seconds", "1") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $MicrostructureLog -RedirectStandardError $MicrostructureErrorLog -PassThru
    Set-Content -LiteralPath $MicrostructurePidFile -Value $collectorProcess.Id -Encoding ascii
    Write-Step "microstructure collector started (pid $($collectorProcess.Id))"
}

function Test-SchedulerHealthy {
    if (-not (Test-Path -LiteralPath $SchedulerPidFile) -or -not (Test-Path -LiteralPath $SchedulerStateFile)) {
        return $false
    }
    $schedulerPid = (Get-Content -LiteralPath $SchedulerPidFile -Raw).Trim()
    if ($schedulerPid -notmatch '^\d+$' -or -not (Get-Process -Id ([int]$schedulerPid) -ErrorAction SilentlyContinue)) {
        return $false
    }
    try {
        $state = Get-Content -LiteralPath $SchedulerStateFile -Raw | ConvertFrom-Json
        if (-not $state.running -or -not $state.heartbeat_at) { return $false }
        $heartbeat = [datetimeoffset]::Parse($state.heartbeat_at)
        return ((([datetimeoffset]::UtcNow - $heartbeat).TotalSeconds) -le 120)
    }
    catch {
        return $false
    }
}

function Assert-ActiveTradingModeContract {
    if ($AutomatedTradingEngine -ne "v2_active") {
        return
    }
    $contractOutput = & $env:AGENT_PYTHON -m services.execution.runtime_state `
        --state-path $SchedulerStateFile `
        --requested-engine $AutomatedTradingEngine `
        --require-active-contract 2>&1
    if ($LASTEXITCODE -ne 0) {
        $detail = ($contractOutput | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ }) -join "; "
        throw "ACTIVE Trading Mode Contract failed: $detail"
    }
    Write-Step "ACTIVE Trading Mode Contract verified"
}

function Disable-EntryForProjectionRecovery {
    $script:StartupStage = "PROJECTION_RECOVERY_ENTRY_DISABLED"
    $body = @{ reason = "STARTUP_PROJECTION_RECOVERY" } | ConvertTo-Json -Compress
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v2/automated-trading/controls/entry-disable" `
        -Method Post -ContentType "application/json" -Body $body -UseBasicParsing -TimeoutSec 5 -Proxy $null
    Write-Step "entry disabled while exact V2 projection gaps are recovered"
}

function Complete-ProjectionRecovery {
    if (-not $script:ProjectionRecoveryPending) {
        return
    }
    $script:StartupStage = "PROJECTION_RECOVERY"
    Write-Step "recovering exact V2 projection gap(s): $($script:ProjectionRecoveryGap)"
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        $captureOutput = & $env:AGENT_PYTHON (Join-Path $Root "scripts\capture_testnet_external_baseline.py") --capture-persisted --json 2>&1
        if ($LASTEXITCODE -eq 0 -and $captureOutput) {
            $baselineJson = ($captureOutput | Select-Object -Last 1).ToString().Trim()
            $env:V2_EXTERNAL_BASELINE_JSON = $baselineJson
            $env:V2_EXTERNAL_BASELINE_SOURCE = "persistent_file:$($env:V2_EXTERNAL_BASELINE_PATH)"
            Remove-Item Env:V2_PROJECTION_RECOVERY_BOOTSTRAP -ErrorAction SilentlyContinue
            $script:ProjectionRecoveryPending = $false
            $script:ProjectionRecoveryBootstrap = $false
            $script:StartupRecoveryResult = "SUCCEEDED"
            $script:StartupStage = "PROJECTION_RECOVERY_COMPLETE"
            Write-Step "projection recovery succeeded; persisted external baseline refreshed: $baselineJson"
            # The recovery scheduler inherited the temporary bootstrap
            # environment when it was spawned.  Restart it after persistence so
            # the next runtime process receives the durable baseline source and
            # cannot continue publishing stale recovery state.
            $script:StartupStage = "PROJECTION_RECOVERY_SCHEDULER_RESTART"
            Stop-RecordedScheduler
            Remove-Item -LiteralPath $SchedulerStateFile -Force -ErrorAction SilentlyContinue
            Reset-LogFile $SchedulerLog
            Reset-LogFile $SchedulerErrorLog
            $schedulerScript = Join-Path $PSScriptRoot "run-local-paper-scheduler.py"
            $schedulerProcess = Start-Process -FilePath $env:AGENT_PYTHON `
                -ArgumentList @($schedulerScript, "--database-url", $SqliteUrl, "--engine", $AutomatedTradingEngine) `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -RedirectStandardOutput $SchedulerLog `
                -RedirectStandardError $SchedulerErrorLog `
                -PassThru
            Set-Content -LiteralPath $SchedulerPidFile -Value $schedulerProcess.Id -Encoding ascii
            $schedulerDeadline = (Get-Date).AddSeconds(30)
            while (-not (Test-SchedulerHealthy) -and (Get-Date) -lt $schedulerDeadline) {
                Start-Sleep -Seconds 1
            }
            if (-not (Test-SchedulerHealthy)) {
                throw "Projection recovery scheduler restart failed. See $SchedulerLog"
            }
            $body = @{ reason = "STARTUP_PROJECTION_RECOVERY_COMPLETE" } | ConvertTo-Json -Compress
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v2/automated-trading/controls/entry-enable" `
                -Method Post -ContentType "application/json" -Body $body -UseBasicParsing -TimeoutSec 5 -Proxy $null
            Write-Step "entry re-enabled after projection recovery"
            # RuntimeScheduler resolves entry authorization during start-up and
            # keeps that snapshot for its published state. Restart once more
            # after enabling the control so the published contract reflects the
            # durable control value rather than the temporary recovery pause.
            $script:StartupStage = "PROJECTION_RECOVERY_FINAL_SCHEDULER_RESTART"
            Stop-RecordedScheduler
            Remove-Item -LiteralPath $SchedulerStateFile -Force -ErrorAction SilentlyContinue
            Reset-LogFile $SchedulerLog
            Reset-LogFile $SchedulerErrorLog
            $schedulerProcess = Start-Process -FilePath $env:AGENT_PYTHON `
                -ArgumentList @($schedulerScript, "--database-url", $SqliteUrl, "--engine", $AutomatedTradingEngine) `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -RedirectStandardOutput $SchedulerLog `
                -RedirectStandardError $SchedulerErrorLog `
                -PassThru
            Set-Content -LiteralPath $SchedulerPidFile -Value $schedulerProcess.Id -Encoding ascii
            $finalSchedulerDeadline = (Get-Date).AddSeconds(30)
            while (-not (Test-SchedulerHealthy) -and (Get-Date) -lt $finalSchedulerDeadline) {
                Start-Sleep -Seconds 1
            }
            if (-not (Test-SchedulerHealthy)) {
                throw "Final projection recovery scheduler restart failed. See $SchedulerLog"
            }
            $contractDeadline = (Get-Date).AddSeconds(30)
            while (-not (Test-ActiveTradingModeContract) -and (Get-Date) -lt $contractDeadline) {
                Start-Sleep -Seconds 1
            }
            if (-not (Test-ActiveTradingModeContract)) {
                throw "Projection recovery completed but the restarted scheduler did not publish an active trading contract. See $SchedulerStateFile"
            }
            return
        }
        Start-Sleep -Seconds 5
    }
    $script:StartupSafetyStop = $true
    throw "Projection recovery timed out; persisted baseline was not refreshed. See $SchedulerLog"
}

function Test-ActiveTradingModeContract {
    if ($AutomatedTradingEngine -ne "v2_active") {
        return $true
    }
    & $env:AGENT_PYTHON -m services.execution.runtime_state `
        --state-path $SchedulerStateFile `
        --requested-engine $AutomatedTradingEngine `
        --require-active-contract *> $null
    return $LASTEXITCODE -eq 0
}

function Stop-ProjectApiProcesses {
    $apiProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            [string]$_.CommandLine -match "apps\.api\.(main|local_server)" -and
            [string]$_.CommandLine -match "--local-console" -and
            [string]$_.CommandLine -match "--port\s+$ApiPort"
        }
    foreach ($apiProcess in $apiProcesses) {
        Write-Step "stopping orphan local API (pid $($apiProcess.ProcessId))"
        Stop-ProcessTree -RootPid ([int]$apiProcess.ProcessId)
    }
}

function Open-Frontend([string]$Url) {
    foreach ($browser in @("msedge.exe", "chrome.exe")) {
        $command = Get-Command $browser -ErrorAction SilentlyContinue
        if ($command) {
            Start-Process -FilePath $command.Source -ArgumentList $Url | Out-Null
            return $true
        }
    }
    Start-Process $Url | Out-Null
    return $true
}

function Ensure-Runtime {
    $script:StartupStage = "RUNTIME_PREPARE"
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "Node.js/npm not found."
    }
    if (-not (Test-Path -LiteralPath $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir | Out-Null
    }
    Reset-LogFile $StartupLog
    Reset-LogFile $ApiLog
    Reset-LogFile $FrontendLog

    if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
        Write-Step "checking Python environment"
        $ensureScript = Join-Path $PSScriptRoot "ensure-venv-ready.ps1"
        $env:AGENT_PYTHON = & $ensureScript
        if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
            throw "Failed to prepare Python environment"
        }
        Write-Step "✓ Python environment ready: $env:AGENT_PYTHON"
    }

    Write-Step "checking frontend dependency versions"
    npm ls --workspace frontend/admin --depth=0 *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "frontend dependencies are missing or stale; running npm install"
        npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed. See npm output above."
        }
    }
    . (Join-Path $PSScriptRoot "load-dotenv.ps1")
    $envPath = Join-Path $Root ".env"
    if (Test-Path -LiteralPath $envPath) {
        Import-DotEnv $envPath | Out-Null
    }
    if (-not $env:BINANCE_HTTPS_PROXY -and $env:HTTPS_PROXY) {
        $env:BINANCE_HTTPS_PROXY = $env:HTTPS_PROXY
    }
    if (-not $env:BINANCE_HTTPS_PROXY) {
        $env:PAPER_CONSOLE_DISABLE_LIVE_WS = "true"
        $env:PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP = "true"
        Write-Step "no Binance proxy configured; disabling live WebSocket collectors for startup stability"
    }
    else {
        $env:PAPER_CONSOLE_DISABLE_LIVE_WS = "false"
        $env:PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP = "false"
    }
    $env:POSTGRES_URL = $SqliteUrl
    $env:VITE_API_BASE_URL = "http://127.0.0.1:$ApiPort"
    $env:VITE_ADMIN_API_TOKEN = "dev-admin-token"
    $env:CORS_ALLOWED_ORIGINS = "http://127.0.0.1:$FrontendPort,http://localhost:$FrontendPort"
    $env:APP_ENV = "development"
    $env:BINANCE_USE_TESTNET = "true"
    $env:LIVE_TRADING_ENABLED = "false"
    # Keep the safe default visible in source and only opt into active mode
    # when the caller explicitly requests it.
    $env:AUTOMATED_TRADING_ENGINE = "v2_shadow"
    if ($AutomatedTradingEngine -eq "v2_active") {
        $env:AUTOMATED_TRADING_ENGINE = $AutomatedTradingEngine
    }
    if ($AutomatedTradingEngine -eq "v2_active" -and -not $EnableNaturalTestnet) {
        throw "v2_active requires -EnableNaturalTestnet; daily launcher remains Shadow by default."
    }
    if ($EnableNaturalTestnet) {
        # Arm only the explicit Gate 17 observer authorization. This flag does
        # not enable order submission; V2_ACTIVE and the persisted entry gate
        # remain the independent execution controls.
        $env:V2_NATURAL_E2E_ENABLED = "true"
    }
    else {
        Remove-Item Env:V2_NATURAL_E2E_ENABLED -ErrorAction SilentlyContinue
    }
    if ($PreserveExternalTestnetBaseline -and $AutomatedTradingEngine -ne "v2_active") {
        throw "-PreserveExternalTestnetBaseline is only valid with v2_active."
    }
    if ($PreserveExternalTestnetBaseline) {
        $env:V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS = "true"
        $env:V2_EXTERNAL_BASELINE_PATH = Join-Path $Root ".local\testnet-external-baseline.json"
        if (-not $script:ProjectionRecoveryPending) {
            Remove-Item Env:V2_EXTERNAL_BASELINE_JSON -ErrorAction SilentlyContinue
            Remove-Item Env:V2_EXTERNAL_BASELINE_SOURCE -ErrorAction SilentlyContinue
        }
        # On first launch, the persisted baseline file does not exist yet. Capture it now
        # so that --require-persisted can verify the current exposure matches the durable record.
        if (-not (Test-Path $env:V2_EXTERNAL_BASELINE_PATH)) {
            Write-Step "capturing initial Testnet external baseline"
            $captureOutput = & $env:AGENT_PYTHON (Join-Path $Root "scripts\capture_testnet_external_baseline.py") --capture-persisted --json 2>&1
            if ($LASTEXITCODE -ne 0) {
                $captureError = ($captureOutput | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ }) -join "; "
                if ($captureError -match "SYSTEM_POSITION_PROJECTION_GAP") {
                    $bootstrap = & $env:AGENT_PYTHON (Join-Path $Root "scripts\capture_testnet_external_baseline.py") --bootstrap-projection-recovery --json 2>&1
                    if ($LASTEXITCODE -ne 0 -or -not $bootstrap) {
                        throw "Failed to bootstrap projection recovery baseline: $captureError"
                    }
                    $env:V2_EXTERNAL_BASELINE_JSON = ($bootstrap | Select-Object -Last 1).ToString().Trim()
                    $env:V2_EXTERNAL_BASELINE_SOURCE = "projection_gap_recovery_bootstrap"
                    $env:V2_PROJECTION_RECOVERY_BOOTSTRAP = "true"
                    $script:ProjectionRecoveryPending = $true
                    $script:ProjectionRecoveryBootstrap = $true
                    $script:ProjectionRecoveryGap = $captureError
                    $script:StartupRecoveryResult = "PENDING"
                    Write-Step "projection gap detected; using temporary recovery bootstrap baseline"
                }
                else {
                    throw "Failed to capture the initial Testnet external position baseline: $captureError"
                }
            }
        }
        if (-not $script:ProjectionRecoveryPending) {
            # Manual exposure may legitimately change after capture.  Inspect
            # the lifecycle read-only and carry the acknowledgement state into
            # Runtime Truth; only V2 projection gaps remain startup-fatal.
            $lifecycleOutput = & $env:AGENT_PYTHON (Join-Path $Root "scripts\capture_testnet_external_baseline.py") --inspect-lifecycle --json 2>&1
            if ($LASTEXITCODE -eq 0 -and $lifecycleOutput) {
                try {
                    $lifecycle = ($lifecycleOutput | Select-Object -Last 1).ToString().Trim() | ConvertFrom-Json
                    if ($lifecycle.status -eq "MANUAL_BASELINE_DRIFT") {
                        $env:V2_EXTERNAL_BASELINE_LIFECYCLE = "MANUAL_BASELINE_ACK_REQUIRED"
                        $env:V2_EXTERNAL_BASELINE_DRIFT_KEYS = (($lifecycle.drift_keys | ConvertTo-Json -Compress))
                        Write-Step "manual baseline drift detected; entries remain symbol-scoped until operator acknowledgement"
                    }
                    else {
                        Remove-Item Env:V2_EXTERNAL_BASELINE_LIFECYCLE -ErrorAction SilentlyContinue
                        Remove-Item Env:V2_EXTERNAL_BASELINE_DRIFT_KEYS -ErrorAction SilentlyContinue
                    }
                }
                catch { Write-Step "manual baseline lifecycle output could not be parsed; preserving fail-closed baseline" }
            }
            $baselineOutput = & $env:AGENT_PYTHON (Join-Path $Root "scripts\capture_testnet_external_baseline.py") --require-persisted --allow-manual-drift --json 2>&1
            if ($LASTEXITCODE -ne 0 -or -not $baselineOutput) {
                $baselineError = ($baselineOutput | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ }) -join "; "
                if ($baselineError -match "SYSTEM_POSITION_PROJECTION_GAP") {
                    $bootstrap = & $env:AGENT_PYTHON (Join-Path $Root "scripts\capture_testnet_external_baseline.py") --bootstrap-projection-recovery --json 2>&1
                    if ($LASTEXITCODE -ne 0 -or -not $bootstrap) {
                        throw "Failed to bootstrap projection recovery baseline: $baselineError"
                    }
                    $env:V2_EXTERNAL_BASELINE_JSON = ($bootstrap | Select-Object -Last 1).ToString().Trim()
                    $env:V2_EXTERNAL_BASELINE_SOURCE = "projection_gap_recovery_bootstrap"
                    $env:V2_PROJECTION_RECOVERY_BOOTSTRAP = "true"
                    $script:ProjectionRecoveryPending = $true
                    $script:ProjectionRecoveryBootstrap = $true
                    $script:ProjectionRecoveryGap = $baselineError
                    $script:StartupRecoveryResult = "PENDING"
                    Write-Step "persisted baseline exposes projection gap; using temporary recovery bootstrap baseline"
                }
                else {
                    throw "Failed to verify the persisted Testnet external position baseline: $baselineError"
                }
            }
            else {
                $env:V2_EXTERNAL_BASELINE_JSON = ($baselineOutput | Select-Object -Last 1).ToString().Trim()
                $env:V2_EXTERNAL_BASELINE_SOURCE = "persistent_file:$($env:V2_EXTERNAL_BASELINE_PATH)"
                Remove-Item Env:V2_PROJECTION_RECOVERY_BOOTSTRAP -ErrorAction SilentlyContinue
                Write-Step "restored persisted Testnet external baseline: $($env:V2_EXTERNAL_BASELINE_JSON)"
            }
        }
    }
    else {
        Remove-Item Env:V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS -ErrorAction SilentlyContinue
        Remove-Item Env:V2_EXTERNAL_BASELINE_JSON -ErrorAction SilentlyContinue
        Remove-Item Env:V2_EXTERNAL_BASELINE_SOURCE -ErrorAction SilentlyContinue
        Remove-Item Env:V2_EXTERNAL_BASELINE_PATH -ErrorAction SilentlyContinue
        Remove-Item Env:V2_EXTERNAL_BASELINE_LIFECYCLE -ErrorAction SilentlyContinue
        Remove-Item Env:V2_EXTERNAL_BASELINE_DRIFT_KEYS -ErrorAction SilentlyContinue
        Remove-Item Env:V2_PROJECTION_RECOVERY_BOOTSTRAP -ErrorAction SilentlyContinue
    }
    $env:RUNTIME_SCHEDULER_MODE = "inprocess"
    $env:RUNTIME_SCHEDULER_AUTOSTART = "true"
    $env:BINANCE_LIVE_UNIVERSE_ENABLED = "true"
    $env:BINANCE_LIVE_MARKET_ENABLED = "true"
    $env:BINANCE_LIVE_WS_ENABLED = if ($env:PAPER_CONSOLE_DISABLE_LIVE_WS -eq "true") { "false" } else { "true" }
    # The isolated scheduler owns background work; foreground market endpoints
    # must still be allowed to read Binance REST data for the trading console.
    $env:PAPER_CONSOLE_API_ONLY = "true"
    Remove-Item Env:VITE_LOCAL_CONSOLE_API_ONLY -ErrorAction SilentlyContinue
    Write-Step "starting isolated Paper scheduler; Testnet mirror remains cost-gated"
}

function Initialize-LocalDatabase {
    Write-Step "preparing local database"
    & $env:AGENT_PYTHON scripts/prepare_database.py --database-url $SqliteUrl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Local database preparation failed; API will not start against an incomplete schema." }
}

$apiReady = Test-EndpointReady $ApiHealthUrl
$frontendReady = Test-EndpointReady "http://127.0.0.1:$FrontendPort/"

# The API loads AUTOMATED_TRADING_ENGINE at process start. A v2_active request
# must never reuse an API that was started in shadow/legacy mode.
if ($AutomatedTradingEngine -eq "v2_active" -and $apiReady) {
    Ensure-Runtime
    Stop-ProjectApiProcesses
    Stop-RecordedProcess $ApiPidFile $ApiPort
    $apiReady = $false
}

$activeContractReady = $AutomatedTradingEngine -ne "v2_active" -or (Test-ActiveTradingModeContract)
if ($apiReady -and $frontendReady -and (Test-ProjectListener $ApiPort) -and (Test-ProjectListener $FrontendPort) -and $activeContractReady) {
    if (-not (Test-SchedulerHealthy)) {
        Ensure-Runtime
        Stop-RecordedScheduler
        Remove-Item -LiteralPath $SchedulerStateFile -Force -ErrorAction SilentlyContinue
        Reset-LogFile $SchedulerLog
        Reset-LogFile $SchedulerErrorLog
        $schedulerScript = Join-Path $PSScriptRoot "run-local-paper-scheduler.py"
        $schedulerProcess = Start-Process -FilePath $env:AGENT_PYTHON `
            -ArgumentList @($schedulerScript, "--database-url", $SqliteUrl, "--engine", $AutomatedTradingEngine) `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $SchedulerLog `
            -RedirectStandardError $SchedulerErrorLog `
            -PassThru
        Set-Content -LiteralPath $SchedulerPidFile -Value $schedulerProcess.Id -Encoding ascii
        Start-Sleep -Seconds 2
        if (-not (Test-SchedulerHealthy)) {
            throw "Paper scheduler failed its startup health check. See $SchedulerLog"
        }
    }
    Start-MicrostructureCollector
    Assert-ActiveTradingModeContract
    if (-not (Test-Path -LiteralPath $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }
    if (-not (Test-Path -LiteralPath $StartupLog)) { New-Item -ItemType File -Path $StartupLog | Out-Null }
    Write-Step "paper console already running"
    Save-ListenerPid $ApiPort $ApiPidFile
    Save-ListenerPid $FrontendPort $FrontendPidFile
    Write-Step "frontend: $FrontendUrl"
    if ($OpenBrowser) {
        Write-Step "opening browser"
        [void](Open-Frontend $FrontendUrl)
    }
    $script:StartupStage = "READY"
    Write-StartupResult -Status "SUCCESS" -ReasonCode "STARTUP_READY" -Detail "ACTIVE runtime and recovery chain are healthy"
    exit 0
}

Ensure-Runtime
Initialize-LocalDatabase

if (-not $apiReady) { Stop-RecordedProcess $ApiPidFile $ApiPort }
if (-not $frontendReady) { Stop-RecordedProcess $FrontendPidFile $FrontendPort }

if (-not $apiReady) {
    $script:StartupStage = "API_START"
    Write-Step "starting API http://127.0.0.1:$ApiPort"
    # Start Uvicorn directly. PowerShell wrapper processes can retain inherited
    # handles and make the Windows ASGI server accept connections without serving them.
    $apiProcess = Start-Process -FilePath $env:AGENT_PYTHON `
        -ArgumentList @("-m", "apps.api.local_server", "--host", "127.0.0.1", "--port", $ApiPort, "--log-level", "warning", "--local-console") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $ApiPidFile -Value $apiProcess.Id -Encoding ascii
}

if ($script:ProjectionRecoveryPending) {
    $apiDeadline = (Get-Date).AddSeconds(30)
    while (-not (Test-EndpointReady $ApiHealthUrl) -and (Get-Date) -lt $apiDeadline) {
        Start-Sleep -Seconds 1
    }
    if (-not (Test-EndpointReady $ApiHealthUrl)) {
        throw "Projection recovery could not start because the API health endpoint is unavailable"
    }
    Disable-EntryForProjectionRecovery
}

$script:StartupStage = "SCHEDULER_START"
Write-Step "scheduler baseline source=$($env:V2_EXTERNAL_BASELINE_SOURCE) bootstrap=$($env:V2_PROJECTION_RECOVERY_BOOTSTRAP)"
Stop-RecordedScheduler
Remove-Item -LiteralPath $SchedulerStateFile -Force -ErrorAction SilentlyContinue
Reset-LogFile $SchedulerLog
Reset-LogFile $SchedulerErrorLog
$schedulerScript = Join-Path $PSScriptRoot "run-local-paper-scheduler.py"
$schedulerProcess = Start-Process -FilePath $env:AGENT_PYTHON `
    -ArgumentList @($schedulerScript, "--database-url", $SqliteUrl, "--engine", $AutomatedTradingEngine) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $SchedulerLog `
    -RedirectStandardError $SchedulerErrorLog `
    -PassThru
Set-Content -LiteralPath $SchedulerPidFile -Value $schedulerProcess.Id -Encoding ascii
Start-MicrostructureCollector

if (-not $frontendReady) {
    Write-Step "starting frontend http://127.0.0.1:$FrontendPort"
    $frontendCmd = "npm --workspace frontend/admin run dev -- --host 127.0.0.1 --port $FrontendPort >> `"$FrontendLog`" 2>&1"
    $frontendProcess = Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/s", "/c", $frontendCmd) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $FrontendPidFile -Value $frontendProcess.Id -Encoding ascii
}

Write-Step "waiting for services (up to 90s)"
$deadline = (Get-Date).AddSeconds(90)
$lastProgressAt = Get-Date
while ((Get-Date) -lt $deadline) {
    if (-not $apiReady) { $apiReady = Test-EndpointReady $ApiHealthUrl }
    if (-not $frontendReady) { $frontendReady = Test-EndpointReady "http://127.0.0.1:$FrontendPort/" }
    if ($apiReady -and $frontendReady) { break }
    if (((Get-Date) - $lastProgressAt).TotalSeconds -ge 10) {
        $apiState = if ($apiReady) { "up" } else { "waiting" }
        $feState = if ($frontendReady) { "up" } else { "waiting" }
        Write-Step "still waiting (api=$apiState frontend=$feState)"
        $lastProgressAt = Get-Date
    }
    Start-Sleep -Seconds 1
}

if ($apiReady -and $frontendReady) {
    $schedulerDeadline = (Get-Date).AddSeconds(30)
    while (
        ((-not (Test-SchedulerHealthy)) -or (-not (Test-ActiveTradingModeContract))) -and
        (Get-Date) -lt $schedulerDeadline
    ) {
        Start-Sleep -Seconds 1
    }
    if (-not (Test-SchedulerHealthy)) {
        if ($AutomatedTradingEngine -eq "v2_active" -and (Test-Path -LiteralPath $SchedulerStateFile)) {
            Assert-ActiveTradingModeContract
        }
        throw "Paper scheduler failed its startup health check. See $SchedulerLog"
    }
    Complete-ProjectionRecovery
    if ($AutomatedTradingEngine -eq "v2_active") {
        Assert-ActiveTradingModeContract
    }
    Save-ListenerPid $ApiPort $ApiPidFile
    Save-ListenerPid $FrontendPort $FrontendPidFile
    Write-Step "services ready"
    Write-Step "frontend: $FrontendUrl"
    Write-Step "API: http://127.0.0.1:$ApiPort"
    if ($OpenBrowser) {
        Write-Step "opening browser"
        [void](Open-Frontend $FrontendUrl)
    }
    $script:StartupStage = "READY"
    Write-StartupResult -Status "SUCCESS" -ReasonCode "STARTUP_READY" -Detail "ACTIVE runtime and recovery chain are healthy"
    exit 0
}

Write-Step "startup failed"
Write-Step "check logs: $ApiLog ; $FrontendLog"
Invoke-StartupSafetyStop -Reason "STARTUP_SERVICES_NOT_READY" | Out-Null
Remove-Item -LiteralPath $ApiPidFile,$FrontendPidFile -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $ApiLog) {
    Write-Host "--- api.log (tail) ---"
    Get-Content -LiteralPath $ApiLog -Tail 15 -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $FrontendLog) {
    Write-Host "--- frontend.log (tail) ---"
    Get-Content -LiteralPath $FrontendLog -Tail 15 -ErrorAction SilentlyContinue
}
Write-Step "browser was not opened because startup did not finish"
Write-StartupResult -Status "FAILED" -ReasonCode "STARTUP_SERVICES_NOT_READY" -Detail "API or frontend did not become ready"
exit 1

param(
    [int]$ApiPort = 8000,
    [string]$DatabasePath = ".local_paper_console.db"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Logs = Join-Path $Root "logs"
$DatabaseFullPath = Join-Path $Root $DatabasePath
$SqliteUrl = "sqlite:///$($DatabaseFullPath.Replace('\', '/'))"
$ApiUrl = "http://127.0.0.1:$ApiPort"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Write-Step($Message) {
    Write-Host "[paper-engine] $Message"
}

function Test-HttpOk($Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 2
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Wait-HttpOk($Url, $Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-HttpOk $Url) {
            return $true
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Stop-ExistingProjectProcess($Port, $ExpectedPattern) {
    $connections = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        $ownerPid = $connection.OwningProcess
        if (-not $ownerPid -or $ownerPid -eq $PID) {
            continue
        }
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
        $commandLine = $processInfo.CommandLine
        if ($commandLine -and (
            $commandLine.Contains($Root) -or
            $commandLine.Contains($ExpectedPattern) -or
            $commandLine.Contains("uvicorn")
        )) {
            Write-Step "stopping previous project process on port $Port (pid $ownerPid)"
            Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
    }
}

function Show-LogTail($Path, $Lines = 40) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Step "log file missing: $Path"
        return
    }
    Write-Host ""
    Write-Host "---- tail $Path ----"
    Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue
    Write-Host "--------------------"
}

. (Join-Path $PSScriptRoot "load-dotenv.ps1")
$envPath = Join-Path $Root ".env"
if (Test-Path -LiteralPath $envPath) {
    Import-DotEnv $envPath | Out-Null
}

Set-Location $Root
Write-Step "initializing local Paper database: $DatabasePath"
$env:POSTGRES_URL = $SqliteUrl
$env:LLM_USE_CATALOG_SEEDS_ONLY = if ($env:LLM_USE_CATALOG_SEEDS_ONLY) { $env:LLM_USE_CATALOG_SEEDS_ONLY } else { "true" }
py -3 -c "from services.database import create_relational_schema, get_engine, reset_database_caches; from services.data.repository import create_timeseries_schema; reset_database_caches(); create_relational_schema(); create_timeseries_schema(get_engine()); print('schema ready')"

Stop-ExistingProjectProcess $ApiPort "apps.api.main:app"

Write-Step "starting 7x24 trading engine (API + scheduler only, no frontend)"
$runApiScript = Join-Path $PSScriptRoot "run-api-local.ps1"
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $runApiScript,
    "-Root", $Root,
    "-PostgresUrl", $SqliteUrl,
    "-Port", $ApiPort,
    "-LogPath", (Join-Path $Logs "api.log")
) -WindowStyle Hidden

Write-Step "waiting for API"
if (-not (Wait-HttpOk "$ApiUrl/health" 120)) {
    Show-LogTail (Join-Path $Logs "api.log")
    throw "Trading engine did not become ready. See logs/api.log"
}

Write-Host ""
Write-Host "Paper trading engine is running 7x24:"
Write-Host "  API:       $ApiUrl"
Write-Host "  Scheduler: in-process auto-cycle (carry + directional Top20)"
Write-Host "  Logs:      $Logs"
Write-Host ""
Write-Host "Frontend is optional. Close this window anytime — the engine keeps running."
Write-Host "Stop reasons: risk gate / manual pause only (not closing the admin UI)."
Write-Host ""
Write-Host "Optional admin UI: run 一键启动.bat or npm --workspace frontend/admin run dev"

param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [Parameter(Mandatory = $true)]
    [string]$PostgresUrl,
    [int]$Port = 8000,
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load-dotenv.ps1")
$envPath = Join-Path $Root ".env"
Import-DotEnv $envPath | Out-Null

$env:POSTGRES_URL = $PostgresUrl
$env:APP_ENV = "development"
$env:BINANCE_USE_TESTNET = "true"
$env:LIVE_TRADING_ENABLED = "false"
$env:RUNTIME_SCHEDULER_MODE = "inprocess"
$env:RUNTIME_SCHEDULER_AUTOSTART = "true"
$env:PAPER_RUNTIME_CYCLE_SECONDS = if ($env:PAPER_RUNTIME_CYCLE_SECONDS) { $env:PAPER_RUNTIME_CYCLE_SECONDS } else { "60" }
$env:BINANCE_AUTO_EXECUTE = if ($env:BINANCE_AUTO_EXECUTE) { $env:BINANCE_AUTO_EXECUTE } else { "true" }
$env:BINANCE_LIVE_UNIVERSE_ENABLED = "true"
$env:BINANCE_LIVE_MARKET_ENABLED = "true"
$env:BINANCE_LIVE_WS_ENABLED = "true"
if (-not $env:BINANCE_LIVE_WS_SYMBOLS) { $env:BINANCE_LIVE_WS_SYMBOLS = "top20" }

if (-not $env:BINANCE_SPOT_REST_BASE) { $env:BINANCE_SPOT_REST_BASE = "https://data-api.binance.vision" }
if (-not $env:BINANCE_USDM_REST_BASE) { $env:BINANCE_USDM_REST_BASE = "https://demo-fapi.binance.com" }
if (-not $env:BINANCE_SPOT_WS_BASE) { $env:BINANCE_SPOT_WS_BASE = "wss://data-stream.binance.vision/ws" }
if (-not $env:BINANCE_USDM_WS_BASE) { $env:BINANCE_USDM_WS_BASE = "wss://stream.binancefuture.com/ws" }

Set-Location $Root
if ($LogPath) {
    py -3 -m uvicorn apps.api.main:app --host 127.0.0.1 --port $Port *> $LogPath
}
else {
    py -3 -m uvicorn apps.api.main:app --host 127.0.0.1 --port $Port
}

param([switch]$Foreground)

$root = Split-Path -Parent $PSScriptRoot
$supervisor = Join-Path $root "tools\codex-strategy-supervisor\supervisor.mjs"
$logDirectory = Join-Path $root "logs"
$stdout = Join-Path $logDirectory "codex-strategy-supervisor.out.log"
$stderr = Join-Path $logDirectory "codex-strategy-supervisor.err.log"

if (-not (Test-Path -LiteralPath $supervisor)) { throw "Supervisor not found: $supervisor" }
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

if ($Foreground) {
  & node $supervisor
  exit $LASTEXITCODE
}

Start-Process -FilePath "node" -ArgumentList @($supervisor) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
Write-Output "Supervisor started. Logs: $stdout ; $stderr"

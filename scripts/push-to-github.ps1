#Requires -Version 5.1
param(
    [string]$Message = "",
    [int]$ProxyPort = 0,
    [switch]$PushOnly
)

$ErrorActionPreference = "Continue"

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$CommonProxyPorts = @(7890, 7891, 7892, 7893, 7897, 10808, 10809, 33210)

function Test-LocalPort {
    param([int]$Port)
    $tcp = $null
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $connect = $tcp.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $connect.AsyncWaitHandle.WaitOne(500, $false)
        if ($ok -and $tcp.Connected) { return $true }
    } catch { return $false }
    finally { if ($null -ne $tcp) { $tcp.Close() } }
    return $false
}

function Find-WorkingProxy {
    param([int]$PreferredPort)
    if ($PreferredPort -gt 0 -and (Test-LocalPort $PreferredPort)) { return $PreferredPort }
    foreach ($port in $CommonProxyPorts) {
        if (Test-LocalPort $port) { return $port }
    }
    foreach ($varName in @("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")) {
        $value = [Environment]::GetEnvironmentVariable($varName, "Process")
        if ($value -match "127\.0\.0\.1:(\d+)") {
            $port = [int]$Matches[1]
            if (Test-LocalPort $port) { return $port }
        }
    }
    return 0
}

function Invoke-Git {
    param([string[]]$GitArgs, [int]$ProxyPort)
    $backup = @{ HTTP_PROXY = $env:HTTP_PROXY; HTTPS_PROXY = $env:HTTPS_PROXY; ALL_PROXY = $env:ALL_PROXY }
    try {
        if ($ProxyPort -gt 0) {
            $proxyUrl = "http://127.0.0.1:$ProxyPort"
            $env:HTTP_PROXY = $proxyUrl
            $env:HTTPS_PROXY = $proxyUrl
            $env:ALL_PROXY = $proxyUrl
        } else {
            Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
            Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
            Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue
        }
        & git @GitArgs
        return $LASTEXITCODE
    } finally {
        if ($null -ne $backup.HTTP_PROXY) { $env:HTTP_PROXY = $backup.HTTP_PROXY } else { Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue }
        if ($null -ne $backup.HTTPS_PROXY) { $env:HTTPS_PROXY = $backup.HTTPS_PROXY } else { Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue }
        if ($null -ne $backup.ALL_PROXY) { $env:ALL_PROXY = $backup.ALL_PROXY } else { Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue }
    }
}

function Write-Step { param([string]$Text); Write-Host ""; Write-Host ">> $Text" -ForegroundColor Cyan }

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) { Write-Host "[ERROR] Not a git repository." -ForegroundColor Red; exit 1 }

Set-Location -LiteralPath $repoRoot
Write-Host "=== Push to GitHub ===" -ForegroundColor White
Write-Host "Repo : $repoRoot"

$remoteUrl = git remote get-url origin 2>$null
if (-not $remoteUrl) {
    Write-Host "[ERROR] Missing origin remote." -ForegroundColor Red
    Write-Host "  git remote add origin https://github.com/Metroids048/AI-.git"
    exit 1
}
Write-Host "Remote: $remoteUrl"

$branch = git rev-parse --abbrev-ref HEAD
Write-Host "Branch: $branch"

$proxyPort = Find-WorkingProxy -PreferredPort $ProxyPort
if ($proxyPort -gt 0) {
    Write-Host "Proxy: 127.0.0.1:$proxyPort (OK)" -ForegroundColor Green
} else {
    Write-Host "Proxy: none detected (7890/7892 etc.)" -ForegroundColor Yellow
    Write-Host "Hint: start Clash/V2Ray, or use -ProxyPort 7890" -ForegroundColor Yellow
}

if (-not $PushOnly) {
    Write-Step "git add -A"
    & git add -A
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $dirty = git status --porcelain
    if ($dirty) {
        if (-not $Message) { $Message = "sync: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }
        Write-Step "git commit"
        $commitCode = Invoke-Git -GitArgs @("commit", "-m", $Message) -ProxyPort $proxyPort
        if ($commitCode -ne 0) { exit $commitCode }
        Write-Host "Committed: $Message" -ForegroundColor Green
    } else {
        Write-Host "No changes, skip commit." -ForegroundColor Yellow
    }
}

$needPush = $true
$upstream = git rev-parse --abbrev-ref "@{u}" 2>$null
if ($LASTEXITCODE -eq 0 -and $upstream) {
    $ahead = git rev-list --count "@{u}..HEAD" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ahead -eq "0") { $needPush = $false }
}

if (-not $needPush) {
    Write-Host ""
    Write-Host "Already up to date with remote." -ForegroundColor Green
    exit 0
}

Write-Step "git push -u origin HEAD"
$pushModes = @()
if ($proxyPort -gt 0) { $pushModes += @{ Label = "proxy $proxyPort"; Port = $proxyPort } }
$pushModes += @{ Label = "direct"; Port = 0 }

$lastExit = 1
foreach ($mode in $pushModes) {
    Write-Host "Trying $($mode.Label) ..."
    $lastExit = Invoke-Git -GitArgs @("push", "-u", "origin", "HEAD") -ProxyPort $mode.Port
    if ($lastExit -eq 0) {
        Write-Host ""
        Write-Host "Push OK: https://github.com/Metroids048/AI-" -ForegroundColor Green
        Write-Host "Branch : $branch"
        exit 0
    }
}

Write-Host ""
Write-Host "Push failed. Check:" -ForegroundColor Red
Write-Host "  1) Proxy running (Clash/V2Ray)"
Write-Host "  2) HTTPS_PROXY=$env:HTTPS_PROXY"
Write-Host "  3) GitHub credentials / PAT"
Write-Host ""
Write-Host "Retry: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\push-to-github.ps1 -ProxyPort 7890"
exit $lastExit

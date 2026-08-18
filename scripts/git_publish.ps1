[CmdletBinding()]
param(
    [string]$Message = "chore: publish Testnet Canary runtime contract",
    [int]$MaxCommitAttempts = 3,
    [int]$MaxPushAttempts = 5,
    [int]$LargeFileBytes = 52428800
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell surfaces native stderr warnings as ErrorRecords;
        # keep those in captured output without treating harmless Git warnings
        # as a failed command.
        $ErrorActionPreference = "Continue"
        $text = (& git @Arguments 2>&1 | Out-String).Trim()
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    [pscustomobject]@{ ExitCode = $exitCode; Output = $text }
}

function Stop-Publish {
    param([string]$Reason)
    Write-Host "PUBLISH_FAILED: $Reason" -ForegroundColor Red
    exit 1
}

function Repair-GitAuth {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($null -eq $gh) { return $false }
    & gh auth status 2>&1 | Out-Host
    & gh auth setup-git 2>&1 | Out-Host
    return $true
}

$rootResult = Invoke-Git @("rev-parse", "--show-toplevel")
if ($rootResult.ExitCode -ne 0) { Stop-Publish "not inside a Git repository" }
Set-Location -LiteralPath $rootResult.Output

$gitDirResult = Invoke-Git @("rev-parse", "--git-dir")
if ($gitDirResult.ExitCode -ne 0) { Stop-Publish "could not resolve Git directory" }
$gitDir = $gitDirResult.Output.Trim()
if ((Test-Path -LiteralPath (Join-Path $gitDir "rebase-merge")) -or
    (Test-Path -LiteralPath (Join-Path $gitDir "rebase-apply"))) {
    Stop-Publish "a rebase is already in progress; resolve it before publishing"
}

$branchResult = Invoke-Git @("symbolic-ref", "--quiet", "--short", "HEAD")
if ($branchResult.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($branchResult.Output)) {
    Stop-Publish "detached HEAD is not publishable"
}
$branch = $branchResult.Output.Trim()

$originResult = Invoke-Git @("remote", "get-url", "origin")
if ($originResult.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($originResult.Output)) {
    Stop-Publish "origin remote is missing"
}

# Remove accidental worktree entries from the index while preserving every local
# worktree directory on disk. .gitignore keeps the mistake from recurring.
$worktreeResult = Invoke-Git @("rm", "--cached", "-r", "-f", "--ignore-unmatch", "--", ".claude/worktrees/")
if ($worktreeResult.ExitCode -ne 0) { Stop-Publish "could not unstage .claude/worktrees" }

# SQLite creates these transient sidecar files while the runtime is active.
# They are local recovery state, never publishable artifacts; untrack them
# without touching the live database or its sidecars on disk.
foreach ($runtimeArtifact in @(".local_paper_console.db-journal", ".local_paper_console.db-wal", ".local_paper_console.db-shm")) {
    $artifactResult = Invoke-Git @("rm", "--cached", "-f", "--ignore-unmatch", "--", $runtimeArtifact)
    if ($artifactResult.ExitCode -ne 0) { Stop-Publish "could not untrack runtime artifact $runtimeArtifact" }
}

# Generated artifacts may be untracked locally, but an unknown large file is
# never deleted or silently ignored by this publisher.
$filesResult = Invoke-Git @("ls-files", "-co", "--exclude-standard")
if ($filesResult.ExitCode -ne 0) { Stop-Publish "could not enumerate repository files" }
$knownGenerated = '(^|/)(artifacts|logs|raw[_ -]?testnet[_ -]?history)(/|$)|(^|/).*\.(db|sqlite|sqlite3)$'
foreach ($path in ($filesResult.Output -split "`r?`n")) {
    if ([string]::IsNullOrWhiteSpace($path)) { continue }
    $item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.PSIsContainer -or $item.Length -le $LargeFileBytes) { continue }
    $normalized = $path.Replace('\', '/')
    if ($normalized -match $knownGenerated) {
        $unstage = Invoke-Git @("rm", "--cached", "--ignore-unmatch", "--", $path)
        if ($unstage.ExitCode -ne 0) { Stop-Publish "could not untrack generated large file $path" }
        Write-Host "Untracked generated large file (kept locally): $path"
    } else {
        Stop-Publish "unknown large file ($([math]::Round($item.Length / 1MB, 1)) MB): $path"
    }
}

$statusBefore = Invoke-Git @("status", "--porcelain")
if ($statusBefore.ExitCode -ne 0) { Stop-Publish "could not inspect working tree" }
$commitCreated = $false
if (-not [string]::IsNullOrWhiteSpace($statusBefore.Output)) {
    for ($attempt = 1; $attempt -le [Math]::Max(1, $MaxCommitAttempts); $attempt++) {
        $add = Invoke-Git @("add", "-A")
        if ($add.ExitCode -ne 0) { Stop-Publish "git add failed: $($add.Output)" }
        $commit = Invoke-Git @("commit", "-m", $Message)
        if ($commit.ExitCode -eq 0) {
            $commitCreated = $true
            break
        }
        if ($attempt -eq [Math]::Max(1, $MaxCommitAttempts)) {
            Stop-Publish "git commit failed after $attempt attempts: $($commit.Output)"
        }
        Write-Host "Commit hook changed files or failed transiently; retrying commit ($($attempt + 1)/$MaxCommitAttempts)."
    }
}

$remoteProbe = Invoke-Git @("ls-remote", "origin")
if ($remoteProbe.ExitCode -ne 0) {
    if (Repair-GitAuth) {
        $remoteProbe = Invoke-Git @("ls-remote", "origin")
    }
    if ($remoteProbe.ExitCode -ne 0) { Stop-Publish "AUTH_BLOCKED or NETWORK_BLOCKED: $($remoteProbe.Output)" }
}

$fetch = Invoke-Git @("fetch", "origin")
if ($fetch.ExitCode -ne 0) {
    Stop-Publish "fetch failed after remote authentication probe: $($fetch.Output)"
}

$remoteRef = "origin/$branch"
$remoteBranch = Invoke-Git @("rev-parse", "--verify", $remoteRef)
if ($remoteBranch.ExitCode -eq 0) {
    $ancestor = Invoke-Git @("merge-base", "--is-ancestor", "HEAD", $remoteRef)
    if ($ancestor.ExitCode -ne 0) {
        $rebase = Invoke-Git @("rebase", $remoteRef)
        if ($rebase.ExitCode -ne 0) { Stop-Publish "remote is ahead and automatic rebase has conflicts" }
    }
}

for ($attempt = 1; $attempt -le [Math]::Max(1, $MaxPushAttempts); $attempt++) {
    $push = Invoke-Git @("push", "-u", "origin", "HEAD")
    if ($push.ExitCode -eq 0) { break }
    if ($attempt -eq [Math]::Max(1, $MaxPushAttempts)) {
        Stop-Publish "push failed after $attempt attempts: $($push.Output)"
    }
    if ($push.Output -match "non-fast-forward|fetch first|rejected") {
        $fetch = Invoke-Git @("fetch", "origin")
        if ($fetch.ExitCode -ne 0) { Stop-Publish "push rejected and fetch failed: $($fetch.Output)" }
        $rebase = Invoke-Git @("rebase", $remoteRef)
        if ($rebase.ExitCode -ne 0) { Stop-Publish "push rejected and rebase has conflicts" }
    } elseif ($push.Output -match "authentication|credential|permission|403|401|denied") {
        if (-not (Repair-GitAuth)) {
            Stop-Publish "AUTH_BLOCKED: push credentials rejected and gh is unavailable"
        }
        $probeAfterAuth = Invoke-Git @("ls-remote", "origin")
        if ($probeAfterAuth.ExitCode -ne 0) {
            Stop-Publish "AUTH_BLOCKED or NETWORK_BLOCKED after credential repair: $($probeAfterAuth.Output)"
        }
    } else {
        $delay = [Math]::Min(40, [int](5 * [Math]::Pow(2, $attempt - 1)))
        Write-Host "Transient push failure; retrying in ${delay}s ($($attempt + 1)/$MaxPushAttempts)."
        Start-Sleep -Seconds $delay
    }
}

$localResult = Invoke-Git @("rev-parse", "HEAD")
$remoteResult = Invoke-Git @("ls-remote", "origin", "refs/heads/$branch")
$local = $localResult.Output.Trim()
$remote = if ($remoteResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($remoteResult.Output)) {
    $remoteResult.Output.Split("`t")[0].Trim()
} else { "" }
$statusAfter = (Invoke-Git @("status", "--porcelain")).Output.Trim()
if ($localResult.ExitCode -ne 0 -or $remoteResult.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($local) -or $local -ne $remote -or -not [string]::IsNullOrWhiteSpace($statusAfter)) {
    Stop-Publish "local/remote SHA or working-tree verification failed"
}

Write-Host "========================================" -ForegroundColor Green
Write-Host " GITHUB PUSH SUCCESS" -ForegroundColor Green
Write-Host " Branch : $branch"
Write-Host " Local  : $local"
Write-Host " Remote : $remote"
Write-Host " Working Tree : CLEAN"
Write-Host "========================================" -ForegroundColor Green

$ErrorActionPreference = "Stop"

$AuditRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RawRoot = Join-Path $AuditRoot "RAW"
$RepoRoot = (Resolve-Path (Join-Path $AuditRoot "..\..\..")).Path
$EnvironmentVariableNames = @(
    "AUTOMATED_TRADING_ENGINE",
    "V2_TESTNET_CONTRACT_ENABLED",
    "NATURAL_E2E_ENABLED",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_HTTPS_PROXY",
    "HTTPS_PROXY",
    "PAPER_CONSOLE_DISABLE_LIVE_WS",
    "DATABASE_URL"
)

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-AuditCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Arguments = @()
    )

    $stdoutPath = Join-Path $RawRoot "$Id.stdout.txt"
    $stderrPath = Join-Path $RawRoot "$Id.stderr.txt"
    $recordPath = Join-Path $RawRoot "$Id.command.json"
    $startedAt = (Get-Date).ToString("o")
    $renderedCommand = (@($Executable) + $Arguments) -join " "
    $exitCode = 127

    try {
        $process = Start-Process `
            -FilePath $Executable `
            -ArgumentList $Arguments `
            -WorkingDirectory $RepoRoot `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath
        $exitCode = $process.ExitCode
    }
    catch {
        $_ | Out-String | Set-Content -LiteralPath $stderrPath -Encoding UTF8
        if (-not (Test-Path -LiteralPath $stdoutPath)) {
            "" | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
        }
    }

    $endedAt = (Get-Date).ToString("o")
    $stdout = Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
    $stderr = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
    Write-JsonFile -Path $recordPath -Value ([ordered]@{
        command = $renderedCommand
        working_directory = $RepoRoot
        environment_variable_names = $EnvironmentVariableNames
        started_at = $startedAt
        ended_at = $endedAt
        exit_code = $exitCode
        stdout = $stdout
        stderr = $stderr
    })
}

$initialRecord = [ordered]@{
    command = "git status --short; git branch --show-current; git rev-parse HEAD; git log -1 --date=iso --pretty=format:%H%n%ad%n%s; git rev-parse --verify 9afa16681e1525897ab03b89ad1febc37c30d807"
    working_directory = $RepoRoot
    environment_variable_names = $EnvironmentVariableNames
    started_at = $null
    ended_at = $null
    exit_code = 0
    stdout = @"
?? docs/audit/AI-global-project-acceptance-audit-master-prompt.md
---BRANCH---
fix/v2-production-closure
---HEAD---
7351110595bc063f3db69afa1b5554cdb8de7d3a
---LOG---
7351110595bc063f3db69afa1b5554cdb8de7d3a
2026-07-29 14:25:46 +0800
fix: wire v2 active fact chain and keep gate5 blocked
---OLD-BASELINE---
9afa16681e1525897ab03b89ad1febc37c30d807
"@
    stderr = ""
    note = "Captured before the audit evidence directory was created; exact start/end timestamps were not available from the tool result and are intentionally null."
}
Write-JsonFile -Path (Join-Path $RawRoot "00-initial-baseline.command.json") -Value $initialRecord

Invoke-AuditCommand -Id "01-git-status" -Executable "git" -Arguments @(
    "status",
    "--short",
    "--untracked-files=all",
    "--",
    ".",
    ":(exclude)audit/global_acceptance/20260729-143208-7351110"
)
Invoke-AuditCommand -Id "02-git-branch" -Executable "git" -Arguments @("branch", "--show-current")
Invoke-AuditCommand -Id "03-git-head" -Executable "git" -Arguments @("rev-parse", "HEAD")
Invoke-AuditCommand -Id "04-git-log-head" -Executable "git" -Arguments @(
    "log",
    "-1",
    "--date=iso",
    "--pretty=format:%H%n%ad%n%s"
)
Invoke-AuditCommand -Id "05-git-remotes" -Executable "git" -Arguments @("remote", "-v")
Invoke-AuditCommand -Id "06-old-baseline" -Executable "git" -Arguments @(
    "rev-parse",
    "--verify",
    "9afa16681e1525897ab03b89ad1febc37c30d807"
)
Invoke-AuditCommand -Id "07-diff-stat" -Executable "git" -Arguments @(
    "diff",
    "--stat",
    "9afa16681e1525897ab03b89ad1febc37c30d807..HEAD"
)
Invoke-AuditCommand -Id "08-diff-name-status" -Executable "git" -Arguments @(
    "diff",
    "--name-status",
    "9afa16681e1525897ab03b89ad1febc37c30d807..HEAD"
)
Invoke-AuditCommand -Id "09-log-range" -Executable "git" -Arguments @(
    "log",
    "--oneline",
    "--decorate",
    "9afa16681e1525897ab03b89ad1febc37c30d807..HEAD"
)
Invoke-AuditCommand -Id "10-python-version" -Executable "python" -Arguments @("--version")
Invoke-AuditCommand -Id "11-pip-version" -Executable "pip" -Arguments @("--version")
Invoke-AuditCommand -Id "12-node-version" -Executable "node" -Arguments @("--version")
Invoke-AuditCommand -Id "13-npm-version" -Executable "npm.cmd" -Arguments @("--version")
Invoke-AuditCommand -Id "14-pnpm-version" -Executable "pnpm" -Arguments @("--version")
Invoke-AuditCommand -Id "15-docker-version" -Executable "docker" -Arguments @("--version")

$environmentPresence = [ordered]@{}
foreach ($name in $EnvironmentVariableNames) {
    $environmentPresence[$name] = [bool](
        [Environment]::GetEnvironmentVariable($name, "Process") -or
        [Environment]::GetEnvironmentVariable($name, "User") -or
        [Environment]::GetEnvironmentVariable($name, "Machine")
    )
}
$environmentRecord = [ordered]@{
    command = "Inspect configured environment variable names for presence only"
    working_directory = $RepoRoot
    environment_variable_names = $EnvironmentVariableNames
    started_at = (Get-Date).ToString("o")
    ended_at = (Get-Date).ToString("o")
    exit_code = 0
    stdout = $environmentPresence
    stderr = ""
}
Write-JsonFile -Path (Join-Path $RawRoot "16-environment-presence.command.json") -Value $environmentRecord

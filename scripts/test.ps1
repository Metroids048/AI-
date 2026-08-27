param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

$python = (& py -3 -c "import sys; print(sys.executable)").Trim()
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "Canonical Python 3 interpreter was not resolved through py -3."
}

& $python -c "import pytest_asyncio; print('pytest-asyncio=' + pytest_asyncio.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "Canonical interpreter is missing pytest-asyncio."
}

& $python -m pytest @PytestArgs
exit $LASTEXITCODE

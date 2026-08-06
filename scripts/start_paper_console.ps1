param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$DatabasePath = ".local_paper_console.db",
    [bool]$OpenBrowser = $true,
    [ValidateSet("v2_shadow", "v2_active")]
    [string]$AutomatedTradingEngine = "v2_shadow",
    [switch]$EnableNaturalTestnet,
    [switch]$PreserveExternalTestnetBaseline
)

# Compatibility entrypoint for older shortcuts. The implementation lives in
# launch-paper-console.ps1 so startup behavior cannot drift between scripts.
& (Join-Path $PSScriptRoot "launch-paper-console.ps1") `
    -ApiPort $ApiPort `
    -FrontendPort $FrontendPort `
    -DatabasePath $DatabasePath `
    -OpenBrowser $OpenBrowser `
    -AutomatedTradingEngine $AutomatedTradingEngine `
    -EnableNaturalTestnet:$EnableNaturalTestnet `
    -PreserveExternalTestnetBaseline:$PreserveExternalTestnetBaseline
exit $LASTEXITCODE

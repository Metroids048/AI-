# 个人工具脚本：修补 OpenAI Codex Windows 客户端以暴露 GPT-5.6 模型，与本项目业务逻辑无关
# Codex GPT-5.6 patch workflow for Windows
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\codex-gpt56-patch.ps1
$ErrorActionPreference = 'Stop'
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$version = '26.707.3748.0'
$sourceApp = "C:\Program Files\WindowsApps\OpenAI.Codex_${version}_x64__2p2nqsd0c76g0\app"
$copyRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\patched-copies\CodexPatchedCopy-$version-gpt56"
$destApp = Join-Path $copyRoot 'app'
$resources = Join-Path $destApp 'resources'
$asar = Join-Path $resources 'app.asar'
$unpackedApp = Join-Path $resources 'app'
$stageRoot = Join-Path $env:TEMP 'codex-gpt56-pack-stage'
$patchScript = Join-Path $copyRoot 'apply-filter-patch.js'

function Assert-PathExists([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Label not found: $Path" }
}

Write-Host "=== Phase 4: Create patched copy ==="
if (-not (Test-Path -LiteralPath (Join-Path $destApp 'ChatGPT.exe'))) {
    New-Item -ItemType Directory -Path $destApp -Force | Out-Null
    robocopy.exe $sourceApp $destApp /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
}
Assert-PathExists (Join-Path $destApp 'ChatGPT.exe') 'ChatGPT.exe'
Assert-PathExists (Join-Path $resources 'codex.exe') 'resources codex.exe'
Assert-PathExists $asar 'app.asar'
Write-Host "Copy ready: $destApp"

Write-Host "=== Phase 5: Extract app.asar ==="
if (-not (Test-Path -LiteralPath $unpackedApp)) {
    npx.cmd --yes @electron/asar extract $asar $unpackedApp
    if ($LASTEXITCODE -ne 0) { throw 'asar extract failed' }
    if (-not (Test-Path -LiteralPath (Join-Path $resources 'app.asar1'))) {
        Rename-Item -LiteralPath $asar -NewName 'app.asar1'
    }
}
Assert-PathExists $unpackedApp 'unpacked app dir'

Write-Host "=== Phase 6: Locate filter bundle ==="
$filterFiles = Get-ChildItem -LiteralPath $unpackedApp -Recurse -Filter 'model-list-filter-*.js' -File -ErrorAction SilentlyContinue
if (-not $filterFiles -or $filterFiles.Count -eq 0) {
    $filterFiles = Get-ChildItem -LiteralPath $unpackedApp -Recurse -Filter '*.js' -File |
        Where-Object { Select-String -LiteralPath $_.FullName -Pattern 'useHiddenModels|availableModels\.has\(' -Quiet -ErrorAction SilentlyContinue }
}
if (-not $filterFiles -or $filterFiles.Count -eq 0) { throw 'No model filter bundle found' }
$target = $filterFiles | Select-Object -First 1
Write-Host "Target bundle: $($target.FullName)"

$origBackup = "$($target.FullName).orig"
if (-not (Test-Path -LiteralPath $origBackup)) {
    Copy-Item -LiteralPath $target.FullName -Destination $origBackup -Force
}

$patchJs = @'
const fs = require('fs');
const target = process.argv[2];
const source = fs.readFileSync(target, 'utf8');
const candidates = [
  ['if(u?n.has(r.model):!r.hidden)', 'if(!r.hidden||u&&n.has(r.model))'],
  ['if(l?t.has(n.model):!n.hidden)', 'if(!n.hidden||l&&t.has(n.model))'],
  ['if(c?i.has(o.model):!o.hidden)', 'if(!o.hidden||c&&i.has(o.model))'],
];
const count = (text, pattern) => text.split(pattern).length - 1;
let applied = false;
let patched = source;
for (const [oldPattern, newPattern] of candidates) {
  const oldCount = count(source, oldPattern);
  const newCount = count(source, newPattern);
  if (oldCount === 1 && newCount === 0) {
    patched = source.replace(oldPattern, newPattern);
    if (count(patched, oldPattern) !== 0 || count(patched, newPattern) !== 1) {
      throw new Error('unexpected patch signature after replace for ' + oldPattern);
    }
    console.log('patched with', oldPattern, '->', newPattern);
    applied = true;
    break;
  }
}
if (!applied) {
  const semantic = /if\(([a-zA-Z_$][\w$]*)\?([a-zA-Z_$][\w$]*)\.has\(([a-zA-Z_$][\w$]*)\.model\):!([a-zA-Z_$][\w$]*)\.hidden\)/g;
  const matches = [...source.matchAll(semantic)];
  if (matches.length !== 1) {
    throw new Error('semantic patch signature not unique: ' + matches.length);
  }
  const m = matches[0];
  const oldPattern = m[0];
  const newPattern = `if(!${m[4]}.hidden||${m[1]}&&${m[2]}.has(${m[3]}.model))`;
  patched = source.replace(oldPattern, newPattern);
  console.log('semantic patched', oldPattern, '->', newPattern);
}
fs.writeFileSync(target, patched);
require('child_process').execFileSync('node', ['--check', target], { stdio: 'inherit' });
const crypto = require('crypto');
const hash = crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex');
console.log('sha256', hash);
'@
New-Item -ItemType Directory -Path $copyRoot -Force | Out-Null
Set-Content -LiteralPath $patchScript -Value $patchJs -Encoding UTF8

Write-Host "=== Phase 7: Apply filter patch ==="
node $patchScript $target.FullName

Write-Host "=== Phase 8: Repack app.asar ==="
if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
$stageAsar = Join-Path $stageRoot 'app.asar'
$stageUnpacked = Join-Path $stageRoot 'app.asar.unpacked'
npx.cmd --yes @electron/asar pack $unpackedApp $stageAsar --unpack-dir node_modules
if ($LASTEXITCODE -ne 0) { throw 'asar pack failed' }
Assert-PathExists $stageAsar 'staged app.asar'
Assert-PathExists $stageUnpacked 'staged app.asar.unpacked'
$runtimeUnpacked = Join-Path $resources 'app.asar.unpacked'
$runtimeUnpackedBackup = Join-Path $resources 'app.asar.unpacked.original'
if (-not (Test-Path -LiteralPath $runtimeUnpackedBackup) -and (Test-Path -LiteralPath $runtimeUnpacked)) {
    Move-Item -LiteralPath $runtimeUnpacked -Destination $runtimeUnpackedBackup
}
Move-Item -LiteralPath $stageAsar -Destination $asar -Force
Move-Item -LiteralPath $stageUnpacked -Destination $runtimeUnpacked -Force
Write-Host "Repack complete"

Write-Host "PATCH_COPY=$destApp"
Write-Host "PATCH_TARGET=$($target.FullName)"
Write-Host "PATCH_BACKUP=$origBackup"

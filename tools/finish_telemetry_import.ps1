param(
    [int[]]$ProcessIds = @(),
    [int[]]$Years = @(2023, 2024, 2026),
    [switch]$MaterializePostgresCache
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$logDir = Join-Path $repo 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'telemetry-finish.log'

if (-not $MaterializePostgresCache) {
    throw 'PG 逐点遥测已废弃；确需从快照临时重建时传入 -MaterializePostgresCache。'
}

foreach ($processId in $ProcessIds) {
    Wait-Process -Id $processId -ErrorAction SilentlyContinue
}

foreach ($year in $Years) {
    "[$(Get-Date -Format o)] extracting $year" | Add-Content -LiteralPath $log -Encoding utf8
    & python tools/extract_openf1_telemetry.py --year $year --materialize-postgres-cache *>> $log
    if ($LASTEXITCODE -ne 0) {
        throw "Telemetry extraction failed for $year (exit=$LASTEXITCODE)"
    }
}

"[$(Get-Date -Format o)] complete" | Add-Content -LiteralPath $log -Encoding utf8

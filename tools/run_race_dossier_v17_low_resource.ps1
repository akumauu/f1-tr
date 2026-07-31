$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$cacheRoot = Join-Path $projectRoot ".runtime-cache"

if (-not $projectRoot.StartsWith("D:\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Race Dossier low-resource entry requires a D: project path."
}

$env:TEMP = Join-Path $cacheRoot "temp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $cacheRoot "python-v17"
$env:MPLCONFIGDIR = Join-Path $cacheRoot "matplotlib"
$env:XDG_CACHE_HOME = Join-Path $cacheRoot "xdg"
$env:JOBLIB_TEMP_FOLDER = Join-Path $cacheRoot "joblib"
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
$env:VECLIB_MAXIMUM_THREADS = "1"
$env:PYTHONIOENCODING = "utf-8"

@(
    $env:TEMP,
    $env:PYTHONPYCACHEPREFIX,
    $env:MPLCONFIGDIR,
    $env:XDG_CACHE_HOME,
    $env:JOBLIB_TEMP_FOLDER
) | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

Push-Location $projectRoot
try {
    & python -u research/run_race_dossiers_v17.py @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

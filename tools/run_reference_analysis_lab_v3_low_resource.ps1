param(
    [ValidateSet(2023, 2024, 2025)]
    [int]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repositoryRoot "research/run_reference_analysis_lab_track_validation_v3.py"
$arguments = @($runner)
if ($PSBoundParameters.ContainsKey("PrepareOnly")) {
    $arguments += @("--prepare-only", $PrepareOnly)
}

Push-Location $repositoryRoot
try {
    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "reference-analysis-lab v3 串行运行失败，退出码：$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

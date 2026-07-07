$ErrorActionPreference = "Stop"

$app = "D:\vibe-coding\F1 TR\MultiViewer\portable-2.7.1\MultiViewer.exe"

if (-not (Test-Path -LiteralPath $app)) {
    throw "找不到 MultiViewer：$app"
}

Start-Process -FilePath $app `
    -WorkingDirectory (Split-Path -Parent $app)

Write-Host "MultiViewer 已启动。请打开目标赛段的 Live Timing 窗口，然后运行："
Write-Host "node tools/save_multiviewer_api_state.mjs --watch"

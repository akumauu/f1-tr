$ErrorActionPreference = "Stop"

$app = "D:\vibe-coding\F1 TR\MultiViewer\portable-2.7.1\MultiViewer.exe"

if (-not (Test-Path -LiteralPath $app)) {
    throw "找不到 MultiViewer：$app"
}

Start-Process -FilePath $app `
    -WorkingDirectory (Split-Path -Parent $app) `
    -ArgumentList "--remote-debugging-port=9223"

Write-Host "MultiViewer 已用调试端口 9223 启动。打开 AI radio transcriptions 面板后，运行："
Write-Host "node tools/save_multiviewer_ai_radio.mjs"

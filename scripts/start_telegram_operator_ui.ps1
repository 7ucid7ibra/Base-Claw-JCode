$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv-telegram-agent\Scripts\python.exe"
$localJCodeDir = Join-Path $projectRoot "tools\jcode"

Set-Location $projectRoot
if (Test-Path (Join-Path $localJCodeDir "jcode.exe")) {
    $env:Path = "$localJCodeDir;$env:Path"
}
if (-not $env:JCODE_NO_TELEMETRY) {
    $env:JCODE_NO_TELEMETRY = "1"
}
if (-not (Test-Path $python)) {
    throw "Telegram operator environment is missing. Run .\install.ps1 -Mode client first."
}
& $python app\telegram_operator_ui.py

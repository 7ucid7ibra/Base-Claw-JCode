$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv-telegram-agent\Scripts\python.exe"

Set-Location $projectRoot
& $python app\telegram_operator_ui.py

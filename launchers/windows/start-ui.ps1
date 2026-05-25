$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
& "$projectRoot\scripts\start_telegram_operator_ui.ps1"

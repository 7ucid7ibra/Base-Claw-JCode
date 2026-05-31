$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
& "$projectRoot\scripts\run_telegram_operator.ps1"

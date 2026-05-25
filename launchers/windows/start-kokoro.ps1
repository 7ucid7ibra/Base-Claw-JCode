$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
& "$projectRoot\scripts\start_kokoro_server.ps1"

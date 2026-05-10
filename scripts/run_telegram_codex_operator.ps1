$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv-telegram-agent\Scripts\python.exe"
$script = Join-Path $projectRoot "app\telegram_codex_operator.py"
$logPath = Join-Path $projectRoot "telegram_codex_operator.supervisor.log"

Set-Location $projectRoot

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$ts starting telegram_codex_operator.py"
    & $python $script 2>&1 | Tee-Object -FilePath $logPath -Append
    $exitCode = $LASTEXITCODE
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$ts operator exited with code $exitCode; restarting in 5 seconds"
    Start-Sleep -Seconds 5
}

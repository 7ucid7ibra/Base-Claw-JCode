$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv-telegram-agent\Scripts\python.exe"
$script = Join-Path $projectRoot "app\telegram_codex_operator.py"
$logPath = Join-Path $projectRoot "telegram_codex_operator.supervisor.log"

Set-Location $projectRoot
$env:BASECLAW_SUPERVISED = "1"

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$ts starting telegram_codex_operator.py"
    $command = "`"$python`" `"$script`" >> `"$logPath`" 2>&1"
    & cmd.exe /D /C $command
    $exitCode = $LASTEXITCODE
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$ts operator exited with code $exitCode; restarting in 5 seconds"
    Start-Sleep -Seconds 5
}

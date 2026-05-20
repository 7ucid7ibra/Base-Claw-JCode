param(
    [string]$ProfileEnv = ""
)

$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv-telegram-agent\Scripts\python.exe"
$script = Join-Path $projectRoot "app\telegram_codex_operator.py"
$profileEnv = $ProfileEnv
if ([string]::IsNullOrWhiteSpace($profileEnv)) {
    $profileEnv = $env:BASECLAW_OPERATOR_ENV_PATH
}
if ([string]::IsNullOrWhiteSpace($profileEnv)) {
    $profileEnv = Join-Path $projectRoot ".env.telegram-operator"
}
$profileDir = Split-Path -Parent $profileEnv
$logPath = Join-Path $profileDir "telegram_codex_operator.supervisor.log"

Set-Location $projectRoot
$env:BASECLAW_SUPERVISED = "1"

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$ts starting telegram_codex_operator.py"
    $command = "`"$python`" `"$script`" --profile-env `"$profileEnv`" >> `"$logPath`" 2>&1"
    & cmd.exe /D /C $command
    $exitCode = $LASTEXITCODE
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$ts operator exited with code $exitCode; restarting in 5 seconds"
    Start-Sleep -Seconds 5
}

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv-kokoro\Scripts\python.exe"
$localEspeak = Join-Path $projectRoot "tools\espeak-ng\eSpeak NG"

if (Test-Path $localEspeak) {
    $env:Path = "$localEspeak;$env:Path"
}

Set-Location $projectRoot
& $python app\speech\server.py

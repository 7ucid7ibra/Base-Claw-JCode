param(
    [ValidateSet("full", "client", "host")]
    [string]$Mode = "full",
    [switch]$NoLocalSpeechFallback
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Ensure-Python311 {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python launcher 'py' was not found. Install Python 3.11 first."
    }
    & py -3.11 --version | Out-Host
}

function Ensure-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Warning "$Name was not found. $InstallHint"
    }
}

Ensure-Python311

$installHost = $Mode -in @("full", "host")
$installClient = $Mode -in @("full", "client")

if ($installHost) {
    Ensure-Command ffmpeg "Install with: choco install ffmpeg -y"
    if (Test-Path ".\tools\espeak-ng\eSpeak NG\espeak-ng.exe") {
        $env:Path = "$projectRoot\tools\espeak-ng\eSpeak NG;$env:Path"
        Write-Host "Using local espeak-ng from tools\espeak-ng."
    } else {
        Ensure-Command espeak-ng "Install with: choco install espeak-ng -y, or add a local eSpeak NG folder to PATH."
    }
}

if ($installClient) {
    Ensure-Command jcode "Install JCode if you want the default local/JCode mode."
    Ensure-Command codex "Install Codex CLI and run: codex login if you want Codex mode."
    Ensure-Command claude "Install Claude CLI and authenticate if you want Claude mode."
    if (Get-Command jcode -ErrorAction SilentlyContinue) {
        Write-Host "JCode CLI found. This is the default local harness."
    }
    if (Get-Command codex -ErrorAction SilentlyContinue) {
        Write-Host "Codex CLI found. If this is a fresh machine, run 'codex login' before using Codex mode."
    }
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        Write-Host "Claude CLI found. Make sure it is authenticated before using Claude mode."
    }
}

if ($installHost -and -not (Test-Path ".\.venv-kokoro")) {
    py -3.11 -m venv .venv-kokoro
}
if ($installHost) {
    & ".\.venv-kokoro\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv-kokoro\Scripts\python.exe" -m pip install -r requirements\kokoro.txt
}

if ($installClient -and -not (Test-Path ".\.venv-telegram-agent")) {
    py -3.11 -m venv .venv-telegram-agent
}
if ($installClient) {
    & ".\.venv-telegram-agent\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv-telegram-agent\Scripts\python.exe" -m pip install -r requirements\client.txt
}

if ($installClient -and -not (Test-Path ".\.env.telegram-operator")) {
    Copy-Item ".\.env.telegram-operator.example" ".\.env.telegram-operator"
    Write-Host "Created .env.telegram-operator from the example. Open the UI or edit it before starting the operator."
}

if ($installClient -and $NoLocalSpeechFallback) {
    $envPath = ".\.env.telegram-operator"
    $content = Get-Content $envPath
    $updated = $false
    $content = $content | ForEach-Object {
        if ($_ -match '^TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK=') {
            $updated = $true
            "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK=false"
        } else {
            $_
        }
    }
    if (-not $updated) {
        $content += "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK=false"
    }
    Set-Content -Path $envPath -Value $content -Encoding utf8
}

if ($installClient) {
    & ".\.venv-telegram-agent\Scripts\python.exe" app\verify_install.py --mode $Mode
} elseif ($installHost) {
    & ".\.venv-kokoro\Scripts\python.exe" app\verify_install.py --mode host
}

param(
    [ValidateSet("full", "client", "host")]
    [string]$Mode = "full",
    [switch]$NoLocalSpeechFallback,
    [switch]$NoLaunch,
    [switch]$InstallProviderTools,
    [switch]$InstallJCode,
    [switch]$InstallCodex,
    [switch]$InstallClaude,
    [switch]$InstallGemini,
    [switch]$Setup,
    [switch]$Yes,
    [string]$JCodeVersion = "0.12.3"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Ask-YesNo {
    param(
        [string]$Question,
        [bool]$DefaultYes = $false
    )
    if ($Yes) {
        return $true
    }
    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $answer = Read-Host "$Question $suffix"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $DefaultYes
        }
        switch ($answer.Trim().ToLowerInvariant()) {
            "y" { return $true }
            "yes" { return $true }
            "n" { return $false }
            "no" { return $false }
            default { Write-Host "Please answer y or n." }
        }
    }
}

function Resolve-Python311 {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    $probe = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
            continue
        }
        $args = @($candidate.Args + @("-c", $probe))
        & $candidate.Exe @args | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $versionArgs = @($candidate.Args + @("--version"))
            $version = (& $candidate.Exe @versionArgs 2>&1 | Out-String).Trim()
            Write-Host "Using $($candidate.Exe) $($candidate.Args -join ' ') ($version)"
            return [pscustomobject]$candidate
        }
    }
    throw "Python 3.11 or newer was not found. Install Python from python.org, then rerun .\install.ps1."
}

function Invoke-BasePython {
    param([string[]]$Arguments)
    $allArgs = @($script:PythonSpec.Args + $Arguments)
    & $script:PythonSpec.Exe @allArgs
}

function Ensure-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Warning "$Name was not found. $InstallHint"
        return
    }
    Write-Host "$Name found."
}

function Install-NpmGlobal {
    param(
        [string]$Package,
        [string]$CommandName
    )
    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        Write-Host "$CommandName already installed."
        return
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Warning "npm was not found. Install Node.js first, then install $Package."
        return
    }
    Write-Host "Installing $Package globally with npm..."
    & npm install -g $Package
}

function Install-JCodeWindows {
    if (Get-Command jcode -ErrorAction SilentlyContinue) {
        Write-Host "JCode already available on PATH."
        return
    }

    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) {
        $arch = $env:PROCESSOR_ARCHITEW6432
    }
    switch -Regex ($arch) {
        "ARM64" { $asset = "jcode-windows-aarch64.exe"; break }
        "AMD64|x86_64" { $asset = "jcode-windows-x86_64.exe"; break }
        default { throw "Unsupported Windows architecture for JCode auto-install: $arch" }
    }

    $targetDir = Join-Path $projectRoot "tools\jcode"
    $target = Join-Path $targetDir "jcode.exe"
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    $url = "https://github.com/1jehuang/jcode/releases/download/v$JCodeVersion/$asset"
    Write-Host "Downloading JCode $JCodeVersion for Windows..."
    Invoke-WebRequest -Uri $url -OutFile $target
    $env:Path = "$targetDir;$env:Path"

    & $target --version | Out-Host
    Write-Host "Installed JCode locally at tools\jcode\jcode.exe."
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Value
    )
    $content = @()
    if (Test-Path $Path) {
        $content = Get-Content $Path
    }
    $updated = $false
    $content = $content | ForEach-Object {
        if ($_ -match "^$([regex]::Escape($Name))=") {
            $updated = $true
            "$Name=$Value"
        } else {
            $_
        }
    }
    if (-not $updated) {
        $content += "$Name=$Value"
    }
    Set-Content -Path $Path -Value $content -Encoding utf8
}

$localJCodeDir = Join-Path $projectRoot "tools\jcode"
if (Test-Path (Join-Path $localJCodeDir "jcode.exe")) {
    $env:Path = "$localJCodeDir;$env:Path"
}
if (-not $env:JCODE_NO_TELEMETRY) {
    $env:JCODE_NO_TELEMETRY = "1"
}

$script:PythonSpec = Resolve-Python311
$modeWasProvided = $PSBoundParameters.ContainsKey("Mode")
$interactiveSetup = $Setup -or -not $modeWasProvided

if ($interactiveSetup) {
    Write-Step "Setup choices"
    if (-not $modeWasProvided) {
        if (Ask-YesNo "Install Kokoro/Whisper speech dependencies on this machine?" $false) {
            $Mode = "full"
        } else {
            $Mode = "client"
        }
    }
    if (-not (Get-Command jcode -ErrorAction SilentlyContinue) -and -not $InstallJCode -and -not $InstallProviderTools) {
        if (Ask-YesNo "Install JCode for the default local model mode?" $true) {
            $InstallJCode = $true
        }
    }
    if (-not (Get-Command codex -ErrorAction SilentlyContinue) -and -not $InstallCodex -and -not $InstallProviderTools) {
        if (Ask-YesNo "Install Codex CLI?" $false) {
            $InstallCodex = $true
        }
    }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and -not $InstallClaude -and -not $InstallProviderTools) {
        if (Ask-YesNo "Install Claude CLI?" $false) {
            $InstallClaude = $true
        }
    }
    if (-not (Get-Command gemini -ErrorAction SilentlyContinue) -and -not $InstallGemini -and -not $InstallProviderTools) {
        if (Ask-YesNo "Install Gemini CLI?" $false) {
            $InstallGemini = $true
        }
    }
}

$installHost = $Mode -in @("full", "host")
$installClient = $Mode -in @("full", "client")

if ($InstallProviderTools -or $InstallJCode) {
    Install-JCodeWindows
}
if ($InstallProviderTools -or $InstallCodex) {
    Install-NpmGlobal -Package "@openai/codex" -CommandName "codex"
}
if ($InstallProviderTools -or $InstallClaude) {
    Install-NpmGlobal -Package "@anthropic-ai/claude-code" -CommandName "claude"
}
if ($InstallProviderTools -or $InstallGemini) {
    Install-NpmGlobal -Package "@google/gemini-cli" -CommandName "gemini"
}

if ($installHost) {
    Write-Step "Checking speech host tools"
    Ensure-Command ffmpeg "Install with Chocolatey, winget, or from ffmpeg.org."
    if (Test-Path ".\tools\espeak-ng\eSpeak NG\espeak-ng.exe") {
        $env:Path = "$projectRoot\tools\espeak-ng\eSpeak NG;$env:Path"
        Write-Host "Using local espeak-ng from tools\espeak-ng."
    } else {
        Ensure-Command espeak-ng "Install eSpeak NG or add a local eSpeak NG folder to PATH."
    }
}

if ($installClient) {
    Write-Step "Checking agent provider tools"
    Ensure-Command jcode "Run .\install.ps1 -Mode client -InstallJCode, or choose Codex/Claude/Gemini in the UI."
    Ensure-Command codex "Run .\install.ps1 -Mode client -InstallCodex and then codex login if you want Codex mode."
    Ensure-Command claude "Run .\install.ps1 -Mode client -InstallClaude and then authenticate if you want Claude mode."
    Ensure-Command gemini "Run .\install.ps1 -Mode client -InstallGemini and then authenticate if you want Gemini mode."
}

if ($installHost) {
    Write-Step "Preparing Kokoro speech environment"
    if (-not (Test-Path ".\.venv-kokoro")) {
        Invoke-BasePython @("-m", "venv", ".venv-kokoro")
    }
    & ".\.venv-kokoro\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv-kokoro\Scripts\python.exe" -m pip install -r requirements\kokoro.txt
}

if ($installClient) {
    Write-Step "Preparing Telegram operator environment"
    if (-not (Test-Path ".\.venv-telegram-agent")) {
        Invoke-BasePython @("-m", "venv", ".venv-telegram-agent")
    }
    & ".\.venv-telegram-agent\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv-telegram-agent\Scripts\python.exe" -m pip install -r requirements\client.txt
}

if ($installClient -and -not (Test-Path ".\.env.telegram-operator")) {
    Copy-Item ".\.env.telegram-operator.example" ".\.env.telegram-operator"
    Write-Host "Created .env.telegram-operator from the example."
}

if ($installClient) {
    $envPath = ".\.env.telegram-operator"
    if ($NoLocalSpeechFallback) {
        Set-EnvValue -Path $envPath -Name "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK" -Value "false"
    } elseif ($Mode -eq "full") {
        Set-EnvValue -Path $envPath -Name "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK" -Value "true"
        Set-EnvValue -Path $envPath -Name "TELEGRAM_OPERATOR_REMOTE_HOST" -Value "127.0.0.1"
    }
}

Write-Step "Verifying installation"
if ($installClient) {
    & ".\.venv-telegram-agent\Scripts\python.exe" app\verify_install.py --mode $Mode
} elseif ($installHost) {
    & ".\.venv-kokoro\Scripts\python.exe" app\verify_install.py --mode host
}

Write-Step "Next steps"
if ($installClient) {
    Write-Host "1. Edit .env.telegram-operator or use the UI to set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS."
    Write-Host "2. Start the UI later with: .\launchers\windows\start-ui.ps1"
    Write-Host "3. Optional provider installs: -InstallJCode, -InstallCodex, -InstallClaude, -InstallGemini, or -InstallProviderTools."
}
if ($installHost) {
    Write-Host "Speech host can be started with: .\launchers\windows\start-kokoro.ps1"
}

if ($installClient -and -not $NoLaunch) {
    Write-Step "Starting UI"
    & ".\launchers\windows\start-ui.ps1"
}

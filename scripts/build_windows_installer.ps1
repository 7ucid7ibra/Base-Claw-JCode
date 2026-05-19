param(
    [string]$Version = "",
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$stageDir = Join-Path $projectRoot "dist\windows-installer-stage"
$issPath = Join-Path $projectRoot "installer\baseclaw.iss"

function Resolve-InnoCompiler {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

if (-not (Test-Path $issPath)) {
    throw "Missing Inno Setup script: $issPath"
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    try {
        $commit = (& git -C $projectRoot rev-parse --short HEAD 2>$null).Trim()
        $Version = "0.1.0-alpha+$commit"
    } catch {
        $Version = "0.1.0-alpha"
    }
}

if (Test-Path $stageDir) {
    Remove-Item -LiteralPath $stageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

$excludeDirs = @(".git", ".venv-kokoro", ".venv-telegram-agent", "dist", "__pycache__")
$excludeFiles = @(
    ".env.telegram-operator",
    "telegram_operator_messages.sqlite3",
    "telegram_operator_state.json",
    "telegram_operator_board_state.json",
    "telegram_codex_operator.log",
    "*.pyc",
    "*.pyo"
)

$robocopyArgs = @(
    $projectRoot,
    $stageDir,
    "/MIR",
    "/XD"
) + $excludeDirs + @("/XF") + $excludeFiles

& robocopy @robocopyArgs | Out-Host
$robocopyExit = $LASTEXITCODE
if ($robocopyExit -gt 7) {
    throw "robocopy failed with exit code $robocopyExit"
}

$iscc = Resolve-InnoCompiler
if (-not $iscc) {
    Write-Host ""
    Write-Host "Inno Setup compiler was not found."
    Write-Host "Install it with: winget install JRSoftware.InnoSetup"
    Write-Host "Then rerun: .\scripts\build_windows_installer.ps1"
    Write-Host "Staged installer files at: $stageDir"
    exit 2
}

$env:BASECLAW_INSTALLER_VERSION = $Version
& $iscc $issPath

if (-not $KeepStage) {
    Remove-Item -LiteralPath $stageDir -Recurse -Force
}

Write-Host ""
Write-Host "Installer build complete: dist\BaseClawSetup.exe"

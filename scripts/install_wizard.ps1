$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $PSScriptRoot
$installScript = Join-Path $projectRoot "install.ps1"
$startScript = Join-Path $projectRoot "start-ui.ps1"

function New-Label {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width = 430, [int]$Height = 24)
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size($Width, $Height)
    return $label
}

function New-Radio {
    param([string]$Text, [int]$X, [int]$Y, [bool]$Checked = $false)
    $radio = New-Object System.Windows.Forms.RadioButton
    $radio.Text = $Text
    $radio.Location = New-Object System.Drawing.Point($X, $Y)
    $radio.Size = New-Object System.Drawing.Size(430, 24)
    $radio.Checked = $Checked
    return $radio
}

function New-Checkbox {
    param([string]$Text, [int]$X, [int]$Y, [bool]$Checked = $false)
    $box = New-Object System.Windows.Forms.CheckBox
    $box.Text = $Text
    $box.Location = New-Object System.Drawing.Point($X, $Y)
    $box.Size = New-Object System.Drawing.Size(430, 24)
    $box.Checked = $Checked
    return $box
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "BaseClaw Windows Installer"
$form.Size = New-Object System.Drawing.Size(540, 500)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$title = New-Label "BaseClaw Windows Installer" 24 20 470 28
$title.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$form.Controls.Add((New-Label "Choose what this machine should run:" 24 62))
$clientMode = New-Radio "Client only: Telegram/UI agent, no local speech stack" 42 92 $true
$fullMode = New-Radio "Full: client plus Kokoro/Whisper speech dependencies" 42 122
$hostMode = New-Radio "Speech host only: Kokoro/Whisper server dependencies" 42 152
$form.Controls.AddRange(@($clientMode, $fullMode, $hostMode))

$form.Controls.Add((New-Label "Optional agent provider tools:" 24 196))
$installJCode = New-Checkbox "Install JCode locally for the default local model mode" 42 226 $true
$installCodex = New-Checkbox "Install Codex CLI with npm" 42 256
$installClaude = New-Checkbox "Install Claude CLI with npm" 42 286
$form.Controls.AddRange(@($installJCode, $installCodex, $installClaude))

$launchUi = New-Checkbox "Launch the BaseClaw UI after installation" 42 330 $true
$form.Controls.Add($launchUi)

$status = New-Label "Ready." 24 370 470 44
$status.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$status.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$form.Controls.Add($status)

$installButton = New-Object System.Windows.Forms.Button
$installButton.Text = "Install"
$installButton.Location = New-Object System.Drawing.Point(290, 425)
$installButton.Size = New-Object System.Drawing.Size(100, 32)
$form.Controls.Add($installButton)

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = "Start UI"
$startButton.Location = New-Object System.Drawing.Point(400, 425)
$startButton.Size = New-Object System.Drawing.Size(100, 32)
$form.Controls.Add($startButton)

$installButton.Add_Click({
    $mode = "client"
    if ($fullMode.Checked) {
        $mode = "full"
    } elseif ($hostMode.Checked) {
        $mode = "host"
    }

    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installScript, "-Mode", $mode)
    if ($installJCode.Checked) {
        $args += "-InstallJCode"
    }
    if ($installCodex.Checked) {
        $args += "-InstallCodex"
    }
    if ($installClaude.Checked) {
        $args += "-InstallClaude"
    }
    if (-not $launchUi.Checked) {
        $args += "-NoLaunch"
    }

    $status.Text = "Installer started in a PowerShell window. Keep that window open until it finishes."
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $projectRoot
})

$startButton.Add_Click({
    if (-not (Test-Path $startScript)) {
        [System.Windows.Forms.MessageBox]::Show("Missing start script: $startScript", "BaseClaw", "OK", "Error") | Out-Null
        return
    }
    $status.Text = "Starting BaseClaw UI..."
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript) -WorkingDirectory $projectRoot
})

[void]$form.ShowDialog()

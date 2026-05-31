# Project Layout

The root is kept small so a new user can find the installer, environment example, and README quickly.

```text
app/
  harnesses/
    bridges.py
    cli.py
    desktop.py
  operator_core/
    attachments.py
    command_handlers.py
    commands.py
    config.py
    media_handlers.py
    storage.py
    updates.py
  speech/
    server.py
    client.py
    urls.py
    whisper_worker.py
  ui/
    speech_panel.py
  process_utils.py
  telegram_operator.py
  telegram_operator_ui.py
tools/
  send_voice_note.py
  verify_install.py
scripts/
  install_windows.ps1
  install_wizard.ps1
  run_telegram_operator.ps1
  speech_server.sh
  start_kokoro_server.ps1
  start_telegram_operator_ui.ps1
launchers/
  macos/
    install-macos.command
    start-macos.command
  windows/
    install-wizard.cmd
    run-operator.ps1
    start-kokoro.ps1
    start-ui.ps1
packaging/
  macos/
    build_app.sh
    build_dmg.sh
    install_launcher.sh
  windows/
    baseclaw.iss
    build_installer.ps1
requirements/
  client.txt
  kokoro.txt
  telegram-operator.txt
docs/
  APP_ARCHITECTURE.md
  INSTALLATION_MODES.md
  KOKORO_REMOTE_USAGE.md
  TELEGRAM_OPERATOR.md
  TROUBLESHOOTING.md
  PUBLISHING.md
agent_workspace/
  AGENT_HOME.md
voice_assets/
  custom/
  german/
  german_package/
```

Root convenience scripts:

- `install.ps1`
- `install.sh`
- `start.sh`

Runtime files such as `.env.telegram-operator`, `profiles/`, local private overlays, logs, SQLite databases, downloaded models, and generated audio stay local and are ignored by git.

Only `agent_workspace/AGENT_HOME.md` is tracked. The installer and UI create ignored local workspace folders such as `skills/`, `automations/`, `projects/`, `slash_commands/`, `notes/`, `scratch/`, `artifacts/`, and `uploads/` for each workspace.

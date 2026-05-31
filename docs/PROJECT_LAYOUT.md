# Project Layout

The root is kept small so a new user can find the installer, environment example, and README quickly.

```text
app/
  operator_core/
    config.py
  speech/
    server.py
    client.py
    whisper_worker.py
  telegram_codex_operator.py
  telegram_operator_ui.py
tools/
  send_voice_note.py
  verify_install.py
scripts/
  install_windows.ps1
  start_kokoro_server.ps1
  start_telegram_operator_ui.ps1
  run_telegram_codex_operator.ps1
launchers/
  macos/
  windows/
packaging/
  windows/
requirements/
  client.txt
  kokoro.txt
  telegram-operator.txt
docs/
  INSTALLATION_MODES.md
  KOKORO_REMOTE_USAGE.md
  TELEGRAM_CODEX_OPERATOR.md
  TROUBLESHOOTING.md
  PUBLISHING.md
overlays/
  README.md
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

Runtime files such as `.env.telegram-operator`, `profiles/`, `overlays/private/`, logs, SQLite databases, downloaded models, and generated audio stay local and are ignored by git.

Only `agent_workspace/AGENT_HOME.md` is tracked. The installer and UI create ignored local workspace folders such as `skills/`, `automations/`, `projects/`, `slash_commands/`, `notes/`, `scratch/`, `artifacts/`, and `uploads/` for each workspace.

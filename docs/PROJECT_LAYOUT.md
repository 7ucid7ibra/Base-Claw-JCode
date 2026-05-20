# Project Layout

The root is kept small so a new user can find the installer, environment example, and README quickly.

```text
app/
  kokoro_server.py
  kokoro_remote_telegram.py
  telegram_codex_operator.py
  telegram_operator_ui.py
  verify_install.py
scripts/
  install_windows.ps1
  start_kokoro_server.ps1
  start_telegram_operator_ui.ps1
  run_telegram_codex_operator.ps1
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
agent_workspace/
custom_voices/
german_kokoro/
kokoro_german/
```

Root convenience scripts:

- `install.ps1`
- `start-kokoro.ps1`
- `start-ui.ps1`
- `run-operator.ps1`

Runtime files such as `.env.telegram-operator`, `profiles/`, logs, SQLite databases, downloaded models, and generated audio stay local and are ignored by git.

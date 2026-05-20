# Publishing Checklist

This project is ready to share as an alpha learning foundation after the local-secret and repository-boundary checks below are clean.

## Before Publishing

- Rotate any Telegram bot token that was ever stored in `.env.telegram-operator`.
- Keep `.env.telegram-operator` local only. Commit `.env.telegram-operator.example`, never the real file.
- Publish from a clean repository rooted at the BaseClaw project folder. Do not publish from a parent user-profile repository.
- Review `git status --short` and make sure only project files are present.
- Remove generated files before release: logs, SQLite databases, voice test WAVs, screenshots, caches, virtual environments, and downloaded model folders.
- Run `bash -n install.sh`.
- Run `python -m py_compile app/telegram_operator_ui.py app/telegram_codex_operator.py app/kokoro_server.py`.
- Start Kokoro and confirm `GET /health`, `GET /voices`, and a real `POST /synthesize`.
- Confirm `POST /synthesize_voice_note` if you want lightweight clients to avoid local ffmpeg.
- Start the Telegram operator in `restricted` or `safe` mode first.

## Security Model

This is a local high-trust agent bridge, not a hardened multi-user service.

- Access scope controls whether the agent may work only in the workspace, in the workspace plus this app code, or across the full machine.
- Action mode controls whether work is read-only, approval-gated, or executed directly.
- Codex has the strongest native sandbox support. Other harnesses receive policy instructions and process-level limits, but not the same sandbox guarantees.

Anyone who can use the allowed Telegram chat can ask the local agent to perform actions with the configured safety level.

## Suggested Release Shape

Use a fresh repository or clean export containing:

- source files
- requirements files
- setup and start scripts
- docs
- `agent_workspace/AGENT_HOME.md`
- placeholder READMEs under `custom_voices/`, `german_kokoro/`, and `kokoro_german/`
- `.env.telegram-operator.example`

Do not include:

- `.env.telegram-operator`
- `.venv-*`
- `*.log`
- `*.sqlite3`
- `telegram_operator_state.json`
- `telegram_operator_memory.jsonl`
- `telegram_operator_board_ed25519`
- `telegram_operator_board_known_hosts`
- local voice/model/cache folders
- generated screenshots or audio test files

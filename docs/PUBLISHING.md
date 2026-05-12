# Publishing Checklist

This project is ready to share as an alpha learning foundation after the local-secret and repository-boundary checks below are clean.

## Before Publishing

- Rotate any Telegram bot token that was ever stored in `.env.telegram-operator`.
- Keep `.env.telegram-operator` local only. Commit `.env.telegram-operator.example`, never the real file.
- Publish from a clean repository rooted at the BaseClaw project folder. Do not publish from a parent user-profile repository.
- Review `git status --short` and make sure only project files are present.
- Remove generated files before release: logs, SQLite databases, voice test WAVs, screenshots, caches, virtual environments, and downloaded model folders.
- Run `python app/verify_install.py`.
- Start Kokoro and confirm `GET /health`, `GET /voices`, and a real `POST /synthesize`.
- Confirm `POST /synthesize_voice_note` if you want lightweight clients to avoid local ffmpeg.
- Start the Telegram operator in `restricted` or `safe` mode first.

## Security Model

This is a local high-trust agent bridge, not a hardened multi-user service.

- `restricted` sends a Telegram approval card before each task. Proposal generation is read-only; approved execution uses Codex workspace-write sandboxing.
- `safe` uses Codex workspace-write sandboxing and treats the configured workspace as the normal operating area.
- `code` uses Codex workspace-write sandboxing from the app repository root and auto-commits pre-run checkpoints plus agent changes so code edits can be reverted.
- `full` bypasses Codex approvals and sandboxing. Use it only for a private bot and a trusted chat id.

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
- local voice/model/cache folders
- generated screenshots or audio test files

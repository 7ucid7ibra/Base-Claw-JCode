# BaseClaw Telegram Operator

BaseClaw turns a Telegram bot into a local coding-agent bridge on one trusted machine. It also provides a small desktop UI with a live chat window, settings, speech controls, and runtime controls.

It supports:

- Telegram text messages and voice notes.
- Desktop chat messages in the local UI.
- Whisper transcription for incoming voice.
- Kokoro voice replies.
- JCode with local or hosted model providers.
- Direct Codex CLI and Claude CLI modes.
- Safety and access controls for local filesystem work.
- Per-chat session state, local message logs, and optional shared history sync.

## Trust Model

This is a high-trust local tool, not a hardened multi-user service.

- Only allow your own Telegram chat id.
- Anyone who can use the allowed Telegram chat can send work to the configured local agent.
- `read` action mode is for inspection only.
- `approve` action mode asks for confirmation before write-oriented work.
- `full` action mode lets the selected harness execute immediately.
- Codex has the strongest native sandbox support. Other harnesses receive policy instructions and process-level limits, but not the same sandbox guarantees.

## Files

- `app/telegram_codex_operator.py`
- `app/telegram_operator_ui.py`
- `requirements/telegram-operator.txt`
- `.env.telegram-operator.example`

## Basic Environment

Copy `.env.telegram-operator.example` to `.env.telegram-operator`, or let `./install.sh` create it for you.

Required:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`

Important local defaults:

- `TELEGRAM_OPERATOR_WORKDIR=agent_workspace`
- `TELEGRAM_OPERATOR_RUN_MODE=local`
- `TELEGRAM_OPERATOR_PROVIDER=jcode`
- `TELEGRAM_OPERATOR_MODEL_PROVIDER=lmstudio`
- `TELEGRAM_OPERATOR_REMOTE_HOST=127.0.0.1`
- `TELEGRAM_OPERATOR_SPEECH_PORT=8766`
- `TELEGRAM_OPERATOR_LLM_PORT=1234`
- `TELEGRAM_OPERATOR_SOURCE_UPDATE_REMOTE=` optional local or SSH archive source for the UI Update button.

Never publish `.env.telegram-operator`. It contains local credentials and machine-specific settings.

## Provider Modes

BaseClaw separates the visible mode from the underlying model provider.

Local mode:

- Uses JCode as the coding harness.
- Can connect to LM Studio, Ollama, OpenRouter, OpenAI-compatible endpoints, and other JCode-supported providers.
- For LM Studio, start the LM Studio server and load a model first.
- For Ollama, start Ollama and make sure the selected model is available.
- For hosted JCode providers, add the relevant API key in the UI.

Cloud mode:

- Uses direct Codex CLI or Claude CLI.
- Codex requires `codex login`.
- Claude requires the Claude CLI to be installed and authenticated.

Key settings:

- `TELEGRAM_OPERATOR_PROVIDER`: selected harness, normally `jcode`, `codex`, or `claude`.
- `TELEGRAM_OPERATOR_MODEL_PROVIDER`: JCode provider, for example `lmstudio`, `ollama`, or `openrouter`.
- `TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE`: optional advanced JCode profile. Leave it empty for the normal UI flow.
- `TELEGRAM_OPERATOR_JCODE_API_KEY`: optional key for hosted JCode providers.
- `TELEGRAM_OPERATOR_CODEX_MODEL`: model name passed to the selected harness when supported.

## Safety And Access

Access scope controls where the agent is allowed to work:

- `workspace`: the selected workspace and explicitly added paths.
- `code`: the workspace plus this app repository.
- `full`: no path restriction from BaseClaw.

When the install is a git checkout, `code` mode creates automatic git checkpoints before and after write-capable agent runs. Archive installs without a `.git` folder skip those checkpoints instead of blocking the request.

Action mode controls how quickly it may act:

- `read`: read-only intent.
- `approve`: ask for confirmation before write-oriented actions.
- `full`: execute without extra confirmation.

Legacy `TELEGRAM_OPERATOR_SAFETY_MODE` and `TELEGRAM_OPERATOR_SAFE_MODE` are kept for compatibility, but new UI controls use access scope plus action mode.

## Speech

Kokoro and Whisper can run locally or on a separate reachable host.

- `TELEGRAM_OPERATOR_REMOTE_HOST` is the shared host for speech and local model services.
- `TELEGRAM_OPERATOR_SPEECH_PORT` is the STT/TTS service port for Whisper transcription and Kokoro voice output.
- `TELEGRAM_OPERATOR_LLM_PORT` is the LM Studio or compatible local model API port.
- `TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK` is an advanced compatibility flag. Normal installs use the configured host and speech port directly; `127.0.0.1` means local speech.

The UI can discover voices from the active Kokoro host. Selecting a voice persists the voice and inferred language code.

## Optional Shared History And Board

The operator can keep local raw chat history and optionally sync it to a shared SQLite database over SSH. This is useful when several trusted machines should coordinate, but it is disabled by default.

Relevant settings:

- `TELEGRAM_OPERATOR_HISTORY_AGENT`
- `TELEGRAM_OPERATOR_HISTORY_DEVICE`
- `TELEGRAM_OPERATOR_HISTORY_REMOTE`
- `TELEGRAM_OPERATOR_HISTORY_REMOTE_DB_PATH`
- `TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_ENABLED`

The optional shared board poller can watch a remote newline-delimited JSON file for coordination notices. It only sends Telegram notices; it does not run implementation tasks automatically.

Relevant settings:

- `TELEGRAM_OPERATOR_BOARD_POLL_ENABLED`
- `TELEGRAM_OPERATOR_BOARD_REMOTE`
- `TELEGRAM_OPERATOR_BOARD_PATH`
- `TELEGRAM_OPERATOR_BOARD_AGENT_ALIASES`

Leave these settings empty for a normal single-machine install.

## Run

Install and open the UI:

```bash
./install.sh
```

Start the UI later:

```bash
source .venv-telegram-agent/bin/activate
python app/telegram_operator_ui.py
```

Run only the Telegram operator:

```bash
source .venv-telegram-agent/bin/activate
python app/telegram_codex_operator.py
```

## Telegram Commands

- `/start`
- `/status`
- `/reset`
- `/voice`
- `/history_status`
- `/history_sync`

Text and voice requests are routed to the selected harness. The bridge keeps Telegram typing or recording indicators active until the final reply is delivered, and sends compact progress updates during longer runs.

## Persistence

Local runtime state is intentionally ignored by git:

- `telegram_operator_state.json`
- `telegram_operator_memory.jsonl`
- `telegram_operator_messages.sqlite3`
- `telegram_operator_board_state.json`

The SQLite database records message metadata, transcripts, callbacks, outgoing replies, and completed agent-turn metadata.

## Publishing Reminder

Before publishing a copy, remove local runtime files and rotate any Telegram bot token that was ever stored in the project. See `docs/PUBLISHING.md`.

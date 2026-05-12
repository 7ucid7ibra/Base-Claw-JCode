# Telegram Coding Agent Operator

This service turns a Telegram bot into a local coding-agent operator on this machine.

It supports:

- Telegram text messages
- Telegram voice notes
- Whisper transcription for incoming voice
- Codex CLI execution with persisted session resume
- Safe mode approval cards with Telegram inline Approve/Cancel buttons
- Kokoro voice replies
- A small local settings UI
- Per-chat persistent session mapping
- Local memory log of requests and replies
- SQLite message journal with message metadata

## Important

This operator is intentionally high trust. It is useful as a private local-agent bridge, but it is not a hardened multi-user service.

- In `restricted` mode, every normal task gets a Telegram approval card first.
- In `safe` mode, Codex uses workspace-write sandboxing.
- In `code` mode, Codex can edit this app repository with workspace-write sandboxing and automatic git commits.
- In `full` mode, Codex uses `--dangerously-bypass-approvals-and-sandbox`.
- Codex runs with the same local permissions as this user account.
- It should only be allowed for your own Telegram chat id.
- Anyone who can send commands through the allowed chat can cause local actions on this machine.
- With restricted mode enabled, normal Telegram requests first produce a read-only Codex proposal card. The real agent request is not executed until you tap `Approve`.
- The sandbox and approval card are practical guardrails, not a complete security boundary.

## Files

- `app/telegram_codex_operator.py`
- `app/telegram_operator_ui.py`
- `scripts/start_telegram_operator_ui.ps1`
- `requirements/telegram-operator.txt`
- `.env.telegram-operator.example`

## Environment

Copy `.env.telegram-operator.example` to `.env.telegram-operator` or set the variables in your shell.

Keep `.env.telegram-operator` local only. It is ignored by git and should never be published. If a real bot token was ever committed or shared, rotate it before making the project public.

Required:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`

The operator script prefers the dedicated file:

- `.env.telegram-operator`

This avoids accidentally picking up credentials from a different Telegram bot config.

Recommended defaults:

- `TELEGRAM_OPERATOR_WORKDIR=agent_workspace`
- `TELEGRAM_OPERATOR_REMOTE_SPEECH_URL=` can be left empty for local speech, or set to one external Kokoro/Whisper host.
- `TELEGRAM_OPERATOR_KOKORO_VOICE=af_alloy`
- `TELEGRAM_OPERATOR_WHISPER_MODEL=base`
- `TELEGRAM_OPERATOR_PROVIDER=codex`

Codex settings:

- `TELEGRAM_OPERATOR_PROVIDER=codex` uses the native Codex bridge and persisted Codex session ids. The UI is intentionally Codex-only for now.
- `TELEGRAM_OPERATOR_CODEX_MODEL` can be empty for the Codex CLI default, or set from the UI dropdown.
- `TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS=900` limits each agent run so a stuck CLI cannot block Telegram forever.
- `TELEGRAM_OPERATOR_SAFETY_MODE=safe` selects the Codex safety level.
- `TELEGRAM_OPERATOR_SAFE_MODE` is a legacy compatibility flag. The UI now writes `TELEGRAM_OPERATOR_SAFETY_MODE`.
- `TELEGRAM_OPERATOR_SQLITE_PATH=telegram_operator_messages.sqlite3` stores incoming messages, outgoing replies, callbacks, transcripts, and agent-turn metadata in SQLite. The UI does not expose this path; it is fixed to the app folder by default.
- `TELEGRAM_OPERATOR_REMOTE_SPEECH_URL` is the single optional remote host for both Kokoro TTS and Whisper transcription. If it is empty or unreachable, the bridge falls back to local `http://127.0.0.1:8766`.
- `TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK=true` controls whether the client tries local `127.0.0.1:8766` when the remote host is empty or unavailable. Set it to `false` for host-only lightweight clients.
- `TELEGRAM_OPERATOR_STARTUP_NOTICE=true` sends a short Telegram text notice to the allowed chat ids after the operator starts.
- The remote speech host can be entered as a bare IP or hostname. The app adds `http://` and port `8766` automatically when they are omitted.
- If no workspace is selected, the app uses `agent_workspace` in this folder. Its default map is `agent/skills`, `agent/memory`, `agent/senses`, `work/prototypes`, `work/projects`, and `work/routines`.

Codex must be installed, available on `PATH`, and authenticated before the operator can run. On a fresh machine, open a normal terminal and run `codex login` before starting the UI. The UI checks for the CLI at startup and the operator returns a clear error if Codex is missing or appears unauthenticated.

On Windows, the app prefers `codex.cmd`, then `codex.exe`, and only falls back to `codex.ps1` through PowerShell. This avoids launching a bare npm shim as if it were a native executable.

## Install

Create the dedicated environment:

```powershell
py -3.11 -m venv .venv-telegram-agent
.\.venv-telegram-agent\Scripts\Activate.ps1
python -m pip install -r requirements\telegram-operator.txt
```

For a lightweight host-only client, use:

```powershell
.\install.ps1 -Mode client -NoLocalSpeechFallback
```

## Run

Make sure the Kokoro server is running first on `127.0.0.1:8766`.

To open the local settings window:

```powershell
.\start-ui.ps1
```

Then start the operator:

```powershell
.\.venv-telegram-agent\Scripts\Activate.ps1
python app\telegram_codex_operator.py
```

For a simple restart loop:

```powershell
.\run-operator.ps1
```

For a quick local check:

```powershell
.\.venv-telegram-agent\Scripts\python.exe app\verify_install.py
```

## Telegram Commands

- `/start`
- `/status`
- `/reset`

Text and voice requests run through one resumed Codex session per Telegram chat. The operator keeps the Telegram typing or recording indicator active until the final reply has been sent. While Codex is running, the bridge sends small status updates every couple of minutes based on streamed Codex events, such as command execution, SSH work, or final reply preparation.

When restricted mode is enabled, regular text and voice-note requests produce a proposal card with:

- the expected actions
- workspace boundary notes
- risks
- `Approve` and `Cancel` buttons

Approving the card runs the original request. Cancelling discards it. Approved requests include the proposal text in the final agent prompt and instruct the agent to stay inside `TELEGRAM_OPERATOR_WORKDIR` unless the approved proposal explicitly covers outside paths.

Safety modes:

- `restricted`: sends a Telegram approval card before each task. The proposal step is read-only and ephemeral; approved execution uses Codex `workspace-write` sandboxing.
- `safe`: runs Codex with `workspace-write` sandboxing. Reading and writing inside the workspace are permitted. Outside-workspace work should be requested explicitly and may be blocked by the sandbox.
- `code`: runs Codex with `workspace-write` sandboxing from the app repository root so it can edit its own code. The bridge commits any existing repo changes before the run as a checkpoint, then commits agent changes after the run for easy revert.
- `full`: runs Codex with `--dangerously-bypass-approvals-and-sandbox`.

## Persistence

Session ids are stored in:

- `telegram_operator_state.json`

Message and reply logs are stored in:

- `telegram_operator_memory.jsonl`

Full message metadata is stored in SQLite:

- `telegram_operator_messages.sqlite3`

The database table is `telegram_messages`. It records the direction, event type, chat id, Telegram message id, Telegram user metadata, message type, text, transcript, session id, safe mode state, approval id, and a JSON metadata column for raw Telegram details.

Quick inspection:

```powershell
sqlite3 .\telegram_operator_messages.sqlite3 "select id, recorded_at, direction, event_type, message_type from telegram_messages order by id desc limit 20;"
```

Codex also persists its own session data under the normal local Codex home.

## Behavior

- Text messages are sent into Codex.
- Voice notes are downloaded, transcribed with Whisper, then sent into Codex.
- Replies are sent back as a Kokoro voice note with the text as its caption when it fits.
- The Kokoro server also exposes `POST /transcribe`, so Whisper can run on the same host as Kokoro. The UI shows one remote speech host field for both.
- Modern speech hosts also expose `POST /synthesize_voice_note`, so lightweight clients do not need local ffmpeg for normal Telegram voice replies.
- If voice transcription fails before Codex receives the request, the bot sends a Telegram error message instead of silently stopping the recording indicator.
- Codex keeps one resumed conversation per Telegram chat id.
- Safe mode proposal generation uses Codex in read-only, ephemeral mode so proposal creation does not intentionally modify files or reuse the active coding session.
- For voice-note requests, the Telegram `recording voice note` indicator is kept alive until the reply delivery finishes.
- Agent turns send compact status updates every couple of minutes while Codex is still running.
- If an agent exceeds the configured timeout, the bridge returns an operator error and unlocks the chat for the next request.

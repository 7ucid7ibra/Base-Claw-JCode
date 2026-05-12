# TTS Server

This project contains a local Kokoro-82M HTTP TTS server for fast preset-voice speech generation. It uses `hexgrad/Kokoro-82M`, serves WAV audio at 24000 Hz, and includes a helper for converting generated speech into a Telegram voice note.

Kokoro is intentionally isolated in its own Python 3.11 environment at `.venv-kokoro`; do not mix this setup into a Tortoise environment.

German Kokoro support is available as an optional local add-on when compatible code and model assets are placed under `kokoro_german/` and `german_kokoro/`. The fresh checkout includes placeholder folders, not the large model files.

## Project Status

This is an alpha local-agent foundation for tinkering, learning, and private-machine workflows. It is intentionally lightweight: Telegram is the remote interface, Kokoro and Whisper provide speech, Codex handles local coding actions, and the app folder contains the default workspace and message journal.

Before publishing or sharing it, read `docs/PUBLISHING.md`. In particular, do not publish a real `.env.telegram-operator`, logs, SQLite databases, generated audio, virtual environments, or downloaded model folders.

## Project Layout

- `app/`: Python entry points for Kokoro, Telegram, the UI, and install verification.
- `scripts/`: Windows installer and service-start helper scripts.
- `requirements/`: split Python dependency files for Kokoro and the Telegram operator.
- `docs/`: usage, operator, publishing, and layout notes.
- `agent_workspace/`: the default assistant home.
- `custom_voices/`, `german_kokoro/`, `kokoro_german/`: optional local voice/model asset folders.

## Windows Setup

For the easiest all-in-one local setup:

```powershell
.\install.ps1 -Mode full
```

For a speech host only:

```powershell
.\install.ps1 -Mode host
```

For a lightweight client that relies only on a speech host:

```powershell
.\install.ps1 -Mode client -NoLocalSpeechFallback
```

Host and full installs need system speech tools:

```powershell
choco install ffmpeg espeak-ng -y
```

If `espeak-ng` cannot be installed globally from a non-admin shell, you can place a local copy under `tools\espeak-ng` and add it to the current shell:

```powershell
$env:Path = "$PWD\tools\espeak-ng\eSpeak NG;$env:Path"
```

See `docs/INSTALLATION_MODES.md` for the full breakdown.

## Start Kokoro

```powershell
.\.venv-kokoro\Scripts\Activate.ps1
$env:Path = "$PWD\tools\espeak-ng\eSpeak NG;$env:Path"
python app\kokoro_server.py
```

The server binds to `0.0.0.0:8766`.

On Windows you can also run:

```powershell
.\start-kokoro.ps1
```

## Endpoints

- `GET /health` returns service status, repo ID, and loaded language pipelines.
- `GET /languages` returns supported Kokoro language codes.
- `GET /voices` scans the local Hugging Face cache and returns cached voice files for `hexgrad/Kokoro-82M`.
- `GET /voices` also reports any local `.pt` files in `custom_voices/` as `custom_voices`.
- `GET /voices` reports German Kokoro voices under `german_voices`.
- `POST /synthesize` accepts `text`, `voice`, `lang_code`, and `speed`, then returns raw WAV bytes.
- `POST /synthesize_voice_note` accepts the same body and returns OGG/Opus bytes for Telegram voice notes.

## Custom Voices

Local community voice packs can be placed in:

```text
custom_voices
```

If a file is named `am_dylan.pt`, you can call `/synthesize` with `"voice": "am_dylan"`.

## Optional German Kokoro

The server can also support:

- `lang_code: "d"` for German
- `voice: "dm_martin"` when `german_kokoro/voices/martin.pt` is installed
- `voice: "dm_victoria"` when `german_kokoro/voices/victoria.pt` is installed

The optional German setup expects:

```text
kokoro_german/kokoro/
german_kokoro/config.json
german_kokoro/kikiri_german_martin_ep10.pth
german_kokoro/voices/*.pt
```

Example:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8766/synthesize `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"text":"Hello from Kokoro","voice":"am_adam","lang_code":"a","speed":1.0}' `
  -OutFile kokoro.wav
```

## Remote Telegram Usage

Use `app\kokoro_remote_telegram.py` from any machine that can reach the Kokoro server:

```powershell
python app\kokoro_remote_telegram.py `
  --server-url http://LAN_IP:8766 `
  --text "Hello from Kokoro" `
  --voice am_adam `
  --lang-code a `
  --speed 1.0 `
  --bot-token YOUR_BOT_TOKEN `
  --chat-id YOUR_CHAT_ID `
  --caption "Kokoro"
```

You can also set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `KOKORO_SERVER_URL` once in the environment and omit the matching flags.

See `docs/KOKORO_REMOTE_USAGE.md` for complete setup, API, and manual conversion examples.

## Telegram Coding Agent Operator

This repo also includes a Telegram-controlled coding-agent operator bridge:

- Telegram text and voice notes in
- Whisper transcription for incoming voice
- Codex CLI operation with a simple local settings UI
- Persistent Codex CLI session resume per Telegram chat when Codex is selected
- Compact live status updates during long foreground tasks
- Kokoro voice replies with selectable voice
- A small Windows settings UI
- Full local permissions through the selected local agent

Files:

- `app/telegram_codex_operator.py`
- `app/telegram_operator_ui.py`
- `scripts/start_kokoro_server.ps1`
- `scripts/start_telegram_operator_ui.ps1`
- `scripts/run_telegram_codex_operator.ps1`
- `requirements/telegram-operator.txt`
- `.env.telegram-operator.example`
- `docs/TELEGRAM_CODEX_OPERATOR.md`

Create its environment:

```powershell
py -3.11 -m venv .venv-telegram-agent
.\.venv-telegram-agent\Scripts\Activate.ps1
python -m pip install -r requirements\telegram-operator.txt
```

Open the settings window:

```powershell
.\start-ui.ps1
```

Run it:

```powershell
.\.venv-telegram-agent\Scripts\Activate.ps1
python app\telegram_codex_operator.py
```

You can also run `.\run-operator.ps1` to keep the operator restarting after crashes.

The operator reads its own dedicated config from `.env.telegram-operator`, so it can stay pinned to a specific bot, chat allowlist, workspace home, Codex model, and Kokoro voice.

`.env.telegram-operator` is local-only and ignored by git. Start from `.env.telegram-operator.example`, then fill in your own bot token and allowed chat id. If a real bot token was ever committed or shared, rotate it before publishing the project.

The UI has four safety modes:

- `Restricted`: sends a Telegram approval card before each task. Proposal generation is read-only; approved execution uses Codex workspace-write sandboxing.
- `Safe`: lets Codex read/write inside the workspace with workspace-write sandboxing. It should ask before outside-workspace work.
- `Code access`: lets Codex edit this app repository with workspace-write sandboxing. Before the run, any existing repo changes are committed as a checkpoint; after the run, agent changes are committed so they can be reverted with git.
- `Full access`: runs Codex with full local access and no sandbox.

The older `TELEGRAM_OPERATOR_SAFE_MODE` flag is kept only for compatibility. New settings use `TELEGRAM_OPERATOR_SAFETY_MODE`.

The operator also keeps an automatic SQLite message journal at `telegram_operator_messages.sqlite3` in the project folder. The `telegram_messages` table records incoming text, incoming voice metadata, transcripts, callbacks, outgoing text and voice replies, safe-mode approval events, and completed agent-turn metadata.

Requests run through one resumed Codex session per Telegram chat. The Telegram typing or recording indicator stays active until the final reply is delivered. During longer Codex runs, the bridge sends small status updates every couple of minutes, based on streamed Codex events.

Voice selection uses Kokoro language codes: `a` is American English, `b` is British English, and `d` is the optional local German Kokoro pipeline. The UI auto-updates the code for common voice prefixes like `af_`, `am_`, `bf_`, `bm_`, and `dm_`.

For speech hosting, `TELEGRAM_OPERATOR_REMOTE_SPEECH_URL` is the only visible host setting. Leave it blank for local speech. If it is set, both Kokoro and Whisper try that host first and fall back to local `http://127.0.0.1:8766` if it is unreachable.

The remote speech host field accepts a bare IP or hostname. The app adds `http://` and port `8766` automatically when they are omitted.

For lightweight clients, turn off `TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK` or install with `.\install.ps1 -Mode client -NoLocalSpeechFallback`. Then the client relies solely on the configured speech host and does not need local Kokoro, faster-whisper, or ffmpeg for normal operation.

When local speech fallback is enabled, the UI tries to start the local Kokoro/Whisper host before starting the Telegram operator. The operator can also send a short startup notice to the allowed chat ids so pressing Start has visible feedback.

If no workspace home is selected, the UI uses `agent_workspace` inside the project folder as the assistant's default home.

That workspace starts with a small folder map: `agent/skills`, `agent/memory`, `agent/senses`, `work/prototypes`, `work/projects`, and `work/routines`. The map is documented in `agent_workspace/AGENT_HOME.md`.

Read `docs/TELEGRAM_CODEX_OPERATOR.md` before using it. This bridge is intentionally high trust.

## Codex Requirements

The Telegram operator expects the Codex CLI to be installed, available on `PATH`, and authenticated before it starts. Run `codex login` in a normal local terminal first. The UI and verifier now check whether `codex` is available and report a setup error instead of silently starting a broken operator.

## Verification

Run:

```powershell
.\.venv-telegram-agent\Scripts\python.exe app\verify_install.py
```

The verifier checks Python syntax, expected imports for the selected mode, speech-host tools for host/full installs, the Codex CLI for client/full installs, and the local Kokoro `/health` endpoint when the server is running.

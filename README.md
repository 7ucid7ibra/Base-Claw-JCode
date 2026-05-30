# BaseClaw

BaseClaw is a minimal local-agent foundation for private-machine workflows. It gives you a Telegram interface, desktop chat, speech in and out, safety controls, and a small local settings UI without trying to become a full agent platform.

Use it as a clean base for:

- learning how local coding agents are wired together
- running a private Telegram-controlled assistant on your own machine
- switching between local models through JCode and cloud CLIs such as Codex, Claude, or Gemini
- building custom skills, automations, and workflows on top of a stable foundation

It also contains a local Kokoro-82M HTTP TTS server for fast preset-voice speech generation. The server uses `hexgrad/Kokoro-82M`, serves WAV audio at 24000 Hz, and includes a helper for converting generated speech into a Telegram voice note.

Kokoro is intentionally isolated in its own Python 3.11 environment at `.venv-kokoro`; do not mix this setup into a Tortoise environment.

German Kokoro support is available as an optional local add-on when compatible code and model assets are placed under `voice_assets/`. The fresh checkout includes placeholder READMEs, not the large model files.

## Project Status

This is an alpha foundation for tinkering, learning, and private-machine workflows. It is intentionally lightweight: Telegram and the desktop UI are the interfaces, Kokoro and Whisper provide speech, JCode can connect to local models, and Codex, Claude, or Gemini can be used directly when preferred.

Before publishing or sharing it, read `docs/PUBLISHING.md`. In particular, do not publish a real `.env.telegram-operator`, logs, SQLite databases, generated audio, virtual environments, or downloaded model folders.

## License

BaseClaw is licensed under the Apache License 2.0. The project is intended to stay permissive for learning, personal use, commercial customization, and hosted setup work.

## Project Layout

- `app/`: Python entry points for Kokoro, Telegram, and the UI.
- `scripts/`: installer, build, and service helper scripts.
- `launchers/`: double-click and platform-specific launcher wrappers.
- `packaging/`: installer packaging definitions.
- `requirements/`: split Python dependency files for Kokoro and the Telegram operator.
- `docs/`: usage, operator, troubleshooting, publishing, and layout notes.
- `agent_workspace/`: the default assistant home.
- `voice_assets/`: optional local Kokoro voice/model asset folders.

## What You Need

Required:

- Python 3.11 or newer
- A Telegram bot token and your allowed chat id

Choose at least one agent path:

- JCode for local mode, usually with LM Studio or Ollama
- Codex CLI, authenticated with `codex login`
- Claude CLI, authenticated through Claude Code
- Gemini CLI, authenticated through the official `@google/gemini-cli`

Optional:

- LM Studio or Ollama for local models
- Kokoro/Whisper speech setup for voice notes and spoken replies

## macOS / Linux Quick Start

After cloning the repository, run:

```bash
./install.sh
```

The script is safe to rerun. First setup asks about optional components such as Codex, Claude, Gemini, JCode, Ollama, and Kokoro voice dependencies, then saves those choices locally. Normal reruns use the saved choices and launch without optional setup prompts. Use `./install.sh --setup` to change optional components later, or `./start.sh` for the shortest daily launch command.

On macOS, `install.sh` also creates a user-level launcher at `~/Applications/BaseClaw.app`. After the first setup, you can start BaseClaw by double-clicking that app instead of opening Terminal in the project folder.

On macOS you can also double-click `launchers/macos/install-macos.command` for first setup or `launchers/macos/start-macos.command` for daily startup. To generate a simple local `BaseClaw.app` wrapper, run:

```bash
./scripts/build_macos_app.sh
```

The generated app is placed under `dist/` and is intentionally not committed. It is a convenience wrapper around `./start.sh`.

To build an alpha macOS DMG from a clean staged copy, run:

```bash
./scripts/build_macos_dmg.sh
```

The DMG is written under `dist/`. It is not signed or notarized yet, so macOS may require Control-click, then Open.

The Runtime panel has an Update button that pulls the newest public BaseClaw archive from GitHub and overlays it onto the current install. Restart the UI manually after the update finishes.

Useful options:

```bash
./install.sh --with-kokoro
./install.sh --without-kokoro
./install.sh --setup
./start.sh
./install.sh --no-launch
./install.sh --yes
```

`--no-launch` skips opening the UI after setup. Start it later with `./start.sh`.

`--yes` also accepts optional global CLI installs, so use it only when npm/Homebrew installs are acceptable on that machine.

Codex, Claude, and Gemini still require their normal login steps after installation. JCode must be installed for the default local mode. LM Studio must be started manually with a loaded model if you want local JCode models.

## Windows Setup

For the easiest all-in-one local setup:

```powershell
.\install.ps1 -Mode full
```

For a guided first setup with questions about Kokoro/Whisper speech and optional provider CLIs:

```powershell
.\install.ps1
```

For a double-click Windows installer window, open:

```text
launchers\windows\install-wizard.cmd
```

The wizard lets you choose client/full/speech-host mode, JCode, Codex, Claude, Gemini, and whether to launch the UI after installation.

To build a normal Windows setup executable, install Inno Setup and run:

```powershell
.\scripts\build_windows_installer.ps1
```

The build script stages a clean copy, excludes local secrets, virtual environments, logs, and SQLite state, then writes `dist\BaseClawSetup.exe`.

Provider tools are optional install choices:

```powershell
.\install.ps1 -Mode client -InstallJCode
.\install.ps1 -Mode client -InstallCodex
.\install.ps1 -Mode client -InstallClaude
.\install.ps1 -Mode client -InstallGemini
.\install.ps1 -Mode client -InstallProviderTools
```

`-InstallJCode` downloads the matching Windows JCode release into `tools\jcode\jcode.exe`. The Windows start script adds that folder to `PATH` automatically, so a project-local JCode install is enough for the UI and operator.

The Windows installer and start script set `JCODE_NO_TELEMETRY=1` by default for private-machine use.

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

See `docs/INSTALLATION_MODES.md` for the full breakdown and `docs/TROUBLESHOOTING.md` if setup opens but one service does not respond.

## Start Kokoro

```powershell
.\.venv-kokoro\Scripts\Activate.ps1
$env:Path = "$PWD\tools\espeak-ng\eSpeak NG;$env:Path"
python app\kokoro_server.py
```

The server binds to `0.0.0.0:8766`.

On Windows you can also run:

```powershell
.\launchers\windows\start-kokoro.ps1
```

## Endpoints

- `GET /health` returns service status, repo ID, and loaded language pipelines.
- `GET /languages` returns supported Kokoro language codes.
- `GET /voices` scans the local Hugging Face cache and returns cached voice files for `hexgrad/Kokoro-82M`.
- `GET /voices` also reports any local `.pt` files in `voice_assets/custom/` as `custom_voices`.
- `GET /voices` reports German Kokoro voices under `german_voices`.
- `POST /synthesize` accepts `text`, `voice`, `lang_code`, and `speed`, then returns raw WAV bytes.
- `POST /synthesize_voice_note` accepts the same body and returns OGG/Opus bytes for Telegram voice notes.

## Custom Voices

Local community voice packs can be placed in:

```text
voice_assets/custom
```

If a file is named `am_dylan.pt`, you can call `/synthesize` with `"voice": "am_dylan"`.

## Optional German Kokoro

The server can also support:

- `lang_code: "d"` for German
- `voice: "dm_martin"` when `voice_assets/german/voices/martin.pt` is installed
- `voice: "dm_victoria"` when `voice_assets/german/voices/victoria.pt` is installed

The optional German setup expects:

```text
voice_assets/german_package/kokoro/
voice_assets/german/config.json
voice_assets/german/kikiri_german_martin_ep10.pth
voice_assets/german/voices/*.pt
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

## BaseClaw Telegram Operator

BaseClaw includes a Telegram-controlled coding-agent operator bridge:

- Telegram text and voice notes in
- Desktop chat in the local UI
- Whisper transcription for incoming voice
- JCode local-model operation with LM Studio, Ollama, or hosted JCode providers
- Direct Codex CLI, Claude CLI, and Gemini CLI operation
- Persistent session resume where the selected harness supports it
- Compact live status updates during long foreground tasks
- Kokoro voice replies with selectable voice
- A small local settings UI
- Local slash commands from the selected workspace
- Named agent profiles for running multiple isolated Telegram bots from one install
- Configurable access and action safety modes

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
.\launchers\windows\start-ui.ps1
```

Run it:

```powershell
.\.venv-telegram-agent\Scripts\Activate.ps1
python app\telegram_codex_operator.py
```

You can also run `.\launchers\windows\run-operator.ps1` to keep the operator restarting after crashes.

The operator reads its own dedicated config from `.env.telegram-operator`, so it can stay pinned to a specific bot, chat allowlist, workspace home, selected harness, model provider, and Kokoro voice. The UI can also create named agent profiles. Each profile gets its own env file, workspace, SQLite history, session state, and logs under `profiles/<name>/`, so multiple Telegram bots can run at the same time from one BaseClaw install.

Telegram has a small built-in command set for status, help, reset, voice, read, update, and restart. `/read` creates one-off voice notes: read the latest assistant reply, reply to an older message with `/read`, or use `/read next` to read only the next assistant response. User-defined slash commands live outside source code in the selected workspace's `slash_commands/` folder. Add a file such as `summarize.md`, then call `/summarize ...` in Telegram to run those local instructions through the selected agent.

`.env.telegram-operator` is local-only and ignored by git. Start from `.env.telegram-operator.example`, then fill in your own bot token and allowed chat id. If a real bot token was ever committed or shared, rotate it before publishing the project.

The UI separates path access from action behavior:

- Access scope: workspace only, workspace plus this app code, or full machine access.
- Additional allowed paths can be added explicitly.
- Action mode: read-only, ask before write-oriented work, or full execution.
- Codex has the strongest native sandbox support. Other harnesses receive policy instructions and process-level limits.

The older `TELEGRAM_OPERATOR_SAFE_MODE` flag is kept only for compatibility. New settings use `TELEGRAM_OPERATOR_SAFETY_MODE`.

The operator also keeps an automatic SQLite message journal at `telegram_operator_messages.sqlite3` in the project folder. The `telegram_messages` table records incoming text, incoming voice metadata, transcripts, callbacks, outgoing text and voice replies, safe-mode approval events, and completed agent-turn metadata.

Requests run through the selected harness. The Telegram typing or recording indicator stays active until the final reply is delivered. During longer runs, the bridge sends small status updates every couple of minutes.

Voice selection uses Kokoro language codes: `a` is American English, `b` is British English, and `d` is the optional local German Kokoro pipeline. The UI auto-updates the code for common voice prefixes like `af_`, `am_`, `bf_`, `bm_`, and `dm_`.

Written Telegram replies and spoken Kokoro replies are separated. Links, file paths, and code blocks stay visible in the text message, but the spoken voice note uses a cleaned version so it says short labels like `plane.so` instead of reading full URLs, slashes, or long local paths aloud.

For speech hosting, set `Host IP / name` and `STT/TTS port` in the UI. Use `127.0.0.1` for local speech, or another reachable IP/hostname for a separate speech host. The STT/TTS port is used for both Whisper transcription and Kokoro voice output. BaseClaw also tries local speech candidates automatically, so a stale remote host does not prevent a working local Kokoro server from being used. If no speech host is reachable, BaseClaw can still start text-only and voice features stay unavailable until speech is configured. The operator can also send a short startup notice to the allowed chat ids so pressing Start has visible feedback.

When JCode is used with LM Studio or Ollama, BaseClaw creates or updates a JCode provider profile from the selected Host IP/name, the automatically selected local model port, and model before running the agent. LM Studio uses port `1234` and Ollama uses port `11434` by default. Session resume state is stored per harness, so switching between Claude, Codex, Gemini, and JCode does not reuse stale session ids.

If no workspace home is selected, the UI uses `agent_workspace` inside the project folder as the assistant's default home.

That workspace starts with local-only folders for `skills`, `automations`, `projects`, `slash_commands`, `notes`, `scratch`, `artifacts`, and `uploads`. Git only tracks the starter note at `agent_workspace/AGENT_HOME.md`; the rest is private runtime work.

Read `docs/TELEGRAM_CODEX_OPERATOR.md` before using it. This bridge is intentionally high trust.

## Provider Requirements

- JCode local mode expects `jcode` on `PATH`. For LM Studio, start the LM Studio local server and load a model first.
- Codex mode expects `codex` on `PATH` and authenticated with `codex login`.
- Claude mode expects `claude` on `PATH` and authenticated through the Claude CLI.
- Gemini mode expects `gemini` on `PATH` from the official `@google/gemini-cli` package and authenticated before use.

## Verification

Basic checks:

```bash
bash -n install.sh
python -m py_compile app/telegram_operator_ui.py app/telegram_codex_operator.py app/kokoro_server.py
```

Also start Kokoro and confirm `/health`, `/voices`, and a real synthesis call before publishing a voice-enabled release.

# BaseClaw

BaseClaw is my attempt to make the most basic, easy-to-use local agent foundation: a small private bridge that lets a person run an AI coding agent from Telegram or a desktop UI, then add their own skills, tools, connectors, automations, slash commands, and workflows over time.

It is meant to stay small. The core should handle the boring local plumbing: install, start, update, choose an agent harness, keep local history, pass files through, and optionally speak or transcribe. Everything personal or experimental should live in the local workspace, not in the public source.

BaseClaw is alpha software. It is useful for personal local-agent work, but it is still changing.

## What It Gives You

- Telegram-controlled local agent sessions.
- A small desktop UI for setup, runtime controls, profiles, speech settings, and logs.
- Agent harness support for Codex, Claude, Gemini, JCode, and a plain shell fallback.
- Optional local Kokoro text-to-speech and Whisper transcription.
- A local workspace for your own commands, skills, tools, connectors, automations, notes, scratch files, uploads, and artifacts.
- Simple install and start entrypoints for macOS, Linux, and Windows.

## Quick Start

macOS or Linux:

```bash
./install.sh
./start.sh
```

Windows PowerShell:

```powershell
.\install.ps1
.\launchers\windows\start-ui.ps1
```

Copy `.env.telegram-operator.example` to `.env.telegram-operator`, then set your own bot token, allowed chat id, workspace path, and preferred agent provider.

On macOS, the installer can also create a local `BaseClaw.app` launcher under `~/Applications`.

## Local Customization

The default workspace is `agent_workspace/`. It is intentionally the place to grow your own system without making the public repo messy.

Use it for:

- `skills/`
- `tools/`
- `connectors/`
- `automations/`
- `slash_commands/`
- `projects/`
- `notes/`
- `scratch/`
- `uploads/`
- `artifacts/`

Most runtime files, secrets, logs, models, profiles, and generated data are ignored by git.

## Repository Layout

- `app/` - operator, desktop UI, harness bridges, speech helpers, and process utilities.
- `scripts/` - install and runtime entrypoints used from a checkout.
- `launchers/` - tiny double-click wrappers for macOS and Windows.
- `packaging/` - files used to build distributable app and installer packages.
- `requirements/` - Python dependency groups for client, operator, Kokoro, and Whisper.
- `voice_assets/` - placeholder folders for local voices and models.
- `agent_workspace/` - tracked starter workspace plus ignored local runtime folders.
- `docs/` - focused reference docs.
- `tools/` - small repository utility commands.

## More Docs

- `docs/PROJECT_LAYOUT.md`
- `docs/APP_ARCHITECTURE.md`
- `docs/TELEGRAM_OPERATOR.md`
- `docs/INSTALLATION_MODES.md`
- `docs/KOKORO_REMOTE_USAGE.md`
- `docs/TROUBLESHOOTING.md`
- `docs/PUBLISHING.md`

## License

BaseClaw is licensed under the Apache License 2.0.

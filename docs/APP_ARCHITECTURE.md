# BaseClaw App Architecture

This is the current tracked `app/` layout on `main` after the cleanup pass.

```text
app/
├── telegram_operator.py
├── telegram_operator_ui.py
├── process_utils.py
├── harnesses/
│   ├── __init__.py
│   ├── bridges.py
│   ├── cli.py
│   └── desktop.py
├── operator_core/
│   ├── __init__.py
│   ├── attachments.py
│   ├── command_handlers.py
│   ├── commands.py
│   ├── config.py
│   ├── media_handlers.py
│   ├── storage.py
│   └── updates.py
├── speech/
│   ├── __init__.py
│   ├── client.py
│   ├── server.py
│   ├── urls.py
│   └── whisper_worker.py
└── ui/
    ├── __init__.py
    └── speech_panel.py
```

## Responsibilities

- `telegram_operator.py` is the Telegram runtime entrypoint. It wires the bot, provider bridges, command mixins, media mixins, update lifecycle mixins, storage, speech, and message dispatch.
- `telegram_operator_ui.py` is the desktop control UI entrypoint. It owns the visible UI layout and button wiring.
- `process_utils.py` contains shared process-launch helpers used by runtime and UI code.
- `harnesses/` contains provider-neutral agent harness integration:
  - `cli.py` resolves CLI commands for Codex, JCode, Claude, Gemini, and future compatible tools.
  - `bridges.py` contains provider bridge classes and routing.
  - `desktop.py` builds desktop chat commands.
- `operator_core/` contains Telegram operator internals that are shared by the runtime:
  - `config.py` handles environment parsing, config loading, startup summaries, and app path resolution.
  - `storage.py` handles operator state, memory logs, SQLite message history, and continuity summaries.
  - `commands.py` contains command text and keyboard builders.
  - `command_handlers.py` contains Telegram command and menu callback handlers.
  - `attachments.py` contains attachment filename/type helpers and PDF/text extraction helpers.
  - `media_handlers.py` contains Telegram document, photo, album, and video intake handlers.
  - `updates.py` contains source update, git checkpoint, and restart lifecycle helpers.
- `speech/` contains speech client/server pieces:
  - `client.py` contains speech client behavior and text/audio helper logic.
  - `server.py` runs the Kokoro speech HTTP service.
  - `urls.py` centralizes speech URL normalization, local-host detection, Tailscale discovery, deduplication, and list building.
  - `whisper_worker.py` handles Whisper transcription worker behavior.
- `ui/` contains focused desktop UI helper modules. `speech_panel.py` handles local speech install, start, stop, and status helpers.

Root-level `tools/` contains standalone utility scripts such as `verify_install.py` and `send_voice_note.py`; those scripts are intentionally outside `app/`.

## Maintenance Rules

- Keep cleanup changes behavior-preserving unless a Plane issue explicitly asks for behavior changes.
- Move one boundary at a time, verify imports and install checks, then commit.
- Prefer neutral names for shared BaseClaw modules. Provider-specific names should only remain in provider-specific implementation details.
- Use `operator_core/` instead of `operator/` so the package does not shadow Python's standard `operator` module.
- Do not move UI and Telegram operator code in the same slice unless the shared boundary is small and verified.

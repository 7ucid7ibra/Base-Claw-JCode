# BaseClaw App Architecture Target

This is the target shape for cleaning up the `app/` folder without changing behavior in large jumps.

```text
app/
├── telegram_operator.py
│   └── main Telegram operator entrypoint
│
├── operator_core/
│   ├── config.py
│   ├── storage.py
│   ├── runtime.py
│   ├── commands.py
│   ├── command_handlers.py
│   ├── callbacks.py
│   ├── attachments.py
│   ├── media_handlers.py
│   └── updates.py
│
├── harnesses/
│   ├── cli.py
│   │   └── generic CLI resolver for Codex, JCode, Claude, and Gemini
│   ├── bridges.py
│   │   └── provider bridge classes and routing
│   └── desktop.py
│       └── desktop chat command building for all providers
│
├── speech/
│   ├── server.py
│   ├── client.py
│   ├── urls.py
│   ├── text.py
│   └── whisper.py
│
├── ui/
│   ├── app.py
│   ├── profiles.py
│   ├── speech_panel.py
│   ├── updates_panel.py
│   ├── desktop_chat.py
│   └── process_controls.py
│
├── tools/
│   ├── verify_install.py
│   └── send_voice_note.py
```

## Refactor Rules

- Keep changes behavior-preserving unless a Plane issue explicitly asks for behavior changes.
- Move one boundary at a time, verify imports and install checks, then commit.
- Prefer neutral names for shared BaseClaw modules. Codex-specific names should only remain in Codex-specific implementation details.
- Use `operator_core/` instead of `operator/` so the package does not shadow Python's standard `operator` module.
- Do not move UI and Telegram operator code in the same slice unless the shared boundary is small and verified.

## Current Transitional State

- `app/harnesses/cli.py` is the generic CLI resolver.
- `app/harnesses/bridges.py` contains provider bridge classes.
- `app/harnesses/desktop.py` contains desktop chat command construction.
- `app/operator_core/attachments.py` contains attachment filename/type helpers and PDF/text extraction helpers.
- `app/operator_core/media_handlers.py` contains Telegram document, photo, album, and video intake handlers.
- `app/operator_core/commands.py` contains Telegram command text and keyboard builders.
- `app/operator_core/command_handlers.py` contains Telegram command and menu callback handlers that still depend on operator runtime state.
- `app/operator_core/config.py` contains operator environment parsing, config loading, and startup summary helpers.
- `app/operator_core/storage.py` contains operator state, memory log, SQLite message history, and continuity summary storage.
- `app/operator_core/updates.py` contains git source update helpers, code-mode git checkpoint helpers, and restart lifecycle helpers.
- `app/speech/client.py` currently holds speech client, text cleanup, Kokoro, and Whisper helpers until it is split into the target `speech/` package.
- `app/speech/urls.py` contains shared speech URL normalization, local-host detection, Tailscale speech host discovery, and URL deduplication.
- `tools/verify_install.py` and `tools/send_voice_note.py` are standalone utility scripts moved out of `app/`.

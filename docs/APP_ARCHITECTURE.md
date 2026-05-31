# BaseClaw App Architecture Target

This is the target shape for cleaning up the `app/` folder without changing behavior in large jumps.

```text
app/
├── telegram_operator.py
│   └── main Telegram operator entrypoint
│
├── operator/
│   ├── config.py
│   ├── runtime.py
│   ├── commands.py
│   ├── callbacks.py
│   ├── attachments.py
│   ├── storage.py
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
│
└── compat/
    ├── telegram_codex_operator.py
    └── codex_cli.py
```

## Refactor Rules

- Keep changes behavior-preserving unless a Plane issue explicitly asks for behavior changes.
- Move one boundary at a time, verify imports and install checks, then commit.
- Prefer neutral names for shared BaseClaw modules. Codex-specific names should only remain in compatibility wrappers or Codex-specific implementation details.
- Keep compatibility wrappers until launch scripts, profiles, and external update paths are migrated.
- Do not move UI and Telegram operator code in the same slice unless the shared boundary is small and verified.

## Current Transitional State

- `app/harnesses/cli.py` is the generic CLI resolver.
- `app/harnesses/codex.py` and `app/codex_cli.py` are compatibility wrappers for older imports.
- `app/harnesses/bridges.py` contains provider bridge classes.
- `app/harnesses/desktop.py` contains desktop chat command construction.
- `app/speech.py` currently holds speech client, text cleanup, Kokoro, and Whisper helpers until it is split into the target `speech/` package.

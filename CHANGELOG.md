# Changelog

## v0.1.3 - 2026-05-30

- Cleaned BaseClaw core so private board, machine, and history-sync workflows live outside public source.
- Removed built-in board shortcuts and history-sync Telegram commands from core.
- Kept generic operator behavior: Telegram chat, voice controls, attachments, local history, profiles, safety flow, update, and restart.
- Added ignored local workspace folders for skills, automations, projects, slash commands, notes, scratch, artifacts, and uploads.
- Added generic local slash-command loading from the active workspace without shipping user commands in source.
- Moved public voice asset placeholders into `voice_assets`.
- Updated installer, UI defaults, docs, ignore rules, and packaging helpers for the cleaned layout.


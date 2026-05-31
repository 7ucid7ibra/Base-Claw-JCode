# Changelog

## v0.1.5 - 2026-05-31

- Pointed the desktop Update button at GitHub `main` by default.
- Added a migration for the old clean-core update branch URL.
- Added non-main profile renaming in the desktop UI.
- Strengthened profile deletion with typed profile-name confirmation.
- Added verifier coverage for the GitHub-main update source.

## v0.1.4 - 2026-05-31

- Finished the public repo cleanup after the app split.
- Removed obsolete root wrappers and stale folders so the root stays small.
- Kept launchers, scripts, packaging, tools, voice assets, and workspace files in focused folders.
- Updated install flows for the split Kokoro and Whisper speech environments.
- Added verifier checks for the cleaned layout and stale wrapper regressions.
- Polished the README into a shorter project description and quick-start guide.

## v0.1.3 - 2026-05-30

- Cleaned BaseClaw core so private board, machine, and history-sync workflows live outside public source.
- Removed built-in board shortcuts and history-sync Telegram commands from core.
- Kept generic operator behavior: Telegram chat, voice controls, attachments, local history, profiles, safety flow, update, and restart.
- Added ignored local workspace folders for skills, automations, projects, slash commands, notes, scratch, artifacts, and uploads.
- Added generic local slash-command loading from the active workspace without shipping user commands in source.
- Moved public voice asset placeholders into `voice_assets`.
- Updated installer, UI defaults, docs, ignore rules, and packaging helpers for the cleaned layout.

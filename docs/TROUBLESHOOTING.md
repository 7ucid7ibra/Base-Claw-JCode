# Troubleshooting

Use this page when a fresh install opens but one part of BaseClaw does not respond.

## The UI Does Not Open

- Make sure Python is 3.11 or newer.
- On macOS, if Tkinter is missing, rerun `./install.sh`; the installer tries to install the matching `python-tk` package and recreate the UI environment.
- Start the UI directly with `.venv-telegram-agent/bin/python app/telegram_operator_ui.py`.

## Telegram Does Not Reply

- Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_IDS` are set in `.env.telegram-operator`.
- Press Start in the Runtime panel.
- Use `/status` in Telegram after the operator is running.
- Check the Recent Log panel for provider or credential errors.

## JCode Mode Fails

- Confirm `jcode` is on `PATH`.
- For LM Studio, start the local server and load a model before sending a request.
- For Ollama, start Ollama and confirm the model exists with `ollama list`.
- Press Refresh beside the model dropdown after changing LM Studio or Ollama.

## Codex Or Claude Mode Fails

- Codex requires the Codex CLI and `codex login`.
- Claude requires the Claude CLI and an authenticated Claude Code setup.
- If the UI says the CLI is missing, install or log in to that provider, then press Start again.

## Voice Or Speech Fails

- Text chat can run without speech.
- Voice notes and voice replies need an STT/TTS host.
- Use `127.0.0.1` and port `8766` for a local Kokoro/Whisper host.
- Use another reachable host/IP and the same STT/TTS port when speech runs on a different machine.
- If speech is unreachable, the UI can start text-only and disable voice replies for that run.

## Private GitHub Update Fails

- Install GitHub CLI.
- Run `gh auth login`.
- Use a repository URL in Update source, for example `https://github.com/OWNER/REPO`.
- A 404 from a private repository usually means GitHub CLI is not authenticated for that account.

## Clean Reinstall

- Keep `.env.telegram-operator` if you want to preserve settings.
- Remove `.venv-telegram-agent` to recreate the UI/operator environment.
- Remove `.venv-kokoro` to recreate the optional speech environment.
- Do not delete `telegram_operator_messages.sqlite3` unless you want to clear local chat history.

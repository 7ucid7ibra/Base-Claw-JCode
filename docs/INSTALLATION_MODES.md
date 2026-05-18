# Installation Modes

The project can be installed in three shapes.

## Full Local

Use this when one machine should run everything:

```powershell
.\install.ps1 -Mode full
```

This installs:

- Kokoro/Whisper speech host in `.venv-kokoro`
- Telegram/Codex client in `.venv-telegram-agent`
- `.env.telegram-operator` from the example if it does not exist

Start the speech host:

```powershell
.\start-kokoro.ps1
```

Start the UI:

```powershell
.\start-ui.ps1
```

## Speech Host

Use this on a stronger machine that will serve Kokoro TTS, Whisper transcription, and Telegram voice-note conversion:

```powershell
.\install.ps1 -Mode host
.\start-kokoro.ps1
```

The host needs:

- Python 3.11
- `ffmpeg`
- `espeak-ng`
- enough disk and CPU/RAM for Kokoro and faster-whisper models

The host exposes:

- `POST /synthesize`
- `POST /synthesize_voice_note`
- `POST /transcribe`

## Lightweight Client

Use this on a smaller machine that should only run Telegram, the UI, and Codex while relying on a speech host:

```powershell
.\install.ps1 -Mode client -NoLocalSpeechFallback
```

This installs only the client dependencies. It does not install Kokoro or faster-whisper locally.

Then open:

```powershell
.\start-ui.ps1
```

Set `Remote speech host` to the host machine, for example:

```text
http://100.x.y.z
```

The app adds port `8766` when no port is provided. With `-NoLocalSpeechFallback`, the client will not try `127.0.0.1:8766` if the host is unreachable.

If no remote host is configured and local fallback is disabled, voice input and voice replies fail with a setup error instead of trying to download local speech models.

## Manual Config

The relevant setting is:

```text
TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK=true
```

Set it to `false` for host-only clients that should never attempt local speech services.

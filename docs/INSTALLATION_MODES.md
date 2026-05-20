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

Set `Host IP / name` to the host machine and keep `STT/TTS port` at `8766`, for example:

```text
100.x.y.z
```

The app builds the speech URL from the host and STT/TTS port. With `-NoLocalSpeechFallback`, the client will not try `127.0.0.1:8766` if the host is unreachable.

If no speech host is configured or reachable, BaseClaw can still start text-only. Voice input and voice replies remain unavailable until STT/TTS is configured.

## Manual Config

The relevant setting is:

```text
TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK=true
```

Set it to `false` for host-only clients that should never attempt local speech services.

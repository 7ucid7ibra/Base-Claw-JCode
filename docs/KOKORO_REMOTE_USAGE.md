# Kokoro Remote Usage

This setup runs Kokoro-82M as a local HTTP server and optionally sends generated audio as a Telegram voice note. The server uses `hexgrad/Kokoro-82M`, binds to `0.0.0.0:8766`, and returns WAV audio at 24000 Hz.

## Create The Environment

From the project folder:

```powershell
py -3.11 -m venv .venv-kokoro
.\.venv-kokoro\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## Install Dependencies

Install Windows system tools:

```powershell
choco install ffmpeg espeak-ng -y
```

If `espeak-ng` cannot be installed globally from a non-admin shell, you can place a local copy under:

```text
tools\espeak-ng\eSpeak NG
```

Use that local copy in the current shell:

```powershell
$env:Path = "$PWD\tools\espeak-ng\eSpeak NG;$env:Path"
```

Install Python packages:

```powershell
python -m pip install -r requirements\kokoro.txt
```

If you are setting this up on macOS instead, install the system tools with Homebrew:

```bash
brew install ffmpeg espeak-ng
```

## Start The Server

```powershell
.\.venv-kokoro\Scripts\Activate.ps1
$env:Path = "$PWD\tools\espeak-ng\eSpeak NG;$env:Path"
python app\speech\server.py
```

Or on Windows:

```powershell
.\launchers\windows\start-kokoro.ps1
```

The server listens on `http://0.0.0.0:8766`. Use `http://127.0.0.1:8766` on the same machine.

## Find The Machine's LAN IP

On Windows:

```powershell
ipconfig
```

Look for the active adapter's `IPv4 Address`, such as `192.168.1.25`. Other devices on the same LAN can use `http://192.168.1.25:8766`.

## API Examples

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/health
```

Languages:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/languages
```

Cached voices:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/voices
```

That response now includes:

- `voices`: stock voices already cached from `hexgrad/Kokoro-82M`
- `custom_voices`: local `.pt` voice packs found in `voice_assets/custom/`

Synthesize WAV:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8766/synthesize `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"text":"Hello from Kokoro","voice":"am_adam","lang_code":"a","speed":1.0}' `
  -OutFile kokoro.wav
```

With `curl.exe`:

```powershell
curl.exe -X POST http://127.0.0.1:8766/synthesize `
  -H "Content-Type: application/json" `
  -d "{\"text\":\"Hello from Kokoro\",\"voice\":\"am_adam\",\"lang_code\":\"a\",\"speed\":1.0}" `
  --output kokoro.wav
```

Transcribe audio with faster-whisper on the same server:

```powershell
curl.exe -X POST http://127.0.0.1:8766/transcribe `
  -F "audio=@incoming.ogg" `
  -F "model=small"
```

The response contains `text` and `model`.

Generate a Telegram-ready OGG/Opus voice note on the host:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8766/synthesize_voice_note `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"text":"Hello from Kokoro","voice":"am_adam","lang_code":"a","speed":1.0}' `
  -OutFile kokoro_voice.ogg
```

## Manual Telegram Voice Conversion

Telegram voice notes should be OGG/Opus. Convert a Kokoro WAV with:

```powershell
ffmpeg -y -i kokoro.wav `
  -af "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11" `
  -c:a libopus -b:a 40k kokoro_voice.ogg
```

Fresh clients normally do not need local `ffmpeg` if the speech host exposes `/synthesize_voice_note`.

Send it with Telegram `sendVoice`:

```powershell
curl.exe -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/sendVoice" `
  -F chat_id=YOUR_CHAT_ID `
  -F voice=@kokoro_voice.ogg `
  -F caption="Kokoro"
```

## Remote Telegram Helper

Run this from a machine that can reach the Kokoro server:

```powershell
python tools\send_voice_note.py `
  --server-url http://192.168.1.25:8766 `
  --text "Hello from Kokoro" `
  --voice am_adam `
  --lang-code a `
  --speed 1.0 `
  --bot-token YOUR_BOT_TOKEN `
  --chat-id YOUR_CHAT_ID `
  --caption "Kokoro voice note"
```

If you prefer, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `KOKORO_SERVER_URL` as environment variables and leave those flags out.

Add `--out-dir C:\tmp\kokoro` to keep the intermediate `kokoro_voice.wav` and `kokoro_voice.ogg` files.

## Supported Language Codes

- `a`: American English
- `b`: British English
- `d`: German
- `e`: Spanish
- `f`: French
- `h`: Hindi
- `i`: Italian
- `j`: Japanese
- `p`: Brazilian Portuguese
- `z`: Mandarin Chinese

## English Voices

American:

`af_alloy`, `af_aoede`, `af_bella`, `af_heart`, `af_jessica`, `af_kore`, `af_nicole`, `af_nova`, `af_river`, `af_sarah`, `af_sky`, `am_adam`, `am_echo`, `am_eric`, `am_fenrir`, `am_liam`, `am_michael`, `am_onyx`, `am_puck`, `am_santa`

British:

`bf_alice`, `bf_emma`, `bf_isabella`, `bf_lily`, `bm_daniel`, `bm_fable`, `bm_george`, `bm_lewis`

## Optional Community Voices

Place compatible community voice `.pt` files in `voice_assets/custom/`. The fresh checkout includes only a README placeholder; model files are ignored by git.

Examples previously tested on the development machine:

- `am_dylan`
- `af_mika`
- `af_heart_young`

## Optional German Kokoro Voices

Place compatible German Kokoro assets in `voice_assets/german_package/` and `voice_assets/german/`. The fresh checkout includes only README placeholders; model files are ignored by git.

Examples previously tested on the development machine:

- `dm_martin`
- `dm_victoria`

Example German request:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8766/synthesize `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"text":"Guten Tag, dies ist ein Test.","voice":"dm_martin","lang_code":"d","speed":1.0}' `
  -OutFile kokoro_de.wav
```

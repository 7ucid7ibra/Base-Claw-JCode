from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Kokoro speech from a remote HTTP server and send it as a Telegram voice note.",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("KOKORO_SERVER_URL", "http://127.0.0.1:8766"),
        help="Base Kokoro server URL, for example http://192.168.1.20:8766",
    )
    parser.add_argument("--text", help="Text to synthesize")
    parser.add_argument("--text-file", dest="text_file", help="Path to a UTF-8 text file to synthesize")
    parser.add_argument("--voice", required=True, help="Kokoro voice name, for example am_adam")
    parser.add_argument("--lang-code", required=True, help="Kokoro language code, for example a")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed")
    parser.add_argument("--bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"), help="Telegram bot token")
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"), help="Telegram chat ID")
    parser.add_argument("--caption", default=None, help="Optional Telegram voice caption")
    parser.add_argument("--out-dir", default=None, help="Optional directory to keep the WAV and OGG files")
    return parser.parse_args()


def resolve_text(args: argparse.Namespace) -> str:
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
        if text.strip():
            return text
    if args.text and args.text.strip():
        return args.text
    env_text = os.environ.get("KOKORO_TEXT", "")
    if env_text.strip():
        return env_text
    raise SystemExit("Missing text. Pass --text, --text-file, or set KOKORO_TEXT.")


def synthesize(server_url: str, text: str, voice: str, lang_code: str, speed: float, wav_path: Path) -> None:
    url = server_url.rstrip("/") + "/synthesize"
    response = requests.post(
        url,
        json={
            "text": text,
            "voice": voice,
            "lang_code": lang_code,
            "speed": speed,
        },
        timeout=180,
    )
    if not response.ok:
        raise RuntimeError(f"Kokoro synth failed ({response.status_code}): {response.text}")
    wav_path.write_bytes(response.content)


def convert_to_telegram_voice(wav_path: Path, ogg_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-af",
        "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a",
        "libopus",
        "-b:a",
        "40k",
        str(ogg_path),
    ]
    subprocess.run(cmd, check=True)


def send_voice(bot_token: str, chat_id: str, ogg_path: Path, caption: Optional[str]) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendVoice"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    with ogg_path.open("rb") as voice_file:
        response = requests.post(url, data=data, files={"voice": voice_file}, timeout=180)
    if not response.ok:
        raise RuntimeError(f"Telegram sendVoice failed ({response.status_code}): {response.text}")
    return response.json()


def main() -> None:
    args = parse_args()
    text = resolve_text(args)
    if not args.bot_token:
        raise SystemExit("Missing Telegram bot token. Pass --bot-token or set TELEGRAM_BOT_TOKEN.")
    if not args.chat_id:
        raise SystemExit("Missing Telegram chat ID. Pass --chat-id or set TELEGRAM_CHAT_ID.")

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = out_dir / "kokoro_voice.wav"
        ogg_path = out_dir / "kokoro_voice.ogg"
        synthesize(args.server_url, text, args.voice, args.lang_code, args.speed, wav_path)
        convert_to_telegram_voice(wav_path, ogg_path)
        result = send_voice(args.bot_token, args.chat_id, ogg_path, args.caption)
    else:
        with tempfile.TemporaryDirectory(prefix="kokoro-telegram-") as tmp:
            tmp_dir = Path(tmp)
            wav_path = tmp_dir / "kokoro_voice.wav"
            ogg_path = tmp_dir / "kokoro_voice.ogg"
            synthesize(args.server_url, text, args.voice, args.lang_code, args.speed, wav_path)
            convert_to_telegram_voice(wav_path, ogg_path)
            result = send_voice(args.bot_token, args.chat_id, ogg_path, args.caption)

    print(result)


if __name__ == "__main__":
    main()

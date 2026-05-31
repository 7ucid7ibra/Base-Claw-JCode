from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests

from process_utils import hidden_subprocess_kwargs

LOGGER = logging.getLogger("telegram_operator")
SPEECH_CONNECT_TIMEOUT_SECONDS = 4
SPEECH_READ_TIMEOUT_SECONDS = 300
SPEECH_REQUEST_TIMEOUT = (SPEECH_CONNECT_TIMEOUT_SECONDS, SPEECH_READ_TIMEOUT_SECONDS)
SPOKEN_TEXT_MAX_CHARS = 2400

MARKDOWN_LINK_RE = re.compile(r"\[([^\]]{1,120})\]\((https?://[^)\s]+)\)")
URL_RE = re.compile(r"https?://[^\s<>)\]]+")
WINDOWS_PATH_RE = re.compile(r"(?<!\w)[A-Za-z]:\\[^\s<>\"]+")
UNIX_PATH_RE = re.compile(
    r"(?<!\w)(?:~|/(?:Users|home|var|tmp|mnt|media|opt|usr|etc|Applications))"
    r"(?:/[^\s<>\":;,|]+)+"
)
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)


def _spoken_url_label(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        return "the link"
    if host.startswith("www."):
        host = host[4:]
    return host or "the link"


def _spoken_path_label(path_text: str) -> str:
    cleaned = path_text.rstrip(".,;:)")
    normalized = cleaned.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1] if "/" in normalized else normalized
    if not name or name in {"~", "."}:
        return "a local path"
    if "." in name and len(name) <= 48:
        return f"the {name} file"
    if len(name) <= 36:
        return f"the {name} path"
    return "a local path"


def spoken_reply_text(text: str) -> str:
    """Return a Kokoro-friendly copy while leaving the written reply unchanged."""
    spoken = FENCED_CODE_RE.sub(" Code block omitted. ", text)
    spoken = MARKDOWN_LINK_RE.sub(lambda match: f"{match.group(1)} at {_spoken_url_label(match.group(2))}", spoken)
    spoken = URL_RE.sub(lambda match: _spoken_url_label(match.group(0)), spoken)
    spoken = WINDOWS_PATH_RE.sub(lambda match: _spoken_path_label(match.group(0)), spoken)
    spoken = UNIX_PATH_RE.sub(lambda match: _spoken_path_label(match.group(0)), spoken)
    spoken = re.sub(
        r"`([^`]{1,160})`",
        lambda match: _spoken_path_label(match.group(1))
        if "/" in match.group(1) or "\\" in match.group(1)
        else match.group(1),
        spoken,
    )
    spoken = re.sub(r"\s+", " ", spoken).strip()
    if len(spoken) > SPOKEN_TEXT_MAX_CHARS:
        spoken = spoken[:SPOKEN_TEXT_MAX_CHARS].rsplit(" ", 1)[0].rstrip() + "."
    return spoken


def readable_message_text(text: Optional[str], transcript: Optional[str], metadata_json: Optional[str] = None) -> str:
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        for key in ("source_text", "caption", "text"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for value in (text, transcript):
        if value and value.strip():
            return value.strip()
    return ""


def infer_kokoro_lang_code(voice: str, fallback: str = "a") -> str:
    prefix_map = {
        "af_": "a",
        "am_": "a",
        "bf_": "b",
        "bm_": "b",
        "dm_": "d",
    }
    for prefix, lang_code in prefix_map.items():
        if voice.startswith(prefix):
            return lang_code
    return fallback or "a"


class RemoteFirstWhisperTranscriber:
    def __init__(self, server_urls: list[str], model_name: str):
        self.server_urls = server_urls
        self.model_name = model_name

    def transcribe(self, audio_path: Path) -> str:
        if not self.server_urls:
            raise RuntimeError(
                "No Whisper hosts are configured. Set TELEGRAM_OPERATOR_REMOTE_SPEECH_URL "
                "or enable TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK with a local Kokoro server."
            )
        last_error: Optional[Exception] = None
        for server_url in self.server_urls:
            try:
                with audio_path.open("rb") as handle:
                    response = requests.post(
                        server_url + "/transcribe",
                        files={"audio": (audio_path.name, handle, "audio/ogg")},
                        data={"model": self.model_name},
                        timeout=SPEECH_REQUEST_TIMEOUT,
                    )
                response.raise_for_status()
                text = str(response.json().get("text", "")).strip()
                if not text:
                    raise RuntimeError("remote Whisper returned an empty transcript")
                LOGGER.info("Remote Whisper transcript succeeded url=%s model=%s", server_url, self.model_name)
                return text
            except Exception as exc:
                last_error = exc
                LOGGER.warning("Remote Whisper failed url=%s model=%s error=%s", server_url, self.model_name, exc)
        assert last_error is not None
        raise RuntimeError(f"All Whisper hosts failed: {last_error}") from last_error


class KokoroVoiceReply:
    def __init__(self, server_urls: list[str], voice: str, lang_code: str):
        self.server_urls = server_urls
        self.voice = voice
        self.lang_code = lang_code

    def synthesize_ogg(self, text: str, output_dir: Path) -> Path:
        if not self.server_urls:
            raise RuntimeError(
                "No Kokoro hosts are configured. Set TELEGRAM_OPERATOR_REMOTE_SPEECH_URL "
                "or enable TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK with a local Kokoro server."
            )
        wav_path = output_dir / "reply.wav"
        ogg_path = output_dir / "reply.ogg"
        last_error: Optional[Exception] = None
        for server_url in self.server_urls:
            try:
                response = requests.post(
                    server_url + "/synthesize_voice_note",
                    json={
                        "text": text,
                        "voice": self.voice,
                        "lang_code": self.lang_code,
                        "speed": 1.0,
                    },
                    timeout=SPEECH_REQUEST_TIMEOUT,
                )
                if response.status_code == 404:
                    raise RuntimeError("host does not expose /synthesize_voice_note")
                response.raise_for_status()
                ogg_path.write_bytes(response.content)
                return ogg_path
            except Exception as exc:
                last_error = exc
                LOGGER.warning("Remote voice-note synthesis failed url=%s error=%s", server_url, exc)

        response = None
        for server_url in self.server_urls:
            try:
                response = requests.post(
                    server_url + "/synthesize",
                    json={
                        "text": text,
                        "voice": self.voice,
                        "lang_code": self.lang_code,
                        "speed": 1.0,
                    },
                    timeout=SPEECH_REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                break
            except Exception as exc:
                last_error = exc
                response = None
        if response is None:
            assert last_error is not None
            raise RuntimeError(f"All Kokoro hosts failed: {last_error}") from last_error
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "The speech host does not support /synthesize_voice_note and local ffmpeg is not installed. "
                "Upgrade the host service or install ffmpeg on this client."
            )
        wav_path.write_bytes(response.content)
        subprocess.run(
            [
                ffmpeg,
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
            ],
            check=True,
            capture_output=True,
            text=True,
            **hidden_subprocess_kwargs(),
        )
        return ogg_path

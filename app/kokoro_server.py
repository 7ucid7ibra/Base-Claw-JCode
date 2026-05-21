from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from huggingface_hub import scan_cache_dir
from kokoro import KPipeline
from pydantic import BaseModel, Field, field_validator


REPO_ID = "hexgrad/Kokoro-82M"
SAMPLE_RATE = 24000
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
CUSTOM_VOICES_DIR = PROJECT_ROOT / "custom_voices"
GERMAN_KOKORO_DIR = PROJECT_ROOT / "german_kokoro"
GERMAN_KOKORO_CODE_DIR = PROJECT_ROOT / "kokoro_german" / "kokoro"

LANGUAGES: Dict[str, str] = {
    "a": "American English",
    "b": "British English",
    "d": "German",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Brazilian Portuguese",
    "z": "Mandarin Chinese",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [kokoro] %(message)s",
)
logger = logging.getLogger("kokoro_server")

app = FastAPI(title="Kokoro TTS Server", version="1.0.0")
_pipelines: Dict[str, KPipeline] = {}
_german_module = None
_german_model = None


class SynthRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = Field(default="am_adam", min_length=1)
    lang_code: str = Field(default="a", min_length=1, max_length=1)
    speed: float = Field(default=1.0, gt=0.0, le=4.0)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value

    @field_validator("lang_code")
    @classmethod
    def lang_code_supported(cls, value: str) -> str:
        if value not in LANGUAGES:
            supported = ", ".join(sorted(LANGUAGES))
            raise ValueError(f"unsupported lang_code '{value}'. Supported: {supported}")
        return value


def get_pipeline(lang_code: str) -> KPipeline:
    if lang_code == "d":
        return get_german_pipeline()
    pipeline = _pipelines.get(lang_code)
    if pipeline is None:
        logger.info("Loading Kokoro pipeline repo_id=%s lang_code=%s", REPO_ID, lang_code)
        pipeline = KPipeline(lang_code=lang_code, repo_id=REPO_ID)
        _pipelines[lang_code] = pipeline
        logger.info("Loaded Kokoro pipeline lang_code=%s", lang_code)
    return pipeline


def load_german_kokoro_module():
    global _german_module
    if _german_module is not None:
        return _german_module

    init_py = GERMAN_KOKORO_CODE_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "kokoro_german_local",
        init_py,
        submodule_search_locations=[str(GERMAN_KOKORO_CODE_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load German Kokoro package from {init_py}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _german_module = module
    return module


def get_german_model():
    global _german_model
    if _german_model is not None:
        return _german_model

    module = load_german_kokoro_module()
    model_path = GERMAN_KOKORO_DIR / "kikiri_german_martin_ep10.pth"
    config_path = GERMAN_KOKORO_DIR / "config.json"
    if not model_path.exists() or not config_path.exists():
        raise RuntimeError("German Kokoro model files are missing")

    logger.info("Loading German Kokoro model from %s", model_path)
    _german_model = module.KModel(
        repo_id=REPO_ID,
        config=str(config_path),
        model=str(model_path),
    ).to("cpu").eval()
    logger.info("Loaded German Kokoro model")
    return _german_model


def get_german_pipeline():
    pipeline = _pipelines.get("d")
    if pipeline is not None:
        return pipeline

    module = load_german_kokoro_module()
    model = get_german_model()
    logger.info("Loading German Kokoro pipeline")
    pipeline = module.KPipeline(lang_code="d", model=model, repo_id=REPO_ID)
    _pipelines["d"] = pipeline
    logger.info("Loaded German Kokoro pipeline")
    return pipeline


def discover_custom_voice_paths() -> Dict[str, str]:
    custom_voices = {}
    if not CUSTOM_VOICES_DIR.exists():
        return custom_voices
    for path in sorted(CUSTOM_VOICES_DIR.glob("*.pt")):
        custom_voices[path.stem] = str(path)
    return custom_voices


def discover_german_voice_paths() -> Dict[str, str]:
    german_voices = {}
    voices_dir = GERMAN_KOKORO_DIR / "voices"
    if not voices_dir.exists():
        return german_voices
    for path in sorted(voices_dir.glob("*.pt")):
        german_voices[f"dm_{path.stem}"] = str(path)
    martin = german_voices.get("dm_martin")
    victoria = german_voices.get("dm_victoria")
    if martin and victoria:
        # Weighted blends create additional useful presets without inventing fake source voices.
        german_voices["dm_martin_blend"] = ",".join([martin, martin, victoria])
        german_voices["dm_victoria_blend"] = ",".join([victoria, victoria, martin])
    return german_voices


def discover_cached_voices(repo_id: str = REPO_ID) -> List[str]:
    voices = set()
    try:
        cache_info = scan_cache_dir()
    except Exception as exc:
        logger.warning("Could not scan Hugging Face cache: %s", exc)
        return []

    for repo in cache_info.repos:
        if repo.repo_id != repo_id:
            continue
        for revision in repo.revisions:
            for cached_file in revision.files:
                path = Path(cached_file.file_path)
                if "voices" not in path.parts:
                    continue
                if path.suffix.lower() in {".pt", ".pth", ".bin", ".safetensors"}:
                    voices.add(path.stem)

    return sorted(voices)


def resolve_voice_name(voice: str) -> str:
    german_voices = discover_german_voice_paths()
    if voice in german_voices:
        return german_voices[voice]
    custom_voices = discover_custom_voice_paths()
    return custom_voices.get(voice, voice)


def resolve_ffmpeg() -> Optional[str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    return None


def synthesize_audio(request: SynthRequest) -> np.ndarray:
    pipeline = get_pipeline(request.lang_code)
    resolved_voice = resolve_voice_name(request.voice)
    logger.info(
        "Synthesizing text_chars=%s voice=%s resolved_voice=%s lang_code=%s speed=%s",
        len(request.text),
        request.voice,
        resolved_voice,
        request.lang_code,
        request.speed,
    )

    parts = []
    try:
        generator = pipeline(
            request.text,
            voice=resolved_voice,
            speed=request.speed,
        )
        for result in generator:
            audio = getattr(result, "audio", None)
            if audio is None and isinstance(result, (tuple, list)) and result:
                audio = result[-1]
            if audio is None:
                continue
            parts.append(np.asarray(audio, dtype=np.float32).reshape(-1))
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        logger.exception("Kokoro synthesis failed")
        raise HTTPException(
            status_code=400,
            detail=f"Could not load or synthesize voice '{request.voice}': {message}",
        ) from exc

    if not parts:
        raise HTTPException(status_code=400, detail="Kokoro returned no audio")

    return parts[0] if len(parts) == 1 else np.concatenate(parts)


def transcribe_audio(audio_path: Path, model_name: str) -> str:
    model_name = model_name.strip() or "small"
    worker = APP_DIR / "whisper_worker.py"
    timeout = int(os.environ.get("BASECLAW_WHISPER_TIMEOUT_SECONDS", "300"))
    process = subprocess.run(
        [sys.executable, str(worker), "--model", model_name, str(audio_path)],
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip() or "Whisper worker failed"
        raise HTTPException(status_code=400, detail=detail)
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Whisper worker returned invalid JSON") from exc
    text = str(result.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Whisper returned an empty transcript")
    return text


def wav_bytes_from_audio(audio: np.ndarray) -> bytes:
    wav_buffer = io.BytesIO()
    sf.write(wav_buffer, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    wav_buffer.seek(0)
    return wav_buffer.read()


def convert_wav_to_ogg(wav_bytes: bytes) -> bytes:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg is required for Telegram voice-note conversion")
    with tempfile.TemporaryDirectory(prefix="kokoro-voice-note-") as tmp:
        wav_path = Path(tmp) / "speech.wav"
        ogg_path = Path(tmp) / "speech.ogg"
        wav_path.write_bytes(wav_bytes)
        process = subprocess.run(
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
            text=True,
            capture_output=True,
        )
        if process.returncode != 0:
            logger.error("ffmpeg voice-note conversion failed: %s", process.stderr.strip())
            raise HTTPException(status_code=500, detail="ffmpeg voice-note conversion failed")
        return ogg_path.read_bytes()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "kokoro-tts",
        "repo_id": REPO_ID,
        "loaded_lang_codes": sorted(_pipelines.keys()),
        "whisper_mode": "isolated_subprocess",
    }


@app.get("/languages")
def languages() -> Dict[str, str]:
    return LANGUAGES


@app.get("/voices")
def voices() -> dict:
    return {
        "repo_id": REPO_ID,
        "voices": discover_cached_voices(),
        "custom_voices": sorted(discover_custom_voice_paths().keys()),
        "german_voices": sorted(discover_german_voice_paths().keys()),
    }


@app.post("/synthesize")
def synthesize(request: SynthRequest) -> Response:
    audio = synthesize_audio(request)
    return Response(content=wav_bytes_from_audio(audio), media_type="audio/wav")


@app.post("/synthesize_voice_note")
def synthesize_voice_note(request: SynthRequest) -> Response:
    audio = synthesize_audio(request)
    ogg_bytes = convert_wav_to_ogg(wav_bytes_from_audio(audio))
    return Response(content=ogg_bytes, media_type="audio/ogg")


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), model: str = Form(default="small")) -> dict:
    suffix = Path(audio.filename or "voice.ogg").suffix or ".ogg"
    try:
        content = await audio.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(content)
            audio_path = Path(handle.name)
        try:
            text = transcribe_audio(audio_path, model)
        finally:
            audio_path.unlink(missing_ok=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Whisper transcription failed")
        raise HTTPException(status_code=400, detail=f"Whisper transcription failed: {exc}") from exc
    return {"text": text, "model": model}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8766)

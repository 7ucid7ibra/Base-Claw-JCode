from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from faster_whisper import WhisperModel


def transcribe(audio_path: Path, model_name: str) -> dict[str, str]:
    model_name = model_name.strip() or "small"
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {"text": text, "model": model_name}


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated faster-whisper transcription worker.")
    parser.add_argument("audio_path")
    parser.add_argument("--model", default="small")
    args = parser.parse_args()
    try:
        result = transcribe(Path(args.audio_path), args.model)
    except Exception as exc:
        print(f"Whisper worker failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

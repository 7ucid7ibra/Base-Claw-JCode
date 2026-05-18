from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from codex_cli import resolve_codex_command


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
PYTHON_FILES = [
    "app/kokoro_server.py",
    "app/kokoro_remote_telegram.py",
    "app/telegram_codex_operator.py",
    "app/telegram_operator_ui.py",
]
KOKORO_IMPORTS = [
    "fastapi",
    "faster_whisper",
    "kokoro",
    "numpy",
    "requests",
    "soundfile",
    "uvicorn",
]
TELEGRAM_IMPORTS = [
    "customtkinter",
    "dotenv",
    "requests",
    "telegram",
]


def ok(message: str) -> None:
    print(f"[ok] {message}")


def warn(message: str) -> None:
    print(f"[warn] {message}")


def fail(message: str) -> None:
    print(f"[fail] {message}")
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the local BaseClaw install.")
    parser.add_argument("--mode", choices=["full", "client", "host"], default="full")
    return parser.parse_args()


def compile_python_files() -> None:
    for name in PYTHON_FILES:
        path = BASE_DIR / name
        if not path.exists():
            fail(f"missing {name}")
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            cwd=BASE_DIR,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            fail(f"{name} did not compile:\n{result.stderr}")
    ok("Python files compile")


def venv_python(name: str) -> Path:
    if sys.platform == "win32":
        return BASE_DIR / name / "Scripts" / "python.exe"
    return BASE_DIR / name / "bin" / "python"


def check_imports(label: str, python: Path, imports: list[str]) -> None:
    if not python.exists():
        warn(f"{label} Python not found at {python}")
        return
    code = (
        "import importlib.util, sys; "
        "missing=[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]; "
        "print('\\n'.join(missing)); "
        "raise SystemExit(1 if missing else 0)"
    )
    result = subprocess.run(
        [str(python), "-c", code, *imports],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
    )
    missing = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if missing:
        warn(f"{label} missing Python packages: " + ", ".join(missing))
        return
    ok(f"{label} packages are importable")


def check_host_commands() -> None:
    if shutil.which("ffmpeg"):
        ok("ffmpeg found")
    else:
        warn("ffmpeg was not found on PATH")

    local_espeak = BASE_DIR / "tools" / "espeak-ng" / "eSpeak NG" / "espeak-ng.exe"
    if shutil.which("espeak-ng"):
        ok("espeak-ng found")
    elif local_espeak.exists():
        ok(f"espeak-ng found locally at {local_espeak}")
    else:
        warn("espeak-ng was not found on PATH")


def check_codex_command() -> None:
    try:
        codex = resolve_codex_command()
        result = subprocess.run(
            [*codex.args, "--version"],
            cwd=BASE_DIR,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = (result.stdout or result.stderr).strip() or codex.display
            ok(f"codex found: {version}")
        else:
            warn("codex CLI was found but did not return a version. Run `codex login` and try again.")
    except RuntimeError as exc:
        warn(str(exc))


def check_kokoro_health() -> None:
    try:
        with urlopen("http://127.0.0.1:8766/health", timeout=3) as response:
            if response.status == 200:
                ok("Kokoro /health responded")
                return
            warn(f"Kokoro /health returned HTTP {response.status}")
    except (OSError, URLError):
        warn("Kokoro server is not running on http://127.0.0.1:8766")


def main() -> None:
    args = parse_args()
    if sys.version_info < (3, 11):
        fail("Python 3.11 or newer is required")
    ok(f"Python {sys.version.split()[0]}")
    compile_python_files()
    if args.mode in {"full", "host"}:
        check_imports("Kokoro env", venv_python(".venv-kokoro"), KOKORO_IMPORTS)
        check_host_commands()
        check_kokoro_health()
    if args.mode in {"full", "client"}:
        check_imports("Telegram env", venv_python(".venv-telegram-agent"), TELEGRAM_IMPORTS)
        check_codex_command()


if __name__ == "__main__":
    main()

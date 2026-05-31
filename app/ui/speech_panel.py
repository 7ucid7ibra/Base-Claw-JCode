from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from speech.urls import tailscale_speech_urls, unique_urls

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
SPEECH_SCRIPT = PROJECT_ROOT / "scripts" / "speech_server.sh"
DEFAULT_LOCAL_SPEECH_URL = "http://127.0.0.1:8766"


def speech_health(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(url.rstrip("/") + "/health", timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def local_speech_urls() -> list[str]:
    urls = [DEFAULT_LOCAL_SPEECH_URL]
    urls.extend(tailscale_speech_urls())
    return unique_urls(urls)


def local_speech_python_path() -> Path:
    if sys.platform.startswith("win"):
        python = BASE_DIR / ".venv-kokoro" / "Scripts" / "pythonw.exe"
        if not python.exists():
            python = BASE_DIR / ".venv-kokoro" / "Scripts" / "python.exe"
        return python
    return BASE_DIR / ".venv-kokoro" / "bin" / "python"


def local_whisper_python_path() -> Path:
    if sys.platform.startswith("win"):
        return BASE_DIR / ".venv-whisper" / "Scripts" / "python.exe"
    return BASE_DIR / ".venv-whisper" / "bin" / "python"


def local_speech_installed() -> bool:
    return local_speech_python_path().exists() and local_whisper_python_path().exists()


def local_speech_state() -> tuple[str, str]:
    if speech_health(DEFAULT_LOCAL_SPEECH_URL):
        return "running", "Local speech server is running."
    if local_speech_installed():
        return "stopped", "Local speech support is installed."
    return "not_installed", "Local speech support is not installed."


def write_local_speech_installed_flag() -> None:
    config_path = PROJECT_ROOT / ".baseclaw-install.conf"
    lines = []
    if config_path.exists():
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    replaced = False
    next_lines = []
    for line in lines:
        if line.startswith("BASECLAW_WITH_KOKORO="):
            next_lines.append("BASECLAW_WITH_KOKORO=1")
            replaced = True
        else:
            next_lines.append(line)
    if not replaced:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append("BASECLAW_WITH_KOKORO=1")
    config_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")


def run_speech_script(action: str, timeout: int = 120) -> tuple[bool, str]:
    if sys.platform.startswith("win") or not SPEECH_SCRIPT.exists():
        return False, "Speech helper script is only available on macOS/Linux."
    result = subprocess.run(
        ["bash", str(SPEECH_SCRIPT), action],
        cwd=str(BASE_DIR),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    detail = (result.stdout or result.stderr).strip()
    return result.returncode == 0, detail or f"speech_server.sh {action} exited with {result.returncode}"


def local_speech_venv_command() -> list[str]:
    if sys.platform.startswith("win"):
        # Windows commonly exposes Microsoft Store/App Execution Alias stubs at
        # WindowsApps/python3.exe. They can appear in PATH but fail for venv
        # creation with exit code 9009. The UI itself is already running under a
        # working Python, so prefer that interpreter for local speech venvs.
        return [sys.executable]
    candidate = shutil.which("python3.12") or shutil.which("python3.11") or shutil.which("python3")
    return [candidate or sys.executable]


def install_local_speech_host() -> tuple[bool, str]:
    if not sys.platform.startswith("win") and SPEECH_SCRIPT.exists():
        return run_speech_script("install", timeout=900)
    py = local_speech_venv_command()
    venv = BASE_DIR / ".venv-kokoro"
    if not venv.exists():
        subprocess.run([*py, "-m", "venv", str(venv)], cwd=str(BASE_DIR), check=True, timeout=120)
    python = local_speech_python_path()
    if not python.exists():
        return False, "Could not create the local speech virtual environment."
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel"], cwd=str(BASE_DIR), check=True, timeout=300)
    subprocess.run([str(python), "-m", "pip", "install", "-r", "requirements/kokoro.txt"], cwd=str(BASE_DIR), check=True, timeout=900)
    whisper_venv = BASE_DIR / ".venv-whisper"
    if not whisper_venv.exists():
        subprocess.run([*py, "-m", "venv", str(whisper_venv)], cwd=str(BASE_DIR), check=True, timeout=120)
    whisper_python = local_whisper_python_path()
    if not whisper_python.exists():
        return False, "Could not create the local Whisper virtual environment."
    subprocess.run([str(whisper_python), "-m", "pip", "install", "--upgrade", "pip", "wheel"], cwd=str(BASE_DIR), check=True, timeout=300)
    subprocess.run([str(whisper_python), "-m", "pip", "install", "-r", "requirements/whisper.txt"], cwd=str(BASE_DIR), check=True, timeout=900)
    write_local_speech_installed_flag()
    return True, "Local speech support installed."


def start_local_speech_host() -> tuple[bool, str]:
    for url in local_speech_urls():
        if speech_health(url):
            return True, f"Local speech host is already running at {url}."
    if not sys.platform.startswith("win") and SPEECH_SCRIPT.exists():
        return run_speech_script("start")
    python = local_speech_python_path()
    if not python.exists():
        return False, "Local speech support is not installed."
    script = APP_DIR / "speech" / "server.py"
    kwargs: dict[str, Any] = {"cwd": str(BASE_DIR)}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(python), str(script)], **kwargs)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if speech_health(DEFAULT_LOCAL_SPEECH_URL):
            return True, "Started local speech host."
        time.sleep(1)
    return False, "Timed out waiting for local speech host on 127.0.0.1:8766."


def stop_local_speech_host() -> tuple[bool, str]:
    import psutil

    if not sys.platform.startswith("win") and SPEECH_SCRIPT.exists():
        return run_speech_script("stop")
    stopped = 0
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "app/speech/server.py" not in cmdline and "app\\speech\\server.py" not in cmdline:
            continue
        try:
            process.terminate()
            stopped += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return True, f"Stopped {stopped} local speech process(es)."

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import tkinter as tk
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import customtkinter as ctk
import psutil
from codex_cli import resolve_codex_command

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
DEFAULT_WORKSPACE = PROJECT_ROOT / "agent_workspace"
ENV_PATH = PROJECT_ROOT / ".env.telegram-operator"
PROFILES_DIR = PROJECT_ROOT / "profiles"
MAIN_PROFILE = "main"
OPERATOR_SCRIPT = APP_DIR / "telegram_codex_operator.py"
SUPERVISOR_SCRIPT = PROJECT_ROOT / "scripts" / "run_telegram_codex_operator.ps1"
LOG_PATH = PROJECT_ROOT / "telegram_codex_operator.log"
UPDATE_SOURCE_URL = "https://github.com/7ucid7ibra/Base-Claw"
UPDATE_VERSION_PATH = PROJECT_ROOT / ".baseclaw-version.json"
SECRET_PATTERNS = [
    re.compile(r"(bot)([0-9]{6,}:[A-Za-z0-9_-]{20,})"),
]
BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")

ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_OPERATOR_WORKDIR",
    "TELEGRAM_OPERATOR_ACCESS_SCOPE",
    "TELEGRAM_OPERATOR_ALLOWED_PATHS",
    "TELEGRAM_OPERATOR_ACTION_MODE",
    "TELEGRAM_OPERATOR_STATE_PATH",
    "TELEGRAM_OPERATOR_MEMORY_LOG",
    "TELEGRAM_OPERATOR_SQLITE_PATH",
    "TELEGRAM_OPERATOR_LOG_PATH",
    "TELEGRAM_OPERATOR_REMOTE_HOST",
    "TELEGRAM_OPERATOR_SPEECH_PORT",
    "TELEGRAM_OPERATOR_LLM_PORT",
    "TELEGRAM_OPERATOR_REMOTE_SPEECH_URL",
    "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK",
    "TELEGRAM_OPERATOR_STARTUP_NOTICE",
    "TELEGRAM_OPERATOR_KOKORO_URL",
    "TELEGRAM_OPERATOR_KOKORO_URLS",
    "TELEGRAM_OPERATOR_KOKORO_VOICE",
    "TELEGRAM_OPERATOR_KOKORO_LANG_CODE",
    "TELEGRAM_OPERATOR_WHISPER_URLS",
    "TELEGRAM_OPERATOR_WHISPER_MODEL",
    "TELEGRAM_OPERATOR_PROVIDER",
    "TELEGRAM_OPERATOR_RUN_MODE",
    "TELEGRAM_OPERATOR_MODEL_PROVIDER",
    "TELEGRAM_OPERATOR_JCODE_API_KEY",
    "TELEGRAM_OPERATOR_AGENT_COMMAND",
    "TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS",
    "TELEGRAM_OPERATOR_CODEX_MODEL",
    "TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE",
    "TELEGRAM_OPERATOR_SHARED_CONTEXT_ENABLED",
    "TELEGRAM_OPERATOR_SHARED_CONTEXT_LIMIT",
    "TELEGRAM_OPERATOR_SAFETY_MODE",
    "TELEGRAM_OPERATOR_SAFE_MODE",
    "TELEGRAM_OPERATOR_SUPERVISOR_ID",
    "TELEGRAM_OPERATOR_SUPERVISOR_NAME",
    "TELEGRAM_OPERATOR_SUPERVISOR_ROLE",
    "TELEGRAM_OPERATOR_SUPERVISOR_DEVICE_LABEL",
    "TELEGRAM_OPERATOR_SUPERVISOR_CORE_PURPOSE",
    "TELEGRAM_OPERATOR_SUPERVISOR_DO_NOT",
    "TELEGRAM_OPERATOR_HISTORY_AGENT",
    "TELEGRAM_OPERATOR_HISTORY_DEVICE",
    "TELEGRAM_OPERATOR_HISTORY_REMOTE",
    "TELEGRAM_OPERATOR_HISTORY_REMOTE_DB_PATH",
    "TELEGRAM_OPERATOR_HISTORY_SSH_KEY",
    "TELEGRAM_OPERATOR_HISTORY_KNOWN_HOSTS",
    "TELEGRAM_OPERATOR_HISTORY_SYNC_LIMIT",
    "TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_ENABLED",
    "TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_INTERVAL_SECONDS",
    "TELEGRAM_OPERATOR_BOARD_REMOTE",
    "TELEGRAM_OPERATOR_BOARD_PATH",
    "TELEGRAM_OPERATOR_BOARD_AGENT_ALIASES",
    "TELEGRAM_OPERATOR_LOCAL_VISION_ENABLED",
    "TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL",
    "TELEGRAM_OPERATOR_LM_STUDIO_VISION_MODEL",
    "TELEGRAM_OPERATOR_LOCAL_VISION_TIMEOUT_SECONDS",
    "TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED",
]

DEFAULTS = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ALLOWED_CHAT_IDS": "",
    "TELEGRAM_OPERATOR_WORKDIR": str(DEFAULT_WORKSPACE),
    "TELEGRAM_OPERATOR_ACCESS_SCOPE": "workspace",
    "TELEGRAM_OPERATOR_ALLOWED_PATHS": "",
    "TELEGRAM_OPERATOR_ACTION_MODE": "full",
    "TELEGRAM_OPERATOR_STATE_PATH": str(BASE_DIR / "telegram_operator_state.json"),
    "TELEGRAM_OPERATOR_MEMORY_LOG": str(BASE_DIR / "telegram_operator_memory.jsonl"),
    "TELEGRAM_OPERATOR_SQLITE_PATH": str(BASE_DIR / "telegram_operator_messages.sqlite3"),
    "TELEGRAM_OPERATOR_LOG_PATH": str(BASE_DIR / "telegram_codex_operator.log"),
    "TELEGRAM_OPERATOR_REMOTE_HOST": "127.0.0.1",
    "TELEGRAM_OPERATOR_SPEECH_PORT": "8766",
    "TELEGRAM_OPERATOR_LLM_PORT": "1234",
    "TELEGRAM_OPERATOR_REMOTE_SPEECH_URL": "",
    "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK": "true",
    "TELEGRAM_OPERATOR_STARTUP_NOTICE": "true",
    "TELEGRAM_OPERATOR_KOKORO_URL": "http://127.0.0.1:8766",
    "TELEGRAM_OPERATOR_KOKORO_URLS": "",
    "TELEGRAM_OPERATOR_KOKORO_VOICE": "af_alloy",
    "TELEGRAM_OPERATOR_KOKORO_LANG_CODE": "a",
    "TELEGRAM_OPERATOR_WHISPER_URLS": "",
    "TELEGRAM_OPERATOR_WHISPER_MODEL": "base",
    "TELEGRAM_OPERATOR_PROVIDER": "jcode",
    "TELEGRAM_OPERATOR_RUN_MODE": "local",
    "TELEGRAM_OPERATOR_MODEL_PROVIDER": "lmstudio",
    "TELEGRAM_OPERATOR_JCODE_API_KEY": "",
    "TELEGRAM_OPERATOR_AGENT_COMMAND": "",
    "TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS": "900",
    "TELEGRAM_OPERATOR_CODEX_MODEL": "qwen3-coder-30b",
    "TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE": "",
    "TELEGRAM_OPERATOR_SHARED_CONTEXT_ENABLED": "false",
    "TELEGRAM_OPERATOR_SHARED_CONTEXT_LIMIT": "12",
    "TELEGRAM_OPERATOR_SAFETY_MODE": "safe",
    "TELEGRAM_OPERATOR_SAFE_MODE": "false",
    "TELEGRAM_OPERATOR_SUPERVISOR_ID": "",
    "TELEGRAM_OPERATOR_SUPERVISOR_NAME": "",
    "TELEGRAM_OPERATOR_SUPERVISOR_ROLE": "",
    "TELEGRAM_OPERATOR_SUPERVISOR_DEVICE_LABEL": "",
    "TELEGRAM_OPERATOR_SUPERVISOR_CORE_PURPOSE": "",
    "TELEGRAM_OPERATOR_SUPERVISOR_DO_NOT": "Do not infer identity from stale chat memory,Do not review your own source update as independent review",
    "TELEGRAM_OPERATOR_HISTORY_AGENT": "",
    "TELEGRAM_OPERATOR_HISTORY_DEVICE": "",
    "TELEGRAM_OPERATOR_HISTORY_REMOTE": "",
    "TELEGRAM_OPERATOR_HISTORY_REMOTE_DB_PATH": "",
    "TELEGRAM_OPERATOR_HISTORY_SSH_KEY": "",
    "TELEGRAM_OPERATOR_HISTORY_KNOWN_HOSTS": "",
    "TELEGRAM_OPERATOR_HISTORY_SYNC_LIMIT": "250",
    "TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_ENABLED": "true",
    "TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_INTERVAL_SECONDS": "300",
    "TELEGRAM_OPERATOR_BOARD_REMOTE": "",
    "TELEGRAM_OPERATOR_BOARD_PATH": "",
    "TELEGRAM_OPERATOR_BOARD_AGENT_ALIASES": "",
    "TELEGRAM_OPERATOR_LOCAL_VISION_ENABLED": "false",
    "TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL": "http://127.0.0.1:1234/v1",
    "TELEGRAM_OPERATOR_LM_STUDIO_VISION_MODEL": "",
    "TELEGRAM_OPERATOR_LOCAL_VISION_TIMEOUT_SECONDS": "180",
    "TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED": "true",
}

CODEX_MODELS = ["default", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2"]
CLAUDE_MODELS = ["", "sonnet", "opus", "claude-sonnet-4-6", "claude-opus-4-6"]
GEMINI_FALLBACK_MODELS = [
    "",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]
GEMINI_MODEL_RE = re.compile(r"\bgemini-(?:1\.5|2(?:\.\d+)?|3(?:\.\d+)?)(?:-[a-z0-9]+)*\b")
JCODE_MODELS = ["qwen3-coder-30b", "qwen/qwen3-coder-30b"]
PROVIDERS = ["jcode", "codex", "claude", "gemini"]
CLOUD_HARNESSES = ["codex", "claude", "gemini"]
LOCAL_HARNESSES = ["jcode"]
RUN_MODE_LABELS = {
    "local": "Local mode",
    "cloud": "Cloud provider mode",
}
RUN_MODE_OPTIONS = [RUN_MODE_LABELS[key] for key in ("local", "cloud")]
RUN_MODE_TO_VALUE = {label: key for key, label in RUN_MODE_LABELS.items()}
MODEL_PROVIDER_LABELS = {
    "lmstudio": "LM Studio",
    "ollama": "Ollama",
    "jcode": "JCode subscription",
    "claude": "Claude via JCode",
    "openai": "OpenAI via JCode",
    "openrouter": "OpenRouter",
    "azure": "Azure OpenAI",
    "groq": "Groq",
    "mistral": "Mistral",
    "perplexity": "Perplexity",
    "togetherai": "Together AI",
    "deepinfra": "Deep Infra",
    "xai": "xAI",
    "gemini": "Gemini",
    "openai-compatible": "OpenAI-compatible",
    "cursor": "Cursor",
    "copilot": "GitHub Copilot",
    "auto": "Auto-detect",
}
MODEL_PROVIDER_ORDER = [
    "lmstudio",
    "ollama",
    "jcode",
    "claude",
    "openai",
    "openrouter",
    "azure",
    "groq",
    "mistral",
    "perplexity",
    "togetherai",
    "deepinfra",
    "xai",
    "gemini",
    "openai-compatible",
    "cursor",
    "copilot",
    "auto",
]
MODEL_PROVIDER_OPTIONS = [MODEL_PROVIDER_LABELS[key] for key in MODEL_PROVIDER_ORDER]
MODEL_PROVIDER_TO_VALUE = {label: key for key, label in MODEL_PROVIDER_LABELS.items()}
DEFAULT_LLM_PORTS = {
    "lmstudio": "1234",
    "ollama": "11434",
}

COLORS = {
    "bg": "#EFE7DC",
    "panel": "#FFFAF1",
    "panel_soft": "#F8F0E4",
    "panel_lift": "#FFFFFF",
    "border": "#D8CDBB",
    "border_soft": "#EFE4D5",
    "text": "#1B1915",
    "muted": "#5F574D",
    "accent": "#20333F",
    "accent_hover": "#31485A",
    "accent_text": "#FFF7EC",
    "danger": "#9A4B4A",
    "danger_hover": "#B15A57",
    "status": "#E5D9C8",
    "user_bubble": "#20333F",
    "assistant_bubble": "#FFFAF1",
    "user_label": "#D7E2DB",
}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
SAFETY_MODE_LABELS = {
    "restricted": "Restricted: approve every task",
    "safe": "Safe: workspace access",
    "code": "Code access: app repo + git commits",
    "full": "Full access",
}
SAFETY_LABELS = [SAFETY_MODE_LABELS[key] for key in ("restricted", "safe", "code", "full")]
SAFETY_LABEL_TO_MODE = {label: key for key, label in SAFETY_MODE_LABELS.items()}
ACCESS_SCOPE_LABELS = {
    "workspace": "Workspace + selected paths",
    "code": "Workspace + own code + selected paths",
    "full": "Everything on this machine",
}
ACCESS_SCOPE_OPTIONS = [ACCESS_SCOPE_LABELS[key] for key in ("workspace", "code", "full")]
ACCESS_SCOPE_TO_MODE = {label: key for key, label in ACCESS_SCOPE_LABELS.items()}
ACTION_MODE_LABELS = {
    "read": "Read only",
    "approve": "Ask before writes or risky actions",
    "full": "Act without extra approval",
}
ACTION_MODE_OPTIONS = [ACTION_MODE_LABELS[key] for key in ("read", "approve", "full")]
ACTION_MODE_TO_MODE = {label: key for key, label in ACTION_MODE_LABELS.items()}
LANGUAGE_CODES = ["a", "b", "d", "e", "f", "h", "i", "j", "p", "z"]
LANGUAGE_CODE_LABELS = {
    "a": "American English (a)",
    "b": "British English (b)",
    "d": "German (d)",
    "e": "Spanish (e)",
    "f": "French (f)",
    "h": "Hindi (h)",
    "i": "Italian (i)",
    "j": "Japanese (j)",
    "p": "Brazilian Portuguese (p)",
    "z": "Mandarin Chinese (z)",
}
LANGUAGE_LABELS = [LANGUAGE_CODE_LABELS[code] for code in LANGUAGE_CODES]
LANGUAGE_LABEL_TO_CODE = {label: code for code, label in LANGUAGE_CODE_LABELS.items()}
INFO_MARK = "  ⓘ"
CARD_HELP_TEXT = {
    "Runtime": "Start, stop, or restart the Telegram bridge process. Settings save automatically and do not require a restart unless the running agent should pick them up.",
    "Connection": "Telegram bot credentials plus the shared host and ports for local services.",
    "Voice": "Controls voice replies, Kokoro voice selection, language, and Whisper transcription model.",
    "Agent": "Controls which agent harness runs, which model/provider it uses, and what files it may access.",
    "Recent Log": "Shows the most recent local bridge log lines for quick troubleshooting.",
}
HELP_TEXT = {
    "Bot token": "The Telegram bot token from BotFather. It is saved locally in your env file.",
    "Chat id(s)": "Allowed Telegram chat IDs. Use commas for multiple chats.",
    "Host IP / name": "The machine hosting local services. Use 127.0.0.1 for this machine or an IP/hostname for another reachable machine.",
    "STT/TTS port": "The speech service port. The same endpoint handles Whisper speech-to-text and Kokoro text-to-speech.",
    "LLM port": "The local model API port. BaseClaw sets this automatically when LM Studio or Ollama is selected.",
    "LLM port (auto)": "The local model API port. LM Studio uses 1234 and Ollama uses 11434 by default.",
    "Workspace home": "The default working folder for the assistant.",
    "Additional allowed paths": "Extra folders the assistant may access when access scope allows selected paths.",
    "Run mode": "Local mode uses JCode with a model provider. Cloud provider mode uses Codex, Claude, or Gemini directly.",
    "Agent harness": "The coding tool that receives the task and performs file or terminal work.",
    "JCode model provider": "The backend JCode connects to for models, such as LM Studio, Ollama, or hosted API providers.",
    "Model": "The model selected for the current harness or provider.",
    "Claude model": "The model name passed to the Claude CLI. Leave blank/default if you want Claude to choose its configured default.",
    "Codex model": "The model name passed to Codex. Use default to rely on the CLI configuration.",
    "Gemini model": "The model name passed to Gemini CLI. Leave blank if you want Gemini to use its configured default.",
    "API key for selected provider": "Only needed for hosted JCode providers that require an API key. Local LM Studio and Ollama do not use this.",
    "Agent timeout seconds": "Maximum time the agent process may run for one request before BaseClaw stops waiting.",
    "Access scope": "Controls which folders the assistant may read or write.",
    "Action mode": "Controls whether the assistant is read-only, asks before writes, or acts without extra approval.",
    "Shared context injection": "When enabled, BaseClaw injects a rolling continuity summary plus recent Telegram and desktop history into every prompt. This keeps continuity across harness switches, but older messages are marked as context only, not new instructions.",
    "Voice": "Kokoro voice used for spoken replies.",
    "Language code": "Speech language/accent code sent to Kokoro.",
    "Whisper model": "Whisper model size for voice note transcription. Larger models are slower but can be more accurate.",
    "Voice replies enabled": "When enabled, text replies can also be sent back as generated voice.",
}

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def read_env(path: Path) -> dict[str, str]:
    values = DEFAULTS.copy()
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lstrip("\ufeff")] = value.strip()
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = []
    seen = set()
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            if raw_line.strip() and not raw_line.lstrip().startswith("#") and "=" in raw_line:
                key = raw_line.split("=", 1)[0].strip().lstrip("\ufeff")
                if key in values:
                    lines.append(f"{key}={values[key]}")
                    seen.add(key)
                    continue
            lines.append(raw_line)
    for key in ENV_KEYS:
        if key not in seen:
            lines.append(f"{key}={values.get(key, '')}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def normalize_bot_token(value: str) -> str:
    token = re.sub(r"\s+", "", value.strip())
    half = len(token) // 2
    if len(token) % 2 == 0 and half and token[:half] == token[half:] and BOT_TOKEN_RE.fullmatch(token[:half]):
        return token[:half]
    return token


def bot_token_validation_error(value: str) -> str:
    token = normalize_bot_token(value)
    if not token:
        return "Bot token is missing. Paste one token from BotFather before starting this profile."
    if not BOT_TOKEN_RE.fullmatch(token):
        return "Bot token looks invalid. Paste exactly one token from BotFather; do not paste it twice."
    return ""


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


def codex_preflight() -> tuple[bool, str]:
    try:
        codex = resolve_codex_command()
    except RuntimeError as exc:
        return False, str(exc)
    try:
        result = subprocess.run(
            [*codex.args, "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, f"Codex check failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return False, detail or "Codex CLI did not respond successfully."
    version = (result.stdout or result.stderr).strip() or "installed"
    return True, f"Codex CLI: {version}"


def jcode_preflight() -> tuple[bool, str]:
    executable = shutil.which("jcode")
    if not executable:
        return False, "JCode is selected, but the jcode CLI is not on PATH. Install JCode or choose Codex/Claude first."
    try:
        result = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, f"JCode check failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return False, detail or "JCode CLI did not respond successfully."
    version = (result.stdout or result.stderr).strip() or "installed"
    return True, f"JCode CLI: {version}"


def claude_preflight() -> tuple[bool, str]:
    executable = shutil.which("claude")
    if not executable:
        return False, "Claude provider is selected, but the claude CLI is not on PATH. Install/login to Claude CLI first, then start again."
    try:
        result = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return True, f"Claude CLI found: {executable}"
    if result.returncode != 0:
        return True, f"Claude CLI found: {executable}"
    version = (result.stdout or result.stderr).strip() or "installed"
    return True, f"Claude CLI: {version}"


def gemini_preflight() -> tuple[bool, str]:
    executable = shutil.which("gemini")
    if not executable:
        return False, "Gemini provider is selected, but the gemini CLI is not on PATH. Install the official @google/gemini-cli package and authenticate first."
    try:
        result = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return True, f"Gemini CLI found: {executable}"
    if result.returncode != 0:
        return True, f"Gemini CLI found: {executable}"
    version = (result.stdout or result.stderr).strip() or "installed"
    return True, f"Gemini CLI: {version}"


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _sort_gemini_models(models: list[str]) -> list[str]:
    preferred = [
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro",
        "gemini-3-pro",
        "gemini-3-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    ]
    clean = [model for model in _dedupe_strings(models) if model]
    preferred_present = [model for model in preferred if model in clean]
    remaining = sorted(model for model in clean if model not in set(preferred_present))
    return preferred_present + remaining


def _gemini_api_models() -> list[str]:
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_GENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return []
    url = f"https://generativelanguage.googleapis.com/v1beta/models?{urlencode({'key': api_key})}"
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return []
    models = []
    for item in payload.get("models", []):
        methods = item.get("supportedGenerationMethods") or []
        if methods and "generateContent" not in methods:
            continue
        model_id = str(item.get("name") or item.get("baseModelId") or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id.split("/", 1)[1]
        if model_id.startswith("gemini-"):
            models.append(model_id)
    return models


def _gemini_cli_bundle_roots() -> list[Path]:
    roots: list[Path] = []
    executable = shutil.which("gemini")
    if executable:
        try:
            resolved = Path(executable).resolve()
            roots.extend(parent / "node_modules" / "@google" / "gemini-cli" for parent in resolved.parents[:5])
            roots.extend(parent / "@google" / "gemini-cli" for parent in resolved.parents[:5])
        except OSError:
            pass
    npm = shutil.which("npm")
    if npm:
        try:
            result = subprocess.run(
                [npm, "root", "-g"],
                text=True,
                capture_output=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            result = None
        if result and result.returncode == 0:
            npm_root_text = (result.stdout or "").strip()
            if npm_root_text:
                roots.append(Path(npm_root_text) / "@google" / "gemini-cli")
    return [Path(root) for root in _dedupe_strings([str(root) for root in roots]) if Path(root).exists()]


def _gemini_cli_bundle_models() -> list[str]:
    models: list[str] = []
    for root_text in _gemini_cli_bundle_roots():
        root = Path(root_text)
        search_roots = [root / "bundle", root / "dist", root]
        for search_root in search_roots:
            if not search_root.exists():
                continue
            for path in search_root.rglob("*.js"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for model_id in GEMINI_MODEL_RE.findall(text):
                    if "9001" in model_id or model_id.endswith("-base"):
                        continue
                    models.append(model_id)
    return models


def gemini_model_options() -> list[str]:
    discovered = _sort_gemini_models([*_gemini_api_models(), *_gemini_cli_bundle_models()])
    return ["", *(discovered or [model for model in GEMINI_FALLBACK_MODELS if model])]


def normalize_speech_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    if url:
        parts = urlsplit(url)
        host_part = parts.netloc.rsplit("@", 1)[-1]
        has_port = ":" in host_part and not host_part.endswith("]")
        if parts.netloc and not has_port:
            url = urlunsplit((parts.scheme or "http", f"{parts.netloc}:8766", parts.path, "", ""))
    return url


def host_from_url(url: str, default: str = "127.0.0.1") -> str:
    url = normalize_speech_url(url)
    if not url:
        return default
    parts = urlsplit(url)
    return parts.hostname or default


def port_from_url(url: str, default: str) -> str:
    url = normalize_speech_url(url)
    if not url:
        return default
    parts = urlsplit(url)
    return str(parts.port or default)


def is_local_host(host: str) -> bool:
    return host.strip().lower() in {"", "127.0.0.1", "localhost", "0.0.0.0", "::1"}


def is_local_speech_url(url: str) -> bool:
    normalized = normalize_speech_url(url)
    if not normalized:
        return True
    return is_local_host(urlsplit(normalized).hostname or "")


def build_host_url(host: str, port: str, suffix: str = "") -> str:
    host = (host or "127.0.0.1").strip().removeprefix("http://").removeprefix("https://").strip("/")
    port = (port or "").strip()
    if not port:
        return ""
    return f"http://{host}:{port}{suffix}"


def parse_bool(value: str, default: bool = False) -> bool:
    value = (value or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on", "enabled"}:
        return True
    if value in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def speech_health(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(url.rstrip("/") + "/health", timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def tailscale_speech_urls() -> list[str]:
    executable = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=4,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return [f"http://{line.strip()}:8766" for line in result.stdout.splitlines() if re.fullmatch(r"100(?:\.\d{1,3}){3}", line.strip())]


def local_speech_urls() -> list[str]:
    urls = [DEFAULTS["TELEGRAM_OPERATOR_KOKORO_URL"]]
    urls.extend(tailscale_speech_urls())
    unique = []
    seen = set()
    for url in urls:
        normalized = normalize_speech_url(url)
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def start_local_speech_host() -> tuple[bool, str]:
    for url in local_speech_urls():
        if speech_health(url):
            return True, f"Local speech host is already running at {url}."
    python = BASE_DIR / ".venv-kokoro" / "Scripts" / "pythonw.exe"
    if not python.exists():
        python = BASE_DIR / ".venv-kokoro" / "Scripts" / "python.exe"
    if not python.exists():
        return False, "Local speech fallback is enabled, but .venv-kokoro was not found. Start a remote host or install host mode."
    script = APP_DIR / "kokoro_server.py"
    subprocess.Popen(
        [str(python), str(script)],
        cwd=str(BASE_DIR),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if speech_health(DEFAULTS["TELEGRAM_OPERATOR_KOKORO_URL"]):
            return True, "Started local speech host."
        time.sleep(1)
    return False, "Timed out waiting for local speech host on 127.0.0.1:8766."


def language_display(value: str) -> str:
    return LANGUAGE_CODE_LABELS.get(value.strip(), value.strip() or LANGUAGE_CODE_LABELS["a"])


def language_code(value: str) -> str:
    value = value.strip()
    if value in LANGUAGE_LABEL_TO_CODE:
        return LANGUAGE_LABEL_TO_CODE[value]
    if len(value) >= 3 and value.endswith(")") and "(" in value:
        candidate = value.rsplit("(", 1)[1].rstrip(")")
        if candidate in LANGUAGE_CODE_LABELS:
            return candidate
    return value


def safety_display(value: str) -> str:
    value = value.strip().lower()
    return SAFETY_MODE_LABELS.get(value, SAFETY_MODE_LABELS["safe"])


def safety_mode(value: str) -> str:
    value = value.strip()
    if value in SAFETY_LABEL_TO_MODE:
        return SAFETY_LABEL_TO_MODE[value]
    value = value.lower()
    return value if value in SAFETY_MODE_LABELS else "safe"


def access_scope_display(value: str) -> str:
    value = value.strip().lower()
    return ACCESS_SCOPE_LABELS.get(value, ACCESS_SCOPE_LABELS["workspace"])


def access_scope_mode(value: str) -> str:
    value = value.strip()
    if value in ACCESS_SCOPE_TO_MODE:
        return ACCESS_SCOPE_TO_MODE[value]
    value = value.lower()
    return value if value in ACCESS_SCOPE_LABELS else "workspace"


def action_mode_display(value: str) -> str:
    value = value.strip().lower()
    return ACTION_MODE_LABELS.get(value, ACTION_MODE_LABELS["full"])


def action_mode_value(value: str) -> str:
    value = value.strip()
    if value in ACTION_MODE_TO_MODE:
        return ACTION_MODE_TO_MODE[value]
    value = value.lower()
    return value if value in ACTION_MODE_LABELS else "full"


def run_mode_display(value: str) -> str:
    value = value.strip().lower()
    return RUN_MODE_LABELS.get(value, RUN_MODE_LABELS["local"])


def run_mode_value(value: str) -> str:
    value = value.strip()
    if value in RUN_MODE_TO_VALUE:
        return RUN_MODE_TO_VALUE[value]
    value = value.lower()
    return value if value in RUN_MODE_LABELS else "local"


def model_provider_display(value: str) -> str:
    value = value.strip().lower()
    return MODEL_PROVIDER_LABELS.get(value, MODEL_PROVIDER_LABELS["lmstudio"])


def model_provider_value(value: str) -> str:
    value = value.strip()
    if value in MODEL_PROVIDER_TO_VALUE:
        return MODEL_PROVIDER_TO_VALUE[value]
    value = value.lower()
    return value if value in MODEL_PROVIDER_LABELS else "lmstudio"


def default_llm_port(model_provider: str) -> str:
    return DEFAULT_LLM_PORTS.get(model_provider_value(model_provider), "1234")


def safe_profile_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip(".-")
    return cleaned[:48] or "agent"


def profile_dir(profile: str) -> Path:
    return PROFILES_DIR / safe_profile_name(profile)


def profile_env_path(profile: str) -> Path:
    if profile == MAIN_PROFILE:
        return ENV_PATH
    return profile_dir(profile) / ".env.telegram-operator"


def profile_log_path(profile: str) -> Path:
    if profile == MAIN_PROFILE:
        return LOG_PATH
    return profile_dir(profile) / "telegram_codex_operator.log"


def profile_state_path(profile: str) -> Path:
    if profile == MAIN_PROFILE:
        return BASE_DIR / "telegram_operator_state.json"
    return profile_dir(profile) / "telegram_operator_state.json"


def profile_memory_log_path(profile: str) -> Path:
    if profile == MAIN_PROFILE:
        return BASE_DIR / "telegram_operator_memory.jsonl"
    return profile_dir(profile) / "telegram_operator_memory.jsonl"


def profile_sqlite_path(profile: str) -> Path:
    if profile == MAIN_PROFILE:
        return BASE_DIR / "telegram_operator_messages.sqlite3"
    return profile_dir(profile) / "telegram_operator_messages.sqlite3"


def profile_workdir(profile: str) -> Path:
    if profile == MAIN_PROFILE:
        return DEFAULT_WORKSPACE
    return profile_dir(profile) / "agent_workspace"


def list_profiles() -> list[str]:
    profiles = [MAIN_PROFILE]
    if PROFILES_DIR.exists():
        for item in sorted(PROFILES_DIR.iterdir()):
            if item.is_dir() and (item / ".env.telegram-operator").exists():
                profiles.append(item.name)
    return profiles


def operator_processes(profile_env: Path | None = None) -> list[dict]:
    processes = []
    profile_env_text = str(profile_env) if profile_env else ""
    for process in psutil.process_iter(["pid", "ppid", "name", "exe", "cmdline"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        # Cross-platform: macOS/Linux Python process names are often just
        # "python" or "Python", so detect the operator/supervisor by script argument.
        if OPERATOR_SCRIPT.name not in cmdline and SUPERVISOR_SCRIPT.name not in cmdline:
            continue
        if profile_env_text and profile_env_text not in cmdline:
            is_legacy_main = profile_env == ENV_PATH and "--profile-env" not in cmdline and "-ProfileEnv" not in cmdline
            if not is_legacy_main:
                continue
        processes.append(
            {
                "ProcessId": process.info["pid"],
                "ParentProcessId": process.info["ppid"],
                "Name": process.info.get("name") or "",
                "ExecutablePath": process.info.get("exe") or "",
                "CommandLine": cmdline,
            }
        )
    return processes


def root_operator_processes(profile_env: Path | None = None) -> list[dict]:
    processes = operator_processes(profile_env)
    operator_ids = {int(item["ProcessId"]) for item in processes}
    roots = [item for item in processes if int(item.get("ParentProcessId") or 0) not in operator_ids]
    return roots or processes


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 350) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.after_id: str | None = None
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event: tk.Event | None = None) -> None:
        self.cancel()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def cancel(self) -> None:
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def show(self) -> None:
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        self.window.configure(bg=COLORS["border"])
        label = tk.Label(
            self.window,
            text=self.text,
            justify="left",
            wraplength=320,
            background=COLORS["panel_lift"],
            foreground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Helvetica", 12),
        )
        label.pack(padx=1, pady=1)

    def hide(self, _event: tk.Event | None = None) -> None:
        self.cancel()
        if self.window:
            self.window.destroy()
            self.window = None


class OperatorUi(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BaseClaw")
        self.geometry("690x760+24+24")
        self.minsize(560, 540)
        self.profile_name = MAIN_PROFILE
        self.profile_var = tk.StringVar(value=self.profile_name)
        self.profile_combo: ctk.CTkComboBox | None = None
        self.values = self._profile_values(self.profile_name)
        self.vars: dict[str, tk.StringVar] = {}
        self.voice_combo: ctk.CTkComboBox | None = None
        self.whisper_combo: ctk.CTkComboBox | None = None
        self.harness_combo: ctk.CTkComboBox | None = None
        self.local_provider_label: ctk.CTkLabel | None = None
        self.local_provider_combo: ctk.CTkComboBox | None = None
        self.llm_port_entry: ctk.CTkEntry | None = None
        self.llm_port_label: ctk.CTkLabel | None = None
        self.model_label: ctk.CTkLabel | None = None
        self.model_picker: ctk.CTkFrame | None = None
        self.model_combo: ctk.CTkComboBox | None = None
        self.model_refresh_button: ctk.CTkButton | None = None
        self.api_key_label: ctk.CTkLabel | None = None
        self.api_key_entry: ctk.CTkEntry | None = None
        self.timeout_label: ctk.CTkLabel | None = None
        self.timeout_entry: ctk.CTkEntry | None = None
        self.status_pill: ctk.CTkLabel | None = None
        self.status_detail: ctk.CTkLabel | None = None
        self.update_button: ctk.CTkButton | None = None
        self.update_check_running = False
        self.update_available = False
        self.update_detail = ""
        self.start_button: ctk.CTkButton | None = None
        self.stop_button: ctk.CTkButton | None = None
        self.restart_button: ctk.CTkButton | None = None
        self.send_button: ctk.CTkButton | None = None
        self.thinking_label: ctk.CTkLabel | None = None
        self.thinking_after_id: str | None = None
        self.thinking_step = 0
        self.log_box: ctk.CTkTextbox | None = None
        self.chat_scroll: ctk.CTkScrollableFrame | None = None
        self.chat_input: ctk.CTkTextbox | None = None
        self.chat_card: ctk.CTkFrame | None = None
        self.settings_button: ctk.CTkButton | None = None
        self.settings_window: ctk.CTkToplevel | None = None
        self.settings_frame: ctk.CTkScrollableFrame | None = None
        self.settings_visible = False
        self.last_chat_row_id = -1
        self.chat_busy = False
        self.autosave_ready = False
        self.autosave_after_id: str | None = None
        self.entry_labels: dict[str, ctk.CTkLabel] = {}
        self.tooltips: list[Tooltip] = []
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._wire_autosave()
        self.autosave_ready = True
        self.refresh_voices()
        self.refresh_status()
        self.refresh_chat_history()
        self.after(900, self.send_ui_startup_notice)
        self.after(2200, self.check_for_updates)
        self.after(5000, self.auto_refresh_status)

    @property
    def env_path(self) -> Path:
        return profile_env_path(self.profile_name)

    @property
    def log_path(self) -> Path:
        return profile_log_path(self.profile_name)

    def _profile_values(self, profile: str) -> dict[str, str]:
        values = read_env(profile_env_path(profile))
        values.setdefault("TELEGRAM_OPERATOR_WORKDIR", str(profile_workdir(profile)))
        values["TELEGRAM_OPERATOR_STATE_PATH"] = str(profile_state_path(profile))
        values["TELEGRAM_OPERATOR_MEMORY_LOG"] = str(profile_memory_log_path(profile))
        values["TELEGRAM_OPERATOR_SQLITE_PATH"] = str(profile_sqlite_path(profile))
        values["TELEGRAM_OPERATOR_LOG_PATH"] = str(profile_log_path(profile))
        return values

    def _refresh_profile_combo(self) -> None:
        if self.profile_combo:
            self.profile_combo.configure(values=list_profiles())

    def create_profile(self) -> None:
        if self.chat_busy:
            messagebox.showinfo("Agent busy", "Wait for the current desktop chat turn to finish before changing profiles.")
            return
        name = simpledialog.askstring("New agent profile", "Profile name:", parent=self)
        if not name:
            return
        profile = safe_profile_name(name)
        if profile == MAIN_PROFILE:
            messagebox.showerror("Profile exists", "The main profile already exists.")
            return
        target_env = profile_env_path(profile)
        if target_env.exists():
            messagebox.showerror("Profile exists", f"Profile already exists: {profile}")
            return
        self.save(show_message=False)
        values = self.current_values()
        values["TELEGRAM_BOT_TOKEN"] = ""
        values["TELEGRAM_ALLOWED_CHAT_IDS"] = ""
        values["TELEGRAM_OPERATOR_WORKDIR"] = str(profile_workdir(profile))
        values["TELEGRAM_OPERATOR_STATE_PATH"] = str(profile_state_path(profile))
        values["TELEGRAM_OPERATOR_MEMORY_LOG"] = str(profile_memory_log_path(profile))
        values["TELEGRAM_OPERATOR_SQLITE_PATH"] = str(profile_sqlite_path(profile))
        values["TELEGRAM_OPERATOR_LOG_PATH"] = str(profile_log_path(profile))
        target_env.parent.mkdir(parents=True, exist_ok=True)
        profile_workdir(profile).mkdir(parents=True, exist_ok=True)
        write_env(target_env, values)
        self._refresh_profile_combo()
        self.profile_var.set(profile)
        self.on_profile_selected(profile)

    def delete_profile(self) -> None:
        if self.chat_busy:
            messagebox.showinfo("Agent busy", "Wait for the current desktop chat turn to finish before deleting a profile.")
            return
        profile = self.profile_name
        if profile == MAIN_PROFILE:
            messagebox.showinfo("Delete profile", "The main profile cannot be deleted.")
            return
        target = profile_dir(profile)
        if not target.exists():
            self.profile_name = MAIN_PROFILE
            self.profile_var.set(MAIN_PROFILE)
            self._refresh_profile_combo()
            self._load_values_into_vars()
            return
        if not messagebox.askyesno(
            "Delete profile",
            f"Delete profile '{profile}'?\n\nThis stops its operator and removes its local config, chat history, session state, workspace, and logs.",
        ):
            return
        self._kill_operator_processes(show_errors=False)
        shutil.rmtree(target)
        self.profile_name = MAIN_PROFILE
        self.profile_var.set(MAIN_PROFILE)
        self._refresh_profile_combo()
        self.values = self._profile_values(MAIN_PROFILE)
        self._load_values_into_vars()
        self.last_chat_row_id = -1
        self.refresh_status()
        self.refresh_chat_history()
        self.set_status("Profile deleted", f"Deleted profile: {profile}", None)

    def on_profile_selected(self, value: str | None = None) -> None:
        if self.chat_busy:
            self.profile_var.set(self.profile_name)
            messagebox.showinfo("Agent busy", "Wait for the current desktop chat turn to finish before switching profiles.")
            return
        profile = safe_profile_name(value or self.profile_var.get() or MAIN_PROFILE)
        if profile == self.profile_name:
            return
        if self.autosave_ready:
            self.save(show_message=False)
        self.profile_name = profile
        self.profile_var.set(profile)
        self.values = self._profile_values(profile)
        self._load_values_into_vars()
        self.refresh_voices()
        self.refresh_status()
        self.refresh_chat_history()
        self.set_status("Profile selected", f"Using profile: {profile}", None)

    def _build(self) -> None:
        self.configure(fg_color=COLORS["bg"])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(22, 10))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)
        header.grid_columnconfigure(2, weight=0)

        title = ctk.CTkLabel(header, text="BaseClaw", font=ctk.CTkFont(size=26, weight="bold"))
        title.grid(row=0, column=0, sticky="w")
        subtitle = ctk.CTkLabel(
            header,
            text="Local agent bridge for Telegram, desktop chat, speech, and safe controls.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=13),
            wraplength=500,
            justify="left",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.status_pill = ctk.CTkLabel(
            header,
            text="Checking",
            width=120,
            height=34,
            corner_radius=17,
            fg_color=COLORS["status"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.status_pill.grid(row=0, column=1, rowspan=2, sticky="e")
        self.settings_button = ctk.CTkButton(
            header,
            text="Settings",
            width=92,
            height=34,
            corner_radius=17,
            command=self.open_settings,
        )
        self.settings_button.grid(row=0, column=2, rowspan=2, sticky="e", padx=(10, 0))

        profile_bar = ctk.CTkFrame(header, fg_color="transparent")
        profile_bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        profile_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            profile_bar,
            text="Agent profile",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.profile_combo = ctk.CTkComboBox(
            profile_bar,
            variable=self.profile_var,
            values=list_profiles(),
            command=self.on_profile_selected,
            height=34,
            corner_radius=12,
            border_width=1,
        )
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            profile_bar,
            text="New profile",
            width=112,
            height=34,
            corner_radius=12,
            command=self.create_profile,
        ).grid(row=0, column=2, sticky="e")
        ctk.CTkButton(
            profile_bar,
            text="Delete",
            width=84,
            height=34,
            corner_radius=12,
            fg_color=COLORS["panel_lift"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=self.delete_profile,
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 16))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_chat_card(body)
        if self.chat_scroll:
            self._configure_scrollable_background(self.chat_scroll)

        self.settings_frame = ctk.CTkScrollableFrame(body, fg_color=COLORS["bg"], height=380)
        self.settings_frame.grid_columnconfigure(0, weight=1)
        self._configure_scrollable_background(self.settings_frame)
        settings_body = self.settings_frame

        connection = self._card(settings_body, "Connection", "Telegram credentials plus local service host and ports.", 1, 0)
        speech_url = self.values.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", "") or self.values.get("TELEGRAM_OPERATOR_KOKORO_URL", "")
        llm_url = self.values.get("TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL", DEFAULTS["TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL"])
        self.values["TELEGRAM_OPERATOR_REMOTE_HOST"] = self.values.get("TELEGRAM_OPERATOR_REMOTE_HOST", "") or host_from_url(speech_url)
        self.values["TELEGRAM_OPERATOR_SPEECH_PORT"] = self.values.get("TELEGRAM_OPERATOR_SPEECH_PORT", "") or port_from_url(speech_url, "8766")
        self.values["TELEGRAM_OPERATOR_LLM_PORT"] = self.values.get("TELEGRAM_OPERATOR_LLM_PORT", "") or port_from_url(llm_url, "1234")
        self._entry(connection, "Bot token", "TELEGRAM_BOT_TOKEN", row=0, secret=True)
        self._entry(connection, "Chat id(s)", "TELEGRAM_ALLOWED_CHAT_IDS", row=2)
        host_row = ctk.CTkFrame(connection, fg_color="transparent")
        host_row.grid(row=4, column=0, sticky="ew")
        host_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="host")
        self._entry(host_row, "Host IP / name", "TELEGRAM_OPERATOR_REMOTE_HOST", row=0, column=0, padx=(0, 6))
        self._entry(host_row, "STT/TTS port", "TELEGRAM_OPERATOR_SPEECH_PORT", row=0, column=1, padx=6)
        self.llm_port_entry = self._entry(host_row, "LLM port", "TELEGRAM_OPERATOR_LLM_PORT", row=0, column=2, padx=(6, 0))
        self.llm_port_label = self.entry_labels.get("TELEGRAM_OPERATOR_LLM_PORT")

        agent = self._card(settings_body, "Agent", "Harness, model provider, workspace, and safety controls.", 3, 0)
        provider = self.values.get("TELEGRAM_OPERATOR_PROVIDER", "").strip() or "jcode"
        if provider not in PROVIDERS:
            provider = "jcode"
        run_mode = self.values.get("TELEGRAM_OPERATOR_RUN_MODE", "").strip()
        if not run_mode:
            run_mode = "cloud" if provider in CLOUD_HARNESSES else "local"
        model_provider = self.values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip() or "lmstudio"
        self.vars["TELEGRAM_OPERATOR_RUN_MODE"] = tk.StringVar(value=run_mode_display(run_mode))
        self.vars["TELEGRAM_OPERATOR_MODEL_PROVIDER"] = tk.StringVar(value=model_provider_display(model_provider))
        self.vars["TELEGRAM_OPERATOR_PROVIDER"] = tk.StringVar(value=provider)
        self.vars["TELEGRAM_OPERATOR_AGENT_COMMAND"] = tk.StringVar(value=self.values.get("TELEGRAM_OPERATOR_AGENT_COMMAND", ""))
        self._path_entry(agent, "Workspace home", "TELEGRAM_OPERATOR_WORKDIR", row=0)
        self._multi_path_entry(agent, "Additional allowed paths", "TELEGRAM_OPERATOR_ALLOWED_PATHS", row=2)
        agent_options = ctk.CTkFrame(agent, fg_color="transparent")
        agent_options.grid(row=4, column=0, sticky="ew")
        agent_options.grid_columnconfigure((0, 1), weight=1, uniform="agent_options")
        self._label(agent_options, "Run mode", row=0, column=0, padx=(0, 6))
        ctk.CTkComboBox(
            agent_options,
            variable=self.vars["TELEGRAM_OPERATOR_RUN_MODE"],
            values=RUN_MODE_OPTIONS,
            command=self.on_run_mode_selected,
            height=38,
            corner_radius=10,
            border_width=1,
        ).grid(row=1, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
        self._label(agent_options, "Agent harness", row=0, column=1, padx=(6, 0))
        self.harness_combo = ctk.CTkComboBox(
            agent_options,
            variable=self.vars["TELEGRAM_OPERATOR_PROVIDER"],
            values=self._harness_options(run_mode),
            command=self.on_provider_selected,
            height=38,
            corner_radius=10,
            border_width=1,
        )
        self.harness_combo.grid(row=1, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))
        model = self.values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip() or "qwen3-coder-30b"
        self.vars["TELEGRAM_OPERATOR_CODEX_MODEL"] = tk.StringVar(value=model)
        self.vars["TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE"] = tk.StringVar(
            value=self.values.get("TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE", "").strip()
        )
        self.local_provider_label = self._label(agent_options, "JCode model provider", row=2, column=0, padx=(0, 6))
        self.local_provider_combo = ctk.CTkComboBox(
            agent_options,
            variable=self.vars["TELEGRAM_OPERATOR_MODEL_PROVIDER"],
            values=MODEL_PROVIDER_OPTIONS,
            command=self.on_model_provider_selected,
            height=38,
            corner_radius=10,
            border_width=1,
        )
        self.local_provider_combo.grid(row=3, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
        self.model_label = self._label(agent_options, "Model", row=2, column=1, padx=(6, 0))
        self.model_picker = ctk.CTkFrame(agent_options, fg_color="transparent")
        self.model_picker.grid(row=3, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))
        self.model_picker.grid_columnconfigure(0, weight=1)
        self.model_combo = ctk.CTkComboBox(
            self.model_picker,
            variable=self.vars["TELEGRAM_OPERATOR_CODEX_MODEL"],
            values=self._model_options(provider, model),
            height=38,
            corner_radius=10,
            border_width=1,
        )
        self.model_combo.grid(row=0, column=0, sticky="ew")
        self.model_refresh_button = ctk.CTkButton(
            self.model_picker,
            text="Refresh",
            width=82,
            height=38,
            corner_radius=10,
            command=self.refresh_models,
        )
        self.model_refresh_button.grid(row=0, column=1, padx=(8, 0))
        self.vars["TELEGRAM_OPERATOR_JCODE_API_KEY"] = tk.StringVar(
            value=self.values.get("TELEGRAM_OPERATOR_JCODE_API_KEY", DEFAULTS["TELEGRAM_OPERATOR_JCODE_API_KEY"])
        )
        self.api_key_label = self._label(agent_options, "API key for selected provider", row=4, column=0, padx=(0, 6))
        self.api_key_entry = ctk.CTkEntry(
            agent_options,
            textvariable=self.vars["TELEGRAM_OPERATOR_JCODE_API_KEY"],
            show="*",
            height=38,
            corner_radius=10,
            border_width=1,
            fg_color=COLORS["panel"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        self.api_key_entry.grid(row=5, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
        self.timeout_entry = self._entry(
            agent_options,
            "Agent timeout seconds",
            "TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS",
            row=4,
            column=1,
            padx=(6, 0),
        )
        self.timeout_label = self.entry_labels.get("TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS")
        self._sync_agent_option_visibility()
        access_scope = self.values.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", "").strip()
        action_mode = self.values.get("TELEGRAM_OPERATOR_ACTION_MODE", "").strip()
        safety_value = self.values.get("TELEGRAM_OPERATOR_SAFETY_MODE", "").strip()
        if not safety_value:
            legacy = self.values.get("TELEGRAM_OPERATOR_SAFE_MODE", DEFAULTS["TELEGRAM_OPERATOR_SAFE_MODE"])
            safety_value = "restricted" if legacy.strip().lower() in {"1", "true", "yes", "on"} else "safe"
        if not access_scope:
            access_scope = "code" if safety_value == "code" else "full" if safety_value == "full" else "workspace"
        if not action_mode:
            action_mode = "approve" if safety_value == "restricted" else "full"
        self.vars["TELEGRAM_OPERATOR_ACCESS_SCOPE"] = tk.StringVar(value=access_scope_display(access_scope))
        self.vars["TELEGRAM_OPERATOR_ACTION_MODE"] = tk.StringVar(value=action_mode_display(action_mode))
        self.vars["TELEGRAM_OPERATOR_SAFETY_MODE"] = tk.StringVar(value=safety_display(safety_value))
        safety_options = ctk.CTkFrame(agent, fg_color="transparent")
        safety_options.grid(row=5, column=0, sticky="ew")
        safety_options.grid_columnconfigure((0, 1), weight=1, uniform="safety_options")
        self._label(safety_options, "Access scope", row=0, column=0, padx=(0, 6))
        ctk.CTkComboBox(
            safety_options,
            variable=self.vars["TELEGRAM_OPERATOR_ACCESS_SCOPE"],
            values=ACCESS_SCOPE_OPTIONS,
            height=38,
            corner_radius=10,
            border_width=1,
        ).grid(row=1, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
        self._label(safety_options, "Action mode", row=0, column=1, padx=(6, 0))
        ctk.CTkComboBox(
            safety_options,
            variable=self.vars["TELEGRAM_OPERATOR_ACTION_MODE"],
            values=ACTION_MODE_OPTIONS,
            height=38,
            corner_radius=10,
            border_width=1,
        ).grid(row=1, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))
        self._switch(agent, "Shared context injection", "TELEGRAM_OPERATOR_SHARED_CONTEXT_ENABLED", row=6)

        voice = self._card(settings_body, "Voice", "Optional STT/TTS setup for voice notes and spoken replies.", 2, 0)
        self.vars["TELEGRAM_OPERATOR_KOKORO_VOICE"] = tk.StringVar(
            value=self.values.get("TELEGRAM_OPERATOR_KOKORO_VOICE", DEFAULTS["TELEGRAM_OPERATOR_KOKORO_VOICE"])
        )
        self._label(voice, "Voice", row=0)
        voice_row = ctk.CTkFrame(voice, fg_color="transparent")
        voice_row.grid(row=1, column=0, sticky="ew", pady=(3, 12))
        voice_row.grid_columnconfigure(0, weight=1)
        self.voice_combo = ctk.CTkComboBox(
            voice_row,
            variable=self.vars["TELEGRAM_OPERATOR_KOKORO_VOICE"],
            values=[self.vars["TELEGRAM_OPERATOR_KOKORO_VOICE"].get()],
            command=self.on_voice_selected,
            height=38,
            corner_radius=10,
            border_width=1,
        )
        self.voice_combo.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(voice_row, text="Refresh", width=92, height=38, corner_radius=10, command=self.refresh_voices).grid(
            row=0, column=1, padx=(8, 0)
        )
        voice_options = ctk.CTkFrame(voice, fg_color="transparent")
        voice_options.grid(row=2, column=0, sticky="ew")
        voice_options.grid_columnconfigure((0, 1), weight=1, uniform="voice_options")
        self.vars["TELEGRAM_OPERATOR_KOKORO_LANG_CODE"] = tk.StringVar(
            value=language_display(
                self.values.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE", DEFAULTS["TELEGRAM_OPERATOR_KOKORO_LANG_CODE"])
            )
        )
        self._label(voice_options, "Language code", row=0, column=0, padx=(0, 6))
        ctk.CTkComboBox(
            voice_options,
            variable=self.vars["TELEGRAM_OPERATOR_KOKORO_LANG_CODE"],
            values=LANGUAGE_LABELS,
            height=38,
            corner_radius=10,
            border_width=1,
        ).grid(row=1, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
        self.vars["TELEGRAM_OPERATOR_WHISPER_MODEL"] = tk.StringVar(
            value=self.values.get("TELEGRAM_OPERATOR_WHISPER_MODEL", DEFAULTS["TELEGRAM_OPERATOR_WHISPER_MODEL"])
        )
        self._label(voice_options, "Whisper model", row=0, column=1, padx=(6, 0))
        self.whisper_combo = ctk.CTkComboBox(
            voice_options,
            variable=self.vars["TELEGRAM_OPERATOR_WHISPER_MODEL"],
            values=WHISPER_MODELS,
            height=38,
            corner_radius=10,
            border_width=1,
        )
        self.whisper_combo.grid(row=1, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))
        self._switch(voice, "Voice replies enabled", "TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED", row=3)

        runtime = self._card(settings_body, "Runtime", "Start, stop, update, and monitor the bridge.", 0, 0)
        buttons = ctk.CTkFrame(runtime, fg_color="transparent")
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        buttons.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.start_button = ctk.CTkButton(
            buttons,
            text="Start bridge",
            height=40,
            corner_radius=12,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["accent_text"],
            command=self.start_operator,
        )
        self.start_button.grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        self.stop_button = ctk.CTkButton(
            buttons,
            text="Stop bridge",
            height=40,
            corner_radius=12,
            fg_color=COLORS["danger"],
            hover_color=COLORS["danger_hover"],
            command=self.stop_operator,
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.restart_button = ctk.CTkButton(
            buttons,
            text="Restart",
            height=40,
            corner_radius=12,
            fg_color=COLORS["panel_lift"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=self.restart_operator,
        )
        self.restart_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        self.update_button = ctk.CTkButton(
            buttons,
            text="Update",
            height=40,
            corner_radius=12,
            fg_color=COLORS["panel_lift"],
            hover_color=COLORS["border"],
            text_color=COLORS["text"],
            command=self.update_from_source,
        )
        self.update_button.grid(row=0, column=3, sticky="ew", padx=(8, 0))
        self.status_detail = ctk.CTkLabel(runtime, text="Status: checking...", text_color=COLORS["muted"], anchor="w")
        self.status_detail.grid(row=1, column=0, sticky="ew")

        logs = self._card(settings_body, "Recent Log", "Latest bridge activity and setup errors.", 4, 0)
        logs.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(
            logs,
            height=180,
            corner_radius=12,
            border_width=1,
            fg_color=COLORS["bg"],
            border_color=COLORS["border_soft"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
        )
        self.log_box.grid(row=0, column=0, sticky="nsew", pady=(4, 0))

    def _build_chat_card(self, parent: ctk.CTkBaseClass) -> None:
        chat = ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_color=COLORS["border"], border_width=1, corner_radius=24)
        self.chat_card = chat
        chat.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        chat.grid_columnconfigure(0, weight=1)
        chat.grid_rowconfigure(1, weight=1)

        chat_header = ctk.CTkFrame(chat, fg_color="transparent")
        chat_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 8))
        chat_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(chat_header, text="Conversation", font=ctk.CTkFont(size=19, weight="bold"), text_color=COLORS["text"]).grid(
            row=0, column=0, sticky="w"
        )
        self.thinking_label = ctk.CTkLabel(
            chat_header,
            text="",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.thinking_label.grid(row=0, column=1, sticky="e")
        self.chat_scroll = ctk.CTkScrollableFrame(
            chat,
            corner_radius=12,
            border_width=1,
            fg_color=COLORS["bg"],
            border_color=COLORS["border_soft"],
        )
        self.chat_scroll.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.chat_scroll.grid_columnconfigure(0, weight=1)

        composer = ctk.CTkFrame(chat, fg_color="transparent")
        composer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        composer.grid_columnconfigure(0, weight=1)
        self.chat_input = ctk.CTkTextbox(
            composer,
            height=72,
            corner_radius=12,
            border_width=1,
            fg_color=COLORS["panel_soft"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            wrap="word",
        )
        self.chat_input.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.send_button = ctk.CTkButton(
            composer,
            text="Send",
            width=96,
            height=72,
            corner_radius=12,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["accent_text"],
            command=self.send_desktop_chat,
        )
        self.send_button.grid(row=0, column=1, sticky="e")

    def open_settings(self) -> None:
        if not self.settings_frame or not self.chat_card:
            return
        self.settings_visible = not self.settings_visible
        if self.settings_visible:
            self.chat_card.grid_remove()
            self.settings_frame.grid(row=0, column=0, sticky="nsew")
            if self.settings_button:
                self.settings_button.configure(text="Back to chat")
            self.refresh_log()
        else:
            self.settings_frame.grid_remove()
            self.chat_card.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
            if self.settings_button:
                self.settings_button.configure(text="Settings")

    def _configure_scrollable_background(self, frame: ctk.CTkScrollableFrame) -> None:
        for attr in ("_parent_canvas",):
            canvas = getattr(frame, attr, None)
            if canvas is not None:
                try:
                    canvas.configure(bg=COLORS["bg"], highlightthickness=0)
                except tk.TclError:
                    pass
        parent_frame = getattr(frame, "_parent_frame", None)
        if parent_frame is not None:
            try:
                parent_frame.configure(fg_color=COLORS["bg"], border_color=COLORS["bg"])
            except tk.TclError:
                pass

    def _card(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        subtitle: str,
        row: int,
        column: int,
        columnspan: int = 1,
        padx: tuple[int, int] = (0, 0),
    ) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=COLORS["panel_soft"], border_color=COLORS["border_soft"], border_width=1, corner_radius=18)
        card.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=padx, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        title_label = ctk.CTkLabel(
            card,
            text=self._label_text(title, CARD_HELP_TEXT),
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"],
        )
        title_label.grid(row=0, column=0, sticky="w", padx=18, pady=(15, 0))
        self._attach_tooltip(title_label, CARD_HELP_TEXT.get(title, ""))
        subtitle_label = ctk.CTkLabel(card, text=subtitle, text_color=COLORS["muted"], font=ctk.CTkFont(size=12), wraplength=560)
        subtitle_label.grid(
            row=1, column=0, sticky="w", padx=18, pady=(2, 12)
        )
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 18))
        inner.grid_columnconfigure(0, weight=1)
        return inner

    def _label(
        self,
        parent: ctk.CTkFrame,
        text: str,
        row: int,
        column: int = 0,
        padx: tuple[int, int] = (0, 0),
    ) -> ctk.CTkLabel:
        label = ctk.CTkLabel(parent, text=self._label_text(text), text_color=COLORS["muted"], font=ctk.CTkFont(size=12), anchor="w")
        label.grid(row=row, column=column, sticky="ew", padx=padx)
        self._attach_tooltip(label, HELP_TEXT.get(text, ""))
        return label

    def _label_text(self, text: str, help_texts: dict[str, str] | None = None) -> str:
        return f"{text}{INFO_MARK}" if (help_texts or HELP_TEXT).get(text) else text

    def _configure_label_text(self, label: ctk.CTkLabel, text: str) -> None:
        label.configure(text=self._label_text(text))
        self._attach_tooltip(label, HELP_TEXT.get(text, ""))

    def _attach_tooltip(self, widget: tk.Widget, text: str) -> None:
        if not text:
            return
        existing = getattr(widget, "_baseclaw_tooltip", None)
        if existing:
            existing.text = text
            return
        tooltip = Tooltip(widget, text)
        setattr(widget, "_baseclaw_tooltip", tooltip)
        self.tooltips.append(tooltip)

    def _entry(
        self,
        parent: ctk.CTkFrame,
        label: str,
        key: str,
        row: int,
        secret: bool = False,
        column: int = 0,
        padx: tuple[int, int] = (0, 0),
    ) -> ctk.CTkEntry:
        self.vars[key] = tk.StringVar(value=self.values.get(key, DEFAULTS[key]))
        self.entry_labels[key] = self._label(parent, label, row=row, column=column, padx=padx)
        entry = ctk.CTkEntry(
            parent,
            textvariable=self.vars[key],
            show="*" if secret else "",
            height=38,
            corner_radius=10,
            border_width=1,
            fg_color=COLORS["panel"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        entry.grid(row=row + 1, column=column, sticky="ew", pady=(3, 12), padx=padx)
        return entry

    def _path_entry(self, parent: ctk.CTkFrame, label: str, key: str, row: int) -> None:
        self.vars[key] = tk.StringVar(value=self.values.get(key, DEFAULTS[key]))
        self._label(parent, label, row=row)
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.grid(row=row + 1, column=0, sticky="ew", pady=(3, 12))
        line.grid_columnconfigure(0, weight=1)
        path_entry = ctk.CTkEntry(
            line,
            textvariable=self.vars[key],
            height=38,
            corner_radius=10,
            border_width=1,
            fg_color=COLORS["panel"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        path_entry.grid(
            row=0, column=0, sticky="ew"
        )
        ctk.CTkButton(line, text="Browse", width=92, height=38, corner_radius=10, command=self.choose_workspace).grid(
            row=0, column=1, padx=(8, 0)
        )

    def _switch(self, parent: ctk.CTkFrame, label: str, key: str, row: int) -> None:
        value = self.values.get(key, DEFAULTS[key]).strip().lower()
        self.vars[key] = tk.StringVar(value="true" if value in {"1", "true", "yes", "on", "enabled"} else "false")
        switch = ctk.CTkSwitch(
            parent,
            text=self._label_text(label),
            variable=self.vars[key],
            onvalue="true",
            offvalue="false",
            button_color="#D8E0E7",
            button_hover_color="#FFFFFF",
            progress_color="#2D6A4F",
            font=ctk.CTkFont(size=13),
        )
        switch.grid(row=row, column=0, sticky="w", pady=(2, 14))
        self._attach_tooltip(switch, HELP_TEXT.get(label, ""))

    def choose_workspace(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.vars["TELEGRAM_OPERATOR_WORKDIR"].get() or str(BASE_DIR))
        if directory:
            self.vars["TELEGRAM_OPERATOR_WORKDIR"].set(directory)

    def choose_allowed_path(self) -> None:
        current = self.vars.get("TELEGRAM_OPERATOR_ALLOWED_PATHS")
        directory = filedialog.askdirectory(initialdir=self.vars["TELEGRAM_OPERATOR_WORKDIR"].get() or str(BASE_DIR))
        if not directory or current is None:
            return
        existing = [part.strip() for part in current.get().split(";") if part.strip()]
        if directory not in existing:
            existing.append(directory)
        current.set("; ".join(existing))

    def remove_allowed_path(self) -> None:
        current = self.vars.get("TELEGRAM_OPERATOR_ALLOWED_PATHS")
        if current is None:
            return
        existing = [part.strip() for part in current.get().split(";") if part.strip()]
        if not existing:
            self.set_status("No paths", "There are no additional allowed paths to remove.", None)
            return
        if len(existing) == 1:
            removed = existing.pop()
        else:
            prompt = "Number to remove:\n\n" + "\n".join(f"{index + 1}. {path}" for index, path in enumerate(existing))
            index = simpledialog.askinteger("Remove allowed path", prompt, minvalue=1, maxvalue=len(existing), parent=self)
            if index is None:
                return
            removed = existing.pop(index - 1)
        current.set("; ".join(existing))
        self.set_status("Path removed", f"Removed allowed path: {removed}", None)

    def on_run_mode_selected(self, _value: str | None = None) -> None:
        mode = run_mode_value(self.vars["TELEGRAM_OPERATOR_RUN_MODE"].get())
        self.vars["TELEGRAM_OPERATOR_RUN_MODE"].set(run_mode_display(mode))
        provider_var = self.vars.get("TELEGRAM_OPERATOR_PROVIDER")
        if provider_var:
            provider = provider_var.get().strip().lower()
            allowed = self._harness_options(mode)
            if provider not in allowed:
                provider_var.set(allowed[0])
            if self.harness_combo:
                self.harness_combo.configure(values=allowed)
        self.on_provider_selected()

    def on_model_provider_selected(self, _value: str | None = None) -> None:
        model_provider = model_provider_value(self.vars["TELEGRAM_OPERATOR_MODEL_PROVIDER"].get())
        self.vars["TELEGRAM_OPERATOR_MODEL_PROVIDER"].set(model_provider_display(model_provider))
        profile_var = self.vars.get("TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE")
        if profile_var:
            profile_var.set("")
        port_var = self.vars.get("TELEGRAM_OPERATOR_LLM_PORT")
        if port_var:
            if model_provider in DEFAULT_LLM_PORTS:
                port_var.set(default_llm_port(model_provider))
        model_var = self.vars.get("TELEGRAM_OPERATOR_CODEX_MODEL")
        if self.model_combo and model_var:
            self.model_combo.configure(values=self._model_options(self.vars["TELEGRAM_OPERATOR_PROVIDER"].get(), model_var.get()))
        self._sync_agent_option_visibility()

    def refresh_models(self) -> None:
        model_var = self.vars.get("TELEGRAM_OPERATOR_CODEX_MODEL")
        if not self.model_combo or not model_var:
            return
        options = self._model_options(self.vars["TELEGRAM_OPERATOR_PROVIDER"].get(), model_var.get().strip())
        self.model_combo.configure(values=options)
        if options and model_var.get().strip() not in options:
            model_var.set(options[0])
        self.set_status("Models refreshed", f"Found {len([item for item in options if item])} model option(s).", True)

    def _multi_path_entry(self, parent: ctk.CTkFrame, label: str, key: str, row: int) -> None:
        self.vars[key] = tk.StringVar(value=self.values.get(key, DEFAULTS[key]))
        self._label(parent, label, row=row)
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.grid(row=row + 1, column=0, sticky="ew", pady=(3, 12))
        line.grid_columnconfigure(0, weight=1)
        path_entry = ctk.CTkEntry(
            line,
            textvariable=self.vars[key],
            height=38,
            corner_radius=10,
            border_width=1,
            fg_color=COLORS["panel"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        path_entry.grid(
            row=0, column=0, sticky="ew"
        )
        ctk.CTkButton(line, text="Add Path", width=92, height=38, corner_radius=10, command=self.choose_allowed_path).grid(
            row=0, column=1, padx=(8, 0)
        )
        ctk.CTkButton(line, text="Remove", width=82, height=38, corner_radius=10, command=self.remove_allowed_path).grid(
            row=0, column=2, padx=(8, 0)
        )

    def on_provider_selected(self, _value: str | None = None) -> None:
        provider = self.vars["TELEGRAM_OPERATOR_PROVIDER"].get().strip().lower() or "jcode"
        self.vars["TELEGRAM_OPERATOR_PROVIDER"].set(provider)
        self.vars["TELEGRAM_OPERATOR_AGENT_COMMAND"].set("")
        model_var = self.vars.get("TELEGRAM_OPERATOR_CODEX_MODEL")
        if not model_var:
            return
        model = model_var.get().strip()
        options = self._model_options(provider, model)
        if provider == "jcode" and not model:
            model_var.set("qwen3-coder-30b")
        elif provider == "codex" and model in {"qwen3-coder-30b", "qwen/qwen3-coder-30b"}:
            model_var.set("default")
        elif provider in {"claude", "gemini"} and model in {"qwen3-coder-30b", "qwen/qwen3-coder-30b"}:
            model_var.set("")
        if self.model_combo:
            self.model_combo.configure(values=self._model_options(provider, model_var.get().strip()))
        self._sync_agent_option_visibility()

    def _sync_agent_option_visibility(self) -> None:
        mode_var = self.vars.get("TELEGRAM_OPERATOR_RUN_MODE")
        provider_var = self.vars.get("TELEGRAM_OPERATOR_PROVIDER")
        model_provider_var = self.vars.get("TELEGRAM_OPERATOR_MODEL_PROVIDER")
        mode = run_mode_value(mode_var.get()) if mode_var else "local"
        provider = provider_var.get().strip().lower() if provider_var else "jcode"
        model_provider = model_provider_value(model_provider_var.get()) if model_provider_var else "lmstudio"
        local_mode = mode == "local" and provider == "jcode"

        if self.local_provider_label and self.local_provider_combo:
            if local_mode:
                self.local_provider_label.grid(row=2, column=0, sticky="ew", padx=(0, 6))
                self.local_provider_combo.grid(row=3, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
            else:
                self.local_provider_label.grid_remove()
                self.local_provider_combo.grid_remove()

        if self.model_label and self.model_picker:
            if local_mode:
                self._configure_label_text(self.model_label, "Model")
                self.model_label.grid(row=2, column=1, sticky="ew", padx=(6, 0))
                self.model_picker.grid(row=3, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))
            else:
                model_label = "Claude model" if provider == "claude" else "Gemini model" if provider == "gemini" else "Codex model"
                self._configure_label_text(self.model_label, model_label)
                self.model_label.grid(row=2, column=0, sticky="ew", padx=(0, 6))
                self.model_picker.grid(row=3, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))

        if self.model_refresh_button:
            if local_mode:
                self.model_refresh_button.grid(row=0, column=1, padx=(8, 0))
            else:
                self.model_refresh_button.grid_remove()

        no_key_providers = {"lmstudio", "ollama", "jcode", "claude", "cursor", "copilot", "gemini", "antigravity", "google", "auto"}
        api_key_needed = local_mode and model_provider not in no_key_providers
        if self.api_key_label and self.api_key_entry:
            if api_key_needed:
                provider_label = MODEL_PROVIDER_LABELS.get(model_provider, model_provider)
                self.api_key_label.configure(text=f"{provider_label} API key{INFO_MARK}")
                self._attach_tooltip(
                    self.api_key_label,
                    f"API key used only for the selected hosted JCode provider: {provider_label}. It is not used for LM Studio or Ollama.",
                )
                self.api_key_label.grid(row=4, column=0, sticky="ew", padx=(0, 6))
                self.api_key_entry.grid(row=5, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
            else:
                self.api_key_label.grid_remove()
                self.api_key_entry.grid_remove()

        if self.llm_port_label and self.llm_port_entry:
            if local_mode and model_provider in DEFAULT_LLM_PORTS:
                self._configure_label_text(self.llm_port_label, "LLM port (auto)")
                self.llm_port_entry.configure(state="disabled")
            else:
                self._configure_label_text(self.llm_port_label, "LLM port")
                self.llm_port_entry.configure(state="normal")

        if self.timeout_label and self.timeout_entry:
            if local_mode:
                self.timeout_label.grid(row=4, column=1, sticky="ew", padx=(6, 0))
                self.timeout_entry.grid(row=5, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))
            else:
                self.timeout_label.grid(row=2, column=1, sticky="ew", padx=(6, 0))
                self.timeout_entry.grid(row=3, column=1, sticky="ew", pady=(3, 12), padx=(6, 0))

    def _model_options(self, provider: str, model: str) -> list[str]:
        provider = provider.strip().lower()
        if provider == "codex":
            options = CODEX_MODELS
        elif provider == "jcode":
            options = self._local_model_options()
        elif provider == "claude":
            options = CLAUDE_MODELS
        elif provider == "gemini":
            options = gemini_model_options()
        else:
            options = ["", *JCODE_MODELS, *CODEX_MODELS]
        return options if model in options else [model, *options]

    def _local_model_options(self) -> list[str]:
        provider_var = self.vars.get("TELEGRAM_OPERATOR_MODEL_PROVIDER")
        model_provider = model_provider_value(provider_var.get()) if provider_var else "lmstudio"
        if model_provider == "lmstudio":
            models = self._lm_studio_models()
            return models or JCODE_MODELS
        if model_provider == "ollama":
            models = self._ollama_models()
            return models or ["gemma4:31b", "deepseek-coder:33b", "deepseek-coder:6.7b", "qwen3-coder-30b"]
        return ["", "auto"]

    def _lm_studio_models(self) -> list[str]:
        host_var = self.vars.get("TELEGRAM_OPERATOR_REMOTE_HOST")
        port_var = self.vars.get("TELEGRAM_OPERATOR_LLM_PORT")
        host = host_var.get().strip() if host_var else self.values.get("TELEGRAM_OPERATOR_REMOTE_HOST", "127.0.0.1")
        port = port_var.get().strip() if port_var else self.values.get("TELEGRAM_OPERATOR_LLM_PORT", "1234")
        url = build_host_url(host, port, "/v1/models")
        try:
            with urlopen(url, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError):
            return []
        models = []
        for item in payload.get("data", []):
            model_id = str(item.get("id") or "").strip()
            if model_id:
                models.append(model_id)
        return sorted(dict.fromkeys(models))

    def _ollama_models(self) -> list[str]:
        host_var = self.vars.get("TELEGRAM_OPERATOR_REMOTE_HOST")
        port_var = self.vars.get("TELEGRAM_OPERATOR_LLM_PORT")
        host = host_var.get().strip() if host_var else self.values.get("TELEGRAM_OPERATOR_REMOTE_HOST", "127.0.0.1")
        port = port_var.get().strip() if port_var else self.values.get("TELEGRAM_OPERATOR_LLM_PORT", "11434")
        url = build_host_url(host, port, "/api/tags")
        try:
            with urlopen(url, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError):
            return []
        models = []
        for item in payload.get("models", []):
            model_id = str(item.get("name") or item.get("model") or "").strip()
            if model_id:
                models.append(model_id)
        return sorted(dict.fromkeys(models))

    def _harness_options(self, mode: str) -> list[str]:
        mode = run_mode_value(mode)
        return CLOUD_HARNESSES if mode == "cloud" else LOCAL_HARNESSES

    def on_voice_selected(self, value: str | None = None) -> None:
        voice = (value or self.vars["TELEGRAM_OPERATOR_KOKORO_VOICE"].get()).strip()
        lang_var = self.vars.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE")
        if not voice or lang_var is None:
            return
        prefix_map = {
            "af_": LANGUAGE_CODE_LABELS["a"],
            "am_": LANGUAGE_CODE_LABELS["a"],
            "bf_": LANGUAGE_CODE_LABELS["b"],
            "bm_": LANGUAGE_CODE_LABELS["b"],
            "dm_": LANGUAGE_CODE_LABELS["d"],
        }
        for prefix, label in prefix_map.items():
            if voice.startswith(prefix):
                lang_var.set(label)
                return

    def current_values(self) -> dict[str, str]:
        values = read_env(self.env_path)
        for key, var in self.vars.items():
            values[key] = var.get().strip()
        values["TELEGRAM_BOT_TOKEN"] = normalize_bot_token(values.get("TELEGRAM_BOT_TOKEN", ""))
        values["TELEGRAM_OPERATOR_KOKORO_LANG_CODE"] = language_code(
            values.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE", "")
        )
        values["TELEGRAM_OPERATOR_RUN_MODE"] = run_mode_value(values.get("TELEGRAM_OPERATOR_RUN_MODE", ""))
        values["TELEGRAM_OPERATOR_MODEL_PROVIDER"] = model_provider_value(values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", ""))
        if values["TELEGRAM_OPERATOR_MODEL_PROVIDER"] in DEFAULT_LLM_PORTS:
            values["TELEGRAM_OPERATOR_LLM_PORT"] = default_llm_port(values["TELEGRAM_OPERATOR_MODEL_PROVIDER"])
        if values["TELEGRAM_OPERATOR_RUN_MODE"] == "local":
            values["TELEGRAM_OPERATOR_PROVIDER"] = "jcode"
            values["TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE"] = ""
        elif values.get("TELEGRAM_OPERATOR_PROVIDER", "").strip().lower() not in CLOUD_HARNESSES:
            values["TELEGRAM_OPERATOR_PROVIDER"] = "codex"
        if values.get("TELEGRAM_OPERATOR_PROVIDER", "").strip().lower() == "codex" and values.get("TELEGRAM_OPERATOR_CODEX_MODEL") == "default":
            values["TELEGRAM_OPERATOR_CODEX_MODEL"] = ""
        values["TELEGRAM_OPERATOR_ACCESS_SCOPE"] = access_scope_mode(values.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", ""))
        values["TELEGRAM_OPERATOR_ACTION_MODE"] = action_mode_value(values.get("TELEGRAM_OPERATOR_ACTION_MODE", ""))
        legacy_safety = {
            ("workspace", "approve"): "restricted",
            ("workspace", "read"): "safe",
            ("workspace", "full"): "safe",
            ("code", "approve"): "restricted",
            ("code", "read"): "code",
            ("code", "full"): "code",
            ("full", "approve"): "restricted",
            ("full", "read"): "safe",
            ("full", "full"): "full",
        }
        values["TELEGRAM_OPERATOR_SAFETY_MODE"] = legacy_safety.get(
            (values["TELEGRAM_OPERATOR_ACCESS_SCOPE"], values["TELEGRAM_OPERATOR_ACTION_MODE"]),
            safety_mode(values.get("TELEGRAM_OPERATOR_SAFETY_MODE", "")),
        )
        values["TELEGRAM_OPERATOR_SAFE_MODE"] = "true" if values["TELEGRAM_OPERATOR_ACTION_MODE"] == "approve" else "false"
        values["TELEGRAM_OPERATOR_STARTUP_NOTICE"] = "true"
        workdir = values.get("TELEGRAM_OPERATOR_WORKDIR") or str(profile_workdir(self.profile_name))
        values["TELEGRAM_OPERATOR_WORKDIR"] = workdir
        values["TELEGRAM_OPERATOR_STATE_PATH"] = str(profile_state_path(self.profile_name))
        values["TELEGRAM_OPERATOR_MEMORY_LOG"] = str(profile_memory_log_path(self.profile_name))
        values["TELEGRAM_OPERATOR_SQLITE_PATH"] = str(profile_sqlite_path(self.profile_name))
        values["TELEGRAM_OPERATOR_LOG_PATH"] = str(profile_log_path(self.profile_name))
        host = values.get("TELEGRAM_OPERATOR_REMOTE_HOST", "").strip()
        speech_port = values.get("TELEGRAM_OPERATOR_SPEECH_PORT", "").strip() or "8766"
        llm_port = values.get("TELEGRAM_OPERATOR_LLM_PORT", "").strip() or "1234"
        if host:
            remote_speech_url = normalize_speech_url(build_host_url(host, speech_port))
            values["TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL"] = build_host_url(host, llm_port, "/v1")
        else:
            remote_speech_url = normalize_speech_url(values.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", ""))
        values["TELEGRAM_OPERATOR_REMOTE_SPEECH_URL"] = remote_speech_url
        values["TELEGRAM_OPERATOR_KOKORO_URL"] = remote_speech_url or DEFAULTS["TELEGRAM_OPERATOR_KOKORO_URL"]
        values["TELEGRAM_OPERATOR_KOKORO_URLS"] = ""
        values["TELEGRAM_OPERATOR_WHISPER_URLS"] = ""
        values["TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK"] = "true"
        values["TELEGRAM_OPERATOR_PROVIDER"] = values.get("TELEGRAM_OPERATOR_PROVIDER", "").strip().lower() or "jcode"
        if values["TELEGRAM_OPERATOR_PROVIDER"] not in PROVIDERS:
            values["TELEGRAM_OPERATOR_PROVIDER"] = "jcode"
        if values["TELEGRAM_OPERATOR_PROVIDER"] != "custom":
            values["TELEGRAM_OPERATOR_AGENT_COMMAND"] = ""
        return values

    def _load_values_into_vars(self) -> None:
        was_ready = self.autosave_ready
        self.autosave_ready = False
        try:
            values = self._profile_values(self.profile_name)
            provider = values.get("TELEGRAM_OPERATOR_PROVIDER", "").strip() or "jcode"
            if provider not in PROVIDERS:
                provider = "jcode"
            run_mode = values.get("TELEGRAM_OPERATOR_RUN_MODE", "").strip()
            if not run_mode:
                run_mode = "cloud" if provider in CLOUD_HARNESSES else "local"
            model_provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip() or "lmstudio"
            safety_value = values.get("TELEGRAM_OPERATOR_SAFETY_MODE", "").strip()
            legacy = values.get("TELEGRAM_OPERATOR_SAFE_MODE", DEFAULTS["TELEGRAM_OPERATOR_SAFE_MODE"])
            if not safety_value:
                safety_value = "restricted" if legacy.strip().lower() in {"1", "true", "yes", "on"} else "safe"
            access_scope = values.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", "").strip()
            action_mode = values.get("TELEGRAM_OPERATOR_ACTION_MODE", "").strip()
            if not access_scope:
                access_scope = "code" if safety_value == "code" else "full" if safety_value == "full" else "workspace"
            if not action_mode:
                action_mode = "approve" if safety_value == "restricted" else "full"

            display_overrides = {
                "TELEGRAM_OPERATOR_RUN_MODE": run_mode_display(run_mode),
                "TELEGRAM_OPERATOR_MODEL_PROVIDER": model_provider_display(model_provider),
                "TELEGRAM_OPERATOR_ACCESS_SCOPE": access_scope_display(access_scope),
                "TELEGRAM_OPERATOR_ACTION_MODE": action_mode_display(action_mode),
                "TELEGRAM_OPERATOR_SAFETY_MODE": safety_display(safety_value),
                "TELEGRAM_OPERATOR_KOKORO_LANG_CODE": language_display(
                    values.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE", DEFAULTS["TELEGRAM_OPERATOR_KOKORO_LANG_CODE"])
                ),
            }
            for key, var in self.vars.items():
                if key == "TELEGRAM_OPERATOR_PROVIDER":
                    var.set(provider)
                elif key in display_overrides:
                    var.set(display_overrides[key])
                else:
                    var.set(values.get(key, DEFAULTS.get(key, "")))
            if self.harness_combo:
                self.harness_combo.configure(values=self._harness_options(run_mode))
            if self.model_combo:
                model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip() or "qwen3-coder-30b"
                self.model_combo.configure(values=self._model_options(provider, model))
            self._sync_agent_option_visibility()
        finally:
            self.autosave_ready = was_ready

    def ensure_speech_ready(self, values: dict[str, str]) -> bool:
        voice_enabled = parse_bool(values.get("TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED", ""), True)
        remote = values.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", "").strip()
        local_fallback = True
        if remote:
            if speech_health(remote):
                return True
        if local_fallback:
            for url in local_speech_urls():
                if speech_health(url):
                    self.set_status("Speech ready", f"Using local speech host at {url}.", None)
                    self.refresh_voices()
                    return True
            ok, detail = start_local_speech_host()
            if not ok:
                if voice_enabled:
                    self._disable_voice_for_text_only_start(values, f"{detail} Starting text-only with voice replies disabled.")
                else:
                    self.set_status("Text-only", detail, None)
                return True
            self.set_status("Speech ready", detail, None)
            self.refresh_voices()
            return True
        detail = "No STT/TTS host is configured. Starting text-only."
        if voice_enabled:
            self._disable_voice_for_text_only_start(values, f"{detail} Voice replies are disabled until speech is configured.")
        else:
            self.set_status("Text-only", detail, None)
        return True

    def _disable_voice_for_text_only_start(self, values: dict[str, str], detail: str) -> None:
        values["TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED"] = "false"
        if "TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED" in self.vars:
            self.vars["TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED"].set("false")
        write_env(self.env_path, values)
        self.values = values
        self.set_status("Text-only", detail, None)
        messagebox.showwarning("Starting text-only", detail)

    def set_status(self, label: str, detail: str, running: bool | None = None) -> None:
        if self.status_pill:
            color = "#2F6B4E" if running else "#743C46" if running is False else COLORS["status"]
            self.status_pill.configure(text=label, fg_color=color)
        if self.status_detail:
            self.status_detail.configure(text=detail)

    def _wire_autosave(self) -> None:
        for var in self.vars.values():
            var.trace_add("write", lambda *_args: self._schedule_autosave())

    def _schedule_autosave(self) -> None:
        if not self.autosave_ready:
            return
        if self.autosave_after_id:
            self.after_cancel(self.autosave_after_id)
        self.autosave_after_id = self.after(450, self._autosave)

    def _autosave(self) -> None:
        self.autosave_after_id = None
        write_env(self.env_path, self.current_values())
        self.set_status("Saved", "Settings saved automatically.", None)

    def save(self, show_message: bool = True) -> None:
        write_env(self.env_path, self.current_values())
        self.set_status("Saved", f"Settings saved to {self.env_path}", None)
        if show_message:
            messagebox.showinfo("Saved", f"Saved settings to {self.env_path}")

    def send_ui_startup_notice(self) -> None:
        values = self.current_values()
        if not parse_bool(values.get("TELEGRAM_OPERATOR_STARTUP_NOTICE", ""), True):
            return
        token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_ids = self._allowed_chat_ids(values)
        if not token or not chat_ids:
            return
        text = self._ui_startup_summary(values)

        def worker() -> None:
            failures = []
            for chat_id in chat_ids:
                try:
                    self._post_telegram_message(token, chat_id, text)
                except Exception as exc:
                    failures.append(str(exc))
            if failures:
                self.after(0, lambda: self.set_status("Notice failed", failures[0], False))

        threading.Thread(target=worker, daemon=True).start()

    def _post_telegram_message(self, token: str, chat_id: int, text: str) -> None:
        payload = urlencode({"chat_id": str(chat_id), "text": text}).encode("utf-8")
        with urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=8) as response:
            if response.status >= 400:
                raise RuntimeError(f"Telegram sendMessage failed with HTTP {response.status}")

    def _ui_startup_summary(self, values: dict[str, str]) -> str:
        provider = values.get("TELEGRAM_OPERATOR_PROVIDER", "jcode").strip().lower() or "jcode"
        run_mode = run_mode_display(values.get("TELEGRAM_OPERATOR_RUN_MODE", "local"))
        model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
        if not model:
            model = "Claude CLI default" if provider == "claude" else "Gemini CLI default" if provider == "gemini" else "Codex CLI default" if provider == "codex" else "JCode default"
        lines = [
            "BaseClaw UI opened.",
            f"Mode: {run_mode}",
            f"Harness: {provider}",
            f"Model: {model}",
        ]
        if provider == "jcode":
            lines.append(f"Model provider: {model_provider_display(values.get('TELEGRAM_OPERATOR_MODEL_PROVIDER', 'lmstudio'))}")
        lines.extend(
            [
                f"Workspace: {values.get('TELEGRAM_OPERATOR_WORKDIR', '')}",
                f"Access: {access_scope_display(values.get('TELEGRAM_OPERATOR_ACCESS_SCOPE', 'workspace'))}",
                f"Actions: {action_mode_display(values.get('TELEGRAM_OPERATOR_ACTION_MODE', 'full'))}",
                f"Voice replies: {'on' if parse_bool(values.get('TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED', ''), True) else 'off'}",
                f"Voice: {values.get('TELEGRAM_OPERATOR_KOKORO_VOICE', '')}",
                f"Whisper: {values.get('TELEGRAM_OPERATOR_WHISPER_MODEL', '')}",
                f"STT/TTS: {values.get('TELEGRAM_OPERATOR_REMOTE_SPEECH_URL', '') or 'not configured'}",
                f"Updates: {UPDATE_SOURCE_URL}",
            ]
        )
        return "\n".join(lines)

    def refresh_voices(self) -> None:
        urls = []
        host_var = self.vars.get("TELEGRAM_OPERATOR_REMOTE_HOST")
        speech_port_var = self.vars.get("TELEGRAM_OPERATOR_SPEECH_PORT")
        if host_var and host_var.get().strip():
            remote = build_host_url(host_var.get(), speech_port_var.get() if speech_port_var else "8766")
        else:
            remote_var = self.vars.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL")
            remote = remote_var.get().strip() if remote_var else self.values.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", "")
        remote = normalize_speech_url(remote)
        if remote:
            urls.append(remote)
        urls.extend(local_speech_urls())

        voices = []
        seen_urls = set()
        for url in urls:
            url = url.rstrip("/")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                with urlopen(url + "/voices", timeout=4) as response:
                    data = json.loads(response.read().decode("utf-8"))
                for value in data.values():
                    if isinstance(value, list):
                        voices.extend(str(item) for item in value)
                    elif isinstance(value, dict):
                        for nested in value.values():
                            if isinstance(nested, list):
                                voices.extend(str(item) for item in nested)
                if voices:
                    break
            except (OSError, URLError, json.JSONDecodeError):
                continue
        current = self.vars.get("TELEGRAM_OPERATOR_KOKORO_VOICE")
        if current and current.get() and current.get() not in voices:
            voices.insert(0, current.get())
        if self.voice_combo:
            self.voice_combo.configure(values=sorted(set(voices)))

    def refresh_status(self) -> None:
        try:
            processes = root_operator_processes(self.env_path)
            if processes:
                pids = ", ".join(str(item["ProcessId"]) for item in processes)
                self.set_status("Running", f"{self.profile_name} running, pid(s): {pids}", True)
            else:
                self.set_status("Stopped", f"{self.profile_name} operator is stopped.", False)
        except Exception as exc:
            self.set_status("Error", f"Status error: {exc}", False)
        self.refresh_log()

    def auto_refresh_status(self) -> None:
        self.refresh_status()
        self.after(5000, self.auto_refresh_status)

    def check_for_updates(self) -> None:
        if self.update_check_running:
            return
        self.update_check_running = True

        def worker() -> None:
            try:
                available, detail = self._check_update_source()
            except Exception as exc:
                available, detail = False, f"Update check failed: {exc}"
            self.after(0, lambda: self._finish_update_check(available, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update_check(self, available: bool, detail: str) -> None:
        self.update_check_running = False
        self.update_available = available
        self.update_detail = detail
        self._refresh_update_button()
        if available:
            self.set_status("Update available", detail, None)
        self.after(30 * 60 * 1000, self.check_for_updates)

    def _refresh_update_button(self) -> None:
        if not self.update_button:
            return
        if self.update_available:
            self.update_button.configure(
                text="Update •",
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["accent_text"],
            )
        else:
            self.update_button.configure(
                text="Update",
                fg_color=COLORS["panel_lift"],
                hover_color=COLORS["border"],
                text_color=COLORS["text"],
            )

    def refresh_log(self) -> None:
        if not self.log_box:
            return
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        if self.log_path.exists():
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            self.log_box.insert("end", redact_secrets("\n".join(lines)))
        self.log_box.configure(state="disabled")

    def refresh_chat_history(self) -> None:
        if not self.chat_scroll:
            return
        sqlite_path = Path(self.current_values().get("TELEGRAM_OPERATOR_SQLITE_PATH") or BASE_DIR / "telegram_operator_messages.sqlite3")
        rows: list[sqlite3.Row] = []
        if sqlite_path.exists():
            try:
                connection = sqlite3.connect(sqlite_path)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT id, recorded_at, direction, event_type, message_type, text, transcript
                    FROM telegram_messages
                    WHERE COALESCE(text, transcript, '') != ''
                    ORDER BY id DESC
                    LIMIT 80
                    """
                ).fetchall()
                connection.close()
            except sqlite3.Error:
                rows = []
        rows = list(reversed(rows))
        latest_id = rows[-1]["id"] if rows else 0
        if latest_id != self.last_chat_row_id:
            self.last_chat_row_id = latest_id
            for child in self.chat_scroll.winfo_children():
                child.destroy()
            if not rows:
                self._add_empty_chat_state()
            for row in rows:
                label = self._chat_row_label(row)
                content = (row["transcript"] or row["text"] or "").strip()
                if not content:
                    continue
                self._add_chat_bubble(label, content)
            self.after(50, self._scroll_chat_to_bottom)
        self.after(1500, self.refresh_chat_history)

    def _add_chat_bubble(self, label: str, content: str) -> None:
        if not self.chat_scroll:
            return
        is_user = label in {"Desktop", "Telegram"}
        row = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        row.grid(sticky="ew", padx=10, pady=(6, 2))
        row.grid_columnconfigure(0, weight=1)
        bubble = ctk.CTkFrame(
            row,
            fg_color=COLORS["user_bubble"] if is_user else COLORS["assistant_bubble"],
            corner_radius=18,
            border_width=0,
        )
        bubble.grid(row=0, column=0, sticky="e" if is_user else "w", padx=(80, 0) if is_user else (0, 80))
        ctk.CTkLabel(
            bubble,
            text=label,
            text_color=COLORS["user_label"] if is_user else COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            bubble,
            text=content,
            text_color=COLORS["accent_text"] if is_user else COLORS["text"],
            font=ctk.CTkFont(size=13),
            justify="left",
            wraplength=430,
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(2, 12))

    def _add_empty_chat_state(self) -> None:
        if not self.chat_scroll:
            return
        empty = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        empty.grid(sticky="nsew", padx=22, pady=80)
        empty.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            empty,
            text="Start a conversation",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0)
        ctk.CTkLabel(
            empty,
            text="Messages from Telegram and this desktop window will appear here. Open Settings first to add your bot token and choose a harness.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=13),
            wraplength=360,
            justify="center",
        ).grid(row=1, column=0, pady=(6, 0))

    def _scroll_chat_to_bottom(self) -> None:
        if not self.chat_scroll:
            return
        try:
            self.chat_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _chat_row_label(self, row: sqlite3.Row) -> str:
        event_type = row["event_type"] or ""
        direction = row["direction"] or ""
        if event_type.startswith("desktop_user"):
            return "Desktop"
        if event_type.startswith("desktop_agent"):
            return "Assistant"
        if direction == "in":
            return "Telegram"
        if direction == "out":
            return "Assistant"
        if event_type == "agent_turn_completed":
            return "Assistant"
        return "System"

    def _append_desktop_history(self, *, direction: str, event_type: str, text: str, session_id: str | None = None, values: dict[str, str] | None = None) -> None:
        values = values or self.current_values()
        sqlite_path = Path(values.get("TELEGRAM_OPERATOR_SQLITE_PATH") or BASE_DIR / "telegram_operator_messages.sqlite3")
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        chat_id = self._first_allowed_chat_id(values)
        with sqlite3.connect(sqlite_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    chat_id INTEGER,
                    telegram_message_id INTEGER,
                    telegram_user_id INTEGER,
                    telegram_username TEXT,
                    telegram_full_name TEXT,
                    message_type TEXT,
                    text TEXT,
                    transcript TEXT,
                    session_id TEXT,
                    safe_mode INTEGER,
                    approval_id TEXT,
                    metadata_json TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO telegram_messages (
                    recorded_at, direction, event_type, chat_id, message_type, text, session_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    direction,
                    event_type,
                    chat_id,
                    "desktop",
                    text,
                    session_id,
                    json.dumps({"source": "desktop_ui"}, ensure_ascii=True),
                ),
            )

    def _desktop_shared_context(self, values: dict[str, str], current_text: str = "") -> str:
        if not parse_bool(values.get("TELEGRAM_OPERATOR_SHARED_CONTEXT_ENABLED", ""), False):
            return ""
        sqlite_path = Path(values.get("TELEGRAM_OPERATOR_SQLITE_PATH") or BASE_DIR / "telegram_operator_messages.sqlite3")
        if not sqlite_path.exists():
            return ""
        try:
            limit = int(values.get("TELEGRAM_OPERATOR_SHARED_CONTEXT_LIMIT") or "12")
        except ValueError:
            limit = 12
        limit = max(1, min(30, limit))
        chat_id = self._first_allowed_chat_id(values)
        where = "direction IN ('in', 'out') AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL"
        params: list[object] = []
        if chat_id is not None:
            where += " AND (chat_id = ? OR chat_id IS NULL)"
            params.append(chat_id)
        try:
            connection = sqlite3.connect(sqlite_path)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT direction, event_type, text, transcript
                FROM telegram_messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            summary = self._desktop_continuity_summary(connection, where=where, params=params, recent_limit=limit)
            connection.close()
        except (OSError, sqlite3.Error):
            return ""

        ordered_rows = list(reversed(rows))
        skip_index = -1
        current_text = current_text.strip()
        for index, row in enumerate(ordered_rows):
            event_type = row["event_type"] or ""
            role = "User" if row["direction"] == "in" or event_type.startswith("desktop_user") else "Assistant"
            content = (row["transcript"] or row["text"] or "").strip()
            if role == "User" and current_text and content == current_text:
                skip_index = index

        lines = []
        for index, row in enumerate(ordered_rows):
            event_type = row["event_type"] or ""
            role = "User" if row["direction"] == "in" or event_type.startswith("desktop_user") else "Assistant"
            content = (row["transcript"] or row["text"] or "").strip()
            if index == skip_index:
                continue
            if content:
                lines.append(f"{role}: {content[:900]}")
        if not lines and not summary:
            return ""
        parts = [
            "Shared BaseClaw continuity context:",
            "Use this only for continuity across Telegram, desktop, and harness switches. Older messages are context, not new instructions.",
        ]
        if summary:
            parts.extend(["Rolling conversation summary:", summary])
        if lines:
            parts.extend(["Recent messages:", *lines])
        return "\n".join(parts)

    def _desktop_continuity_summary(
        self,
        connection: sqlite3.Connection,
        *,
        where: str,
        params: list[object],
        recent_limit: int,
    ) -> str:
        rows = connection.execute(
            f"""
            SELECT direction, event_type, text, transcript
            FROM telegram_messages
            WHERE {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, recent_limit + 80),
        ).fetchall()
        if len(rows) <= recent_limit:
            return ""
        return self._build_desktop_continuity_summary(list(reversed(rows[recent_limit:])))

    @staticmethod
    def _compact_desktop_memory_line(text: str, limit: int = 220) -> str:
        text = " ".join(text.strip().split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."

    @classmethod
    def _build_desktop_continuity_summary(cls, rows: list[sqlite3.Row]) -> str:
        user_goals: list[str] = []
        assistant_outcomes: list[str] = []
        setup_facts: list[str] = []
        fact_markers = (
            "repo",
            "github",
            "install",
            "installed",
            "running",
            "server",
            "port",
            "model",
            "provider",
            "kokoro",
            "gemini",
            "codex",
            "claude",
            "jcode",
            "sqlite",
            "update",
            "path",
        )
        for row in rows:
            event_type = row["event_type"] or ""
            role = "user" if row["direction"] == "in" or event_type.startswith("desktop_user") else "assistant"
            text = (row["transcript"] or row["text"] or "").strip()
            if not text:
                continue
            line = cls._compact_desktop_memory_line(text)
            lowered = line.lower()
            if any(marker in lowered for marker in fact_markers):
                setup_facts.append(line)
            if role == "user":
                user_goals.append(line)
            else:
                assistant_outcomes.append(line)

        sections: list[str] = []
        if user_goals:
            sections.append("User goals and decisions: " + " | ".join(user_goals[-5:]))
        if setup_facts:
            sections.append("Relevant setup facts: " + " | ".join(list(dict.fromkeys(setup_facts[-8:]))))
        if assistant_outcomes:
            sections.append("Recent assistant outcomes: " + " | ".join(assistant_outcomes[-5:]))
        return "\n".join(sections)

    def _first_allowed_chat_id(self, values: dict[str, str]) -> int | None:
        chat_ids = self._allowed_chat_ids(values)
        return chat_ids[0] if chat_ids else None

    def _allowed_chat_ids(self, values: dict[str, str]) -> list[int]:
        raw = values.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        chat_ids = []
        for part in re.split(r"[,\\s]+", raw):
            if part.strip().lstrip("-").isdigit():
                chat_ids.append(int(part.strip()))
        return chat_ids

    def _load_desktop_session_id(self, values: dict[str, str], provider: str) -> str | None:
        state_path = Path(values.get("TELEGRAM_OPERATOR_STATE_PATH") or BASE_DIR / "telegram_operator_state.json")
        if not state_path.exists():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        chat_id = self._first_allowed_chat_id(values)
        if chat_id is None:
            return None
        provider = provider.strip().lower()
        if provider:
            return (data.get("provider_sessions") or {}).get(str(chat_id), {}).get(provider)
        return (data.get("sessions") or {}).get(str(chat_id))

    def _save_desktop_session_id(self, session_id: str, values: dict[str, str], provider: str) -> None:
        if not session_id:
            return
        state_path = Path(values.get("TELEGRAM_OPERATOR_STATE_PATH") or BASE_DIR / "telegram_operator_state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data.setdefault("sessions", {})
        data.setdefault("provider_sessions", {})
        chat_id = self._first_allowed_chat_id(values)
        if chat_id is not None:
            data["sessions"][str(chat_id)] = session_id
            provider = provider.strip().lower()
            if provider:
                data["provider_sessions"].setdefault(str(chat_id), {})[provider] = session_id
            state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _start_thinking_indicator(self) -> None:
        self.thinking_step = 0
        if self.send_button:
            self.send_button.configure(state="disabled", text="Working")
        self._animate_thinking_indicator()

    def _animate_thinking_indicator(self) -> None:
        if not self.chat_busy:
            self._stop_thinking_indicator()
            return
        dots = "." * ((self.thinking_step % 3) + 1)
        if self.thinking_label:
            self.thinking_label.configure(text=f"Assistant thinking{dots}")
        self.thinking_step += 1
        self.thinking_after_id = self.after(450, self._animate_thinking_indicator)

    def _stop_thinking_indicator(self) -> None:
        if self.thinking_after_id:
            self.after_cancel(self.thinking_after_id)
            self.thinking_after_id = None
        if self.thinking_label:
            self.thinking_label.configure(text="")
        if self.send_button:
            self.send_button.configure(state="normal", text="Send")

    def _finish_desktop_agent_turn(self) -> None:
        self.chat_busy = False
        self._stop_thinking_indicator()
        self.refresh_chat_history()

    def send_desktop_chat(self) -> None:
        if self.chat_busy or not self.chat_input:
            return
        text = self.chat_input.get("1.0", "end").strip()
        if not text:
            return
        values = self.current_values()
        self.chat_input.delete("1.0", "end")
        self.chat_busy = True
        self._start_thinking_indicator()
        self._append_desktop_history(direction="in", event_type="desktop_user_message", text=text, values=values)
        self.refresh_chat_history()
        threading.Thread(target=self._run_desktop_agent_turn, args=(text, values), daemon=True).start()

    def _run_desktop_agent_turn(self, text: str, values: dict[str, str]) -> None:
        try:
            prompt = self._desktop_prompt(text, values)
            provider = values.get("TELEGRAM_OPERATOR_PROVIDER", "jcode").strip().lower() or "jcode"
            session_id = self._load_desktop_session_id(values, provider)
            cmd, stdin_text = self._desktop_agent_command(provider, prompt, session_id, values)
            env = dict(os.environ)
            if provider == "jcode":
                env["JCODE_NO_TELEMETRY"] = "1"
                base_url = self._jcode_base_url(values)
                if base_url:
                    env["BASECLAW_JCODE_BASE_URL"] = base_url
                    env["OPENAI_BASE_URL"] = base_url
                    env["LM_STUDIO_BASE_URL"] = base_url
                    if values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip().lower() == "ollama":
                        env["OLLAMA_HOST"] = base_url.removesuffix("/v1")
            result = subprocess.run(
                cmd,
                input=stdin_text,
                cwd=values.get("TELEGRAM_OPERATOR_WORKDIR") or str(DEFAULT_WORKSPACE),
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(values.get("TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS") or "900"),
                env=env,
            )
            if result.returncode != 0 and not result.stdout.strip():
                reply = (result.stderr or f"{provider} returned an error").strip()
                new_session_id = session_id
            elif provider == "jcode":
                new_session_id, reply = self._parse_jcode_json(result.stdout.strip(), session_id)
            else:
                new_session_id = session_id or f"{provider}:latest"
                reply = result.stdout.strip()
            if new_session_id:
                self._save_desktop_session_id(new_session_id, values, provider)
            self._append_desktop_history(
                direction="out",
                event_type="desktop_agent_reply",
                text=reply,
                session_id=new_session_id,
                values=values,
            )
        except Exception as exc:
            self._append_desktop_history(direction="out", event_type="desktop_agent_reply", text=f"Desktop chat error: {exc}", values=values)
        finally:
            self.after(0, self._finish_desktop_agent_turn)

    def _desktop_agent_command(
        self,
        provider: str,
        prompt: str,
        session_id: str | None,
        values: dict[str, str],
    ) -> tuple[list[str], str | None]:
        if provider == "jcode":
            self._ensure_jcode_api_key(values)
            cmd = [
                self._require_executable("jcode"),
                "--quiet",
                "--no-update",
                "--no-selfdev",
            ]
            profile = values.get("TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE", "").strip()
            jcode_provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip()
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
            if not profile:
                profile = self._ensure_jcode_local_profile(values)
            if profile:
                cmd.extend(["--provider-profile", profile])
            elif jcode_provider:
                cmd.extend(["--provider", jcode_provider])
            if model:
                cmd.extend(["--model", model])
            if session_id and not session_id.startswith("jcode:latest"):
                cmd.extend(["--resume", session_id])
            cmd.extend(["run", "--json", prompt])
            return cmd, None
        if provider == "claude":
            cmd = [self._require_executable("claude"), "-p", "--dangerously-skip-permissions", "--output-format", "text"]
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
            if model and model != "default":
                cmd.extend(["--model", model])
            if session_id:
                cmd.append("--continue")
            return cmd, prompt
        if provider == "gemini":
            cmd = [self._require_executable("gemini"), "--prompt", "", "--skip-trust", "--output-format", "text"]
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
            if model and model != "default":
                cmd.extend(["--model", model])
            action_mode = values.get("TELEGRAM_OPERATOR_ACTION_MODE", "full").strip().lower()
            approval_mode = "plan" if action_mode == "read" else "default" if action_mode == "approve" else "yolo"
            cmd.extend(["--approval-mode", approval_mode])
            if session_id:
                cmd.extend(["--resume", "latest"])
            return cmd, prompt
        if provider == "codex":
            codex = resolve_codex_command()
            workdir = Path(values.get("TELEGRAM_OPERATOR_WORKDIR") or DEFAULT_WORKSPACE).resolve()
            access_scope = values.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", "workspace").strip().lower()
            action_mode = values.get("TELEGRAM_OPERATOR_ACTION_MODE", "full").strip().lower()
            execution_dir = BASE_DIR if access_scope == "code" else workdir
            cmd = [
                *codex.args,
                "exec",
                "--skip-git-repo-check",
                "-C",
                str(execution_dir),
            ]
            if access_scope == "full" and action_mode == "full":
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                cmd.extend(["--sandbox", "read-only" if action_mode == "read" else "workspace-write"])
                if action_mode != "read":
                    add_dirs = self._allowed_write_dirs(values, execution_dir)
                    for path in add_dirs[1:]:
                        cmd.extend(["--add-dir", str(path)])
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
            if model and model != "default":
                cmd.extend(["--model", model])
            cmd.append("-")
            return cmd, prompt
        raise RuntimeError(f"Desktop chat currently supports jcode, codex, claude, and gemini, not {provider}.")

    def _ensure_jcode_api_key(self, values: dict[str, str]) -> None:
        api_key = values.get("TELEGRAM_OPERATOR_JCODE_API_KEY", "").strip()
        jcode_provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip()
        if not api_key or jcode_provider in {"", "lmstudio", "ollama"}:
            return
        subprocess.run(
            [
                self._require_executable("jcode"),
                "login",
                "--provider",
                jcode_provider,
                "--api-key",
                api_key,
                "--no-validate",
                "--quiet",
            ],
            cwd=values.get("TELEGRAM_OPERATOR_WORKDIR") or str(DEFAULT_WORKSPACE),
            text=True,
            capture_output=True,
            timeout=30,
        )

    def _jcode_base_url(self, values: dict[str, str]) -> str:
        provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip().lower()
        if provider == "ollama":
            host = values.get("TELEGRAM_OPERATOR_REMOTE_HOST", "").strip() or "127.0.0.1"
            port = values.get("TELEGRAM_OPERATOR_LLM_PORT", "").strip() or "11434"
            return build_host_url(host, port, "/v1")
        return values.get("TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL", "").strip().rstrip("/")

    def _ensure_jcode_local_profile(self, values: dict[str, str]) -> str:
        provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip().lower()
        model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
        base_url = self._jcode_base_url(values)
        if provider not in {"lmstudio", "ollama"} or not model or not base_url:
            return ""
        profile = f"baseclaw-{provider}"
        result = subprocess.run(
            [
                self._require_executable("jcode"),
                "provider",
                "add",
                profile,
                "--base-url",
                base_url,
                "--model",
                model,
                "--no-api-key",
                "--auth",
                "none",
                "--overwrite",
                "--quiet",
            ],
            cwd=values.get("TELEGRAM_OPERATOR_WORKDIR") or str(DEFAULT_WORKSPACE),
            text=True,
            capture_output=True,
            timeout=30,
        )
        return profile if result.returncode == 0 else ""

    def _allowed_write_dirs(self, values: dict[str, str], execution_dir: Path) -> list[Path]:
        paths = [execution_dir]
        workdir = Path(values.get("TELEGRAM_OPERATOR_WORKDIR") or DEFAULT_WORKSPACE).resolve()
        if workdir != execution_dir:
            paths.append(workdir)
        for part in (values.get("TELEGRAM_OPERATOR_ALLOWED_PATHS") or "").split(";"):
            value = part.strip()
            if value:
                paths.append(Path(value).expanduser().resolve())
        unique = []
        seen = set()
        for path in paths:
            key = str(path)
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    def _require_executable(self, name: str) -> str:
        path = shutil.which(name)
        if not path:
            raise RuntimeError(f"Could not find {name} on PATH")
        return path

    def _parse_jcode_json(self, output: str, fallback_session_id: str | None) -> tuple[str | None, str]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return fallback_session_id, output
        if not isinstance(payload, dict):
            return fallback_session_id, output
        reply = str(payload.get("text") or "").strip()
        if not reply:
            reply = "I received an empty response from jcode. Please try once more."
        return str(payload.get("session_id") or fallback_session_id or ""), reply

    def _desktop_prompt(self, text: str, values: dict[str, str]) -> str:
        provider = values.get("TELEGRAM_OPERATOR_PROVIDER", "jcode")
        backend_detail = self._desktop_backend_detail(values)
        parts = [
            "You are responding through the BaseClaw desktop chat window on the user's own machine.",
            f"The selected coding agent provider is {provider}.",
            backend_detail,
            self._desktop_access_policy(values),
            "Reply concisely and conversationally. Avoid raw JSON unless the user asks for it.",
        ]
        shared_context = self._desktop_shared_context(values, current_text=text)
        if shared_context:
            parts.append(shared_context)
        parts.extend(["User message:", text])
        return "\n\n".join(parts)

    def _desktop_backend_detail(self, values: dict[str, str]) -> str:
        provider = values.get("TELEGRAM_OPERATOR_PROVIDER", "jcode").strip().lower()
        if provider == "jcode":
            jcode_provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "auto")
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "")
            return (
                f"Backend details: jcode harness, model provider `{jcode_provider}`, model `{model}`. "
                "For LM Studio and Ollama, the selected model host and LLM port are used for model discovery."
            )
        if provider == "claude":
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip() or "Claude CLI default"
            return f"Backend details: Claude CLI provider, model `{model}`."
        if provider == "gemini":
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip() or "Gemini CLI default"
            return f"Backend details: Gemini CLI provider, model `{model}`."
        if provider == "codex":
            model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "") or "default"
            return f"Backend details: Codex CLI provider, model `{model}`."
        allowed_paths = values.get("TELEGRAM_OPERATOR_ALLOWED_PATHS", "").strip() or "none"
        return (
            f"Backend details: provider `{provider}`. "
            f"Access scope `{values.get('TELEGRAM_OPERATOR_ACCESS_SCOPE', 'workspace')}`, "
            f"action mode `{values.get('TELEGRAM_OPERATOR_ACTION_MODE', 'full')}`, "
            f"additional paths `{allowed_paths}`."
        )

    def _desktop_access_policy(self, values: dict[str, str]) -> str:
        scope = values.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", "workspace")
        action = values.get("TELEGRAM_OPERATOR_ACTION_MODE", "full")
        allowed = values.get("TELEGRAM_OPERATOR_ALLOWED_PATHS", "").strip() or "none"
        return (
            f"Access policy: scope `{scope}`, action mode `{action}`, additional allowed paths `{allowed}`. "
            "Respect this policy even when the provider CLI cannot enforce it directly."
        )

    def start_operator(self) -> None:
        self.save(show_message=False)
        values = self.current_values()
        token_error = bot_token_validation_error(values.get("TELEGRAM_BOT_TOKEN", ""))
        if token_error:
            write_env(self.env_path, values)
            self.set_status("Token invalid", token_error, False)
            messagebox.showerror("Telegram token invalid", token_error)
            return
        Path(values["TELEGRAM_OPERATOR_WORKDIR"]).mkdir(parents=True, exist_ok=True)
        provider = values.get("TELEGRAM_OPERATOR_PROVIDER", "").strip().lower()
        if provider == "jcode":
            jcode_ok, jcode_detail = jcode_preflight()
            if not jcode_ok:
                self.set_status("Setup needed", jcode_detail, False)
                messagebox.showerror("JCode setup needed", jcode_detail)
                return
        if provider == "codex":
            codex_ok, codex_detail = codex_preflight()
            if not codex_ok:
                self.set_status("Setup needed", codex_detail, False)
                messagebox.showerror("Codex setup needed", codex_detail)
                return
        if provider == "claude":
            claude_ok, claude_detail = claude_preflight()
            if not claude_ok:
                self.set_status("Setup needed", claude_detail, False)
                messagebox.showerror("Claude setup needed", claude_detail)
                return
            self.set_status("Claude ready", claude_detail, None)
        if provider == "gemini":
            gemini_ok, gemini_detail = gemini_preflight()
            if not gemini_ok:
                self.set_status("Setup needed", gemini_detail, False)
                messagebox.showerror("Gemini setup needed", gemini_detail)
                return
            self.set_status("Gemini ready", gemini_detail, None)
        if not self.ensure_speech_ready(values):
            return
        existing = root_operator_processes(self.env_path)
        if existing:
            pids = ", ".join(str(item["ProcessId"]) for item in existing)
            self.set_status("Running", f"Already running, pid(s): {pids}", True)
            self.refresh_log()
            return
        self.set_status("Starting", "Starting operator...", None)
        if sys.platform.startswith("win"):
            if not SUPERVISOR_SCRIPT.exists():
                self.set_status("Setup needed", f"Missing supervisor script: {SUPERVISOR_SCRIPT}", False)
                messagebox.showerror("Setup needed", f"Missing supervisor script:\n{SUPERVISOR_SCRIPT}")
                return
            powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
            env = dict(os.environ)
            env["BASECLAW_OPERATOR_ENV_PATH"] = str(self.env_path)
            subprocess.Popen(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SUPERVISOR_SCRIPT), "-ProfileEnv", str(self.env_path)],
                cwd=str(BASE_DIR),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            env = dict(os.environ)
            env["BASECLAW_OPERATOR_ENV_PATH"] = str(self.env_path)
            subprocess.Popen(
                [sys.executable, str(OPERATOR_SCRIPT), "--profile-env", str(self.env_path)],
                cwd=str(BASE_DIR),
                env=env,
                start_new_session=True,
            )
        self.after(1500, self.refresh_status)

    def _kill_operator_processes(self, *, show_errors: bool = True) -> None:
        self._kill_operator_processes_for_env(self.env_path, show_errors=show_errors)

    def _kill_operator_processes_for_env(self, profile_env: Path, *, show_errors: bool = True) -> None:
        def kill_items(items: list[dict]) -> None:
            for item in items:
                try:
                    psutil.Process(int(item["ProcessId"])).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    if show_errors:
                        messagebox.showerror("Stop failed", str(exc))

        processes = operator_processes(profile_env)
        supervisors = [item for item in processes if SUPERVISOR_SCRIPT.name in item.get("CommandLine", "")]
        workers = [item for item in processes if item not in supervisors]
        kill_items(supervisors)
        kill_items(workers)
        time.sleep(0.4)
        kill_items(operator_processes(profile_env))

    def _running_operator_profiles(self) -> list[str]:
        return [profile for profile in list_profiles() if root_operator_processes(profile_env_path(profile))]

    def _start_operator_process_for_profile(self, profile: str) -> None:
        profile_env = profile_env_path(profile)
        values = self._profile_values(profile)
        Path(values["TELEGRAM_OPERATOR_WORKDIR"]).mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["BASECLAW_OPERATOR_ENV_PATH"] = str(profile_env)
        if sys.platform.startswith("win"):
            powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
            subprocess.Popen(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SUPERVISOR_SCRIPT), "-ProfileEnv", str(profile_env)],
                cwd=str(BASE_DIR),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.Popen(
                [sys.executable, str(OPERATOR_SCRIPT), "--profile-env", str(profile_env)],
                cwd=str(BASE_DIR),
                env=env,
                start_new_session=True,
            )

    def _restart_operator_profiles(self, profiles: list[str]) -> tuple[int, list[str]]:
        restarted: list[str] = []
        failed: list[str] = []
        for profile in profiles:
            profile_env = profile_env_path(profile)
            try:
                self._kill_operator_processes_for_env(profile_env, show_errors=False)
                self._start_operator_process_for_profile(profile)
                restarted.append(profile)
            except Exception:
                failed.append(profile)
        return len(restarted), failed

    def stop_operator(self) -> None:
        self.set_status("Stopping", "Stopping operator...", None)
        self._kill_operator_processes(show_errors=True)
        self.after(1000, self.refresh_status)

    def restart_operator(self) -> None:
        self.save(show_message=False)
        self.stop_operator()
        self.after(1800, self.start_operator)

    def update_from_source(self) -> None:
        self.save(show_message=False)
        source = UPDATE_SOURCE_URL
        running_profiles = self._running_operator_profiles()
        if not messagebox.askyesno(
            "Update BaseClaw",
            f"Pull the newest BaseClaw update from GitHub and overlay it onto this install?\n\nSource: {UPDATE_SOURCE_URL}\n\nAfter a successful update, BaseClaw will restart {len(running_profiles)} running operator profile(s).",
        ):
            return
        if self.update_button:
            self.update_button.configure(state="disabled", text="Updating")
        self.set_status("Updating", "Pulling latest BaseClaw archive...", None)

        def worker() -> None:
            ok, detail = self._pull_archive_update(source)
            self.after(0, lambda: self._finish_update(ok, detail, running_profiles))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update(self, ok: bool, detail: str, running_profiles: list[str]) -> None:
        if self.update_button:
            self.update_button.configure(state="normal", text="Update")
        if ok:
            restart_detail = ""
            if running_profiles:
                restarted, failed = self._restart_operator_profiles(running_profiles)
                restart_detail = f" Restarted {restarted} operator profile(s)."
                if failed:
                    restart_detail += f" Failed to restart: {', '.join(failed)}."
            self.update_available = False
            self.update_detail = ""
            self._refresh_update_button()
            self.set_status("Updated", detail + restart_detail, True)
            restart = messagebox.askyesno(
                "Update complete",
                f"{detail}{restart_detail}\n\nRestart the BaseClaw UI now to load the updated interface?",
            )
            if restart:
                self.restart_ui_process()
        else:
            self.set_status("Update failed", detail, False)
            messagebox.showerror("Update failed", detail)

    def restart_ui_process(self) -> None:
        self.save(show_message=False)
        env = dict(os.environ)
        env["BASECLAW_OPERATOR_ENV_PATH"] = str(self.env_path)
        kwargs: dict[str, Any] = {
            "cwd": str(BASE_DIR),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, str(APP_DIR / "telegram_operator_ui.py")], **kwargs)
        self.destroy()

    def _pull_archive_update(self, source: str) -> tuple[bool, str]:
        try:
            backup_detail = ""
            if (PROJECT_ROOT / ".git").exists():
                backup_detail = self._backup_git_update_state()

            with tempfile.TemporaryDirectory(prefix="baseclaw-update-") as tmp:
                tmp_path = Path(tmp)
                archive_path, archive_name = self._resolve_update_archive(source, tmp_path)
                extract_dir = tmp_path / "extract"
                extract_dir.mkdir()
                with tarfile.open(archive_path, "r:gz") as archive:
                    self._safe_extract(archive, extract_dir)
                roots = [item for item in extract_dir.iterdir() if item.is_dir()]
                source_root = roots[0] if len(roots) == 1 else extract_dir
                copied = self._overlay_update(source_root)
                latest_commit = ""
                github_repo = self._github_repo_from_source(source)
                if github_repo:
                    try:
                        latest_commit = self._latest_github_commit(github_repo)
                    except Exception:
                        latest_commit = ""
                self._write_update_version_marker(source, archive_name, latest_commit)
                suffix = f" Local git changes were backed up in {backup_detail}." if backup_detail else ""
                return True, f"Updated from {archive_name}. Copied {copied} top-level item(s). Restart the UI manually to run the new code.{suffix}"
        except Exception as exc:
            return False, str(exc)

    def _check_update_source(self) -> tuple[bool, str]:
        github_repo = self._github_repo_from_source(UPDATE_SOURCE_URL)
        if not github_repo:
            return False, "Automatic update checks currently support GitHub update sources."
        latest = self._latest_github_commit(github_repo)
        current = self._current_update_commit(UPDATE_SOURCE_URL)
        if not latest:
            return False, "Could not read the latest GitHub commit."
        short_latest = latest[:7]
        if current and current == latest:
            return False, f"BaseClaw is current at {short_latest}."
        if current:
            return True, f"New BaseClaw update available: {short_latest}."
        return True, f"BaseClaw update available: {short_latest}."

    def _current_update_commit(self, source: str) -> str:
        marker = self._read_update_version_marker()
        if marker.get("source") == source and marker.get("commit"):
            return str(marker["commit"])
        if (PROJECT_ROOT / ".git").exists():
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        return ""

    def _read_update_version_marker(self) -> dict[str, str]:
        if not UPDATE_VERSION_PATH.exists():
            return {}
        try:
            data = json.loads(UPDATE_VERSION_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_update_version_marker(self, source: str, archive_name: str, commit: str) -> None:
        data = {
            "source": source,
            "archive": archive_name,
            "commit": commit,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        UPDATE_VERSION_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _backup_git_update_state(self) -> str:
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=15,
        )
        if status.returncode != 0 or not status.stdout.strip():
            return ""

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_dir = PROJECT_ROOT / ".local-update-backups" / f"runtime-update-{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "git-status.txt").write_text(status.stdout, encoding="utf-8")
        for name, args in {
            "git-diff.patch": ["git", "diff", "--binary"],
            "git-diff-staged.patch": ["git", "diff", "--cached", "--binary"],
        }.items():
            result = subprocess.run(
                args,
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                (backup_dir / name).write_text(result.stdout, encoding="utf-8")
        return str(backup_dir.relative_to(PROJECT_ROOT))

    def _resolve_update_archive(self, source: str, tmp_path: Path) -> tuple[Path, str]:
        github_repo = self._github_repo_from_source(source)
        if github_repo:
            return self._download_github_archive(github_repo, tmp_path)

        if source.startswith(("http://", "https://")):
            archive_path = tmp_path / Path(urlsplit(source).path).name
            if not archive_path.name.endswith(".tar.gz"):
                archive_path = tmp_path / "baseclaw-latest.tar.gz"
            with urlopen(source, timeout=45) as response:
                archive_path.write_bytes(response.read())
            return archive_path, archive_path.name

        if self._looks_like_ssh_source(source):
            host, remote_path = source.split(":", 1)
            remote_archive = self._latest_remote_archive(host, remote_path)
            archive_path = tmp_path / Path(remote_archive).name
            scp = subprocess.run(
                ["scp", "-q", "-o", "BatchMode=yes", f"{host}:{remote_archive}", str(archive_path)],
                text=True,
                capture_output=True,
                timeout=120,
            )
            if scp.returncode != 0:
                raise RuntimeError((scp.stderr or scp.stdout).strip() or "scp failed")
            return archive_path, archive_path.name

        path = Path(source).expanduser()
        if path.is_dir():
            matches = sorted(
                {item for pattern in ("baseclaw-*.tar.gz", "baseclaw-alpha-*.tar.gz") for item in path.glob(pattern)},
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if not matches:
                raise RuntimeError(f"No BaseClaw archives found in {path}")
            return matches[0], matches[0].name
        if path.is_file():
            return path, path.name
        raise RuntimeError("Update source was not found.")

    def _github_repo_from_source(self, source: str) -> tuple[str, str, str] | None:
        parts = urlsplit(source)
        if parts.netloc.lower() != "github.com":
            return None
        path_parts = [part for part in parts.path.strip("/").split("/") if part]
        if len(path_parts) < 2:
            return None
        owner, repo = path_parts[0], path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        branch = "main"
        if len(path_parts) >= 5 and path_parts[2] in {"tree", "blob"}:
            branch = path_parts[3]
        return owner, repo, branch

    def _download_github_archive(self, repo: tuple[str, str, str], tmp_path: Path) -> tuple[Path, str]:
        owner, name, branch = repo
        archive_path = tmp_path / f"{name}-{branch}.tar.gz"
        public_url = f"https://codeload.github.com/{owner}/{name}/tar.gz/{branch}"
        try:
            with urlopen(public_url, timeout=120) as response:
                archive_path.write_bytes(response.read())
            return archive_path, f"github:{owner}/{name}@{branch}"
        except Exception:
            pass
        gh = shutil.which("gh")
        if not gh:
            raise RuntimeError("GitHub update failed. Confirm the repository is public and this machine can reach github.com.")
        with archive_path.open("wb") as output:
            result = subprocess.run(
                [gh, "api", f"repos/{owner}/{name}/tarball/{branch}"],
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", errors="replace").strip() or "GitHub archive download failed"
            raise RuntimeError(f"GitHub update failed. Detail: {detail}")
        return archive_path, f"github:{owner}/{name}@{branch}"

    def _latest_github_commit(self, repo: tuple[str, str, str]) -> str:
        owner, name, branch = repo
        api_url = f"https://api.github.com/repos/{owner}/{name}/commits/{branch}"
        try:
            request = Request(api_url, headers={"User-Agent": "BaseClaw-Update-Checker"})
            with urlopen(request, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8"))
            sha = str(data.get("sha") or "")
            if sha:
                return sha
        except Exception:
            pass
        result = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{owner}/{name}.git", f"refs/heads/{branch}"],
            text=True,
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError("Could not check GitHub for updates.")
        return result.stdout.split()[0]

    def _looks_like_ssh_source(self, source: str) -> bool:
        return ":" in source and not source.startswith("/") and not source.startswith(("http://", "https://"))

    def _latest_remote_archive(self, host: str, remote_path: str) -> str:
        quoted = shlex.quote(remote_path)
        command = (
            f"if [ -f {quoted} ]; then printf '%s\\n' {quoted}; "
            f"else ls -t {quoted}/baseclaw-*.tar.gz {quoted}/baseclaw-alpha-*.tar.gz 2>/dev/null | head -n 1; fi"
        )
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host, command],
            text=True,
            capture_output=True,
            timeout=45,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "ssh update source lookup failed")
        archive = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if not archive:
            raise RuntimeError("No BaseClaw archive found on update source.")
        return archive

    def _safe_extract(self, archive: tarfile.TarFile, target: Path) -> None:
        target_resolved = str(target.resolve())
        for member in archive.getmembers():
            member_path = str((target / member.name).resolve())
            if os.path.commonpath([target_resolved, member_path]) != target_resolved:
                raise RuntimeError(f"Unsafe archive path: {member.name}")
        archive.extractall(target)

    def _overlay_update(self, source_root: Path) -> int:
        excluded = {
            ".env.telegram-operator",
            ".git",
            ".local-update-backups",
            ".venv-kokoro",
            ".venv-telegram-agent",
            ".baseclaw-install.conf",
            ".baseclaw-version.json",
            "agent_workspace",
            "profiles",
            "telegram_uploads",
            "telegram_codex_operator.log",
            "telegram_operator_messages.sqlite3",
            "telegram_operator_memory.jsonl",
            "telegram_operator_state.json",
        }
        copied = 0
        for item in source_root.iterdir():
            if item.name in excluded:
                continue
            destination = PROJECT_ROOT / item.name
            if item.is_dir():
                if destination.exists() and not destination.is_dir():
                    destination.unlink()
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                if destination.exists() and destination.is_dir():
                    shutil.rmtree(destination)
                shutil.copy2(item, destination)
            copied += 1
        return copied

    def on_close(self) -> None:
        self.save(show_message=False)
        self._kill_operator_processes(show_errors=False)
        self.destroy()


if __name__ == "__main__":
    OperatorUi().mainloop()

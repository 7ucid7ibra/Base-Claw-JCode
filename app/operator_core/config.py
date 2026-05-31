from __future__ import annotations

import os
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from harnesses.cli import resolve_codex_command
from speech import build_speech_urls, is_local_speech_url

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
DEFAULT_WORKSPACE = PROJECT_ROOT / "agent_workspace"
DEFAULT_MANUAL_UPDATE_REF = "main"
BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")


def _operator_env_path_from_args() -> Path:
    if "--profile-env" in sys.argv:
        index = sys.argv.index("--profile-env")
        if index + 1 < len(sys.argv):
            return Path(sys.argv[index + 1]).expanduser()
    return Path(os.environ.get("BASECLAW_OPERATOR_ENV_PATH") or PROJECT_ROOT / ".env.telegram-operator").expanduser()


OPERATOR_ENV_PATH = _operator_env_path_from_args()
load_dotenv(OPERATOR_ENV_PATH, override=True)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def normalize_bot_token(value: str) -> str:
    token = re.sub(r"\s+", "", value.strip())
    half = len(token) // 2
    if len(token) % 2 == 0 and half and token[:half] == token[half:] and BOT_TOKEN_RE.fullmatch(token[:half]):
        return token[:half]
    return token


def require_bot_token() -> str:
    token = normalize_bot_token(require_env("TELEGRAM_BOT_TOKEN"))
    if not BOT_TOKEN_RE.fullmatch(token):
        raise RuntimeError("TELEGRAM_BOT_TOKEN looks invalid. Paste exactly one token from BotFather; do not paste it twice.")
    return token


def parse_allowed_chat_ids(raw: str) -> set[int]:
    values = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        values.add(int(part))
    if not values:
        raise RuntimeError("TELEGRAM_ALLOWED_CHAT_IDS must contain at least one chat id")
    return values


def parse_positive_int(raw: str, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def parse_bool(raw: str, default: bool) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on", "enabled"}:
        return True
    if value in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def parse_url_list(raw: str) -> list[str]:
    urls: list[str] = []
    for part in raw.split(","):
        url = part.strip()
        if url:
            urls.append(url.rstrip("/"))
    return urls


def parse_csv_list(raw: str) -> list[str]:
    values = []
    seen = set()
    for part in raw.split(","):
        value = part.strip()
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def resolve_app_path(raw: str, default: Path) -> Path:
    value = (raw or "").strip()
    path = Path(value) if value else default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def parse_path_list(raw: str) -> list[Path]:
    values = []
    seen = set()
    for part in re.split(r"[;\n]", raw or ""):
        value = part.strip()
        if not value:
            continue
        path = resolve_app_path(value, BASE_DIR)
        key = str(path)
        if key not in seen:
            values.append(path)
            seen.add(key)
    return values


def build_host_url(host: str, port: str, suffix: str = "") -> str:
    host = (host or "127.0.0.1").strip().removeprefix("http://").removeprefix("https://").strip("/")
    port = (port or "").strip()
    if not port:
        return ""
    return f"http://{host}:{port}{suffix}"


def update_operator_env(values: dict[str, str]) -> None:
    lines = []
    seen = set()
    if OPERATOR_ENV_PATH.exists():
        for raw_line in OPERATOR_ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            if raw_line.strip() and not raw_line.lstrip().startswith("#") and "=" in raw_line:
                key = raw_line.split("=", 1)[0].strip().lstrip("\ufeff")
                if key in values:
                    lines.append(f"{key}={values[key]}")
                    seen.add(key)
                    continue
            lines.append(raw_line)
    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    OPERATOR_ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def codex_executable() -> str:
    return resolve_codex_command().display


@dataclass
class OperatorConfig:
    bot_token: str
    allowed_chat_ids: set[int]
    workdir: Path
    access_scope: str
    allowed_paths: list[Path]
    action_mode: str
    state_path: Path
    memory_log_path: Path
    sqlite_path: Path
    kokoro_urls: list[str]
    kokoro_voice: str
    kokoro_lang_code: str
    whisper_urls: list[str]
    whisper_model_name: str
    local_speech_fallback: bool
    startup_notice: bool
    agent_provider: str
    agent_command: str
    agent_timeout_seconds: int
    codex_model: str
    jcode_provider_id: str
    jcode_api_key: str
    jcode_provider_profile: str
    jcode_base_url: str
    shared_context_enabled: bool
    shared_context_limit: int
    safety_mode: str
    safe_mode: bool
    supervisor_id: str
    supervisor_name: str
    supervisor_role: str
    source_update_remote: str
    manual_update_ref: str
    supervisor_device_label: str
    supervisor_core_purpose: str
    supervisor_do_not: list[str]
    local_vision_enabled: bool
    local_vision_base_url: str
    local_vision_model: str
    local_vision_timeout_seconds: int
    voice_replies_enabled: bool


def startup_summary(config: OperatorConfig, *, source: str) -> str:
    provider = config.agent_provider.strip().lower() or "unknown"
    model = config.codex_model.strip() or (
        "Claude CLI default" if provider == "claude"
        else "Gemini CLI default" if provider == "gemini"
        else "Codex CLI default" if provider == "codex"
        else "JCode default"
    )
    lines = [
        f"BaseClaw {source} online.",
        f"Harness: {provider}",
        f"Model: {model}",
    ]
    if provider == "jcode":
        lines.append(f"Model provider: {config.jcode_provider_id or 'auto'}")
        lines.append(f"Model base URL: {config.jcode_base_url or 'provider default'}")
    lines.extend(
        [
            f"Workspace: {config.workdir}",
            f"Access: {config.access_scope}",
            f"Actions: {config.action_mode}",
            f"Shared context: {'on' if config.shared_context_enabled else 'off'}",
            f"Voice replies: {'on' if config.voice_replies_enabled else 'off'}",
            f"Voice: {config.kokoro_voice}",
            f"Whisper: {config.whisper_model_name}",
            f"STT/TTS hosts: {', '.join(config.kokoro_urls) if config.kokoro_urls else 'none'}",
            f"Timeout: {config.agent_timeout_seconds}s",
        ]
    )
    return "\n".join(lines)


def load_config() -> OperatorConfig:
    remote_speech_url = os.environ.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", "").strip()
    if not remote_speech_url:
        legacy_url = os.environ.get("TELEGRAM_OPERATOR_KOKORO_URL", "").strip()
        if legacy_url and not is_local_speech_url(legacy_url):
            remote_speech_url = legacy_url
    local_speech_fallback = parse_bool(os.environ.get("TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK", ""), True)
    speech_urls = build_speech_urls(remote_speech_url, local_speech_fallback)
    safety_mode = os.environ.get("TELEGRAM_OPERATOR_SAFETY_MODE", "").strip().lower()
    if safety_mode not in {"restricted", "safe", "code", "full"}:
        safety_mode = "restricted" if parse_bool(os.environ.get("TELEGRAM_OPERATOR_SAFE_MODE", ""), False) else "safe"
    access_scope = os.environ.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", "").strip().lower()
    if access_scope not in {"workspace", "code", "full"}:
        access_scope = "code" if safety_mode == "code" else "full" if safety_mode == "full" else "workspace"
    action_mode = os.environ.get("TELEGRAM_OPERATOR_ACTION_MODE", "").strip().lower()
    if action_mode not in {"read", "approve", "full"}:
        action_mode = "approve" if safety_mode == "restricted" else "full"
    supervisor_id = os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_ID", "").strip()
    if not supervisor_id:
        supervisor_id = "local-supervisor"
    supervisor_name = os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_NAME", "").strip() or supervisor_id
    supervisor_role = os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_ROLE", "").strip() or "unspecified"
    supervisor_device_label = (
        os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_DEVICE_LABEL", "").strip()
        or os.environ.get("COMPUTERNAME", "").strip()
        or socket.gethostname()
    )
    supervisor_core_purpose = (
        os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_CORE_PURPOSE", "").strip()
        or "Local Telegram-controlled coding supervisor."
    )
    supervisor_do_not = parse_csv_list(
        os.environ.get(
            "TELEGRAM_OPERATOR_SUPERVISOR_DO_NOT",
            "Do not infer identity from stale chat memory,Do not review your own source update as independent review",
        )
    )
    jcode_provider_id = os.environ.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip().lower()
    jcode_base_url = os.environ.get("TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1").strip()
    if jcode_provider_id in {"lmstudio", "ollama"}:
        remote_host = os.environ.get("TELEGRAM_OPERATOR_REMOTE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        llm_port = "11434" if jcode_provider_id == "ollama" else "1234"
        jcode_base_url = build_host_url(remote_host, llm_port, "/v1")
    return OperatorConfig(
        bot_token=require_bot_token(),
        allowed_chat_ids=parse_allowed_chat_ids(require_env("TELEGRAM_ALLOWED_CHAT_IDS")),
        workdir=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_WORKDIR", ""), DEFAULT_WORKSPACE),
        access_scope=access_scope,
        allowed_paths=parse_path_list(os.environ.get("TELEGRAM_OPERATOR_ALLOWED_PATHS", "")),
        action_mode=action_mode,
        state_path=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_STATE_PATH", ""), BASE_DIR / "telegram_operator_state.json"),
        memory_log_path=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_MEMORY_LOG", ""), BASE_DIR / "telegram_operator_memory.jsonl"),
        sqlite_path=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_SQLITE_PATH", ""), BASE_DIR / "telegram_operator_messages.sqlite3"),
        kokoro_urls=speech_urls,
        kokoro_voice=os.environ.get("TELEGRAM_OPERATOR_KOKORO_VOICE", "af_alloy"),
        kokoro_lang_code=os.environ.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE", "a"),
        whisper_urls=speech_urls,
        whisper_model_name=os.environ.get("TELEGRAM_OPERATOR_WHISPER_MODEL", "base"),
        local_speech_fallback=local_speech_fallback,
        startup_notice=(
            parse_bool(os.environ.get("TELEGRAM_OPERATOR_STARTUP_NOTICE", ""), True)
            and not parse_bool(os.environ.get("BASECLAW_SUPPRESS_STARTUP_NOTICE_ONCE", ""), False)
        ),
        agent_provider=os.environ.get("TELEGRAM_OPERATOR_PROVIDER", "codex"),
        agent_command=os.environ.get("TELEGRAM_OPERATOR_AGENT_COMMAND", ""),
        agent_timeout_seconds=parse_positive_int(os.environ.get("TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS", ""), 900),
        codex_model=os.environ.get("TELEGRAM_OPERATOR_CODEX_MODEL", ""),
        jcode_provider_id=jcode_provider_id,
        jcode_api_key=os.environ.get("TELEGRAM_OPERATOR_JCODE_API_KEY", ""),
        jcode_provider_profile=os.environ.get("TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE", ""),
        jcode_base_url=jcode_base_url,
        shared_context_enabled=parse_bool(os.environ.get("TELEGRAM_OPERATOR_SHARED_CONTEXT_ENABLED", ""), True),
        shared_context_limit=parse_positive_int(os.environ.get("TELEGRAM_OPERATOR_SHARED_CONTEXT_LIMIT", ""), 12),
        safety_mode=safety_mode,
        safe_mode=action_mode != "full" or access_scope != "full",
        supervisor_id=supervisor_id,
        supervisor_name=supervisor_name,
        supervisor_role=supervisor_role,
        source_update_remote=os.environ.get("TELEGRAM_OPERATOR_SOURCE_UPDATE_REMOTE", "").strip(),
        manual_update_ref=os.environ.get("TELEGRAM_OPERATOR_MANUAL_UPDATE_REF", DEFAULT_MANUAL_UPDATE_REF).strip()
        or DEFAULT_MANUAL_UPDATE_REF,
        supervisor_device_label=supervisor_device_label,
        supervisor_core_purpose=supervisor_core_purpose,
        supervisor_do_not=supervisor_do_not,
        local_vision_enabled=parse_bool(os.environ.get("TELEGRAM_OPERATOR_LOCAL_VISION_ENABLED", ""), False),
        local_vision_base_url=os.environ.get("TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1").strip(),
        local_vision_model=os.environ.get("TELEGRAM_OPERATOR_LM_STUDIO_VISION_MODEL", "").strip(),
        local_vision_timeout_seconds=parse_positive_int(
            os.environ.get("TELEGRAM_OPERATOR_LOCAL_VISION_TIMEOUT_SECONDS", ""),
            180,
        ),
        voice_replies_enabled=parse_bool(os.environ.get("TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED", ""), True),
    )

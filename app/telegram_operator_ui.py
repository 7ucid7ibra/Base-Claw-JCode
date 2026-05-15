from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import customtkinter as ctk
import psutil
from codex_cli import resolve_codex_command

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
DEFAULT_WORKSPACE = PROJECT_ROOT / "agent_workspace"
ENV_PATH = PROJECT_ROOT / ".env.telegram-operator"
OPERATOR_SCRIPT = APP_DIR / "telegram_codex_operator.py"
SUPERVISOR_SCRIPT = PROJECT_ROOT / "scripts" / "run_telegram_codex_operator.ps1"
LOG_PATH = PROJECT_ROOT / "telegram_codex_operator.log"
SECRET_PATTERNS = [
    re.compile(r"(bot)([0-9]{6,}:[A-Za-z0-9_-]{20,})"),
]

ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_OPERATOR_WORKDIR",
    "TELEGRAM_OPERATOR_STATE_PATH",
    "TELEGRAM_OPERATOR_MEMORY_LOG",
    "TELEGRAM_OPERATOR_SQLITE_PATH",
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
    "TELEGRAM_OPERATOR_AGENT_COMMAND",
    "TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS",
    "TELEGRAM_OPERATOR_CODEX_MODEL",
    "TELEGRAM_OPERATOR_SAFETY_MODE",
    "TELEGRAM_OPERATOR_SAFE_MODE",
    "TELEGRAM_OPERATOR_BOARD_POLL_ENABLED",
    "TELEGRAM_OPERATOR_BOARD_POLL_INTERVAL_SECONDS",
    "TELEGRAM_OPERATOR_BOARD_REMOTE",
    "TELEGRAM_OPERATOR_BOARD_PATH",
    "TELEGRAM_OPERATOR_BOARD_STATE_PATH",
    "TELEGRAM_OPERATOR_BOARD_AGENT_ALIASES",
]

DEFAULTS = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ALLOWED_CHAT_IDS": "",
    "TELEGRAM_OPERATOR_WORKDIR": str(DEFAULT_WORKSPACE),
    "TELEGRAM_OPERATOR_STATE_PATH": str(BASE_DIR / "telegram_operator_state.json"),
    "TELEGRAM_OPERATOR_MEMORY_LOG": str(BASE_DIR / "telegram_operator_memory.jsonl"),
    "TELEGRAM_OPERATOR_SQLITE_PATH": str(BASE_DIR / "telegram_operator_messages.sqlite3"),
    "TELEGRAM_OPERATOR_REMOTE_SPEECH_URL": "",
    "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK": "true",
    "TELEGRAM_OPERATOR_STARTUP_NOTICE": "true",
    "TELEGRAM_OPERATOR_KOKORO_URL": "http://127.0.0.1:8766",
    "TELEGRAM_OPERATOR_KOKORO_URLS": "",
    "TELEGRAM_OPERATOR_KOKORO_VOICE": "af_alloy",
    "TELEGRAM_OPERATOR_KOKORO_LANG_CODE": "a",
    "TELEGRAM_OPERATOR_WHISPER_URLS": "",
    "TELEGRAM_OPERATOR_WHISPER_MODEL": "base",
    "TELEGRAM_OPERATOR_PROVIDER": "codex",
    "TELEGRAM_OPERATOR_AGENT_COMMAND": "",
    "TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS": "900",
    "TELEGRAM_OPERATOR_CODEX_MODEL": "",
    "TELEGRAM_OPERATOR_SAFETY_MODE": "safe",
    "TELEGRAM_OPERATOR_SAFE_MODE": "false",
    "TELEGRAM_OPERATOR_BOARD_POLL_ENABLED": "true",
    "TELEGRAM_OPERATOR_BOARD_POLL_INTERVAL_SECONDS": "180",
    "TELEGRAM_OPERATOR_BOARD_REMOTE": "",
    "TELEGRAM_OPERATOR_BOARD_PATH": "/home/ai/agent_board/entries.ndjson",
    "TELEGRAM_OPERATOR_BOARD_STATE_PATH": str(BASE_DIR / "telegram_operator_board_state.json"),
    "TELEGRAM_OPERATOR_BOARD_AGENT_ALIASES": "baseclaw,maat-supervisor,developer-agent",
}

CODEX_MODELS = ["default", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
SAFETY_MODE_LABELS = {
    "restricted": "Restricted: approve every task",
    "safe": "Safe: workspace access",
    "code": "Code access: app repo + git commits",
    "full": "Full access",
}
SAFETY_LABELS = [SAFETY_MODE_LABELS[key] for key in ("restricted", "safe", "code", "full")]
SAFETY_LABEL_TO_MODE = {label: key for key, label in SAFETY_MODE_LABELS.items()}
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

ctk.set_appearance_mode("dark")
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


def operator_processes() -> list[dict]:
    processes = []
    for process in psutil.process_iter(["pid", "ppid", "name", "exe", "cmdline"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        # Cross-platform: macOS/Linux Python process names are often just
        # "python" or "Python", so detect the operator/supervisor by script argument.
        if OPERATOR_SCRIPT.name not in cmdline and SUPERVISOR_SCRIPT.name not in cmdline:
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


def root_operator_processes() -> list[dict]:
    processes = operator_processes()
    operator_ids = {int(item["ProcessId"]) for item in processes}
    roots = [item for item in processes if int(item.get("ParentProcessId") or 0) not in operator_ids]
    return roots or processes


class OperatorUi(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BaseClaw")
        self.geometry("690x760+24+24")
        self.minsize(560, 540)
        self.values = read_env(ENV_PATH)
        self.vars: dict[str, tk.StringVar] = {}
        self.voice_combo: ctk.CTkComboBox | None = None
        self.whisper_combo: ctk.CTkComboBox | None = None
        self.status_pill: ctk.CTkLabel | None = None
        self.status_detail: ctk.CTkLabel | None = None
        self.log_box: ctk.CTkTextbox | None = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self.refresh_voices()
        self.refresh_status()
        self.after(5000, self.auto_refresh_status)

    def _build(self) -> None:
        self.configure(fg_color="#0F1317")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(22, 10))
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(header, text="BaseClaw", font=ctk.CTkFont(size=24, weight="bold"))
        title.grid(row=0, column=0, sticky="w")
        subtitle = ctk.CTkLabel(
            header,
            text="Minimal local agent bridge for Telegram, speech, Codex, and safe controls.",
            text_color="#A8B3BD",
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
            fg_color="#26313A",
            text_color="#D8E0E7",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.status_pill.grid(row=0, column=1, rowspan=2, sticky="e")

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 16))
        body.grid_columnconfigure(0, weight=1)

        connection = self._card(body, "Connection", "Telegram credentials and allowed chat.", 0, 0)
        self._entry(connection, "Bot token", "TELEGRAM_BOT_TOKEN", row=0, secret=True)
        self._entry(connection, "Chat id(s)", "TELEGRAM_ALLOWED_CHAT_IDS", row=2)
        self._entry(connection, "Remote speech host", "TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", row=4)
        self._switch(connection, "Use local speech fallback", "TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK", row=6)

        agent = self._card(body, "Codex", "Home folder, model, and safety controls.", 1, 0)
        self.vars["TELEGRAM_OPERATOR_PROVIDER"] = tk.StringVar(value="codex")
        self.vars["TELEGRAM_OPERATOR_AGENT_COMMAND"] = tk.StringVar(value="")
        self._path_entry(agent, "Workspace home", "TELEGRAM_OPERATOR_WORKDIR", row=0)
        agent_options = ctk.CTkFrame(agent, fg_color="transparent")
        agent_options.grid(row=2, column=0, sticky="ew")
        agent_options.grid_columnconfigure((0, 1), weight=1, uniform="agent_options")
        codex_model = self.values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip() or "default"
        self.vars["TELEGRAM_OPERATOR_CODEX_MODEL"] = tk.StringVar(value=codex_model)
        self._label(agent_options, "Codex model", row=0, column=0, padx=(0, 6))
        ctk.CTkComboBox(
            agent_options,
            variable=self.vars["TELEGRAM_OPERATOR_CODEX_MODEL"],
            values=CODEX_MODELS if codex_model in CODEX_MODELS else [codex_model, *CODEX_MODELS],
            height=38,
            corner_radius=10,
            border_width=1,
        ).grid(row=1, column=0, sticky="ew", pady=(3, 12), padx=(0, 6))
        self._entry(
            agent_options,
            "Agent timeout seconds",
            "TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS",
            row=0,
            column=1,
            padx=(6, 0),
        )
        safety_value = self.values.get("TELEGRAM_OPERATOR_SAFETY_MODE", "").strip()
        if not safety_value:
            legacy = self.values.get("TELEGRAM_OPERATOR_SAFE_MODE", DEFAULTS["TELEGRAM_OPERATOR_SAFE_MODE"])
            safety_value = "restricted" if legacy.strip().lower() in {"1", "true", "yes", "on"} else "safe"
        self.vars["TELEGRAM_OPERATOR_SAFETY_MODE"] = tk.StringVar(value=safety_display(safety_value))
        self._label(agent, "Safety mode", row=3)
        ctk.CTkComboBox(
            agent,
            variable=self.vars["TELEGRAM_OPERATOR_SAFETY_MODE"],
            values=SAFETY_LABELS,
            height=38,
            corner_radius=10,
            border_width=1,
        ).grid(row=4, column=0, sticky="ew", pady=(3, 12))

        voice = self._card(body, "Voice", "Kokoro reply voice and speech language.", 2, 0)
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

        runtime = self._card(body, "Runtime", "Start, stop, and keep an eye on the bridge.", 3, 0)
        buttons = ctk.CTkFrame(runtime, fg_color="transparent")
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        buttons.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(buttons, text="Start", height=40, corner_radius=12, command=self.start_operator).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ctk.CTkButton(
            buttons,
            text="Stop",
            height=40,
            corner_radius=12,
            fg_color="#8E3B46",
            hover_color="#A84855",
            command=self.stop_operator,
        ).grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(
            buttons,
            text="Restart",
            height=40,
            corner_radius=12,
            fg_color="#3C6478",
            hover_color="#47788F",
            command=self.restart_operator,
        ).grid(row=0, column=2, sticky="ew", padx=(6, 0))
        ctk.CTkButton(
            runtime,
            text="Save Settings",
            height=40,
            corner_radius=12,
            fg_color="#4B6B55",
            hover_color="#5B8066",
            command=self.save,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.status_detail = ctk.CTkLabel(runtime, text="Status: checking...", text_color="#A8B3BD", anchor="w")
        self.status_detail.grid(row=2, column=0, sticky="ew")

        logs = self._card(body, "Recent Log", "Latest bridge activity.", 4, 0)
        logs.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(
            logs,
            height=180,
            corner_radius=12,
            border_width=1,
            fg_color="#0C1014",
            border_color="#27313A",
            text_color="#C8D0D7",
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
        )
        self.log_box.grid(row=0, column=0, sticky="nsew", pady=(4, 0))

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
        card = ctk.CTkFrame(parent, fg_color="#171D22", border_color="#2A3640", border_width=1, corner_radius=16)
        card.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=padx, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=17, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(15, 0)
        )
        ctk.CTkLabel(card, text=subtitle, text_color="#8F9AA3", font=ctk.CTkFont(size=12), wraplength=560).grid(
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
    ) -> None:
        ctk.CTkLabel(parent, text=text, text_color="#B8C2CA", font=ctk.CTkFont(size=12), anchor="w").grid(
            row=row, column=column, sticky="ew", padx=padx
        )

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
        self._label(parent, label, row=row, column=column, padx=padx)
        entry = ctk.CTkEntry(
            parent,
            textvariable=self.vars[key],
            show="*" if secret else "",
            height=38,
            corner_radius=10,
            border_width=1,
        )
        entry.grid(row=row + 1, column=column, sticky="ew", pady=(3, 12), padx=padx)
        return entry

    def _path_entry(self, parent: ctk.CTkFrame, label: str, key: str, row: int) -> None:
        self.vars[key] = tk.StringVar(value=self.values.get(key, DEFAULTS[key]))
        self._label(parent, label, row=row)
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.grid(row=row + 1, column=0, sticky="ew", pady=(3, 12))
        line.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(line, textvariable=self.vars[key], height=38, corner_radius=10, border_width=1).grid(
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
            text=label,
            variable=self.vars[key],
            onvalue="true",
            offvalue="false",
            button_color="#D8E0E7",
            button_hover_color="#FFFFFF",
            progress_color="#2D6A4F",
            font=ctk.CTkFont(size=13),
        )
        switch.grid(row=row, column=0, sticky="w", pady=(2, 14))

    def choose_workspace(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.vars["TELEGRAM_OPERATOR_WORKDIR"].get() or str(BASE_DIR))
        if directory:
            self.vars["TELEGRAM_OPERATOR_WORKDIR"].set(directory)

    def on_provider_selected(self, _value: str | None = None) -> None:
        self.vars["TELEGRAM_OPERATOR_PROVIDER"].set("codex")
        self.vars["TELEGRAM_OPERATOR_AGENT_COMMAND"].set("")

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
        values = read_env(ENV_PATH)
        for key, var in self.vars.items():
            values[key] = var.get().strip()
        values["TELEGRAM_OPERATOR_KOKORO_LANG_CODE"] = language_code(
            values.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE", "")
        )
        if values.get("TELEGRAM_OPERATOR_CODEX_MODEL") == "default":
            values["TELEGRAM_OPERATOR_CODEX_MODEL"] = ""
        values["TELEGRAM_OPERATOR_SAFETY_MODE"] = safety_mode(values.get("TELEGRAM_OPERATOR_SAFETY_MODE", ""))
        values["TELEGRAM_OPERATOR_SAFE_MODE"] = "true" if values["TELEGRAM_OPERATOR_SAFETY_MODE"] == "restricted" else "false"
        values["TELEGRAM_OPERATOR_STARTUP_NOTICE"] = "true"
        workdir = values.get("TELEGRAM_OPERATOR_WORKDIR") or str(DEFAULT_WORKSPACE)
        values["TELEGRAM_OPERATOR_WORKDIR"] = workdir
        values.setdefault("TELEGRAM_OPERATOR_STATE_PATH", str(BASE_DIR / "telegram_operator_state.json"))
        values.setdefault("TELEGRAM_OPERATOR_MEMORY_LOG", str(BASE_DIR / "telegram_operator_memory.jsonl"))
        values.setdefault("TELEGRAM_OPERATOR_SQLITE_PATH", str(BASE_DIR / "telegram_operator_messages.sqlite3"))
        remote_speech_url = normalize_speech_url(values.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", ""))
        values["TELEGRAM_OPERATOR_REMOTE_SPEECH_URL"] = remote_speech_url
        values["TELEGRAM_OPERATOR_KOKORO_URL"] = remote_speech_url or DEFAULTS["TELEGRAM_OPERATOR_KOKORO_URL"]
        values["TELEGRAM_OPERATOR_KOKORO_URLS"] = ""
        values["TELEGRAM_OPERATOR_WHISPER_URLS"] = ""
        values["TELEGRAM_OPERATOR_PROVIDER"] = "codex"
        values["TELEGRAM_OPERATOR_AGENT_COMMAND"] = ""
        values["TELEGRAM_OPERATOR_SQLITE_PATH"] = str(BASE_DIR / "telegram_operator_messages.sqlite3")
        return values

    def ensure_speech_ready(self, values: dict[str, str]) -> bool:
        remote = values.get("TELEGRAM_OPERATOR_REMOTE_SPEECH_URL", "").strip()
        local_fallback = parse_bool(values.get("TELEGRAM_OPERATOR_LOCAL_SPEECH_FALLBACK", ""), True)
        if remote:
            if speech_health(remote):
                return True
            if not local_fallback:
                self.set_status("Speech offline", f"Remote speech host is unreachable: {remote}", False)
                messagebox.showerror("Speech host unreachable", f"Remote speech host is unreachable:\n{remote}")
                return False
        if local_fallback:
            for url in local_speech_urls():
                if speech_health(url):
                    self.set_status("Speech ready", f"Using local speech host at {url}.", None)
                    self.refresh_voices()
                    return True
            ok, detail = start_local_speech_host()
            if not ok:
                self.set_status("Speech offline", detail, False)
                messagebox.showerror("Speech host needed", detail)
                return False
            self.set_status("Speech ready", detail, None)
            self.refresh_voices()
            return True
        self.set_status("Speech needed", "No remote speech host is configured and local fallback is disabled.", False)
        messagebox.showerror(
            "Speech host needed",
            "No remote speech host is configured and local fallback is disabled.",
        )
        return False

    def set_status(self, label: str, detail: str, running: bool | None = None) -> None:
        if self.status_pill:
            color = "#2D6A4F" if running else "#6A3A3A" if running is False else "#33424D"
            self.status_pill.configure(text=label, fg_color=color)
        if self.status_detail:
            self.status_detail.configure(text=detail)

    def save(self, show_message: bool = True) -> None:
        write_env(ENV_PATH, self.current_values())
        self.set_status("Saved", f"Settings saved to {ENV_PATH}", None)
        if show_message:
            messagebox.showinfo("Saved", f"Saved settings to {ENV_PATH}")

    def refresh_voices(self) -> None:
        urls = []
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
            processes = root_operator_processes()
            if processes:
                pids = ", ".join(str(item["ProcessId"]) for item in processes)
                self.set_status("Running", f"Operator running, pid(s): {pids}", True)
            else:
                self.set_status("Stopped", "Operator is stopped.", False)
        except Exception as exc:
            self.set_status("Error", f"Status error: {exc}", False)
        self.refresh_log()

    def auto_refresh_status(self) -> None:
        self.refresh_status()
        self.after(5000, self.auto_refresh_status)

    def refresh_log(self) -> None:
        if not self.log_box:
            return
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        if LOG_PATH.exists():
            lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            self.log_box.insert("end", redact_secrets("\n".join(lines)))
        self.log_box.configure(state="disabled")

    def start_operator(self) -> None:
        self.save(show_message=False)
        values = self.current_values()
        Path(values["TELEGRAM_OPERATOR_WORKDIR"]).mkdir(parents=True, exist_ok=True)
        codex_ok, codex_detail = codex_preflight()
        if not codex_ok:
            self.set_status("Setup needed", codex_detail, False)
            messagebox.showerror("Codex setup needed", codex_detail)
            return
        if not self.ensure_speech_ready(values):
            return
        existing = root_operator_processes()
        if existing:
            pids = ", ".join(str(item["ProcessId"]) for item in existing)
            self.set_status("Running", f"Already running, pid(s): {pids}", True)
            self.refresh_log()
            return
        self.set_status("Starting", "Starting operator...", None)
        if not SUPERVISOR_SCRIPT.exists():
            self.set_status("Setup needed", f"Missing supervisor script: {SUPERVISOR_SCRIPT}", False)
            messagebox.showerror("Setup needed", f"Missing supervisor script:\n{SUPERVISOR_SCRIPT}")
            return
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
        subprocess.Popen(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SUPERVISOR_SCRIPT)],
            cwd=str(BASE_DIR),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.after(1500, self.refresh_status)

    def _kill_operator_processes(self, *, show_errors: bool = True) -> None:
        def kill_items(items: list[dict]) -> None:
            for item in items:
                try:
                    psutil.Process(int(item["ProcessId"])).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    if show_errors:
                        messagebox.showerror("Stop failed", str(exc))

        processes = operator_processes()
        supervisors = [item for item in processes if SUPERVISOR_SCRIPT.name in item.get("CommandLine", "")]
        workers = [item for item in processes if item not in supervisors]
        kill_items(supervisors)
        kill_items(workers)
        time.sleep(0.4)
        kill_items(operator_processes())

    def stop_operator(self) -> None:
        self.set_status("Stopping", "Stopping operator...", None)
        self._kill_operator_processes(show_errors=True)
        self.after(1000, self.refresh_status)

    def restart_operator(self) -> None:
        self.save(show_message=False)
        self.stop_operator()
        self.after(1800, self.start_operator)

    def on_close(self) -> None:
        self.save(show_message=False)
        self._kill_operator_processes(show_errors=False)
        self.destroy()


if __name__ == "__main__":
    OperatorUi().mainloop()

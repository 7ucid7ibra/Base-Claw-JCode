from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from codex_cli import resolve_codex_command
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
DEFAULT_WORKSPACE = PROJECT_ROOT / "agent_workspace"
OPERATOR_ENV_PATH = PROJECT_ROOT / ".env.telegram-operator"
load_dotenv(OPERATOR_ENV_PATH, override=True)
LOG_PATH = BASE_DIR / "telegram_codex_operator.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
LOGGER = logging.getLogger("telegram_codex_operator")
SPEECH_CONNECT_TIMEOUT_SECONDS = 4
SPEECH_READ_TIMEOUT_SECONDS = 300
SPEECH_REQUEST_TIMEOUT = (SPEECH_CONNECT_TIMEOUT_SECONDS, SPEECH_READ_TIMEOUT_SECONDS)
CODEX_FINAL_MESSAGE_GRACE_SECONDS = 8.0
STATUS_UPDATE_INITIAL_DELAY_SECONDS = 120
STATUS_UPDATE_INTERVAL_SECONDS = 120
STATUS_CHANGE_MIN_INTERVAL_SECONDS = 12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


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


def is_local_speech_url(url: str) -> bool:
    normalized = normalize_speech_url(url).lower()
    return normalized in {
        "http://127.0.0.1:8766",
        "http://localhost:8766",
        "http://0.0.0.0:8766",
    }


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


def build_speech_urls(remote_url: str, local_fallback: bool = True) -> list[str]:
    urls = []
    remote_url = normalize_speech_url(remote_url)
    local_url = "http://127.0.0.1:8766"
    if remote_url and not is_local_speech_url(remote_url):
        urls.append(remote_url)
    if local_fallback:
        urls.append(local_url)
    return urls


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def agent_subprocess_kwargs() -> dict:
    kwargs = hidden_subprocess_kwargs()
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return kwargs


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill")
        if taskkill:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                **hidden_subprocess_kwargs(),
            )
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            LOGGER.warning("Failed to kill process pid=%s", process.pid, exc_info=True)


def codex_executable() -> str:
    return resolve_codex_command().display


def friendly_codex_error(detail: str, exit_code: int) -> str:
    cleaned = (detail or "").strip()
    lower = cleaned.lower()
    if any(token in lower for token in ("not authenticated", "login", "log in", "unauthorized", "api key", "authentication")):
        return (
            "Codex CLI appears to be unauthenticated. Run `codex login` in a local terminal, "
            "confirm Codex works, then restart the Telegram operator."
        )
    if "not recognized" in lower or "not found" in lower:
        return "Codex CLI was not found on PATH. Install Codex and restart the Telegram operator."
    return cleaned or f"Codex exited with code {exit_code}"


def resolve_app_path(raw: str, default: Path) -> Path:
    value = (raw or "").strip()
    path = Path(value) if value else default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


@dataclass
class OperatorConfig:
    bot_token: str
    allowed_chat_ids: set[int]
    workdir: Path
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
    safety_mode: str
    safe_mode: bool


@dataclass
class PendingApproval:
    chat_id: int
    telegram_user: str
    text: str
    transcript: Optional[str]
    proposal: str
    created_at: str


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = {"sessions": {}}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {"sessions": loaded.get("sessions", {})}
        self._data.setdefault("sessions", {})

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get_session_id(self, chat_id: int) -> Optional[str]:
        return self._data.get("sessions", {}).get(str(chat_id))

    def set_session_id(self, chat_id: int, session_id: str) -> None:
        self._data.setdefault("sessions", {})[str(chat_id)] = session_id
        self.save()

    def clear_session_id(self, chat_id: int) -> None:
        self._data.setdefault("sessions", {}).pop(str(chat_id), None)
        self.save()


class MemoryLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


class SQLiteMessageStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
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
                "CREATE INDEX IF NOT EXISTS idx_telegram_messages_chat_time ON telegram_messages(chat_id, recorded_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_telegram_messages_event ON telegram_messages(event_type)"
            )

    def append(
        self,
        *,
        direction: str,
        event_type: str,
        chat_id: Optional[int] = None,
        telegram_message_id: Optional[int] = None,
        telegram_user_id: Optional[int] = None,
        telegram_username: Optional[str] = None,
        telegram_full_name: Optional[str] = None,
        message_type: Optional[str] = None,
        text: Optional[str] = None,
        transcript: Optional[str] = None,
        session_id: Optional[str] = None,
        safe_mode: Optional[bool] = None,
        approval_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        payload = json.dumps(metadata or {}, ensure_ascii=True, default=str)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO telegram_messages (
                        recorded_at,
                        direction,
                        event_type,
                        chat_id,
                        telegram_message_id,
                        telegram_user_id,
                        telegram_username,
                        telegram_full_name,
                        message_type,
                        text,
                        transcript,
                        session_id,
                        safe_mode,
                        approval_id,
                        metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        utc_now(),
                        direction,
                        event_type,
                        chat_id,
                        telegram_message_id,
                        telegram_user_id,
                        telegram_username,
                        telegram_full_name,
                        message_type,
                        text,
                        transcript,
                        session_id,
                        None if safe_mode is None else int(safe_mode),
                        approval_id,
                        payload,
                    ),
                )
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to record Telegram message event_type=%s direction=%s", event_type, direction)


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


class CodexBridge:
    def __init__(self, workdir: Path, model: str, timeout_seconds: int, safety_mode: str = "safe"):
        self.workdir = workdir
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.safety_mode = safety_mode

    @property
    def execution_dir(self) -> Path:
        if self.safety_mode == "code":
            return PROJECT_ROOT
        return self.workdir

    def _base_command(self, proposal_mode: bool = False) -> list[str]:
        cmd = [
            *resolve_codex_command().args,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(self.execution_dir),
        ]
        if proposal_mode:
            cmd.extend(["--sandbox", "read-only", "--ephemeral"])
        elif self.safety_mode == "full":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.safety_mode in {"restricted", "safe", "code"}:
            cmd.extend(["--sandbox", "workspace-write"])
        else:
            cmd.extend(["--sandbox", "workspace-write"])
        if self.model:
            cmd.extend(["-m", self.model])
        return cmd

    @staticmethod
    def _summarize_shell_command(command: str) -> str:
        command_lower = command.lower()
        if "ssh " in command_lower:
            return "running an SSH command"
        if command_lower.strip().startswith("git ") or " git " in command_lower:
            return "running a git command"
        if "python" in command_lower:
            return "running a Python command"
        if "powershell" in command_lower or command_lower.strip().startswith("$"):
            return "running a PowerShell command"
        if "npm " in command_lower or "node " in command_lower:
            return "running a Node command"
        if "curl" in command_lower or "invoke-restmethod" in command_lower:
            return "calling a local or remote service"
        return "running a shell command"

    @staticmethod
    def _short_status_text(text: str, limit: int = 140) -> str:
        line = " ".join((text or "").strip().split())
        if not line:
            return ""
        for marker in (". ", "! ", "? "):
            if marker in line:
                line = line.split(marker, 1)[0] + marker.strip()
                break
        if len(line) > limit:
            line = line[: limit - 3].rstrip() + "..."
        return line

    def _status_from_codex_event(self, raw_line: str) -> Optional[str]:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return None

        event_type = event.get("type", "")
        if event_type == "thread.started":
            return "starting a Codex session"

        if event_type == "event_msg":
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            if payload_type == "task_started":
                return "starting the agent turn"
            if payload_type == "agent_message":
                phase = payload.get("phase")
                if phase == "commentary":
                    status = self._short_status_text(str(payload.get("message") or ""))
                    return status or None
                if phase == "final_answer":
                    return "preparing the final reply"
            if payload_type == "task_complete":
                return "finishing up"

        if event_type == "response_item":
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            if payload_type == "function_call":
                if payload.get("name") == "shell_command":
                    try:
                        arguments = json.loads(str(payload.get("arguments") or "{}"))
                    except json.JSONDecodeError:
                        arguments = {}
                    return self._summarize_shell_command(str(arguments.get("command") or ""))
                return f"using tool {payload.get('name')}"
            if payload_type == "function_call_output":
                return "checking command output"
            if payload_type == "reasoning":
                return "thinking through the next step"

        if event_type == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "command_execution":
                return "checking command output"
            if item_type == "agent_message":
                status = self._short_status_text(str(item.get("text") or item.get("message") or ""))
                return status or None

        return None

    @staticmethod
    def _event_indicates_more_work(raw_line: str) -> bool:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return False
        if event.get("type") != "response_item":
            return False
        payload = event.get("payload", {})
        return payload.get("type") in {"function_call", "function_call_output"}

    def _record_codex_event(
        self,
        raw_line: str,
        *,
        stderr_chunks: list[str],
    ) -> tuple[str, str, bool, bool]:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return "", "", False, False

        event_type = event.get("type", "")
        session_id = ""
        agent_message = ""
        completed = False
        final_answer = False

        if event_type == "thread.started":
            session_id = str(event.get("thread_id", "") or "")

        if event_type == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "agent_message":
                agent_message = str(item.get("text") or item.get("message") or "")
            if item_type == "command_execution" and item.get("exit_code") not in (None, 0):
                stderr_chunks.append(str(item.get("aggregated_output", "")))

        if event_type == "event_msg":
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            if payload_type == "agent_message" and payload.get("phase") == "final_answer":
                agent_message = str(payload.get("message") or "")
                final_answer = True
            if payload_type == "task_complete":
                agent_message = str(payload.get("last_agent_message") or "")
                completed = True
                final_answer = True

        if event_type in {"task_complete", "turn.completed"}:
            agent_message = str(event.get("last_agent_message") or event.get("message") or "")
            completed = True
            final_answer = True

        return session_id, agent_message, completed, final_answer

    @staticmethod
    def _stream_reader(
        stream: Any,
        stream_name: str,
        events: "queue.Queue[tuple[str, Optional[str]]]",
    ) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((stream_name, line))
        finally:
            events.put((stream_name, None))

    def _run(
        self,
        cmd: list[str],
        prompt: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        process: Optional[subprocess.Popen[str]] = None
        stderr_chunks: list[str] = []
        last_agent_message = ""
        last_final_message = ""
        session_id = ""
        completion_seen = False
        final_message_seen_at: Optional[float] = None
        stdout_closed = False
        stderr_closed = False

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.execution_dir),
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **agent_subprocess_kwargs(),
            )

            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            process.stdin.write(prompt)
            process.stdin.close()

            events: queue.Queue[tuple[str, Optional[str]]] = queue.Queue()
            stdout_thread = threading.Thread(
                target=self._stream_reader,
                args=(process.stdout, "stdout", events),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._stream_reader,
                args=(process.stderr, "stderr", events),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if completion_seen and last_final_message:
                    terminate_process_tree(process)
                    return session_id, last_final_message

                if (
                    final_message_seen_at is not None
                    and last_final_message
                    and time.monotonic() - final_message_seen_at >= CODEX_FINAL_MESSAGE_GRACE_SECONDS
                    and process.poll() is None
                ):
                    LOGGER.warning(
                        "Codex produced a final message but did not exit after %.1fs; recovering reply pid=%s",
                        CODEX_FINAL_MESSAGE_GRACE_SECONDS,
                        process.pid,
                    )
                    terminate_process_tree(process)
                    return session_id, last_final_message

                if process.poll() is not None and stdout_closed and stderr_closed:
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    terminate_process_tree(process)
                    if last_final_message:
                        LOGGER.warning(
                            "Codex timed out after %ss, but a final message was recovered pid=%s",
                            self.timeout_seconds,
                            process.pid,
                        )
                        return session_id, last_final_message
                    raise RuntimeError(f"Codex timed out after {self.timeout_seconds} seconds")

                try:
                    stream_name, line = events.get(timeout=min(0.25, max(0.05, remaining)))
                except queue.Empty:
                    continue

                if line is None:
                    if stream_name == "stdout":
                        stdout_closed = True
                    else:
                        stderr_closed = True
                    continue

                if stream_name == "stderr":
                    if line.strip():
                        stderr_chunks.append(line)
                    continue

                raw_line = line.strip()
                if not raw_line:
                    continue
                if status_callback:
                    status = self._status_from_codex_event(raw_line)
                    if status:
                        status_callback(status)
                new_session_id, agent_message, completed, final_answer = self._record_codex_event(
                    raw_line,
                    stderr_chunks=stderr_chunks,
                )
                if new_session_id:
                    session_id = new_session_id
                if agent_message:
                    last_agent_message = agent_message
                    if final_answer:
                        last_final_message = agent_message
                        final_message_seen_at = time.monotonic()
                if completed:
                    if not last_final_message and last_agent_message:
                        last_final_message = last_agent_message
                    completion_seen = True

        except BrokenPipeError as exc:
            raise RuntimeError("Codex process closed before it accepted the prompt") from exc
        finally:
            if process is not None and process.poll() is None and not (completion_seen and last_final_message):
                terminate_process_tree(process)

        assert process is not None
        return_code = process.returncode if process.returncode is not None else 1
        if return_code != 0 and not last_agent_message:
            detail = "".join(stderr_chunks).strip()
            raise RuntimeError(friendly_codex_error(detail, return_code))
        if not last_agent_message:
            raise RuntimeError("Codex returned no final agent message")
        return session_id, last_final_message or last_agent_message

    def send(
        self,
        prompt: str,
        session_id: Optional[str],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        if session_id:
            cmd = self._base_command() + ["resume", session_id, "-"]
        else:
            cmd = self._base_command() + ["-"]
        return self._run(cmd, prompt, status_callback=status_callback)

    def propose(self, prompt: str) -> str:
        cmd = self._base_command(proposal_mode=True) + ["-"]
        _session_id, proposal = self._run(cmd, prompt)
        return proposal


class GenericCliBridge:
    def __init__(self, provider: str, workdir: Path, command_template: str, timeout_seconds: int):
        self.provider = provider
        self.workdir = workdir
        self.command_template = command_template.strip()
        self.timeout_seconds = timeout_seconds

    def send(self, prompt: str, session_id: Optional[str]) -> tuple[str, str]:
        if not self.command_template:
            raise RuntimeError(
                f"No command template configured for provider '{self.provider}'. "
                "Set TELEGRAM_OPERATOR_AGENT_COMMAND in .env.telegram-operator."
            )

        prompt_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as handle:
                handle.write(prompt)
                prompt_path = Path(handle.name)

            command = self.command_template.format(
                prompt_file=str(prompt_path),
                workdir=str(self.workdir),
                session_id=session_id or "",
            )
            env = os.environ.copy()
            env["TELEGRAM_OPERATOR_PROVIDER"] = self.provider
            env["TELEGRAM_OPERATOR_WORKDIR"] = str(self.workdir)
            env["TELEGRAM_OPERATOR_SESSION_ID"] = session_id or ""
            env["TELEGRAM_OPERATOR_PROMPT_FILE"] = str(prompt_path)
            try:
                process = subprocess.run(
                    command,
                    input=prompt,
                    shell=True,
                    text=True,
                    capture_output=True,
                    cwd=str(self.workdir),
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=self.timeout_seconds,
                    **hidden_subprocess_kwargs(),
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{self.provider} timed out after {self.timeout_seconds} seconds") from exc
        finally:
            if prompt_path and prompt_path.exists():
                prompt_path.unlink(missing_ok=True)

        output = process.stdout.strip()
        detail = process.stderr.strip()
        if process.returncode != 0 and not output:
            raise RuntimeError(detail or f"{self.provider} exited with code {process.returncode}")
        if not output:
            raise RuntimeError(f"{self.provider} returned no stdout reply")
        return session_id or f"{self.provider}:stateless", output


class LocalCliBridge:
    def __init__(self, provider: str, workdir: Path, timeout_seconds: int):
        self.provider = provider
        self.workdir = workdir
        self.timeout_seconds = timeout_seconds

    def _command(self, prompt: str, session_id: Optional[str]) -> tuple[list[str], Optional[str]]:
        if self.provider == "claude":
            cmd = ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "text"]
            if session_id:
                cmd.append("--continue")
            return cmd, prompt
        if self.provider == "gemini":
            cmd = ["gemini", "-p", "", "--yolo", "--skip-trust", "--output-format", "text"]
            if session_id:
                cmd.extend(["--resume", "latest"])
            return cmd, prompt
        if self.provider == "opencode":
            cmd = [
                self._opencode_executable(),
                "run",
                "--dangerously-skip-permissions",
                "--format",
                "default",
                "--dir",
                str(self.workdir),
            ]
            if session_id:
                cmd.append("--continue")
            cmd.append(prompt)
            return cmd, None
        raise RuntimeError(f"Unsupported provider: {self.provider}")

    def _opencode_executable(self) -> str:
        for name in ("opencode.cmd", "opencode.exe", "opencode"):
            path = shutil.which(name)
            if path:
                return path
        raise RuntimeError("Could not find opencode on PATH")

    def send(self, prompt: str, session_id: Optional[str]) -> tuple[str, str]:
        cmd, stdin_text = self._command(prompt, session_id)
        try:
            process = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
                capture_output=True,
                cwd=str(self.workdir),
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{self.provider} timed out after {self.timeout_seconds} seconds") from exc
        output = process.stdout.strip()
        detail = process.stderr.strip()
        if process.returncode != 0 and not output:
            raise RuntimeError(detail or f"{self.provider} exited with code {process.returncode}")
        if not output:
            raise RuntimeError(f"{self.provider} returned no stdout reply")
        return session_id or f"{self.provider}:latest", output


def build_agent_bridge(config: OperatorConfig):
    provider = config.agent_provider.strip().lower() or "codex"
    if provider == "codex":
        return CodexBridge(config.workdir, config.codex_model, config.agent_timeout_seconds, config.safety_mode)
    if provider in {"claude", "gemini", "opencode"} and not config.agent_command.strip():
        return LocalCliBridge(provider, config.workdir, config.agent_timeout_seconds)
    return GenericCliBridge(provider, config.workdir, config.agent_command, config.agent_timeout_seconds)


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


class TelegramCodexOperator:
    def __init__(self, config: OperatorConfig):
        self.config = config
        self.state = StateStore(config.state_path)
        self.memory_log = MemoryLog(config.memory_log_path)
        self.message_store = SQLiteMessageStore(config.sqlite_path)
        self.transcriber = RemoteFirstWhisperTranscriber(config.whisper_urls, config.whisper_model_name)
        self.agent = build_agent_bridge(config)
        self.proposal_agent = CodexBridge(config.workdir, config.codex_model, min(config.agent_timeout_seconds, 180), "restricted")
        self.voice = KokoroVoiceReply(config.kokoro_urls, config.kokoro_voice, config.kokoro_lang_code)
        self.chat_locks: Dict[int, asyncio.Lock] = {}
        self.pending_approvals: Dict[str, PendingApproval] = {}

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self.chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self.chat_locks[chat_id] = lock
        return lock

    def _authorized(self, chat_id: int) -> bool:
        return chat_id in self.config.allowed_chat_ids

    def _telegram_user_metadata(self, update: Update) -> dict[str, Any]:
        user = update.effective_user
        if not user:
            return {}
        return {
            "telegram_user_id": user.id,
            "telegram_username": user.username,
            "telegram_full_name": user.full_name,
        }

    def _record_incoming_message(
        self,
        update: Update,
        *,
        event_type: str,
        message_type: str,
        text: Optional[str] = None,
        transcript: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        message = update.message or update.effective_message
        chat = update.effective_chat
        user_meta = self._telegram_user_metadata(update)
        message_dict: dict[str, Any] = {}
        if message:
            try:
                message_dict = message.to_dict()
            except Exception:
                message_dict = {}
        self.message_store.append(
            direction="in",
            event_type=event_type,
            chat_id=chat.id if chat else None,
            telegram_message_id=message.message_id if message else None,
            message_type=message_type,
            text=text,
            transcript=transcript,
            safe_mode=self.config.safe_mode,
            metadata={"message": message_dict, **(metadata or {})},
            **user_meta,
        )

    async def _send_text_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        *,
        event_type: str = "outgoing_text",
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ):
        sent = await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        self.message_store.append(
            direction="out",
            event_type=event_type,
            chat_id=chat_id,
            telegram_message_id=sent.message_id,
            message_type="text",
            text=text,
            safe_mode=self.config.safe_mode,
            metadata=metadata,
        )
        return sent

    def _record_callback(self, update: Update, action: str, approval_id: Optional[str]) -> None:
        query = update.callback_query
        user = update.effective_user
        chat = update.effective_chat
        if chat is None and query and query.message and getattr(query.message, "chat", None):
            chat = query.message.chat
        message = query.message if query else None
        self.message_store.append(
            direction="in",
            event_type=f"callback_{action}",
            chat_id=chat.id if chat else None,
            telegram_message_id=message.message_id if message else None,
            telegram_user_id=user.id if user else None,
            telegram_username=user.username if user else None,
            telegram_full_name=user.full_name if user else None,
            message_type="callback",
            text=query.data if query else None,
            safe_mode=self.config.safe_mode,
            approval_id=approval_id,
            metadata={"callback_data": query.data if query else None},
        )

    def _build_proposal_prompt(self, telegram_user: str, body: str, transcript: Optional[str]) -> str:
        source = "Telegram voice note transcript" if transcript else "Telegram text"
        return "\n\n".join(
            [
                "You are preparing a RESTRICTED MODE proposal for a Telegram-controlled coding agent.",
                "Do not modify files, do not run mutating commands, do not install packages, do not start or stop services, and do not access paths outside the assigned workspace.",
                "Your only job is to inspect the request text and produce a concise approval card for the user.",
                f"Assigned workspace: {self.config.workdir}",
                f"Selected execution provider after approval: {self.config.agent_provider}",
                f"Sender: {telegram_user}",
                f"Source: {source}",
                "Return plain text with these headings:",
                "Proposal:",
                "Likely actions:",
                "Workspace boundary:",
                "Risks:",
                "Approval needed:",
                "User request:",
                body.strip(),
            ]
        )

    def _build_approved_prompt(self, telegram_user: str, body: str, transcript: Optional[str], proposal: str) -> str:
        base_prompt = self._build_prompt(telegram_user, body, transcript)
        safe_mode_context = "\n\n".join(
            [
                "RESTRICTED MODE APPROVAL CONTEXT:",
                "The user approved the proposal below through a Telegram inline keyboard.",
                "Stay inside the assigned workspace unless the approved proposal explicitly names outside paths or outside-machine actions.",
                f"Assigned workspace: {self.config.workdir}",
                "If the task requires unapproved file changes, destructive actions, credential access, service restarts, or paths outside the assigned workspace, stop and ask for a new approval instead of proceeding.",
                "Approved proposal:",
                proposal.strip(),
            ]
        )
        return f"{safe_mode_context}\n\n{base_prompt}"

    async def _queue_safe_mode_proposal(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        telegram_user: str,
        text: str,
        transcript: Optional[str],
    ) -> None:
        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.TYPING))
        try:
            proposal_prompt = self._build_proposal_prompt(telegram_user, text, transcript)
            proposal = await asyncio.to_thread(self.proposal_agent.propose, proposal_prompt)
        except Exception as exc:
            LOGGER.exception("Safe mode proposal failed chat_id=%s", chat_id)
            await self._send_text_message(
                context,
                chat_id,
                f"Safe mode proposal failed: {exc}",
                event_type="safe_mode_proposal_error",
            )
            return
        finally:
            keepalive.cancel()
            try:
                await keepalive
            except (asyncio.CancelledError, Exception):
                pass

        approval_id = secrets.token_urlsafe(8)
        self.pending_approvals[approval_id] = PendingApproval(
            chat_id=chat_id,
            telegram_user=telegram_user,
            text=text,
            transcript=transcript,
            proposal=proposal,
            created_at=utc_now(),
        )
        card = (
            "Safe mode proposal\n\n"
            f"{proposal.strip()[:2800]}\n\n"
            f"Workspace: {self.config.workdir}\n"
            "Approve to let the agent run this request."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Approve", callback_data=f"safe:approve:{approval_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"safe:cancel:{approval_id}"),
                ]
            ]
        )
        sent = await self._send_text_message(
            context,
            chat_id,
            card[:3900],
            event_type="safe_mode_proposal",
            metadata={"proposal": proposal, "approval_id": approval_id, "reply_markup": "approve_cancel"},
            reply_markup=keyboard,
        )
        self.message_store.append(
            direction="out",
            event_type="safe_mode_proposal_metadata",
            chat_id=chat_id,
            telegram_message_id=sent.message_id,
            message_type="metadata",
            text=None,
            safe_mode=self.config.safe_mode,
            approval_id=approval_id,
            metadata={"proposal": proposal},
        )

    async def _chat_action_keepalive(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        action: str,
        interval: float = 3.0,
    ) -> None:
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=action)
            except Exception as exc:
                LOGGER.warning("Telegram chat action failed chat_id=%s action=%s error=%s", chat_id, action, exc)
            await asyncio.sleep(interval)

    async def _stop_keepalive(self, keepalive: Optional[asyncio.Task]) -> None:
        if keepalive is None:
            return
        keepalive.cancel()
        try:
            await keepalive
        except (asyncio.CancelledError, Exception):
            pass

    async def _delayed_progress_note(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        message: str,
        delay_seconds: int = 90,
    ) -> None:
        await asyncio.sleep(delay_seconds)
        await self._send_text_message(context, chat_id, message, event_type="progress_note")

    async def _status_update_pump(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        status_queue: "asyncio.Queue[str]",
        *,
        label: str = "Still working",
        initial_delay_seconds: int = STATUS_UPDATE_INITIAL_DELAY_SECONDS,
        interval_seconds: int = STATUS_UPDATE_INTERVAL_SECONDS,
        send_on_status_change: bool = False,
        status_change_min_interval_seconds: int = STATUS_CHANGE_MIN_INTERVAL_SECONDS,
    ) -> None:
        latest_status = "starting"
        last_sent = ""
        last_sent_at = 0.0
        next_send_at = time.monotonic() + initial_delay_seconds
        while True:
            timeout = max(0.1, next_send_at - time.monotonic())
            try:
                status = await asyncio.wait_for(status_queue.get(), timeout=timeout)
                if status:
                    latest_status = status
                    if send_on_status_change:
                        message = f"{label}: {latest_status}."
                        now = time.monotonic()
                        if message != last_sent and now - last_sent_at >= status_change_min_interval_seconds:
                            await self._send_text_message(
                                context,
                                chat_id,
                                message,
                                event_type="status_update",
                                metadata={"latest_status": latest_status},
                            )
                            last_sent = message
                            last_sent_at = now
                continue
            except asyncio.TimeoutError:
                pass

            message = f"{label}: {latest_status}."
            if message != last_sent:
                await self._send_text_message(
                    context,
                    chat_id,
                    message,
                    event_type="status_update",
                    metadata={"latest_status": latest_status},
                )
                last_sent = message
                last_sent_at = time.monotonic()
            next_send_at = time.monotonic() + interval_seconds

    def _make_status_callback(self, loop: asyncio.AbstractEventLoop, status_queue: "asyncio.Queue[str]"):
        def status_callback(status: str) -> None:
            loop.call_soon_threadsafe(status_queue.put_nowait, status)

        return status_callback

    def _send_agent_prompt(
        self,
        prompt: str,
        session_id: Optional[str],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        if isinstance(self.agent, CodexBridge):
            return self.agent.send(
                prompt,
                session_id,
                status_callback=status_callback,
            )
        return self.agent.send(prompt, session_id)

    def _git_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )

    def _git_dirty(self) -> bool:
        result = self._git_command(["status", "--porcelain", "--untracked-files=normal"])
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "git status failed")
        return bool(result.stdout.strip())

    def _git_commit_all(self, message: str) -> Optional[str]:
        if not self._git_dirty():
            return None
        add_result = self._git_command(["add", "-A"])
        if add_result.returncode != 0:
            raise RuntimeError((add_result.stderr or add_result.stdout).strip() or "git add failed")
        commit_result = self._git_command(["commit", "-m", message])
        if commit_result.returncode != 0:
            raise RuntimeError((commit_result.stderr or commit_result.stdout).strip() or "git commit failed")
        rev_result = self._git_command(["rev-parse", "--short", "HEAD"])
        if rev_result.returncode == 0:
            return rev_result.stdout.strip()
        return None

    def _prepare_code_mode_checkpoint(self) -> Optional[str]:
        if self.config.safety_mode != "code":
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit = self._git_commit_all(f"Code mode pre-run checkpoint {stamp}")
        if commit:
            LOGGER.info("Code mode created pre-run checkpoint commit=%s", commit)
        return commit

    def _commit_code_mode_result(self) -> Optional[str]:
        if self.config.safety_mode != "code":
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit = self._git_commit_all(f"Code mode agent changes {stamp}")
        if commit:
            LOGGER.info("Code mode committed agent changes commit=%s", commit)
        return commit

    async def _send_voice_reply(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
        with tempfile.TemporaryDirectory(prefix="telegram-codex-reply-") as tmp:
            ogg_path = await asyncio.to_thread(self.voice.synthesize_ogg, text, Path(tmp))
            caption = text if len(text) <= 900 else text[:897].rstrip() + "..."
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
            except Exception as exc:
                LOGGER.warning("Telegram chat action failed before voice upload chat_id=%s error=%s", chat_id, exc)
            with ogg_path.open("rb") as handle:
                sent = await context.bot.send_voice(chat_id=chat_id, voice=handle, caption=caption)
            self.message_store.append(
                direction="out",
                event_type="outgoing_voice_reply",
                chat_id=chat_id,
                telegram_message_id=sent.message_id,
                message_type="voice",
                text=caption,
                safe_mode=self.config.safe_mode,
                metadata={
                    "source_text": text,
                    "caption": caption,
                    "voice_duration": getattr(sent.voice, "duration", None) if sent.voice else None,
                    "voice_file_id": getattr(sent.voice, "file_id", None) if sent.voice else None,
                },
            )

    async def _send_assistant_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        *,
        always_send_text: bool = False,
    ) -> None:
        try:
            await self._send_voice_reply(context, chat_id, text)
        except Exception as exc:
            LOGGER.exception("Voice reply failed chat_id=%s", chat_id)
            fallback = (
                f"{text}\n\n[Voice reply failed: {exc}]"
                if len(text) <= 3200
                else f"Voice reply failed: {exc}"
            )
            await self._send_text_chunks(context, chat_id, fallback)
        else:
            if always_send_text or len(text) > 900:
                await self._send_text_chunks(context, chat_id, text)

    async def _send_text_chunks(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
        chunk_size = 3500
        for i in range(0, len(text), chunk_size):
            await self._send_text_message(
                context,
                chat_id,
                text[i : i + chunk_size],
                event_type="outgoing_text_chunk",
                metadata={"chunk_start": i, "chunk_size": chunk_size},
            )

    def _build_prompt(self, telegram_user: str, body: str, transcript: Optional[str]) -> str:
        parts = [
            "You are responding through a Telegram operator bridge running on the user's own machine.",
            f"The selected coding agent provider is {self.config.agent_provider}.",
            "The user has explicitly granted full local permissions to this bridge.",
            "You are the baseline local assistant: lightweight, practical, and able to learn or add capabilities only when the user's goals make them necessary.",
            f"Use this workspace home for unspecified files and experiments: {self.config.workdir}",
            "Prefer small, understandable steps over building a large framework before it is needed.",
            f"Safety mode: {self.config.safety_mode}.",
            "In safe mode, read and write inside the workspace; ask before touching paths outside it.",
            "In code mode, you may edit this application's repository as well as the normal workspace. Keep changes small, explain what changed, and rely on the bridge's automatic git checkpoints and commits for revert safety.",
            "In restricted mode, only proceed with the approved request scope and ask again before additional writes.",
            "In full access mode, the user has allowed unrestricted local execution, but still avoid destructive surprises.",
            "Treat requests as immediate foreground work by default.",
            "Start doing the work instead of promising future work.",
            "While working, send short natural progress updates only when there is a real step change.",
            "Do not create approval loops for small next steps unless there is real risk, missing access, or destructive impact.",
            "Reply concisely but helpfully for Telegram chat, and assume your text reply will also be spoken aloud with Kokoro.",
            f"Telegram sender: {telegram_user}",
        ]
        if transcript:
            parts.append("This message came from a Telegram voice note.")
            parts.append(f"Transcript: {transcript}")
        else:
            parts.append("This message came from Telegram text.")
        parts.append("User message:")
        parts.append(body.strip())
        return "\n\n".join(parts)

    async def _process_user_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
        transcript: Optional[str] = None,
        approved_proposal: Optional[str] = None,
        keepalive: Optional[asyncio.Task] = None,
    ) -> None:
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = user.username or user.full_name or str(user.id)

        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return

        if self.config.safety_mode == "restricted" and approved_proposal is None:
            try:
                if keepalive is None:
                    action = ChatAction.RECORD_VOICE if transcript else ChatAction.TYPING
                    keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, action))
                await self._queue_safe_mode_proposal(context, chat_id, username, text, transcript)
            finally:
                await self._stop_keepalive(keepalive)
            return

        async with self._lock_for(chat_id):
            session_id = self.state.get_session_id(chat_id)
            action = ChatAction.RECORD_VOICE if transcript else ChatAction.TYPING
            if keepalive is None:
                keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, action))
            loop = asyncio.get_running_loop()
            status_queue: asyncio.Queue[str] = asyncio.Queue()
            status_callback = self._make_status_callback(loop, status_queue)
            status_updates = asyncio.create_task(
                self._status_update_pump(
                    context,
                    chat_id,
                    status_queue,
                    label="Still working",
                )
            )
            code_mode_checkpoint = None
            code_mode_commit = None
            try:
                LOGGER.info("Processing message chat_id=%s user=%s transcript=%s", chat_id, username, bool(transcript))
                if approved_proposal:
                    prompt = self._build_approved_prompt(username, text, transcript, approved_proposal)
                else:
                    prompt = self._build_prompt(username, text, transcript)
                code_mode_checkpoint = await asyncio.to_thread(self._prepare_code_mode_checkpoint)
                new_session_id, reply_text = await asyncio.to_thread(
                    self._send_agent_prompt,
                    prompt,
                    session_id,
                    status_callback,
                )
                code_mode_commit = await asyncio.to_thread(self._commit_code_mode_result)
            except Exception as exc:
                LOGGER.exception("Operator request failed chat_id=%s", chat_id)
                reply_text = f"Operator error: {exc}"
                if code_mode_checkpoint:
                    reply_text = f"{reply_text}\n\nCode mode git safety: pre-run checkpoint `{code_mode_checkpoint}` was created before the error."
                new_session_id = session_id or ""

            if code_mode_checkpoint or code_mode_commit:
                notes = []
                if code_mode_checkpoint:
                    notes.append(f"pre-run checkpoint `{code_mode_checkpoint}`")
                if code_mode_commit:
                    notes.append(f"agent changes `{code_mode_commit}`")
                reply_text = f"{reply_text}\n\nCode mode git safety: committed " + " and ".join(notes) + "."

            if new_session_id:
                self.state.set_session_id(chat_id, new_session_id)

            self.memory_log.append(
                {
                    "ts": utc_now(),
                    "chat_id": chat_id,
                    "telegram_user": username,
                    "session_id": new_session_id or session_id,
                    "input_text": text,
                    "voice_transcript": transcript,
                    "safe_mode": self.config.safe_mode,
                    "approved_proposal": approved_proposal,
                    "reply_text": reply_text,
                }
            )
            self.message_store.append(
                direction="internal",
                event_type="agent_turn_completed",
                chat_id=chat_id,
                telegram_user_id=user.id if user else None,
                telegram_username=user.username if user else None,
                telegram_full_name=user.full_name if user else None,
                message_type="agent_turn",
                text=reply_text,
                transcript=transcript,
                session_id=new_session_id or session_id,
                safe_mode=self.config.safe_mode,
                metadata={
                    "input_text": text,
                    "approved_proposal": approved_proposal,
                    "provider": self.config.agent_provider,
                    "workdir": str(self.config.workdir),
                    "code_mode_checkpoint": code_mode_checkpoint,
                    "code_mode_commit": code_mode_commit,
                },
            )

            try:
                await self._send_assistant_reply(context, chat_id, reply_text)
            finally:
                await self._stop_keepalive(keepalive)
                status_updates.cancel()
                try:
                    await status_updates
                except (asyncio.CancelledError, Exception):
                    pass

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._record_incoming_message(
            update,
            event_type="command_start",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/start",
        )
        await self._send_text_message(
            context,
            update.effective_chat.id,
            "Telegram Codex operator is online. Send text or voice notes.",
            event_type="command_start_reply",
        )
        await self._send_voice_reply(context, update.effective_chat.id, "Telegram Codex operator is online.")

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_reset",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/reset",
        )
        self.state.clear_session_id(chat_id)
        await self._send_text_message(
            context,
            chat_id,
            "Cleared the persisted Codex session for this chat.",
            event_type="command_reset_reply",
        )
        await self._send_voice_reply(context, chat_id, "I cleared the persisted Codex session for this chat.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_status",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/status",
        )
        session_id = self.state.get_session_id(chat_id)
        try:
            codex_status = f"available at {codex_executable()}"
        except RuntimeError as exc:
            codex_status = str(exc)
        text = (
            f"Provider: {self.config.agent_provider}\n"
            f"Workdir: {self.config.workdir}\n"
            f"Session: {session_id or 'none'}\n"
            f"Safety mode: {self.config.safety_mode}\n"
            f"Codex: {codex_status}\n"
            f"Voice: {self.config.kokoro_voice}\n"
            f"Whisper model: {self.config.whisper_model_name}\n"
            f"Speech hosts: {', '.join(self.config.whisper_urls) or 'none'}\n"
            f"Local speech fallback: {self.config.local_speech_fallback}"
        )
        await self._send_text_message(context, chat_id, text, event_type="command_status_reply")
        await self._send_voice_reply(context, chat_id, "Status sent in text.")

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        self._record_incoming_message(
            update,
            event_type="incoming_text",
            message_type="text",
            text=update.message.text,
        )
        await self._process_user_message(update, context, update.message.text)

    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.voice:
            return
        chat_id = update.effective_chat.id
        voice = update.message.voice
        voice_metadata = {
            "voice_file_id": voice.file_id,
            "voice_file_unique_id": voice.file_unique_id,
            "duration": voice.duration,
            "mime_type": voice.mime_type,
            "file_size": voice.file_size,
        }
        self._record_incoming_message(
            update,
            event_type="incoming_voice",
            message_type="voice",
            metadata=voice_metadata,
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return

        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.RECORD_VOICE))
        try:
            with tempfile.TemporaryDirectory(prefix="telegram-codex-voice-") as tmp:
                tmp_dir = Path(tmp)
                ogg_path = tmp_dir / "incoming.ogg"
                voice_file = await context.bot.get_file(update.message.voice.file_id)
                await voice_file.download_to_drive(custom_path=str(ogg_path))
                transcript = await asyncio.to_thread(self.transcriber.transcribe, ogg_path)
                LOGGER.info("Voice note transcribed chat_id=%s chars=%s", chat_id, len(transcript))
                self._record_incoming_message(
                    update,
                    event_type="voice_transcript",
                    message_type="transcript",
                    text=transcript,
                    transcript=transcript,
                    metadata=voice_metadata,
                )
        except Exception as exc:
            LOGGER.exception("Voice note handling failed chat_id=%s", chat_id)
            await self._stop_keepalive(keepalive)
            await self._send_text_message(
                context,
                chat_id,
                f"Voice note failed before it reached Codex: {exc}",
                event_type="voice_transcription_failed",
            )
            return
        await self._process_user_message(update, context, transcript, transcript=transcript, keepalive=keepalive)

    async def on_approval_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        message = query.message
        chat_id = None
        if message and getattr(message, "chat", None):
            chat_id = message.chat.id
        elif update.effective_chat:
            chat_id = update.effective_chat.id
        if chat_id is None:
            await query.answer("Could not identify this chat.", show_alert=True)
            return
        LOGGER.info("Safe mode callback chat_id=%s data=%s", chat_id, query.data)
        if not self._authorized(chat_id):
            await query.answer("Unauthorized chat.", show_alert=True)
            return

        parts = query.data.split(":", 2)
        if len(parts) != 3 or parts[0] != "safe":
            await query.answer()
            return
        action, approval_id = parts[1], parts[2]
        self._record_callback(update, action, approval_id)
        pending = self.pending_approvals.pop(approval_id, None)
        if pending is None:
            await query.answer("This proposal is no longer pending.", show_alert=True)
            return
        if pending.chat_id != chat_id:
            await query.answer("This proposal belongs to a different chat.", show_alert=True)
            return

        if action == "cancel":
            await query.answer("Cancelled.")
            LOGGER.info("Safe mode proposal cancelled chat_id=%s approval_id=%s", chat_id, approval_id)
            if message:
                try:
                    await query.edit_message_text("Safe mode proposal cancelled.")
                    self.message_store.append(
                        direction="out",
                        event_type="safe_mode_proposal_cancelled_edit",
                        chat_id=chat_id,
                        telegram_message_id=message.message_id,
                        message_type="text_edit",
                        text="Safe mode proposal cancelled.",
                        safe_mode=self.config.safe_mode,
                        approval_id=approval_id,
                    )
                except Exception:
                    LOGGER.exception("Failed to edit cancelled safe mode proposal chat_id=%s", chat_id)
            return
        if action != "approve":
            await query.answer("Unknown action.", show_alert=True)
            return

        LOGGER.info("Safe mode proposal approved chat_id=%s approval_id=%s", chat_id, approval_id)
        await query.answer("Approved. Running the agent.")
        await self._send_text_message(
            context,
            chat_id,
            "Approved. Running the agent now.",
            event_type="safe_mode_approved_notice",
            metadata={"approval_id": approval_id},
        )
        if message:
            try:
                await query.edit_message_text("Safe mode proposal approved. Running the agent now.")
                self.message_store.append(
                    direction="out",
                    event_type="safe_mode_proposal_approved_edit",
                    chat_id=chat_id,
                    telegram_message_id=message.message_id,
                    message_type="text_edit",
                    text="Safe mode proposal approved. Running the agent now.",
                    safe_mode=self.config.safe_mode,
                    approval_id=approval_id,
                )
            except Exception:
                LOGGER.exception("Failed to edit approved safe mode proposal chat_id=%s", chat_id)

        asyncio.create_task(self._run_approved_pending(context, pending))

    async def _run_approved_pending(self, context: ContextTypes.DEFAULT_TYPE, pending: PendingApproval) -> None:
        class ApprovalUser:
            id = 0
            username = None
            full_name = pending.telegram_user

        class ApprovalChat:
            id = pending.chat_id

        class ApprovalUpdate:
            effective_chat = ApprovalChat()
            effective_user = ApprovalUser()

        await self._process_user_message(
            ApprovalUpdate(),
            context,
            pending.text,
            transcript=pending.transcript,
            approved_proposal=pending.proposal,
        )


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
    return OperatorConfig(
        bot_token=require_env("TELEGRAM_BOT_TOKEN"),
        allowed_chat_ids=parse_allowed_chat_ids(require_env("TELEGRAM_ALLOWED_CHAT_IDS")),
        workdir=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_WORKDIR", ""), DEFAULT_WORKSPACE),
        state_path=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_STATE_PATH", ""), BASE_DIR / "telegram_operator_state.json"),
        memory_log_path=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_MEMORY_LOG", ""), BASE_DIR / "telegram_operator_memory.jsonl"),
        sqlite_path=resolve_app_path(os.environ.get("TELEGRAM_OPERATOR_SQLITE_PATH", ""), BASE_DIR / "telegram_operator_messages.sqlite3"),
        kokoro_urls=speech_urls,
        kokoro_voice=os.environ.get("TELEGRAM_OPERATOR_KOKORO_VOICE", "af_alloy"),
        kokoro_lang_code=os.environ.get("TELEGRAM_OPERATOR_KOKORO_LANG_CODE", "a"),
        whisper_urls=speech_urls,
        whisper_model_name=os.environ.get("TELEGRAM_OPERATOR_WHISPER_MODEL", "base"),
        local_speech_fallback=local_speech_fallback,
        startup_notice=parse_bool(os.environ.get("TELEGRAM_OPERATOR_STARTUP_NOTICE", ""), True),
        agent_provider=os.environ.get("TELEGRAM_OPERATOR_PROVIDER", "codex"),
        agent_command=os.environ.get("TELEGRAM_OPERATOR_AGENT_COMMAND", ""),
        agent_timeout_seconds=parse_positive_int(os.environ.get("TELEGRAM_OPERATOR_AGENT_TIMEOUT_SECONDS", ""), 900),
        codex_model=os.environ.get("TELEGRAM_OPERATOR_CODEX_MODEL", ""),
        safety_mode=safety_mode,
        safe_mode=safety_mode != "full",
    )


async def main() -> None:
    config = load_config()
    if config.agent_provider == "codex":
        codex_executable()
    LOGGER.info(
        "Starting Telegram operator provider=%s workdir=%s voice=%s whisper=%s safety=%s timeout=%ss",
        config.agent_provider,
        config.workdir,
        config.kokoro_voice,
        config.whisper_model_name,
        config.safety_mode,
        config.agent_timeout_seconds,
    )
    operator = TelegramCodexOperator(config)
    application = Application.builder().token(config.bot_token).build()
    application.add_handler(CommandHandler("start", operator.start))
    application.add_handler(CommandHandler("reset", operator.reset))
    application.add_handler(CommandHandler("status", operator.status))
    application.add_handler(CallbackQueryHandler(operator.on_approval_callback, pattern=r"^safe:"))
    application.add_handler(MessageHandler(filters.VOICE, operator.on_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, operator.on_text))
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    if config.startup_notice:
        for chat_id in config.allowed_chat_ids:
            try:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Telegram Codex operator is online.\n"
                        f"Voice: {config.kokoro_voice}\n"
                        f"Safety: {config.safety_mode}\n"
                        f"Speech hosts: {', '.join(config.kokoro_urls) or 'none'}"
                    ),
                )
            except Exception:
                LOGGER.exception("Failed to send startup notice chat_id=%s", chat_id)
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        LOGGER.exception("Telegram Codex operator crashed")
        raise

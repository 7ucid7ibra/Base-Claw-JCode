from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import platform
import queue
import re
import shlex
import secrets
import shutil
import sqlite3
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
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
PDF_EXTRACT_MAX_CHARS = 60000
PHOTO_ALBUM_SETTLE_SECONDS = 1.5
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
VIDEO_MIME_PREFIX = "video/"
DEFAULT_MANUAL_UPDATE_REF = "candidate/whitebook-combined-video-buttons-durable-callbacks-v1"


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


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return cleaned or "document.pdf"


def is_video_file(filename: str, mime_type: str) -> bool:
    if (mime_type or "").lower().startswith(VIDEO_MIME_PREFIX):
        return True
    return Path(filename or "").suffix.lower() in VIDEO_EXTENSIONS


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
            **hidden_subprocess_kwargs(),
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    urls = []
    for line in result.stdout.splitlines():
        ip = line.strip()
        if re.fullmatch(r"100(?:\.\d{1,3}){3}", ip):
            urls.append(f"http://{ip}:8766")
    return urls


def unique_urls(urls: list[str]) -> list[str]:
    unique = []
    seen = set()
    for url in urls:
        normalized = normalize_speech_url(url)
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def build_speech_urls(remote_url: str, local_fallback: bool = True) -> list[str]:
    urls = []
    remote_url = normalize_speech_url(remote_url)
    local_url = "http://127.0.0.1:8766"
    if remote_url and not is_local_speech_url(remote_url):
        urls.append(remote_url)
    if local_fallback:
        urls.append(local_url)
        urls.extend(tailscale_speech_urls())
    return unique_urls(urls)


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


def agent_subprocess_env() -> dict[str, str]:
    """Run coding agents without inheriting BaseClaw's Telegram bot credentials."""
    env = os.environ.copy()
    blocked_keys = {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "TELEGRAM_ALLOWED_CHAT_ID",
        "TELEGRAM_CHAT_ID",
    }
    for key in list(env):
        if key in blocked_keys:
            env.pop(key, None)
    return env


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
    supervisor_id: str
    supervisor_name: str
    supervisor_role: str
    history_agent_name: str
    history_device_name: str
    history_remote: str
    history_remote_db_path: str
    history_ssh_key_path: Path
    history_known_hosts_path: Path
    history_sync_limit: int
    history_auto_sync_enabled: bool
    history_auto_sync_interval_seconds: int
    board_poll_enabled: bool
    board_poll_interval_seconds: int
    board_remote: str
    board_path: str
    board_state_path: Path
    board_agent_aliases: list[str]
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


@dataclass
class PendingApproval:
    chat_id: int
    telegram_user: str
    text: str
    transcript: Optional[str]
    proposal: str
    created_at: str


@dataclass
class PendingSourceUpdate:
    chat_id: int
    entry: dict[str, Any]
    created_at: str


@dataclass
class PendingBoardEntry:
    chat_id: int
    entry: dict[str, Any]
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_sync_state (
                    local_message_id INTEGER PRIMARY KEY,
                    sync_source TEXT,
                    synced_at TEXT,
                    sync_error TEXT,
                    FOREIGN KEY(local_message_id) REFERENCES telegram_messages(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_sync_synced_at ON history_sync_state(synced_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS board_action_cards (
                    card_id TEXT NOT NULL,
                    card_type TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    entry_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    choice TEXT,
                    PRIMARY KEY (card_id, card_type)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_board_action_cards_chat ON board_action_cards(chat_id, card_type)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS supervisor_identity (
                    identity_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
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

    def find_by_telegram_message_id(self, *, chat_id: int, telegram_message_id: int) -> Optional[dict[str, Any]]:
        try:
            with self._connect() as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    """
                    SELECT direction, event_type, message_type, text, transcript, recorded_at, metadata_json
                    FROM telegram_messages
                    WHERE chat_id = ? AND telegram_message_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (chat_id, telegram_message_id),
                ).fetchone()
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to load Telegram reply context message_id=%s", telegram_message_id)
            return None
        if row is None:
            return None
        metadata: dict[str, Any] = {}
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = {}
        return {
            "direction": row["direction"],
            "event_type": row["event_type"],
            "message_type": row["message_type"],
            "text": row["text"],
            "transcript": row["transcript"],
            "recorded_at": row["recorded_at"],
            "metadata": metadata,
        }

    def upsert_supervisor_identity(self, *, identity_key: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=True, default=str)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO supervisor_identity (identity_key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(identity_key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at
                    """,
                    (identity_key, payload, utc_now()),
                )
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to store supervisor identity key=%s", identity_key)

    def load_supervisor_identity(self, *, identity_key: str) -> Optional[dict[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value_json FROM supervisor_identity WHERE identity_key = ?",
                    (identity_key,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to load supervisor identity key=%s", identity_key)
            return None
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            LOGGER.exception("Stored supervisor identity is invalid JSON key=%s", identity_key)
            return None

    def _eligible_history_where(self) -> str:
        return """
            direction IN ('in', 'out')
            AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL
        """

    def history_sync_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM telegram_messages WHERE {self._eligible_history_where()}"
            ).fetchone()[0]
            synced = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM telegram_messages m
                JOIN history_sync_state s ON s.local_message_id = m.id
                WHERE {self._eligible_history_where()} AND s.synced_at IS NOT NULL
                """
            ).fetchone()[0]
            unsynced = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM telegram_messages m
                LEFT JOIN history_sync_state s ON s.local_message_id = m.id
                WHERE {self._eligible_history_where()} AND s.synced_at IS NULL
                """
            ).fetchone()[0]
            last_synced = connection.execute(
                "SELECT MAX(synced_at) FROM history_sync_state WHERE synced_at IS NOT NULL"
            ).fetchone()[0]
            last_error = connection.execute(
                """
                SELECT sync_error
                FROM history_sync_state
                WHERE sync_error IS NOT NULL AND sync_error != ''
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "total": int(total),
            "synced": int(synced),
            "unsynced": int(unsynced),
            "last_synced": last_synced,
            "last_error": last_error[0] if last_error else None,
        }

    def unsynced_history_rows(self, *, agent: str, device: str, limit: int = 250) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT
                    m.id,
                    m.recorded_at,
                    m.direction,
                    m.event_type,
                    m.chat_id,
                    m.telegram_message_id,
                    m.telegram_user_id,
                    m.telegram_username,
                    m.telegram_full_name,
                    m.message_type,
                    m.text,
                    m.transcript,
                    m.session_id,
                    m.safe_mode,
                    m.approval_id,
                    m.metadata_json
                FROM telegram_messages m
                LEFT JOIN history_sync_state s ON s.local_message_id = m.id
                WHERE {self._eligible_history_where()} AND s.synced_at IS NULL
                ORDER BY m.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        payloads = []
        for row in rows:
            role = "user" if row["direction"] == "in" else "assistant"
            message_text = row["text"] or row["transcript"] or ""
            metadata = {
                "local_message_id": row["id"],
                "event_type": row["event_type"],
                "direction": row["direction"],
                "telegram_message_id": row["telegram_message_id"],
                "telegram_username": row["telegram_username"],
                "telegram_full_name": row["telegram_full_name"],
                "safe_mode": row["safe_mode"],
                "approval_id": row["approval_id"],
            }
            if row["metadata_json"]:
                try:
                    metadata["local_metadata"] = json.loads(row["metadata_json"])
                except json.JSONDecodeError:
                    metadata["local_metadata_raw"] = row["metadata_json"]
            payloads.append(
                {
                    "local_id": row["id"],
                    "agent": agent,
                    "device": device,
                    "conversation_id": row["session_id"] or (str(row["chat_id"]) if row["chat_id"] is not None else None),
                    "role": role,
                    "channel": "telegram",
                    "sender_id": str(row["telegram_user_id"] or row["chat_id"] or ""),
                    "transport": row["message_type"] or row["event_type"],
                    "source": f"{agent}:{device}:{row['id']}",
                    "message_text": message_text,
                    "transcript_text": row["transcript"],
                    "raw_payload_json": row["metadata_json"],
                    "metadata_json": json.dumps(metadata, ensure_ascii=True, default=str),
                    "message_ts": row["recorded_at"],
                    "recorded_at": row["recorded_at"],
                    "synced_at": utc_now(),
                    "sync_source": "telegram_operator_history_sync",
                }
            )
        return payloads

    def mark_history_synced(self, local_ids: list[int], *, sync_source: str) -> None:
        if not local_ids:
            return
        synced_at = utc_now()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO history_sync_state (local_message_id, sync_source, synced_at, sync_error)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(local_message_id) DO UPDATE SET
                    sync_source=excluded.sync_source,
                    synced_at=excluded.synced_at,
                    sync_error=NULL
                """,
                [(local_id, sync_source, synced_at) for local_id in local_ids],
            )

    def mark_history_sync_error(self, local_ids: list[int], error: str) -> None:
        if not local_ids:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO history_sync_state (local_message_id, sync_source, synced_at, sync_error)
                VALUES (?, NULL, NULL, ?)
                ON CONFLICT(local_message_id) DO UPDATE SET
                    sync_error=excluded.sync_error
                """,
                [(local_id, error[:1000]) for local_id in local_ids],
            )

    def save_board_action_card(self, *, card_id: str, card_type: str, chat_id: int, entry: dict[str, Any]) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO board_action_cards (card_id, card_type, chat_id, entry_json, created_at, updated_at, choice)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(card_id, card_type) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    entry_json=excluded.entry_json,
                    updated_at=excluded.updated_at
                """,
                (card_id, card_type, chat_id, json.dumps(entry, ensure_ascii=True, default=str), now, now),
            )

    def load_board_action_card(self, *, card_id: str, card_type: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT card_id, card_type, chat_id, entry_json, created_at, updated_at, choice
                FROM board_action_cards
                WHERE card_id = ? AND card_type = ?
                """,
                (card_id, card_type),
            ).fetchone()
        if not row:
            return None
        try:
            entry = json.loads(row["entry_json"])
        except json.JSONDecodeError:
            LOGGER.exception("Stored board action card is invalid card_id=%s card_type=%s", card_id, card_type)
            return None
        return {
            "card_id": row["card_id"],
            "card_type": row["card_type"],
            "chat_id": int(row["chat_id"]),
            "entry": entry,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "choice": row["choice"],
        }

    def mark_board_action_card(self, *, card_id: str, card_type: str, choice: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE board_action_cards
                SET choice = ?, updated_at = ?
                WHERE card_id = ? AND card_type = ?
                """,
                (choice, utc_now(), card_id, card_type),
            )


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
                env=agent_subprocess_env(),
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
            env = agent_subprocess_env()
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
                env=agent_subprocess_env(),
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
        self.identity = self._load_or_initialize_identity()
        self.transcriber = RemoteFirstWhisperTranscriber(config.whisper_urls, config.whisper_model_name)
        self.agent = build_agent_bridge(config)
        self.proposal_agent = CodexBridge(config.workdir, config.codex_model, min(config.agent_timeout_seconds, 180), "restricted")
        self.voice = KokoroVoiceReply(config.kokoro_urls, config.kokoro_voice, config.kokoro_lang_code)
        self.chat_locks: Dict[int, asyncio.Lock] = {}
        self.pending_approvals: Dict[str, PendingApproval] = {}
        self.pending_source_updates: Dict[str, PendingSourceUpdate] = {}
        self.pending_board_entries: Dict[str, PendingBoardEntry] = {}
        self.pending_manual_updates: Dict[int, str] = {}
        self.photo_albums: Dict[tuple[int, str], dict[str, Any]] = {}

    def _local_memory_gb(self) -> Optional[float]:
        if platform.system().lower() != "windows":
            return None
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return round(status.ullTotalPhys / (1024**3), 1)
        except Exception:
            return None

    def _current_source_commit(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _build_identity_record(self) -> dict[str, Any]:
        memory_gb = self._local_memory_gb()
        return {
            "supervisor_id": self.config.supervisor_id,
            "display_name": self.config.supervisor_name,
            "role": self.config.supervisor_role,
            "team_slot": self.config.supervisor_id,
            "role_source": "local supervisor configuration and system inventory",
            "core_purpose": self.config.supervisor_core_purpose,
            "do_not": self.config.supervisor_do_not,
            "machine": {
                "hostname": socket.gethostname(),
                "device_label": self.config.supervisor_device_label,
                "os": platform.platform(),
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
                "ram_gb": memory_gb,
            },
            "operator": {
                "provider": self.config.agent_provider,
                "workdir": str(self.config.workdir),
                "repo": str(PROJECT_ROOT),
                "source_commit": self._current_source_commit(),
                "safety_mode": self.config.safety_mode,
            },
            "coordination": {
                "history_agent_name": self.config.history_agent_name,
                "history_device_name": self.config.history_device_name,
                "board_remote": self.config.board_remote,
                "board_path": self.config.board_path,
                "board_aliases": self.config.board_agent_aliases,
            },
        }

    def _load_or_initialize_identity(self) -> dict[str, Any]:
        identity = self._build_identity_record()
        self.message_store.upsert_supervisor_identity(identity_key="self", value=identity)
        return self.message_store.load_supervisor_identity(identity_key="self") or identity

    def _identity_prompt_block(self) -> str:
        machine = self.identity.get("machine", {})
        operator = self.identity.get("operator", {})
        coordination = self.identity.get("coordination", {})
        return "\n".join(
            [
                f"Supervisor identity: {self.identity.get('display_name')} ({self.identity.get('supervisor_id')})",
                f"Role: {self.identity.get('role')}",
                f"Core purpose: {self.identity.get('core_purpose')}",
                f"Machine: {machine.get('hostname')} / {machine.get('device_label')} / {machine.get('os')}",
                f"Specs: CPU count {machine.get('cpu_count')}, RAM {machine.get('ram_gb')} GB",
                f"Source commit: {operator.get('source_commit')}",
                f"Board aliases: {', '.join(coordination.get('board_aliases') or [])}",
                "Identity rule: use this database identity for self-reference; do not infer identity from stale chat memory.",
            ]
        )

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
        reply_to_message_id = None
        if message and getattr(message, "reply_to_message", None):
            reply_to_message_id = message.reply_to_message.message_id
        self.message_store.append(
            direction="in",
            event_type=event_type,
            chat_id=chat.id if chat else None,
            telegram_message_id=message.message_id if message else None,
            message_type=message_type,
            text=text,
            transcript=transcript,
            safe_mode=self.config.safe_mode,
            metadata={"message": message_dict, "reply_to_message_id": reply_to_message_id, **(metadata or {})},
            **user_meta,
        )

    def _reply_context_for_update(self, update: Update, max_chars: int = 1600) -> Optional[str]:
        message = update.message or update.effective_message
        chat = update.effective_chat
        reply = getattr(message, "reply_to_message", None) if message else None
        if not reply or not chat:
            return None

        stored = self.message_store.find_by_telegram_message_id(
            chat_id=chat.id,
            telegram_message_id=reply.message_id,
        )
        source = "Telegram embedded reply preview"
        direction = "unknown"
        event_type = "unknown"
        message_type = "unknown"
        content = None
        if stored:
            source = "local message history"
            direction = stored.get("direction") or direction
            event_type = stored.get("event_type") or event_type
            message_type = stored.get("message_type") or message_type
            content = stored.get("text") or stored.get("transcript")

        if not content:
            content = reply.text or reply.caption
        if not content:
            if getattr(reply, "voice", None):
                content = "[Referenced Telegram voice message; transcript not available in local history.]"
            elif getattr(reply, "photo", None):
                content = "[Referenced Telegram photo; caption not available.]"
            elif getattr(reply, "video", None):
                content = "[Referenced Telegram video; caption not available.]"
            elif getattr(reply, "document", None):
                name = reply.document.file_name if reply.document else None
                content = f"[Referenced Telegram document: {name or 'unnamed document'}]"
            else:
                content = "[Referenced Telegram message has no text preview.]"

        content = content.strip()
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n[Reply context truncated.]"

        return "\n".join(
            [
                "The current Telegram message is a reply to an earlier message.",
                f"Referenced Telegram message ID: {reply.message_id}",
                f"Reply context source: {source}",
                f"Stored direction/type: {direction}/{event_type}/{message_type}",
                "Referenced message excerpt:",
                content,
            ]
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

    def _build_approved_prompt(
        self,
        telegram_user: str,
        body: str,
        transcript: Optional[str],
        proposal: str,
        reply_context: Optional[str] = None,
    ) -> str:
        base_prompt = self._build_prompt(telegram_user, body, transcript, reply_context=reply_context)
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

    def _history_sync_available(self) -> tuple[bool, str]:
        missing = []
        if not self.config.history_remote:
            missing.append("TELEGRAM_OPERATOR_HISTORY_REMOTE")
        if not self.config.history_remote_db_path:
            missing.append("TELEGRAM_OPERATOR_HISTORY_REMOTE_DB_PATH")
        if not self.config.history_ssh_key_path.exists():
            missing.append(f"SSH key missing: {self.config.history_ssh_key_path}")
        if not self.config.history_known_hosts_path.exists():
            missing.append(f"known_hosts missing: {self.config.history_known_hosts_path}")
        if missing:
            return False, "; ".join(missing)
        return True, "configured"

    def _history_ssh_base_command(self) -> list[str]:
        return [
            "ssh",
            "-i",
            str(self.config.history_ssh_key_path),
            "-o",
            f"UserKnownHostsFile={self.config.history_known_hosts_path}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            self.config.history_remote,
        ]

    def _history_scp_base_command(self) -> list[str]:
        return [
            "scp",
            "-i",
            str(self.config.history_ssh_key_path),
            "-o",
            f"UserKnownHostsFile={self.config.history_known_hosts_path}",
            "-o",
            "StrictHostKeyChecking=yes",
        ]

    def _board_available(self) -> tuple[bool, str]:
        missing = []
        if not self.config.board_poll_enabled:
            missing.append("board polling disabled")
        if not self.config.board_remote:
            missing.append("TELEGRAM_OPERATOR_BOARD_REMOTE or TELEGRAM_OPERATOR_HISTORY_REMOTE")
        if not self.config.board_path:
            missing.append("TELEGRAM_OPERATOR_BOARD_PATH")
        if not self.config.history_ssh_key_path.exists():
            missing.append(f"SSH key missing: {self.config.history_ssh_key_path}")
        if not self.config.history_known_hosts_path.exists():
            missing.append(f"known_hosts missing: {self.config.history_known_hosts_path}")
        if missing:
            return False, "; ".join(missing)
        return True, "configured"

    def _board_ssh_base_command(self) -> list[str]:
        return [
            "ssh",
            "-i",
            str(self.config.history_ssh_key_path),
            "-o",
            f"UserKnownHostsFile={self.config.history_known_hosts_path}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            self.config.board_remote,
        ]

    def _load_board_state(self) -> dict[str, Any]:
        if not self.config.board_state_path.exists():
            return {"initialized": False, "seen": []}
        try:
            data = json.loads(self.config.board_state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {"initialized": False, "seen": []}
        data.setdefault("initialized", False)
        data.setdefault("seen", [])
        return data

    def _save_board_state(self, state: dict[str, Any]) -> None:
        self.config.board_state_path.parent.mkdir(parents=True, exist_ok=True)
        state["seen"] = list(dict.fromkeys(state.get("seen", [])))[-500:]
        self.config.board_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _board_entry_id(self, raw_line: str) -> str:
        return hashlib.sha256(raw_line.encode("utf-8", errors="replace")).hexdigest()

    def _board_callback_id(self, entry: dict[str, Any]) -> str:
        return (entry.get("_entry_id") or self._board_entry_id(json.dumps(entry, sort_keys=True)))[:12]

    def _fetch_board_entries(self) -> list[dict[str, Any]]:
        available, detail = self._board_available()
        if not available:
            raise RuntimeError(detail)
        remote_command = f"tail -n 80 {shlex.quote(self.config.board_path)}"
        result = subprocess.run(
            [*self._board_ssh_base_command(), remote_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "board fetch failed")
        entries = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry["_entry_id"] = self._board_entry_id(line)
            entries.append(entry)
        return entries

    def _board_entry_relevant(self, entry: dict[str, Any]) -> bool:
        sender = str(entry.get("agent") or "").strip()
        if sender in set(self.config.board_agent_aliases):
            return False
        if str(entry.get("type") or "").strip() == "source_baseline_update":
            return True
        if str(entry.get("type") or "").strip() in {"result", "source_update_result"}:
            return False
        target = entry.get("to")
        if target is None:
            return False
        targets = target if isinstance(target, list) else [target]
        aliases = {alias.lower() for alias in self.config.board_agent_aliases}
        aliases.add("all")
        return any(str(item).strip().lower() in aliases for item in targets)

    def _format_board_notice(self, entry: dict[str, Any]) -> str:
        body = entry.get("body")
        if isinstance(body, dict):
            body_text = json.dumps(body, ensure_ascii=False)
        else:
            body_text = str(body or "").strip()
        if len(body_text) > 1200:
            body_text = body_text[:1197].rstrip() + "..."
        return (
            "New Raspberry Pi board entry noticed.\n"
            f"From: {entry.get('agent', 'unknown')}\n"
            f"Type: {entry.get('type', 'unknown')}\n"
            f"Thread: {entry.get('thread_id', 'none')}\n"
            f"Task: {entry.get('task_id', 'none')}\n"
            f"Status: {entry.get('status', 'none')}\n\n"
            f"{body_text}\n\n"
            "Use the buttons below so the action stays tied to this exact board entry."
        )

    def _format_board_entry_details(self, entry: dict[str, Any]) -> str:
        details = json.dumps(
            {key: value for key, value in entry.items() if key != "_entry_id"},
            ensure_ascii=False,
            indent=2,
        )
        if len(details) > 3000:
            details = details[:2997].rstrip() + "..."
        return f"Board entry details:\n{details}"

    def _mark_board_entry_choice(self, board_entry_id: str, choice: str) -> None:
        state = self._load_board_state()
        choices = state.setdefault("board_entry_choices", {})
        choices[board_entry_id] = {"choice": choice, "time": utc_now()}
        self._save_board_state(state)
        self.message_store.mark_board_action_card(card_id=board_entry_id, card_type="board", choice=choice)

    def _load_pending_board_entry(self, board_entry_id: str) -> Optional[PendingBoardEntry]:
        pending = self.pending_board_entries.get(board_entry_id)
        if pending is not None:
            return pending
        card = self.message_store.load_board_action_card(card_id=board_entry_id, card_type="board")
        if not card:
            return None
        pending = PendingBoardEntry(
            chat_id=int(card["chat_id"]),
            entry=card["entry"],
            created_at=str(card.get("created_at") or utc_now()),
        )
        self.pending_board_entries[board_entry_id] = pending
        return pending

    async def _send_board_entry_card(
        self,
        application: Application,
        chat_id: int,
        entry: dict[str, Any],
    ) -> None:
        board_entry_id = self._board_callback_id(entry)
        self.pending_board_entries[board_entry_id] = PendingBoardEntry(
            chat_id=chat_id,
            entry=entry,
            created_at=utc_now(),
        )
        self.message_store.save_board_action_card(
            card_id=board_entry_id,
            card_type="board",
            chat_id=chat_id,
            entry=entry,
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Proceed", callback_data=f"board:proceed:{board_entry_id}"),
                    InlineKeyboardButton("Later", callback_data=f"board:later:{board_entry_id}"),
                ],
                [
                    InlineKeyboardButton("Details", callback_data=f"board:details:{board_entry_id}"),
                    InlineKeyboardButton("Dismiss", callback_data=f"board:dismiss:{board_entry_id}"),
                ],
            ]
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text=self._format_board_notice(entry)[:3900],
            reply_markup=keyboard,
        )

    def _build_board_entry_prompt(self, entry: dict[str, Any]) -> str:
        body = entry.get("body")
        body_text = json.dumps(body, ensure_ascii=False, indent=2) if isinstance(body, dict) else str(body or "")
        meta = entry.get("meta")
        meta_text = json.dumps(meta, ensure_ascii=False, indent=2) if isinstance(meta, dict) else str(meta or "")
        return "\n\n".join(
            [
                "Handle the following Raspberry Pi board entry.",
                "The user clicked the inline Proceed button for this exact entry.",
                "Do not treat unrelated recent chat context as approval for a different board item.",
                "If this is a source update candidate, review it according to SOURCE_CONTRIBUTION_REVIEW.md and report a result. Do not silently promote it to baseline.",
                "If this is an implementation task, keep the work bounded to the board entry and report blockers clearly.",
                f"From: {entry.get('agent', 'unknown')}",
                f"Type: {entry.get('type', 'unknown')}",
                f"To: {entry.get('to', 'unknown')}",
                f"Thread: {entry.get('thread_id', 'none')}",
                f"Task: {entry.get('task_id', 'none')}",
                f"Status: {entry.get('status', 'none')}",
                "Body:",
                body_text,
                "Meta:",
                meta_text,
            ]
        )

    def _source_update_payload(self, entry: dict[str, Any]) -> dict[str, Any]:
        body = entry.get("body")
        if isinstance(body, dict):
            return body
        return {"purpose": str(body or "").strip()}

    def _source_update_id(self, entry: dict[str, Any]) -> str:
        return (entry.get("_entry_id") or self._board_entry_id(json.dumps(entry, sort_keys=True)))[:12]

    def _format_source_update_card(self, entry: dict[str, Any]) -> str:
        body = self._source_update_payload(entry)
        purpose = str(body.get("purpose") or "").strip() or "No purpose provided."
        if len(purpose) > 900:
            purpose = purpose[:897].rstrip() + "..."
        lines = [
            "Source update available.",
            f"Repo: {body.get('repo_id') or 'unknown'}",
            f"Branch: {body.get('branch') or 'unknown'}",
            f"Commit: {body.get('commit') or 'unknown'}",
            f"Action: {body.get('action_for_supervisors') or 'unspecified'}",
            "",
            purpose,
            "",
            "Choose Update now only if you want this supervisor to adopt the accepted baseline.",
        ]
        return "\n".join(lines)

    def _format_source_update_details(self, entry: dict[str, Any]) -> str:
        body = self._source_update_payload(entry)
        details = json.dumps(body, ensure_ascii=False, indent=2)
        if len(details) > 3000:
            details = details[:2997].rstrip() + "..."
        return f"Source update details:\n{details}"

    def _mark_source_update_choice(self, source_update_id: str, choice: str) -> None:
        state = self._load_board_state()
        choices = state.setdefault("source_update_choices", {})
        choices[source_update_id] = {"choice": choice, "time": utc_now()}
        self._save_board_state(state)
        self.message_store.mark_board_action_card(card_id=source_update_id, card_type="source", choice=choice)

    def _load_pending_source_update(self, source_update_id: str) -> Optional[PendingSourceUpdate]:
        pending = self.pending_source_updates.get(source_update_id)
        if pending is not None:
            return pending
        card = self.message_store.load_board_action_card(card_id=source_update_id, card_type="source")
        if not card:
            return None
        pending = PendingSourceUpdate(
            chat_id=int(card["chat_id"]),
            entry=card["entry"],
            created_at=str(card.get("created_at") or utc_now()),
        )
        self.pending_source_updates[source_update_id] = pending
        return pending

    def _validate_source_update_for_pull(self, pending: PendingSourceUpdate) -> tuple[bool, str, dict[str, Any]]:
        body = self._source_update_payload(pending.entry)
        repo_id = str(body.get("repo_id") or "").strip()
        branch = str(body.get("branch") or "").strip()
        commit = str(body.get("commit") or "").strip()
        contributor = str(body.get("contributor") or "").strip()
        reviewer = str(body.get("reviewer") or "").strip()
        if repo_id and repo_id != "agent-system":
            return False, f"Unsupported repo: {repo_id}", body
        if not branch:
            return False, "Board entry has no branch.", body
        if not commit:
            return False, "Board entry has no commit.", body
        if not contributor or not reviewer:
            return False, "Board entry has no contributor/reviewer metadata.", body
        if contributor == reviewer:
            return False, "Contributor and reviewer are the same.", body
        if self._git_dirty():
            return False, "Local worktree is not clean.", body
        return True, "ok", body

    def _pull_source_update(self, pending: PendingSourceUpdate) -> str:
        ok, reason, body = self._validate_source_update_for_pull(pending)
        if not ok:
            return f"Update blocked: {reason}"
        branch = str(body.get("branch") or "main").strip()
        commit = str(body.get("commit") or "").strip()
        remote = self._select_source_update_remote()
        if not remote:
            return (
                "Update blocked: no usable source update remote is configured. "
                "Set TELEGRAM_OPERATOR_SOURCE_UPDATE_REMOTE or add a Pi mirror remote."
            )
        fetch = self._git_command(["fetch", remote, branch])
        if fetch.returncode != 0:
            return "Update blocked: git fetch failed: " + (fetch.stderr or fetch.stdout).strip()
        current = self._git_command(["rev-parse", "--short", "HEAD"])
        if current.returncode == 0 and current.stdout.strip() == commit[: len(current.stdout.strip())]:
            return f"Already current at {current.stdout.strip()}."
        merge = self._git_command(["merge", "--ff-only", commit])
        if merge.returncode != 0:
            return "Update blocked: git fast-forward failed: " + (merge.stderr or merge.stdout).strip()
        return f"Updated local source to {commit[:7]}. Restart the operator to run the new code."

    def _manual_update_ref(self, requested_ref: Optional[str] = None) -> str:
        ref = (requested_ref or "").strip() or self.config.manual_update_ref.strip() or DEFAULT_MANUAL_UPDATE_REF
        return ref

    def _manual_update_summary(self, requested_ref: Optional[str] = None) -> str:
        remote = self._select_source_update_remote()
        ref = self._manual_update_ref(requested_ref)
        current = self._git_command(["rev-parse", "--short", "HEAD"])
        current_text = current.stdout.strip() if current.returncode == 0 else "unknown"
        dirty = "yes" if self._git_dirty() else "no"
        return (
            "Manual source update.\n"
            f"Remote: {remote or 'not configured'}\n"
            f"Ref: {ref}\n"
            f"Current commit: {current_text}\n"
            f"Local changes: {dirty}\n\n"
            "This will fetch from the Raspberry Pi and fast-forward only. It will not overwrite local edits."
        )

    def _pull_manual_update(self, requested_ref: Optional[str] = None) -> str:
        if self._git_dirty():
            return "Update blocked: local worktree is not clean."
        remote = self._select_source_update_remote()
        if not remote:
            return (
                "Update blocked: no usable source update remote is configured. "
                "Set TELEGRAM_OPERATOR_SOURCE_UPDATE_REMOTE or add a Pi mirror remote."
            )
        ref = self._manual_update_ref(requested_ref)
        fetch = self._git_command(["fetch", remote, ref])
        if fetch.returncode != 0:
            return "Update blocked: git fetch failed: " + (fetch.stderr or fetch.stdout).strip()
        target = self._git_command(["rev-parse", "--short", "FETCH_HEAD"])
        target_text = target.stdout.strip() if target.returncode == 0 else ref
        current = self._git_command(["rev-parse", "--short", "HEAD"])
        if current.returncode == 0 and current.stdout.strip() == target_text:
            return f"Already current at {target_text}."
        merge = self._git_command(["merge", "--ff-only", "FETCH_HEAD"])
        if merge.returncode != 0:
            return "Update blocked: git fast-forward failed: " + (merge.stderr or merge.stdout).strip()
        return f"Updated local source to {target_text}. Restart the operator to run the new code."

    def _select_source_update_remote(self) -> Optional[str]:
        preferred = self.config.source_update_remote.strip()
        remotes = self._git_command(["remote"])
        if remotes.returncode != 0:
            return preferred or None
        names = {line.strip() for line in remotes.stdout.splitlines() if line.strip()}
        if preferred and preferred in names:
            return preferred
        if "pi-mirror" in names:
            return "pi-mirror"
        if "origin" in names:
            origin_url = self._git_command(["remote", "get-url", "origin"])
            if origin_url.returncode == 0:
                url = origin_url.stdout.strip()
                if "agent-system.git" in url or self.config.board_remote.split("@")[-1].split(":")[0] in url:
                    return "origin"
        return None

    async def _send_source_update_card(
        self,
        application: Application,
        chat_id: int,
        entry: dict[str, Any],
    ) -> None:
        source_update_id = self._source_update_id(entry)
        self.pending_source_updates[source_update_id] = PendingSourceUpdate(
            chat_id=chat_id,
            entry=entry,
            created_at=utc_now(),
        )
        self.message_store.save_board_action_card(
            card_id=source_update_id,
            card_type="source",
            chat_id=chat_id,
            entry=entry,
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Update now", callback_data=f"source:update:{source_update_id}"),
                    InlineKeyboardButton("Later", callback_data=f"source:later:{source_update_id}"),
                ],
                [
                    InlineKeyboardButton("Decline", callback_data=f"source:decline:{source_update_id}"),
                    InlineKeyboardButton("Details", callback_data=f"source:details:{source_update_id}"),
                ],
            ]
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text=self._format_source_update_card(entry)[:3900],
            reply_markup=keyboard,
        )

    async def board_poll_loop(self, application: Application) -> None:
        available, detail = self._board_available()
        if not available:
            LOGGER.info("Board polling not active: %s", detail)
            return
        while True:
            if not self.config.board_poll_enabled:
                await asyncio.sleep(self.config.board_poll_interval_seconds)
                continue
            try:
                entries = await asyncio.to_thread(self._fetch_board_entries)
                state = await asyncio.to_thread(self._load_board_state)
                seen = set(state.get("seen", []))
                current_ids = [entry["_entry_id"] for entry in entries]
                if not state.get("initialized"):
                    state["initialized"] = True
                    state["seen"] = current_ids
                    await asyncio.to_thread(self._save_board_state, state)
                    LOGGER.info("Board polling initialized seen=%s", len(current_ids))
                else:
                    new_relevant = [
                        entry
                        for entry in entries
                        if entry["_entry_id"] not in seen and self._board_entry_relevant(entry)
                    ]
                    if new_relevant:
                        new_relevant_ids = {entry["_entry_id"] for entry in new_relevant}
                        delivered_ids: set[str] = set()
                        for entry in new_relevant:
                            delivered = False
                            for chat_id in self.config.allowed_chat_ids:
                                try:
                                    if str(entry.get("type") or "").strip() == "source_baseline_update":
                                        await self._send_source_update_card(application, chat_id, entry)
                                    else:
                                        await self._send_board_entry_card(application, chat_id, entry)
                                    delivered = True
                                except Exception:
                                    LOGGER.exception("Failed to send board notice chat_id=%s", chat_id)
                            if delivered:
                                delivered_ids.add(entry["_entry_id"])
                        safe_seen_ids = (set(current_ids) - new_relevant_ids).union(delivered_ids)
                        state["seen"] = list(seen.union(safe_seen_ids))
                        await asyncio.to_thread(self._save_board_state, state)
                    elif current_ids:
                        state["seen"] = list(seen.union(current_ids))
                        await asyncio.to_thread(self._save_board_state, state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("Board polling failed: %s", exc)
            await asyncio.sleep(self.config.board_poll_interval_seconds)

    async def history_auto_sync_loop(self) -> None:
        if not self.config.history_auto_sync_enabled:
            LOGGER.info("History auto-sync not active: disabled")
            return
        while True:
            try:
                available, detail = self._history_sync_available()
                if not available:
                    LOGGER.warning("History auto-sync skipped: %s", detail)
                else:
                    result = await asyncio.to_thread(self._sync_history_to_pi)
                    selected = int(result.get("selected", 0) or 0)
                    inserted = int(result.get("inserted", 0) or 0)
                    skipped = int(result.get("skipped", 0) or 0)
                    if selected:
                        LOGGER.info(
                            "History auto-sync complete selected=%s inserted=%s skipped=%s",
                            selected,
                            inserted,
                            skipped,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("History auto-sync failed; will retry later: %s", exc)
            await asyncio.sleep(self.config.history_auto_sync_interval_seconds)

    def _sync_history_to_pi(self) -> dict[str, Any]:
        available, detail = self._history_sync_available()
        if not available:
            raise RuntimeError(detail)

        rows = self.message_store.unsynced_history_rows(
            agent=self.config.history_agent_name,
            device=self.config.history_device_name,
            limit=self.config.history_sync_limit,
        )
        if not rows:
            return {"selected": 0, "inserted": 0, "skipped": 0, "remote_db": self.config.history_remote_db_path}

        local_ids = [int(row["local_id"]) for row in rows]
        transfer_id = uuid.uuid4().hex
        remote_dir = f"/tmp/baseclaw-history-sync-{transfer_id}"
        remote_rows = f"{remote_dir}/rows.ndjson"
        remote_script = f"{remote_dir}/sync_history.py"

        script = r'''
import json
import sqlite3
import sys
from pathlib import Path

rows_path = Path(sys.argv[1])
db_path = Path(sys.argv[2])
db_path.parent.mkdir(parents=True, exist_ok=True)

schema = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    device TEXT,
    conversation_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    channel TEXT NOT NULL DEFAULT 'telegram',
    sender_id TEXT,
    transport TEXT,
    source TEXT,
    message_text TEXT NOT NULL,
    transcript_text TEXT,
    raw_payload_json TEXT,
    metadata_json TEXT,
    message_ts TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    sync_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_agent ON messages (agent);
CREATE INDEX IF NOT EXISTS idx_messages_device ON messages (device);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_message_ts ON messages (message_ts);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages (role);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages (channel);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_source_unique ON messages (source);
"""

with rows_path.open("r", encoding="utf-8") as handle:
    rows = [json.loads(line) for line in handle if line.strip()]

inserted = 0
with sqlite3.connect(db_path) as connection:
    connection.executescript(schema)
    for row in rows:
        before = connection.total_changes
        connection.execute(
            """
            INSERT OR IGNORE INTO messages (
                agent, device, conversation_id, role, channel, sender_id, transport, source,
                message_text, transcript_text, raw_payload_json, metadata_json,
                message_ts, recorded_at, synced_at, sync_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("agent"),
                row.get("device"),
                row.get("conversation_id"),
                row.get("role"),
                row.get("channel") or "telegram",
                row.get("sender_id"),
                row.get("transport"),
                row.get("source"),
                row.get("message_text") or "",
                row.get("transcript_text"),
                row.get("raw_payload_json"),
                row.get("metadata_json"),
                row.get("message_ts"),
                row.get("recorded_at"),
                row.get("synced_at"),
                row.get("sync_source"),
            ),
        )
        if connection.total_changes > before:
            inserted += 1

print(json.dumps({"selected": len(rows), "inserted": inserted, "skipped": len(rows) - inserted, "remote_db": str(db_path)}))
'''

        with tempfile.TemporaryDirectory(prefix="baseclaw-history-sync-") as tmp:
            tmp_dir = Path(tmp)
            rows_path = tmp_dir / "rows.ndjson"
            script_path = tmp_dir / "sync_history.py"
            with rows_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")
            script_path.write_text(script, encoding="utf-8")

            mkdir_result = subprocess.run(
                [*self._history_ssh_base_command(), f"mkdir -p {remote_dir}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
            if mkdir_result.returncode != 0:
                raise RuntimeError((mkdir_result.stderr or mkdir_result.stdout).strip() or "failed to create remote temp dir")

            scp_result = subprocess.run(
                [*self._history_scp_base_command(), str(rows_path), str(script_path), f"{self.config.history_remote}:{remote_dir}/"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
            if scp_result.returncode != 0:
                raise RuntimeError((scp_result.stderr or scp_result.stdout).strip() or "failed to copy history sync files")

            remote_command = (
                f"python3 {remote_script} {remote_rows} {self.config.history_remote_db_path}; "
                f"status=$?; rm -rf {remote_dir}; exit $status"
            )
            sync_result = subprocess.run(
                [*self._history_ssh_base_command(), remote_command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
            if sync_result.returncode != 0:
                raise RuntimeError((sync_result.stderr or sync_result.stdout).strip() or "remote history sync failed")

        summary = json.loads((sync_result.stdout or "{}").strip().splitlines()[-1])
        self.message_store.mark_history_synced(local_ids, sync_source="pi_shared_history")
        return summary

    async def _send_voice_reply(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
        if not self.config.voice_replies_enabled:
            return
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
        if not self.config.voice_replies_enabled:
            await self._send_text_chunks(context, chat_id, text)
            return
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

    def _build_prompt(
        self,
        telegram_user: str,
        body: str,
        transcript: Optional[str],
        *,
        reply_context: Optional[str] = None,
    ) -> str:
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
            "Voice-friendly reply rule: prefer plain conversational text. Avoid decorative Markdown such as bold/italic markers unless necessary. Do not quote long file paths, long numbers, long commands, logs, or code blocks in normal spoken replies; summarize them and include only short labels or essential names. If exact paths, commands, or code are needed, put them after a short spoken summary and keep them minimal.",
            f"Telegram sender: {telegram_user}",
            "Loaded supervisor identity:",
            self._identity_prompt_block(),
        ]
        if transcript:
            parts.append("This message came from a Telegram voice note.")
            parts.append(f"Transcript: {transcript}")
        else:
            parts.append("This message came from Telegram text.")
        if reply_context:
            parts.append("Telegram reply context:")
            parts.append(reply_context)
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
        reply_context = self._reply_context_for_update(update)

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
                    prompt = self._build_approved_prompt(
                        username,
                        text,
                        transcript,
                        approved_proposal,
                        reply_context=reply_context,
                    )
                else:
                    prompt = self._build_prompt(username, text, transcript, reply_context=reply_context)
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
            "BaseClaw is online. Send text or voice notes.",
            event_type="command_start_reply",
        )
        await self._send_voice_reply(context, update.effective_chat.id, "BaseClaw is online.")

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_reset",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/reset",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        await self._send_reset_confirmation(context, chat_id)

    async def _send_reset_confirmation(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Yes, reset session", callback_data="reset:confirm"),
                    InlineKeyboardButton("Cancel", callback_data="reset:cancel"),
                ]
            ]
        )
        await self._send_text_message(
            context,
            chat_id,
            "Reset clears this chat's persisted Codex session. Are you sure?",
            event_type="command_reset_confirm",
            reply_markup=keyboard,
        )

    def _status_text(self, chat_id: int) -> str:
        session_id = self.state.get_session_id(chat_id)
        try:
            codex_status = f"available at {codex_executable()}"
        except RuntimeError as exc:
            codex_status = str(exc)
        return (
            f"Provider: {self.config.agent_provider}\n"
            f"Workdir: {self.config.workdir}\n"
            f"Session: {session_id or 'none'}\n"
            f"Safety mode: {self.config.safety_mode}\n"
            f"Codex: {codex_status}\n"
            f"Voice: {self.config.kokoro_voice}\n"
            f"Whisper model: {self.config.whisper_model_name}\n"
            f"Speech hosts: {', '.join(self.config.whisper_urls) or 'none'}\n"
            f"Voice replies: {'on' if self.config.voice_replies_enabled else 'off'}\n"
            f"Local speech fallback: {self.config.local_speech_fallback}\n"
            f"Board polling: {'on' if self.config.board_poll_enabled else 'off'}\n"
            f"History auto-sync: {'on' if self.config.history_auto_sync_enabled else 'off'}"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_status",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/status",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        text = self._status_text(chat_id)
        await self._send_text_message(context, chat_id, text, event_type="command_status_reply")
        await self._send_voice_reply(context, chat_id, "Status sent in text.")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_help",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/help",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        await self._send_help_menu(context, chat_id)

    async def _send_help_menu(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Status", callback_data="menu:status"),
                    InlineKeyboardButton("Voice", callback_data="menu:voice"),
                ],
                [
                    InlineKeyboardButton("History status", callback_data="menu:history_status"),
                    InlineKeyboardButton("Sync history", callback_data="menu:history_sync"),
                ],
                [
                    InlineKeyboardButton("Board status", callback_data="menu:board_status"),
                    InlineKeyboardButton("Voice status", callback_data="menu:voice_status"),
                ],
                [
                    InlineKeyboardButton("Restart", callback_data="menu:restart"),
                    InlineKeyboardButton("Reset session", callback_data="menu:reset"),
                ],
                [InlineKeyboardButton("Update from Pi", callback_data="menu:update")],
            ]
        )
        text = (
            "BaseClaw help menu.\n"
            "Use the buttons below for common operator actions.\n\n"
            "Board behavior: supervisor-to-supervisor messages should go through targeted Raspberry Pi board entries, so the recipient can handle them with the right context."
        )
        await self._send_text_message(context, chat_id, text, event_type="command_help_reply", reply_markup=keyboard)

    def _available_voices(self) -> list[str]:
        voices: list[str] = []
        for server_url in self.config.kokoro_urls:
            try:
                response = requests.get(server_url.rstrip("/") + "/voices", timeout=(4, 12))
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                LOGGER.warning("Voice discovery failed url=%s error=%s", server_url, exc)
                continue
            for value in data.values():
                if isinstance(value, list):
                    voices.extend(str(item) for item in value)
                elif isinstance(value, dict):
                    for nested in value.values():
                        if isinstance(nested, list):
                            voices.extend(str(item) for item in nested)
            if voices:
                break
        if self.config.kokoro_voice and self.config.kokoro_voice not in voices:
            voices.insert(0, self.config.kokoro_voice)
        return sorted(set(voices))

    async def voice_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_voice",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/voice",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        await self._send_voice_menu(context, chat_id)

    async def _send_voice_menu(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        voices = await asyncio.to_thread(self._available_voices)
        if not voices:
            await self._send_text_message(
                context,
                chat_id,
                "No Kokoro voices found. I could not reach /voices on the configured speech hosts.",
                event_type="command_voice_no_voices",
            )
            return
        buttons = [
            InlineKeyboardButton(
                f"{'✓ ' if voice == self.config.kokoro_voice else ''}{voice}",
                callback_data=f"voice:set:{voice}",
            )
            for voice in voices[:48]
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Current voice: {self.config.kokoro_voice}\nChoose a Kokoro voice:",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def on_voice_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        await query.answer()
        chat_id = query.message.chat_id
        if not self._authorized(chat_id):
            await query.edit_message_text("Unauthorized chat.")
            return
        parts = (query.data or "").split(":", 2)
        if len(parts) != 3 or parts[1] != "set":
            await query.edit_message_text("Unknown voice action.")
            return
        voice = parts[2].strip()
        available = await asyncio.to_thread(self._available_voices)
        if voice not in available:
            await query.edit_message_text(f"Voice is no longer available: {voice}")
            return
        lang_code = infer_kokoro_lang_code(voice, self.config.kokoro_lang_code)
        self.config.kokoro_voice = voice
        self.config.kokoro_lang_code = lang_code
        self.voice.voice = voice
        self.voice.lang_code = lang_code
        await asyncio.to_thread(
            update_operator_env,
            {
                "TELEGRAM_OPERATOR_KOKORO_VOICE": voice,
                "TELEGRAM_OPERATOR_KOKORO_LANG_CODE": lang_code,
            },
        )
        self.message_store.append(
            direction="internal",
            event_type="voice_changed",
            chat_id=chat_id,
            message_type="callback",
            text=f"Voice changed to {voice}",
            metadata={"voice": voice, "lang_code": lang_code},
        )
        await query.edit_message_text(f"Voice changed to {voice}.\nLanguage code: {lang_code}")
        await self._send_voice_reply(context, chat_id, f"Voice changed to {voice}.")

    async def voice_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_voice_status",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/voice_status",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        await self._send_text_message(
            context,
            chat_id,
            f"Voice replies are {'enabled' if self.config.voice_replies_enabled else 'disabled'}. Current voice: {self.config.kokoro_voice}.",
            event_type="command_voice_status_reply",
        )

    async def voice_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_voice_replies(update, context, enabled=True)

    async def voice_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_voice_replies(update, context, enabled=False)

    async def _set_voice_replies(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, enabled: bool) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_voice_on" if enabled else "command_voice_off",
            message_type="command",
            text=update.effective_message.text if update.effective_message else ("/voice_on" if enabled else "/voice_off"),
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        self.config.voice_replies_enabled = enabled
        await asyncio.to_thread(
            update_operator_env,
            {"TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED": "true" if enabled else "false"},
        )
        await self._send_text_message(
            context,
            chat_id,
            f"Voice replies {'enabled' if enabled else 'disabled'}. This is active now and saved for restart.",
            event_type="command_voice_toggle_reply",
        )

    async def history_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_history_status",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/history_status",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return

        text = await self._history_status_text()
        await self._send_text_message(context, chat_id, text, event_type="command_history_status_reply")

    async def _history_status_text(self) -> str:
        status = await asyncio.to_thread(self.message_store.history_sync_status)
        available, detail = self._history_sync_available()
        return (
            "History sync status\n"
            f"Agent: {self.config.history_agent_name}\n"
            f"Device: {self.config.history_device_name}\n"
            f"Remote: {self.config.history_remote}\n"
            f"Remote DB: {self.config.history_remote_db_path}\n"
            f"Configured: {'yes' if available else 'no'} ({detail})\n\n"
            f"Eligible local rows: {status['total']}\n"
            f"Synced: {status['synced']}\n"
            f"Unsynced: {status['unsynced']}\n"
            f"Last synced: {status['last_synced'] or 'never'}\n"
            f"Last error: {status['last_error'] or 'none'}"
        )

    async def history_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_history_sync",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/history_sync",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return

        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.TYPING))
        try:
            text = await self._history_sync_text()
            event_type = "command_history_sync_reply"
        except Exception as exc:
            LOGGER.exception("History sync failed chat_id=%s", chat_id)
            text = f"History sync failed: {exc}"
            event_type = "command_history_sync_failed"
        finally:
            await self._stop_keepalive(keepalive)
        await self._send_text_message(context, chat_id, text, event_type=event_type)

    async def _history_sync_text(self) -> str:
        result = await asyncio.to_thread(self._sync_history_to_pi)
        return (
            "History sync complete\n"
            f"Selected: {result.get('selected', 0)}\n"
            f"Inserted: {result.get('inserted', 0)}\n"
            f"Skipped duplicates: {result.get('skipped', 0)}\n"
            f"Remote DB: {result.get('remote_db', self.config.history_remote_db_path)}"
        )

    async def board_poll_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_board_poll_status",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/board_poll_status",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        await self._send_text_message(
            context,
            chat_id,
            (
                f"Board polling is {'enabled' if self.config.board_poll_enabled else 'disabled'}.\n"
                f"Interval: {self.config.board_poll_interval_seconds} seconds.\n"
                f"Board: {self.config.board_remote} {self.config.board_path}"
            ),
            event_type="command_board_poll_status_reply",
        )

    async def board_poll_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_board_poll(update, context, enabled=True)

    async def board_poll_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_board_poll(update, context, enabled=False)

    async def _set_board_poll(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, enabled: bool) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_board_poll_on" if enabled else "command_board_poll_off",
            message_type="command",
            text=update.effective_message.text if update.effective_message else ("/board_poll_on" if enabled else "/board_poll_off"),
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        was_enabled = self.config.board_poll_enabled
        self.config.board_poll_enabled = enabled
        await asyncio.to_thread(
            update_operator_env,
            {"TELEGRAM_OPERATOR_BOARD_POLL_ENABLED": "true" if enabled else "false"},
        )
        restart_note = "The running polling loop will use this setting on its next interval."
        if was_enabled and not enabled:
            restart_note = "This disables future polling checks in the running process and is saved for restart."
        elif not was_enabled and enabled:
            restart_note = "This enables polling in the running process; the loop will check again on its next interval."
        await self._send_text_message(
            context,
            chat_id,
            f"Board polling {'enabled' if enabled else 'disabled'}. {restart_note}",
            event_type="command_board_poll_toggle_reply",
        )

    async def update_from_pi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_update",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/update",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        requested_ref = " ".join(context.args).strip() if context.args else ""
        ref = self._manual_update_ref(requested_ref)
        self.pending_manual_updates[chat_id] = ref
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Yes, update", callback_data="update:confirm"),
                    InlineKeyboardButton("Cancel", callback_data="update:cancel"),
                ]
            ]
        )
        await self._send_text_message(
            context,
            chat_id,
            self._manual_update_summary(ref),
            event_type="command_update_confirm",
            reply_markup=keyboard,
        )

    def _spawn_replacement_operator(self) -> subprocess.Popen:
        script_path = Path(__file__).resolve()
        command = [sys.executable, str(script_path)]
        creationflags = 0
        if os.name == "nt":
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
            creationflags=creationflags,
        )

    async def _exit_after_restart(self, delay_seconds: float = 1.5) -> None:
        await asyncio.sleep(delay_seconds)
        LOGGER.info("Exiting old Telegram operator process after self-restart pid=%s", os.getpid())
        os._exit(0)

    async def restart_operator(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._record_incoming_message(
            update,
            event_type="command_restart_operator",
            message_type="command",
            text=update.effective_message.text if update.effective_message else "/restart_operator",
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        supervised = parse_bool(os.environ.get("BASECLAW_SUPERVISED", ""), False)
        replacement_pid: Optional[int] = None
        if not supervised:
            try:
                replacement = self._spawn_replacement_operator()
                replacement_pid = replacement.pid
            except Exception as exc:
                LOGGER.exception("Operator self-restart spawn failed")
                await self._send_text_message(
                    context,
                    chat_id,
                    f"Restart failed before the new operator could start: {exc}",
                    event_type="command_restart_operator_failed",
                )
                return

        LOGGER.info(
            "Operator self-restart requested chat_id=%s old_pid=%s new_pid=%s supervised=%s",
            chat_id,
            os.getpid(),
            replacement_pid,
            supervised,
        )
        reply = (
            "Restarting the Telegram operator now. The supervisor should bring me back online automatically."
            if supervised
            else "Restarting the Telegram operator now. If everything worked, I should come back online automatically."
        )
        await self._send_text_message(
            context,
            chat_id,
            reply,
            event_type="command_restart_operator_reply",
            metadata={"old_pid": os.getpid(), "new_pid": replacement_pid, "supervised": supervised},
        )
        asyncio.create_task(self._exit_after_restart())

    def _extract_pdf_text(self, pdf_path: Path, max_chars: int = PDF_EXTRACT_MAX_CHARS) -> tuple[str, bool]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf is not installed in the Telegram operator environment") from exc

        reader = PdfReader(str(pdf_path))
        parts: list[str] = []
        total_chars = 0
        truncated = False
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            page_block = f"--- Page {page_number} ---\n{page_text}"
            parts.append(page_block)
            total_chars += len(page_block)
            if total_chars >= max_chars:
                truncated = True
                break
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True
        if truncated:
            text += "\n\n[PDF extraction truncated before sending to the agent.]"
        return text, truncated

    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.document:
            return
        chat_id = update.effective_chat.id
        document = update.message.document
        filename = document.file_name or "document.pdf"
        mime_type = document.mime_type or ""
        is_pdf = mime_type.lower() == "application/pdf" or filename.lower().endswith(".pdf")
        is_video_document = is_video_file(filename, mime_type)
        document_metadata = {
            "document_file_id": document.file_id,
            "document_file_unique_id": document.file_unique_id,
            "file_name": filename,
            "mime_type": mime_type,
            "file_size": document.file_size,
            "caption": update.message.caption,
        }
        self._record_incoming_message(
            update,
            event_type="incoming_document",
            message_type="document",
            text=update.message.caption,
            metadata=document_metadata,
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        if is_video_document and not is_pdf:
            await self._process_video_attachment(
                update,
                context,
                file_id=document.file_id,
                file_unique_id=document.file_unique_id,
                filename=filename,
                mime_type=mime_type,
                file_size=document.file_size,
                duration=None,
                width=None,
                height=None,
                caption=update.message.caption,
                source="document",
            )
            return
        if not is_pdf:
            await self._send_text_message(
                context,
                chat_id,
                "I received a document, but it is not a PDF or video. For now I only handle PDF and video documents.",
                event_type="unsupported_document",
            )
            return

        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.TYPING))
        try:
            upload_dir = self.config.workdir / "telegram_uploads" / "pdfs"
            upload_dir.mkdir(parents=True, exist_ok=True)
            saved_name = f"{update.message.message_id}_{safe_filename(filename)}"
            pdf_path = upload_dir / saved_name
            telegram_file = await context.bot.get_file(document.file_id)
            await telegram_file.download_to_drive(custom_path=str(pdf_path))
            extracted_text, truncated = await asyncio.to_thread(self._extract_pdf_text, pdf_path)
            extracted_chars = len(extracted_text)
            document_metadata.update(
                {
                    "saved_path": str(pdf_path),
                    "extracted_chars": extracted_chars,
                    "truncated": truncated,
                }
            )
            self.message_store.append(
                direction="in",
                event_type="pdf_document_saved",
                chat_id=chat_id,
                telegram_message_id=update.message.message_id,
                telegram_user_id=update.effective_user.id if update.effective_user else None,
                telegram_username=update.effective_user.username if update.effective_user else None,
                telegram_full_name=update.effective_user.full_name if update.effective_user else None,
                message_type="pdf",
                text=update.message.caption,
                safe_mode=self.config.safe_mode,
                metadata=document_metadata,
            )
        except Exception as exc:
            LOGGER.exception("PDF document handling failed chat_id=%s", chat_id)
            await self._stop_keepalive(keepalive)
            await self._send_text_message(
                context,
                chat_id,
                f"PDF handling failed before it reached Codex: {exc}",
                event_type="pdf_document_failed",
            )
            return

        if extracted_text.strip():
            body = "\n\n".join(
                [
                    update.message.caption or "Please read this PDF and tell me what is inside.",
                    "PDF attachment received.",
                    f"Filename: {filename}",
                    f"Saved locally as: {pdf_path}",
                    f"Extracted characters: {extracted_chars}",
                    "Extracted PDF text:",
                    extracted_text,
                ]
            )
        else:
            body = "\n\n".join(
                [
                    update.message.caption or "Please read this PDF and tell me what is inside.",
                    "PDF attachment received, but no selectable text could be extracted.",
                    f"Filename: {filename}",
                    f"Saved locally as: {pdf_path}",
                    "This PDF likely needs OCR or vision analysis.",
                ]
            )
        await self._process_user_message(update, context, body, keepalive=keepalive)

    async def _process_video_attachment(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        file_id: str,
        file_unique_id: str,
        filename: str,
        mime_type: str,
        file_size: Optional[int],
        duration: Optional[int],
        width: Optional[int],
        height: Optional[int],
        caption: Optional[str],
        source: str,
    ) -> None:
        if not update.message:
            return
        chat_id = update.effective_chat.id
        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.TYPING))
        upload_dir = self.config.workdir / "telegram_uploads" / "videos"
        metadata = {
            "source": source,
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "file_name": filename,
            "mime_type": mime_type,
            "file_size": file_size,
            "duration": duration,
            "width": width,
            "height": height,
            "caption": caption,
        }
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(filename or "").suffix
            if not suffix:
                if mime_type == "video/quicktime":
                    suffix = ".mov"
                elif mime_type == "video/webm":
                    suffix = ".webm"
                else:
                    suffix = ".mp4"
            saved_name = f"{update.message.message_id}_{file_unique_id}{suffix}"
            video_path = upload_dir / safe_filename(saved_name)
            telegram_file = await context.bot.get_file(file_id)
            await telegram_file.download_to_drive(custom_path=str(video_path))
            metadata["saved_path"] = str(video_path)
            self.message_store.append(
                direction="in",
                event_type="video_saved",
                chat_id=chat_id,
                telegram_message_id=update.message.message_id,
                telegram_user_id=update.effective_user.id if update.effective_user else None,
                telegram_username=update.effective_user.username if update.effective_user else None,
                telegram_full_name=update.effective_user.full_name if update.effective_user else None,
                message_type="video",
                text=caption,
                safe_mode=self.config.safe_mode,
                metadata=metadata,
            )
        except Exception as exc:
            LOGGER.exception("Video handling failed chat_id=%s source=%s", chat_id, source)
            await self._stop_keepalive(keepalive)
            await self._send_text_message(
                context,
                chat_id,
                f"Video handling failed before it reached Codex: {exc}",
                event_type="video_handling_failed",
            )
            return

        local_vision_summary = ""
        try:
            local_vision_summary = await asyncio.to_thread(
                self._summarize_video_with_local_vision,
                video_path,
                metadata,
            )
        except Exception as exc:
            LOGGER.warning("Local video vision summary failed chat_id=%s error=%s", chat_id, exc)
            local_vision_summary = f"Local vision summary failed: {exc}"

        details = [
            f"Saved locally as: {video_path}",
            f"Source: Telegram {source}",
        ]
        if duration is not None:
            details.append(f"Duration: {duration} seconds")
        if width and height:
            details.append(f"Dimensions: {width}x{height}")
        if mime_type:
            details.append(f"MIME type: {mime_type}")
        if file_size is not None:
            details.append(f"File size: {file_size} bytes")
        if local_vision_summary:
            details.extend(["", "Local LM Studio vision summary:", local_vision_summary])

        body = "\n\n".join(
            [
                caption or "Please process this Telegram video attachment.",
                "Telegram video received.",
                "\n".join(details),
                "Preserve the original file and use a working copy for edits.",
            ]
        )
        await self._process_user_message(update, context, body, keepalive=keepalive)

    def _resolve_ffmpeg(self) -> Optional[str]:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if Path(candidate).exists():
                return candidate
        return None

    def _summarize_video_with_local_vision(self, video_path: Path, metadata: dict[str, Any]) -> str:
        if not self.config.local_vision_enabled:
            return ""
        if not self.config.local_vision_model:
            return "Local vision is enabled, but TELEGRAM_OPERATOR_LM_STUDIO_VISION_MODEL is empty."
        ffmpeg = self._resolve_ffmpeg()
        if not ffmpeg:
            return "Local vision skipped because ffmpeg is not available."

        contact_sheet = video_path.with_name(f"{video_path.stem}_contact_sheet.jpg")
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            "fps=1,scale=384:-1,tile=3x3:padding=6:margin=6",
            "-frames:v",
            "1",
            str(contact_sheet),
        ]
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        if process.returncode != 0:
            return "Local vision skipped because contact sheet generation failed: " + (process.stderr or process.stdout).strip()[:600]

        image_base64 = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")
        prompt = (
            "Describe this contact sheet from a Telegram video in 3 to 6 concise bullets. "
            "Focus on visible subjects, scene changes, readable text if any, and whether the clip seems useful for follow-up editing or analysis."
        )
        payload = {
            "model": self.config.local_vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": 250,
        }
        base_url = self.config.local_vision_base_url.rstrip("/")
        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            timeout=(10, self.config.local_vision_timeout_seconds),
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return "Local vision returned no choices."
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            content = "\n".join(part for part in parts if part)
        summary = str(content).strip()
        return summary or "Local vision returned an empty summary."

    async def _download_photo_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, index: int) -> dict[str, Any]:
        if not update.message or not update.message.photo:
            raise RuntimeError("Photo update did not contain a photo")
        photo = update.message.photo[-1]
        upload_dir = self.config.workdir / "telegram_uploads" / "images"
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved_name = f"{update.message.message_id}_{index}_{photo.file_unique_id}.jpg"
        image_path = upload_dir / safe_filename(saved_name)
        telegram_file = await context.bot.get_file(photo.file_id)
        await telegram_file.download_to_drive(custom_path=str(image_path))
        return {
            "message_id": update.message.message_id,
            "file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "width": photo.width,
            "height": photo.height,
            "file_size": photo.file_size,
            "caption": update.message.caption,
            "saved_path": str(image_path),
        }

    async def _process_photo_updates(
        self,
        updates: list[Update],
        context: ContextTypes.DEFAULT_TYPE,
        *,
        media_group_id: Optional[str] = None,
    ) -> None:
        representative = updates[0]
        chat_id = representative.effective_chat.id
        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.TYPING))
        try:
            images = []
            captions = []
            for index, item in enumerate(updates, start=1):
                if item.message and item.message.caption:
                    captions.append(item.message.caption)
                images.append(await self._download_photo_message(item, context, index))

            caption = "\n".join(dict.fromkeys(captions)).strip()
            image_lines = [
                f"{idx}. {image['saved_path']} ({image['width']}x{image['height']})"
                for idx, image in enumerate(images, start=1)
            ]
            metadata = {
                "media_group_id": media_group_id,
                "image_count": len(images),
                "images": images,
                "caption": caption,
            }
            self.message_store.append(
                direction="in",
                event_type="photo_images_saved",
                chat_id=chat_id,
                telegram_message_id=representative.message.message_id if representative.message else None,
                telegram_user_id=representative.effective_user.id if representative.effective_user else None,
                telegram_username=representative.effective_user.username if representative.effective_user else None,
                telegram_full_name=representative.effective_user.full_name if representative.effective_user else None,
                message_type="photo",
                text=caption,
                safe_mode=self.config.safe_mode,
                metadata=metadata,
            )
        except Exception as exc:
            LOGGER.exception("Photo handling failed chat_id=%s media_group_id=%s", chat_id, media_group_id)
            await self._stop_keepalive(keepalive)
            await self._send_text_message(
                context,
                chat_id,
                f"Image handling failed before it reached Codex: {exc}",
                event_type="photo_handling_failed",
            )
            return

        body_parts = [
            caption or "Please look at these screenshot attachments and respond to them.",
            f"{'Telegram photo album' if len(images) > 1 else 'Telegram photo'} received.",
            f"Saved image count: {len(images)}",
            "Saved locally as:",
            "\n".join(image_lines),
        ]
        await self._process_user_message(representative, context, "\n\n".join(body_parts), keepalive=keepalive)

    async def _flush_photo_album_after_delay(
        self,
        key: tuple[int, str],
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        try:
            await asyncio.sleep(PHOTO_ALBUM_SETTLE_SECONDS)
            album = self.photo_albums.pop(key, None)
            if not album:
                return
            await self._process_photo_updates(album["updates"], context, media_group_id=key[1])
        except asyncio.CancelledError:
            return

    async def on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.photo:
            return
        chat_id = update.effective_chat.id
        photo = update.message.photo[-1]
        media_group_id = update.message.media_group_id
        self._record_incoming_message(
            update,
            event_type="incoming_photo",
            message_type="photo",
            text=update.message.caption,
            metadata={
                "media_group_id": media_group_id,
                "file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "file_size": photo.file_size,
                "caption": update.message.caption,
            },
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return

        if media_group_id:
            key = (chat_id, media_group_id)
            album = self.photo_albums.setdefault(key, {"updates": [], "task": None})
            album["updates"].append(update)
            task = album.get("task")
            if task and not task.done():
                task.cancel()
            album["task"] = asyncio.create_task(self._flush_photo_album_after_delay(key, context))
            return

        await self._process_photo_updates([update], context)

    async def on_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.video:
            return
        chat_id = update.effective_chat.id
        video = update.message.video
        filename = video.file_name or f"telegram_video_{update.message.message_id}.mp4"
        mime_type = video.mime_type or ""
        metadata = {
            "file_id": video.file_id,
            "file_unique_id": video.file_unique_id,
            "file_name": filename,
            "mime_type": mime_type,
            "file_size": video.file_size,
            "duration": video.duration,
            "width": video.width,
            "height": video.height,
            "caption": update.message.caption,
        }
        self._record_incoming_message(
            update,
            event_type="incoming_video",
            message_type="video",
            text=update.message.caption,
            metadata=metadata,
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        await self._process_video_attachment(
            update,
            context,
            file_id=video.file_id,
            file_unique_id=video.file_unique_id,
            filename=filename,
            mime_type=mime_type,
            file_size=video.file_size,
            duration=video.duration,
            width=video.width,
            height=video.height,
            caption=update.message.caption,
            source="video",
        )

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

    async def on_reset_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        chat_id = query.message.chat_id
        action = (query.data or "").split(":", 1)[1] if ":" in (query.data or "") else ""
        self._record_callback(update, f"reset_{action}", None)
        if not self._authorized(chat_id):
            await query.answer("Unauthorized chat.", show_alert=True)
            return
        if action == "cancel":
            await query.answer("Cancelled.")
            await query.edit_message_text("Reset cancelled.")
            return
        if action != "confirm":
            await query.answer("Unknown reset action.", show_alert=True)
            return
        self.state.clear_session_id(chat_id)
        await query.answer("Session reset.")
        await query.edit_message_text("Cleared the persisted Codex session for this chat.")

    async def on_menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        chat_id = query.message.chat_id
        action = (query.data or "").split(":", 1)[1] if ":" in (query.data or "") else ""
        self._record_callback(update, f"menu_{action}", None)
        if not self._authorized(chat_id):
            await query.answer("Unauthorized chat.", show_alert=True)
            return
        await query.answer()
        if action == "status":
            await self._send_text_message(context, chat_id, self._status_text(chat_id), event_type="menu_status_reply")
            return
        if action == "voice":
            await self._send_voice_menu(context, chat_id)
            return
        if action == "history_status":
            await self._send_text_message(context, chat_id, await self._history_status_text(), event_type="menu_history_status_reply")
            return
        if action == "history_sync":
            try:
                text = await self._history_sync_text()
            except Exception as exc:
                LOGGER.exception("Menu history sync failed chat_id=%s", chat_id)
                text = f"History sync failed: {exc}"
            await self._send_text_message(context, chat_id, text, event_type="menu_history_sync_reply")
            return
        if action == "board_status":
            await self._send_text_message(
                context,
                chat_id,
                (
                    f"Board polling is {'enabled' if self.config.board_poll_enabled else 'disabled'}.\n"
                    f"Interval: {self.config.board_poll_interval_seconds} seconds.\n"
                    f"Board: {self.config.board_remote} {self.config.board_path}"
                ),
                event_type="menu_board_status_reply",
            )
            return
        if action == "voice_status":
            await self._send_text_message(
                context,
                chat_id,
                f"Voice replies are {'enabled' if self.config.voice_replies_enabled else 'disabled'}. Current voice: {self.config.kokoro_voice}.",
                event_type="menu_voice_status_reply",
            )
            return
        if action == "restart":
            await self._send_text_message(
                context,
                chat_id,
                "Use /restart to restart the operator. I keep restart as a command so it is not triggered by an accidental help-menu tap.",
                event_type="menu_restart_notice",
            )
            return
        if action == "reset":
            await self._send_reset_confirmation(context, chat_id)
            return
        if action == "update":
            self.pending_manual_updates[chat_id] = self._manual_update_ref()
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Yes, update", callback_data="update:confirm"),
                        InlineKeyboardButton("Cancel", callback_data="update:cancel"),
                    ]
                ]
            )
            await self._send_text_message(
                context,
                chat_id,
                self._manual_update_summary(),
                event_type="menu_update_confirm",
                reply_markup=keyboard,
            )
            return
        await self._send_text_message(context, chat_id, "Unknown help menu action.", event_type="menu_unknown_action")

    async def on_manual_update_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        chat_id = query.message.chat_id
        action = (query.data or "").split(":", 1)[1] if ":" in (query.data or "") else ""
        self._record_callback(update, f"manual_update_{action}", None)
        if not self._authorized(chat_id):
            await query.answer("Unauthorized chat.", show_alert=True)
            return
        if action == "cancel":
            self.pending_manual_updates.pop(chat_id, None)
            await query.answer("Cancelled.")
            await query.edit_message_text("Update cancelled.")
            return
        if action != "confirm":
            await query.answer("Unknown update action.", show_alert=True)
            return
        ref = self.pending_manual_updates.pop(chat_id, None) or self._manual_update_ref()
        await query.answer("Updating from Pi.")
        result = await asyncio.to_thread(self._pull_manual_update, ref)
        await self._send_text_message(context, chat_id, result, event_type="manual_update_result")
        try:
            await query.edit_message_text(result[:3900])
        except Exception:
            LOGGER.exception("Failed to edit manual update callback message chat_id=%s", chat_id)

    async def on_source_update_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self._handle_source_update_callback(update, context)
        except Exception as exc:
            LOGGER.exception("Source update callback failed")
            query = update.callback_query
            if query:
                try:
                    await query.answer(f"Button failed: {exc}", show_alert=True)
                except Exception:
                    LOGGER.exception("Failed to answer failed source update callback")

    async def _handle_source_update_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        if not self._authorized(chat_id):
            await query.answer("Unauthorized chat.", show_alert=True)
            return

        parts = query.data.split(":", 2)
        if len(parts) != 3 or parts[0] != "source":
            await query.answer()
            return
        action, source_update_id = parts[1], parts[2]
        self._record_callback(update, f"source_{action}", source_update_id)
        pending = self._load_pending_source_update(source_update_id)
        if pending is None:
            await query.answer("This source update card expired. Ask me to refresh board notices.", show_alert=True)
            if message:
                try:
                    await query.edit_message_text("This source update card expired. Ask me to refresh board notices.")
                except Exception:
                    LOGGER.exception("Failed to edit expired source update card chat_id=%s", chat_id)
            return
        if pending.chat_id != chat_id:
            await query.answer("This update belongs to a different chat.", show_alert=True)
            return

        if action == "details":
            await query.answer("Details sent.")
            await self._send_text_message(
                context,
                chat_id,
                self._format_source_update_details(pending.entry),
                event_type="source_update_details",
                metadata={"source_update_id": source_update_id},
            )
            return
        if action == "later":
            self._mark_source_update_choice(source_update_id, "later")
            await query.answer("Deferred.")
            if message:
                await query.edit_message_text("Source update deferred for this supervisor.")
            return
        if action == "decline":
            self._mark_source_update_choice(source_update_id, "declined")
            self.pending_source_updates.pop(source_update_id, None)
            await query.answer("Declined.")
            if message:
                await query.edit_message_text("Source update declined for this supervisor.")
            return
        if action != "update":
            await query.answer("Unknown action.", show_alert=True)
            return

        result = await asyncio.to_thread(self._pull_source_update, pending)
        self._mark_source_update_choice(source_update_id, "update_now")
        self.pending_source_updates.pop(source_update_id, None)
        await query.answer("Update check finished.")
        await self._send_text_message(
            context,
            chat_id,
            result,
            event_type="source_update_result",
            metadata={"source_update_id": source_update_id},
        )
        if message:
            try:
                await query.edit_message_text(result[:3900])
            except Exception:
                LOGGER.exception("Failed to edit source update card chat_id=%s", chat_id)

    async def on_board_entry_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self._handle_board_entry_callback(update, context)
        except Exception as exc:
            LOGGER.exception("Board entry callback failed")
            query = update.callback_query
            if query:
                try:
                    await query.answer(f"Button failed: {exc}", show_alert=True)
                except Exception:
                    LOGGER.exception("Failed to answer failed board entry callback")

    async def _handle_board_entry_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        if not self._authorized(chat_id):
            await query.answer("Unauthorized chat.", show_alert=True)
            return

        parts = query.data.split(":", 2)
        if len(parts) != 3 or parts[0] != "board":
            await query.answer()
            return
        action, board_entry_id = parts[1], parts[2]
        self._record_callback(update, f"board_{action}", board_entry_id)
        pending = self._load_pending_board_entry(board_entry_id)
        if pending is None:
            await query.answer("This board card expired. Ask me to refresh board notices.", show_alert=True)
            if message:
                try:
                    await query.edit_message_text("This board card expired. Ask me to refresh board notices.")
                except Exception:
                    LOGGER.exception("Failed to edit expired board entry card chat_id=%s", chat_id)
            return
        if pending.chat_id != chat_id:
            await query.answer("This board entry belongs to a different chat.", show_alert=True)
            return

        if action == "details":
            await query.answer("Details sent.")
            await self._send_text_message(
                context,
                chat_id,
                self._format_board_entry_details(pending.entry),
                event_type="board_entry_details",
                metadata={"board_entry_id": board_entry_id},
            )
            return
        if action == "later":
            self._mark_board_entry_choice(board_entry_id, "later")
            await query.answer("Deferred.")
            if message:
                await query.edit_message_text("Board entry deferred for this supervisor.")
            return
        if action == "dismiss":
            self._mark_board_entry_choice(board_entry_id, "dismissed")
            self.pending_board_entries.pop(board_entry_id, None)
            await query.answer("Dismissed.")
            if message:
                await query.edit_message_text("Board entry dismissed for this supervisor.")
            return
        if action != "proceed":
            await query.answer("Unknown action.", show_alert=True)
            return

        self._mark_board_entry_choice(board_entry_id, "proceed")
        self.pending_board_entries.pop(board_entry_id, None)
        await query.answer("Proceeding.")
        if message:
            try:
                await query.edit_message_text("Proceeding with this board entry.")
            except Exception:
                LOGGER.exception("Failed to edit board entry card chat_id=%s", chat_id)
        prompt = self._build_board_entry_prompt(pending.entry)
        asyncio.create_task(
            self._process_user_message(
                update,
                context,
                prompt,
                transcript=None,
                approved_proposal="User clicked Proceed on a Raspberry Pi board entry inline keyboard.",
            )
        )

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
    history_remote = os.environ.get("TELEGRAM_OPERATOR_HISTORY_REMOTE", "").strip()
    history_ssh_key_path = resolve_app_path(
        os.environ.get("TELEGRAM_OPERATOR_HISTORY_SSH_KEY", ""),
        Path.home() / "Downloads" / "maat_pi_board_ed25519",
    )
    history_known_hosts_path = resolve_app_path(
        os.environ.get("TELEGRAM_OPERATOR_HISTORY_KNOWN_HOSTS", ""),
        Path.home() / "Downloads" / "maat_pi_known_hosts",
    )
    history_agent_name = os.environ.get("TELEGRAM_OPERATOR_HISTORY_AGENT", "baseclaw").strip() or "baseclaw"
    supervisor_id = os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_ID", "").strip()
    if not supervisor_id:
        supervisor_id = f"{history_agent_name}-supervisor" if history_agent_name else "local-supervisor"
    supervisor_name = os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_NAME", "").strip() or supervisor_id
    supervisor_role = os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_ROLE", "").strip() or "unspecified"
    supervisor_device_label = (
        os.environ.get("TELEGRAM_OPERATOR_SUPERVISOR_DEVICE_LABEL", "").strip()
        or os.environ.get("TELEGRAM_OPERATOR_HISTORY_DEVICE", "").strip()
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
    board_aliases = parse_csv_list(
        os.environ.get(
            "TELEGRAM_OPERATOR_BOARD_AGENT_ALIASES",
            f"{supervisor_id},{history_agent_name},developer-agent",
        )
    )
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
        supervisor_id=supervisor_id,
        supervisor_name=supervisor_name,
        supervisor_role=supervisor_role,
        history_agent_name=history_agent_name,
        history_device_name=os.environ.get("TELEGRAM_OPERATOR_HISTORY_DEVICE", os.environ.get("COMPUTERNAME", "unknown-device")).strip() or "unknown-device",
        history_remote=history_remote,
        history_remote_db_path=os.environ.get("TELEGRAM_OPERATOR_HISTORY_REMOTE_DB_PATH", "").strip(),
        history_ssh_key_path=history_ssh_key_path,
        history_known_hosts_path=history_known_hosts_path,
        history_sync_limit=parse_positive_int(os.environ.get("TELEGRAM_OPERATOR_HISTORY_SYNC_LIMIT", ""), 250),
        history_auto_sync_enabled=parse_bool(os.environ.get("TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_ENABLED", ""), True),
        history_auto_sync_interval_seconds=parse_positive_int(
            os.environ.get("TELEGRAM_OPERATOR_HISTORY_AUTO_SYNC_INTERVAL_SECONDS", ""),
            300,
        ),
        board_poll_enabled=parse_bool(os.environ.get("TELEGRAM_OPERATOR_BOARD_POLL_ENABLED", ""), True),
        board_poll_interval_seconds=parse_positive_int(os.environ.get("TELEGRAM_OPERATOR_BOARD_POLL_INTERVAL_SECONDS", ""), 180),
        board_remote=os.environ.get("TELEGRAM_OPERATOR_BOARD_REMOTE", history_remote).strip(),
        board_path=os.environ.get("TELEGRAM_OPERATOR_BOARD_PATH", "/home/ai/agent_board/entries.ndjson").strip(),
        board_state_path=resolve_app_path(
            os.environ.get("TELEGRAM_OPERATOR_BOARD_STATE_PATH", ""),
            BASE_DIR / "telegram_operator_board_state.json",
        ),
        board_agent_aliases=board_aliases,
        source_update_remote=os.environ.get("TELEGRAM_OPERATOR_SOURCE_UPDATE_REMOTE", "pi-mirror").strip() or "pi-mirror",
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
    application = Application.builder().token(config.bot_token).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", operator.start))
    application.add_handler(CommandHandler("help", operator.help_command))
    application.add_handler(CommandHandler("reset", operator.reset))
    application.add_handler(CommandHandler("status", operator.status))
    application.add_handler(CommandHandler("voice", operator.voice_menu))
    application.add_handler(CommandHandler("voice_status", operator.voice_status))
    application.add_handler(CommandHandler("voice_on", operator.voice_on))
    application.add_handler(CommandHandler("voice_off", operator.voice_off))
    application.add_handler(CommandHandler("history_status", operator.history_status))
    application.add_handler(CommandHandler("history_sync", operator.history_sync))
    application.add_handler(CommandHandler("board_poll_status", operator.board_poll_status))
    application.add_handler(CommandHandler("board_poll_on", operator.board_poll_on))
    application.add_handler(CommandHandler("board_poll_off", operator.board_poll_off))
    application.add_handler(CommandHandler("update", operator.update_from_pi))
    application.add_handler(CommandHandler("restart_operator", operator.restart_operator))
    application.add_handler(CommandHandler("restart", operator.restart_operator))
    application.add_handler(CallbackQueryHandler(operator.on_menu_callback, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(operator.on_reset_callback, pattern=r"^reset:"))
    application.add_handler(CallbackQueryHandler(operator.on_manual_update_callback, pattern=r"^update:"))
    application.add_handler(CallbackQueryHandler(operator.on_voice_callback, pattern=r"^voice:"))
    application.add_handler(CallbackQueryHandler(operator.on_source_update_callback, pattern=r"^source:"))
    application.add_handler(CallbackQueryHandler(operator.on_board_entry_callback, pattern=r"^board:"))
    application.add_handler(CallbackQueryHandler(operator.on_approval_callback, pattern=r"^safe:"))
    application.add_handler(MessageHandler(filters.VIDEO, operator.on_video))
    application.add_handler(MessageHandler(filters.Document.ALL, operator.on_document))
    application.add_handler(MessageHandler(filters.PHOTO, operator.on_photo))
    application.add_handler(MessageHandler(filters.VOICE, operator.on_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, operator.on_text))
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    board_task = asyncio.create_task(operator.board_poll_loop(application))
    history_sync_task = asyncio.create_task(operator.history_auto_sync_loop())
    if config.startup_notice:
        for chat_id in config.allowed_chat_ids:
            try:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "BaseClaw is online.\n"
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
        board_task.cancel()
        history_sync_task.cancel()
        try:
            await board_task
        except asyncio.CancelledError:
            pass
        try:
            await history_sync_task
        except asyncio.CancelledError:
            pass
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        LOGGER.exception("BaseClaw Telegram operator crashed")
        raise

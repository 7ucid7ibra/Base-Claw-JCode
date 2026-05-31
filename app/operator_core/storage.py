from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from speech import readable_message_text

LOGGER = logging.getLogger("telegram_codex_operator")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = {"sessions": {}, "provider_sessions": {}}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {
                "sessions": loaded.get("sessions", {}),
                "provider_sessions": loaded.get("provider_sessions", {}),
                "read_next_chats": loaded.get("read_next_chats", {}),
            }
        self._data.setdefault("sessions", {})
        self._data.setdefault("provider_sessions", {})
        self._data.setdefault("read_next_chats", {})

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get_session_id(self, chat_id: int, provider: str = "") -> Optional[str]:
        provider = provider.strip().lower()
        if provider:
            return self._data.get("provider_sessions", {}).get(str(chat_id), {}).get(provider)
        return self._data.get("sessions", {}).get(str(chat_id))

    def set_session_id(self, chat_id: int, session_id: str, provider: str = "") -> None:
        self._data.setdefault("sessions", {})[str(chat_id)] = session_id
        provider = provider.strip().lower()
        if provider:
            self._data.setdefault("provider_sessions", {}).setdefault(str(chat_id), {})[provider] = session_id
        self.save()

    def clear_session_id(self, chat_id: int, provider: str = "") -> None:
        provider = provider.strip().lower()
        if provider:
            self._data.setdefault("provider_sessions", {}).setdefault(str(chat_id), {}).pop(provider, None)
            if self._data.get("sessions", {}).get(str(chat_id), "").startswith(f"{provider}:"):
                self._data.setdefault("sessions", {}).pop(str(chat_id), None)
            self.save()
            return
        self._data.setdefault("sessions", {}).pop(str(chat_id), None)
        self._data.setdefault("provider_sessions", {}).pop(str(chat_id), None)
        self.save()

    def arm_read_next(self, chat_id: int) -> None:
        self._data.setdefault("read_next_chats", {})[str(chat_id)] = utc_now()
        self.save()

    def consume_read_next(self, chat_id: int) -> bool:
        armed = str(chat_id) in self._data.setdefault("read_next_chats", {})
        if armed:
            self._data["read_next_chats"].pop(str(chat_id), None)
            self.save()
        return armed


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
                CREATE TABLE IF NOT EXISTS conversation_memory (
                    chat_id INTEGER PRIMARY KEY,
                    summary_text TEXT NOT NULL,
                    source_max_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
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

    def latest_assistant_reply_text(self, *, chat_id: int) -> str:
        for query, params in (
            (
                """
                SELECT text, transcript, metadata_json
                FROM telegram_messages
                WHERE chat_id = ?
                    AND direction = 'internal'
                    AND event_type = 'agent_turn_completed'
                    AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id,),
            ),
            (
                """
                SELECT text, transcript, metadata_json
                FROM telegram_messages
                WHERE chat_id = ?
                    AND direction = 'out'
                    AND message_type IN ('text', 'voice')
                    AND COALESCE(NULLIF(text, ''), NULLIF(transcript, ''), NULLIF(metadata_json, '')) IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id,),
            ),
        ):
            try:
                with self._connect() as connection:
                    connection.row_factory = sqlite3.Row
                    row = connection.execute(query, params).fetchone()
            except (OSError, sqlite3.Error):
                LOGGER.exception("Failed to load latest assistant reply chat_id=%s", chat_id)
                return ""
            if row is None:
                continue
            text = readable_message_text(row["text"], row["transcript"], row["metadata_json"])
            if text:
                return text
        return ""

    def recent_context_rows(self, *, chat_id: int, limit: int) -> list[dict[str, str]]:
        limit = max(1, min(30, limit))
        try:
            with self._connect() as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT direction, event_type, text, transcript
                    FROM telegram_messages
                    WHERE
                        direction IN ('in', 'out')
                        AND (chat_id = ? OR chat_id IS NULL)
                        AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (chat_id, limit),
                ).fetchall()
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to load shared context rows chat_id=%s", chat_id)
            return []
        payload = []
        for row in reversed(rows):
            event_type = row["event_type"] or ""
            role = "user" if row["direction"] == "in" or event_type.startswith("desktop_user") else "assistant"
            text = (row["transcript"] or row["text"] or "").strip()
            if text:
                payload.append({"role": role, "text": text})
        return payload

    def continuity_summary(self, *, chat_id: int, recent_limit: int) -> str:
        recent_limit = max(1, min(30, recent_limit))
        try:
            with self._connect() as connection:
                connection.row_factory = sqlite3.Row
                max_id = connection.execute(
                    """
                    SELECT COALESCE(MAX(id), 0)
                    FROM telegram_messages
                    WHERE
                        direction IN ('in', 'out')
                        AND (chat_id = ? OR chat_id IS NULL)
                        AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL
                    """,
                    (chat_id,),
                ).fetchone()[0]
                cached = connection.execute(
                    """
                    SELECT summary_text, source_max_id
                    FROM conversation_memory
                    WHERE chat_id = ?
                    """,
                    (chat_id,),
                ).fetchone()
                if cached and int(cached["source_max_id"]) == int(max_id):
                    return str(cached["summary_text"] or "")

                rows = connection.execute(
                    """
                    SELECT id, direction, event_type, text, transcript, session_id, recorded_at
                    FROM telegram_messages
                    WHERE
                        direction IN ('in', 'out')
                        AND (chat_id = ? OR chat_id IS NULL)
                        AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (chat_id, recent_limit + 80),
                ).fetchall()
                summary_rows = list(reversed(rows[recent_limit:])) if len(rows) > recent_limit else []
                summary = self._build_continuity_summary(summary_rows)
                connection.execute(
                    """
                    INSERT INTO conversation_memory (chat_id, summary_text, source_max_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        summary_text = excluded.summary_text,
                        source_max_id = excluded.source_max_id,
                        updated_at = excluded.updated_at
                    """,
                    (chat_id, summary, int(max_id), utc_now()),
                )
                return summary
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to build continuity summary chat_id=%s", chat_id)
            return ""

    @staticmethod
    def _recall_terms(text: str, *, limit: int = 8) -> list[str]:
        stop_words = {
            "about",
            "after",
            "again",
            "because",
            "before",
            "could",
            "from",
            "have",
            "here",
            "latest",
            "like",
            "more",
            "should",
            "that",
            "their",
            "there",
            "this",
            "through",
            "what",
            "when",
            "where",
            "which",
            "with",
            "would",
            "your",
        }
        terms: list[str] = []
        for term in re.findall(r"[A-Za-z0-9_@.-]{4,}", text.lower()):
            normalized = term.strip("._-")
            if not normalized or normalized in stop_words:
                continue
            if normalized not in terms:
                terms.append(normalized)
            if len(terms) >= limit:
                break
        return terms

    def recalled_context_rows(self, *, chat_id: int, current_text: str, limit: int = 6) -> list[dict[str, str]]:
        terms = self._recall_terms(current_text)
        if not terms:
            return []
        limit = max(1, min(12, limit))
        where_clauses = []
        params: list[Any] = [chat_id]
        for term in terms:
            where_clauses.append("LOWER(COALESCE(text, '') || ' ' || COALESCE(transcript, '')) LIKE ?")
            params.append(f"%{term}%")
        params.append(limit)
        try:
            with self._connect() as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    f"""
                    SELECT id, direction, event_type, text, transcript, recorded_at
                    FROM telegram_messages
                    WHERE
                        direction IN ('in', 'out')
                        AND (chat_id = ? OR chat_id IS NULL)
                        AND COALESCE(NULLIF(text, ''), NULLIF(transcript, '')) IS NOT NULL
                        AND ({" OR ".join(where_clauses)})
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        except (OSError, sqlite3.Error):
            LOGGER.exception("Failed to recall context rows chat_id=%s", chat_id)
            return []

        payload = []
        current_norm = " ".join(current_text.strip().split())
        for row in reversed(rows):
            event_type = row["event_type"] or ""
            role = "user" if row["direction"] == "in" or event_type.startswith("desktop_user") else "assistant"
            text = (row["transcript"] or row["text"] or "").strip()
            if not text or " ".join(text.split()) == current_norm:
                continue
            payload.append({"role": role, "text": text, "recorded_at": str(row["recorded_at"] or "")})
        return payload

    @staticmethod
    def _compact_memory_line(text: str, limit: int = 220) -> str:
        text = " ".join(text.strip().split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."

    @classmethod
    def _build_continuity_summary(cls, rows: list[sqlite3.Row]) -> str:
        if not rows:
            return ""
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
            line = cls._compact_memory_line(text)
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
            deduped_facts = list(dict.fromkeys(setup_facts[-8:]))
            sections.append("Relevant setup facts: " + " | ".join(deduped_facts))
        if assistant_outcomes:
            sections.append("Recent assistant outcomes: " + " | ".join(assistant_outcomes[-5:]))
        return "\n".join(sections)

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

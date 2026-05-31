from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests
from harnesses.bridges import CodexBridge, build_agent_bridge
from operator_core.command_handlers import CommandHandlersMixin, friendly_voice_error
from operator_core.media_handlers import MediaHandlersMixin
from operator_core.config import (
    OPERATOR_ENV_PATH,
    OperatorConfig,
    codex_executable,
    load_config,
    startup_summary,
)
from operator_core.storage import MemoryLog, SQLiteMessageStore, StateStore, utc_now
from process_utils import hidden_subprocess_kwargs
from speech import (
    KokoroVoiceReply,
    RemoteFirstWhisperTranscriber,
    spoken_reply_text,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
BASE_DIR = PROJECT_ROOT
DEFAULT_WORKSPACE = PROJECT_ROOT / "agent_workspace"


LOG_PATH = Path(os.environ.get("TELEGRAM_OPERATOR_LOG_PATH") or BASE_DIR / "telegram_operator.log").expanduser()
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
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
LOGGER = logging.getLogger("telegram_operator")
CODEX_FINAL_MESSAGE_GRACE_SECONDS = 8.0
STATUS_UPDATE_INITIAL_DELAY_SECONDS = 120
STATUS_UPDATE_INTERVAL_SECONDS = 120
STATUS_CHANGE_MIN_INTERVAL_SECONDS = 12
VOICE_CAPTION_MAX_CHARS = 999
SLASH_COMMAND_EXTENSIONS = (".md", ".txt", ".prompt")


@dataclass
class PendingApproval:
    chat_id: int
    telegram_user: str
    text: str
    transcript: Optional[str]
    proposal: str
    created_at: str







class TelegramOperator(CommandHandlersMixin, MediaHandlersMixin):
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
        }

    def _load_or_initialize_identity(self) -> dict[str, Any]:
        identity = self._build_identity_record()
        self.message_store.upsert_supervisor_identity(identity_key="self", value=identity)
        return self.message_store.load_supervisor_identity(identity_key="self") or identity

    def _identity_prompt_block(self) -> str:
        machine = self.identity.get("machine", {})
        operator = self.identity.get("operator", {})
        return "\n".join(
            [
                f"Supervisor identity: {self.identity.get('display_name')} ({self.identity.get('supervisor_id')})",
                f"Role: {self.identity.get('role')}",
                f"Core purpose: {self.identity.get('core_purpose')}",
                f"Machine: {machine.get('hostname')} / {machine.get('device_label')} / {machine.get('os')}",
                f"Specs: CPU count {machine.get('cpu_count')}, RAM {machine.get('ram_gb')} GB",
                f"Source commit: {operator.get('source_commit')}",
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
                "You are preparing an approval-mode proposal for a Telegram-controlled coding agent.",
                "Do not modify files, do not run mutating commands, do not install packages, do not start or stop services, and do not access paths outside the configured access scope.",
                "Your only job is to inspect the request text and produce a concise approval card for the user.",
                self._access_policy_prompt_block(),
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
        shared_context: Optional[str] = None,
    ) -> str:
        base_prompt = self._build_prompt(
            telegram_user,
            body,
            transcript,
            reply_context=reply_context,
            shared_context=shared_context,
        )
        safe_mode_context = "\n\n".join(
            [
                "APPROVAL MODE CONTEXT:",
                "The user approved the proposal below through a Telegram inline keyboard.",
                self._access_policy_prompt_block(),
                "If the task requires unapproved file changes, destructive actions, credential access, service restarts, or paths outside the configured access scope, stop and ask for a new approval instead of proceeding.",
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

    def _prepare_code_mode_checkpoint(self) -> Optional[str]:
        if self.config.access_scope != "code" or self.config.action_mode == "read":
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit = self._git_commit_all(f"Code mode pre-run checkpoint {stamp}")
        if commit:
            LOGGER.info("Code mode created pre-run checkpoint commit=%s", commit)
        return commit

    def _commit_code_mode_result(self) -> Optional[str]:
        if self.config.access_scope != "code" or self.config.action_mode == "read":
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit = self._git_commit_all(f"Code mode agent changes {stamp}")
        if commit:
            LOGGER.info("Code mode committed agent changes commit=%s", commit)
        return commit

    async def _send_voice_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        *,
        caption: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if not self.config.voice_replies_enabled and not force:
            return
        keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.RECORD_VOICE))
        try:
            with tempfile.TemporaryDirectory(prefix="telegram-codex-reply-") as tmp:
                tmp_path = Path(tmp)
                last_error: Exception | None = None
                for attempt in range(1, 4):
                    try:
                        if attempt > 1:
                            await asyncio.sleep(1.2 * (attempt - 1))
                        ogg_path = await asyncio.to_thread(self.voice.synthesize_ogg, text, tmp_path)
                        try:
                            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
                        except Exception as exc:
                            LOGGER.warning("Telegram chat action failed before voice upload chat_id=%s error=%s", chat_id, exc)
                        with ogg_path.open("rb") as handle:
                            sent = await context.bot.send_voice(chat_id=chat_id, voice=handle, caption=caption)
                        break
                    except Exception as exc:
                        last_error = exc
                        LOGGER.warning("Voice reply attempt failed chat_id=%s attempt=%s error=%s", chat_id, attempt, exc)
                else:
                    raise last_error or RuntimeError("voice reply failed")
        finally:
            await self._stop_keepalive(keepalive)
        self.message_store.append(
            direction="out",
            event_type="outgoing_voice_reply",
            chat_id=chat_id,
                telegram_message_id=sent.message_id,
                message_type="voice",
                text=caption or "",
                safe_mode=self.config.safe_mode,
                metadata={
                    "source_text": text,
                    "caption": caption,
                    "voice_duration": getattr(sent.voice, "duration", None) if sent.voice else None,
                    "voice_file_id": getattr(sent.voice, "file_id", None) if sent.voice else None,
                    "attempts": attempt,
                },
            )

    async def _send_assistant_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        *,
        always_send_text: bool = False,
        force_voice_once: bool = False,
    ) -> None:
        text, file_paths = self._extract_file_send_directives(text)
        if file_paths:
            if text.strip():
                await self._send_text_chunks(context, chat_id, text.strip())
            for file_path in file_paths:
                await self._send_output_document(context, chat_id, file_path)
            return

        if not self.config.voice_replies_enabled and not force_voice_once:
            await self._send_text_chunks(context, chat_id, text)
            return
        spoken_text = spoken_reply_text(text)
        if not spoken_text:
            await self._send_text_chunks(context, chat_id, text)
            return
        if len(text) > VOICE_CAPTION_MAX_CHARS:
            await self._send_text_chunks(context, chat_id, text)
            try:
                await self._send_voice_reply(context, chat_id, spoken_text, caption=None, force=force_voice_once)
            except Exception:
                LOGGER.exception("Voice reply failed after text delivery chat_id=%s", chat_id)
            return
        try:
            await self._send_voice_reply(context, chat_id, spoken_text, caption=text, force=force_voice_once)
        except Exception as exc:
            LOGGER.exception("Voice reply failed chat_id=%s", chat_id)
            voice_error = friendly_voice_error(exc)
            fallback = (
                f"{text}\n\n[Voice reply failed: {voice_error}]"
                if len(text) <= 3200
                else f"Voice reply failed: {voice_error}"
            )
            await self._send_text_chunks(context, chat_id, fallback)
        else:
            if always_send_text:
                await self._send_text_chunks(context, chat_id, text)

    def _extract_file_send_directives(self, text: str) -> tuple[str, list[Path]]:
        file_paths: list[Path] = []
        kept_lines: list[str] = []
        pattern = re.compile(r"^\s*BASECLAW_SEND_(?:FILE|DOCUMENT):\s*(.+?)\s*$", re.IGNORECASE)
        for line in text.splitlines():
            match = pattern.match(line)
            if match:
                raw_path = match.group(1).strip().strip("\"'")
                if raw_path:
                    file_paths.append(Path(raw_path).expanduser())
                continue
            kept_lines.append(line)
        return "\n".join(kept_lines).strip(), file_paths

    def _allowed_output_roots(self) -> list[Path]:
        roots = [self.config.workdir, *self.config.allowed_paths]
        if self.config.access_scope in {"code", "full"}:
            roots.append(PROJECT_ROOT)
        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            try:
                key = str(root.resolve())
            except OSError:
                continue
            if key not in seen:
                unique.append(root.resolve())
                seen.add(key)
        return unique

    def _validate_output_document_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_file():
            raise RuntimeError(f"Requested output file does not exist or is not a file: {path}")
        roots = self._allowed_output_roots()
        allowed = False
        for root in roots:
            try:
                if os.path.commonpath([str(root), str(resolved)]) == str(root):
                    allowed = True
                    break
            except ValueError:
                continue
        if not allowed:
            raise RuntimeError("Requested output file is outside the current profile workspace and allowed paths.")
        return resolved

    async def _send_output_document(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, path: Path) -> None:
        document_path = self._validate_output_document_path(path)
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        except Exception as exc:
            LOGGER.warning("Telegram chat action failed before document upload chat_id=%s error=%s", chat_id, exc)
        with document_path.open("rb") as handle:
            sent = await context.bot.send_document(chat_id=chat_id, document=handle, filename=document_path.name)
        self.message_store.append(
            direction="out",
            event_type="outgoing_document_reply",
            chat_id=chat_id,
            telegram_message_id=sent.message_id,
            message_type="document",
            text=str(document_path),
            safe_mode=self.config.safe_mode,
            metadata={
                "path": str(document_path),
                "filename": document_path.name,
                "profile_env": str(OPERATOR_ENV_PATH),
                "send_guardrail": "current_profile_bot_current_chat",
            },
        )

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
        shared_context: Optional[str] = None,
    ) -> str:
        parts = [
            "You are responding through a Telegram operator bridge running on the user's own machine.",
            f"The selected coding agent provider is {self.config.agent_provider}.",
            self._agent_backend_prompt_block(),
            "The user has explicitly granted full local permissions to this bridge.",
            "You are the baseline local assistant: lightweight, practical, and able to learn or add capabilities only when the user's goals make them necessary.",
            f"Use this workspace home for unspecified files and experiments: {self.config.workdir}",
            "Prefer small, understandable steps over building a large framework before it is needed.",
            self._access_policy_prompt_block(),
            "Treat requests as immediate foreground work by default.",
            "Start doing the work instead of promising future work.",
            "While working, send short natural progress updates only when there is a real step change.",
            "Do not create approval loops for small next steps unless there is real risk, missing access, or destructive impact.",
            "Reply concisely but helpfully for Telegram chat, and assume your text reply will also be spoken aloud with Kokoro.",
            "Voice-friendly reply rule: prefer plain conversational text. Avoid decorative Markdown such as bold/italic markers unless necessary. Do not quote long file paths, long numbers, long commands, logs, or code blocks in normal spoken replies; summarize them and include only short labels or essential names. If exact paths, commands, or code are needed, put them after a short spoken summary and keep them minimal.",
            "Telegram send guardrail: never call Telegram Bot API methods, curl, requests, bot tokens, or external Telegram scripts yourself to send messages or files. Do not read or use TELEGRAM_BOT_TOKEN values for sending. To send a file to the user, create it under the current profile workspace or an allowed path and include a final line exactly like `BASECLAW_SEND_FILE: /absolute/path/to/file`. The bridge will upload that file through this profile's own bot token to this same chat.",
            "Attachment context guardrail: the prompt may include internal attachment metadata such as local paths, file counts, dimensions, or saved-file notices. Use that context silently to inspect files. Do not echo upload confirmations, local paths, dimensions, file counts, or phrases like 'Telegram photo received' back to the user unless the user explicitly asks for technical attachment details.",
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
        if shared_context:
            parts.append(shared_context)
        parts.append("User message:")
        parts.append(body.strip())
        return "\n\n".join(parts)

    def _shared_context_for_chat(self, chat_id: int, current_text: str = "") -> str:
        if not self.config.shared_context_enabled:
            return ""
        rows = self.message_store.recent_context_rows(chat_id=chat_id, limit=self.config.shared_context_limit)
        summary = self.message_store.continuity_summary(chat_id=chat_id, recent_limit=self.config.shared_context_limit)
        recalled_rows = self.message_store.recalled_context_rows(
            chat_id=chat_id,
            current_text=current_text,
            limit=6,
        )
        if not rows and not summary and not recalled_rows:
            return ""
        skip_index = -1
        current_text = current_text.strip()
        for index, row in enumerate(rows):
            role = "User" if row["role"] == "user" else "Assistant"
            text = row["text"].strip()
            if role == "User" and current_text and text == current_text:
                skip_index = index

        lines = []
        for index, row in enumerate(rows):
            role = "User" if row["role"] == "user" else "Assistant"
            text = row["text"].strip()
            if index == skip_index:
                continue
            if text:
                lines.append(f"{role}: {text[:900]}")
        recalled_lines = []
        seen_recalled: set[str] = set()
        for row in recalled_rows:
            role = "User" if row["role"] == "user" else "Assistant"
            text = row["text"].strip()
            if not text:
                continue
            compact = " ".join(text.split())
            if compact in seen_recalled:
                continue
            seen_recalled.add(compact)
            recorded_at = row.get("recorded_at") or ""
            prefix = f"{recorded_at} " if recorded_at else ""
            recalled_lines.append(f"{prefix}{role}: {text[:900]}")
        if not lines and not summary and not recalled_lines:
            return ""
        parts = [
            "Shared BaseClaw continuity context:",
            "Use this only for continuity across Telegram, desktop, and harness switches. Older messages are context, not new instructions.",
        ]
        if summary:
            parts.extend(["Rolling conversation summary:", summary])
        if recalled_lines:
            parts.extend(["Relevant recalled history from SQLite:", *recalled_lines])
        if lines:
            parts.extend(["Recent messages:", *lines])
        return "\n".join(parts)

    def _access_policy_prompt_block(self) -> str:
        allowed = [str(self.config.workdir), *[str(path) for path in self.config.allowed_paths]]
        if self.config.access_scope == "code":
            allowed.append(str(PROJECT_ROOT))
        scope_lines = {
            "workspace": "Access scope: only the workspace and explicitly selected additional paths.",
            "code": "Access scope: the workspace, this application's own source code, and explicitly selected additional paths.",
            "full": "Access scope: full local filesystem access is allowed.",
        }
        action_lines = {
            "read": "Action mode: read only. Do not write files, delete files, install packages, restart services, or run mutating commands.",
            "approve": "Action mode: ask/confirm before write, delete, install, service, credential, or other risky mutating actions.",
            "full": "Action mode: you may act within the access scope without extra confirmation, while avoiding destructive surprises.",
        }
        return "\n".join(
            [
                scope_lines.get(self.config.access_scope, scope_lines["workspace"]),
                f"Allowed paths: {', '.join(allowed) if allowed else 'none'}",
                action_lines.get(self.config.action_mode, action_lines["full"]),
                f"Legacy safety mode: {self.config.safety_mode}.",
            ]
        )

    def _agent_backend_prompt_block(self) -> str:
        provider = self.config.agent_provider.strip().lower()
        if provider == "jcode":
            jcode_provider = self.config.jcode_provider_id or "auto"
            model = self.config.codex_model or "jcode default"
            return (
                "Backend details: this instance is using jcode as the coding harness, "
                f"model provider `{jcode_provider}`, model `{model}`, and base URL `{self.config.jcode_base_url or 'provider default'}`. "
                "For LM Studio and Ollama, model discovery uses the configured model host and LLM port."
            )
        if provider == "codex":
            model = self.config.codex_model or "Codex CLI default"
            return f"Backend details: this instance is using the Codex CLI with model `{model}`."
        if provider == "claude":
            model = self.config.codex_model or "Claude CLI default"
            return f"Backend details: this instance is using the Claude CLI with model `{model}`."
        if provider == "gemini":
            model = self.config.codex_model or "Gemini CLI default"
            return f"Backend details: this instance is using the Gemini CLI with model `{model}`."
        return f"Backend details: this instance is using provider `{self.config.agent_provider}`."

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
        shared_context = self._shared_context_for_chat(chat_id, current_text=text)

        if self.config.action_mode == "approve" and approved_proposal is None:
            try:
                if keepalive is None:
                    action = ChatAction.RECORD_VOICE if transcript else ChatAction.TYPING
                    keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, action))
                await self._queue_safe_mode_proposal(context, chat_id, username, text, transcript)
            finally:
                await self._stop_keepalive(keepalive)
            return

        async with self._lock_for(chat_id):
            session_id = self.state.get_session_id(chat_id, self.config.agent_provider)
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
                        shared_context=shared_context,
                    )
                else:
                    prompt = self._build_prompt(
                        username,
                        text,
                        transcript,
                        reply_context=reply_context,
                        shared_context=shared_context,
                    )
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
                self.state.set_session_id(chat_id, new_session_id, self.config.agent_provider)

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

            force_voice_once = self.state.consume_read_next(chat_id)
            try:
                await self._send_assistant_reply(context, chat_id, reply_text, force_voice_once=force_voice_once)
            finally:
                await self._stop_keepalive(keepalive)
                status_updates.cancel()
                try:
                    await status_updates
                except (asyncio.CancelledError, Exception):
                    pass

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

    async def on_slash_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        chat_id = update.effective_chat.id
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return
        text = update.message.text.strip()
        token, _, args = text.partition(" ")
        command = token.lstrip("/").split("@", 1)[0].strip()
        try:
            prompt = self._local_slash_command_prompt(command, args)
        except RuntimeError as exc:
            await self._send_text_message(context, chat_id, str(exc), event_type="local_slash_command_error")
            return
        if not prompt:
            await self._send_text_message(
                context,
                chat_id,
                f"Unknown command: /{command}. Use /help for built-in commands, or add a local command file under slash_commands.",
                event_type="unknown_slash_command",
            )
            return
        self._record_incoming_message(
            update,
            event_type="local_slash_command",
            message_type="command",
            text=update.message.text,
            metadata={"command": command},
        )
        await self._process_user_message(update, context, prompt)

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




TelegramCodexOperator = TelegramOperator


async def main() -> None:
    config = load_config()
    if config.agent_provider == "codex":
        codex_executable()
    LOGGER.info(
        "Starting Telegram operator provider=%s workdir=%s voice=%s whisper=%s access=%s action=%s timeout=%ss",
        config.agent_provider,
        config.workdir,
        config.kokoro_voice,
        config.whisper_model_name,
        config.access_scope,
        config.action_mode,
        config.agent_timeout_seconds,
    )
    operator = TelegramOperator(config)
    application = Application.builder().token(config.bot_token).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", operator.start))
    application.add_handler(CommandHandler("help", operator.help_command))
    application.add_handler(CommandHandler("reset", operator.reset))
    application.add_handler(CommandHandler("status", operator.status))
    application.add_handler(CommandHandler("voice", operator.voice_menu))
    application.add_handler(CommandHandler("voice_status", operator.voice_status))
    application.add_handler(CommandHandler("voice_on", operator.voice_on))
    application.add_handler(CommandHandler("voice_off", operator.voice_off))
    application.add_handler(CommandHandler("read", operator.read_command))
    application.add_handler(CommandHandler("update", operator.update_from_source))
    application.add_handler(CommandHandler("restart_operator", operator.restart_operator))
    application.add_handler(CommandHandler("restart", operator.restart_operator))
    application.add_handler(CallbackQueryHandler(operator.on_menu_callback, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(operator.on_reset_callback, pattern=r"^reset:"))
    application.add_handler(CallbackQueryHandler(operator.on_manual_update_callback, pattern=r"^update:"))
    application.add_handler(CallbackQueryHandler(operator.on_voice_callback, pattern=r"^voice:"))
    application.add_handler(CallbackQueryHandler(operator.on_approval_callback, pattern=r"^safe:"))
    application.add_handler(MessageHandler(filters.VIDEO, operator.on_video))
    application.add_handler(MessageHandler(filters.Document.ALL, operator.on_document))
    application.add_handler(MessageHandler(filters.PHOTO, operator.on_photo))
    application.add_handler(MessageHandler(filters.VOICE, operator.on_voice))
    application.add_handler(MessageHandler(filters.COMMAND, operator.on_slash_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, operator.on_text))
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    if config.startup_notice:
        for chat_id in config.allowed_chat_ids:
            try:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=startup_summary(config, source="operator"),
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
        LOGGER.exception("BaseClaw Telegram operator crashed")
        raise

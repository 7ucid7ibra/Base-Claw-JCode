from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

import requests
from operator_core.commands import (
    attachments_help_text,
    commands_help_text,
    help_menu_keyboard,
    help_menu_text,
    manual_update_keyboard,
    read_help_text,
    reset_confirmation_keyboard,
    status_text,
    voice_menu_markup,
    voice_status_text,
    workspace_help_text,
)
from operator_core.config import codex_executable, parse_bool, startup_summary, update_operator_env
from operator_core.updates import UpdateLifecycleMixin
from speech import infer_kokoro_lang_code, readable_message_text, spoken_reply_text
from telegram import Update
from telegram.ext import ContextTypes

LOGGER = logging.getLogger("telegram_operator")


def friendly_voice_error(exc: Exception) -> str:
    detail = str(exc)
    lower = detail.lower()
    if "timed out" in lower or "connecttimeout" in lower:
        return "STT/TTS host timed out. Check the Host IP and STT/TTS port, or turn voice replies off for this test."
    if "connection refused" in lower or "failed to establish" in lower:
        return "STT/TTS host is not accepting connections. Start the speech server, fix the host/port, or turn voice replies off."
    if "all kokoro hosts failed" in lower:
        return "All STT/TTS hosts failed. Check the speech server settings or turn voice replies off."
    return detail[:500]


class CommandHandlersMixin(UpdateLifecycleMixin):
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
            startup_summary(self.config, source="operator"),
            event_type="command_start_reply",
        )
        await self._send_voice_reply(context, update.effective_chat.id, "BaseClaw operator is online.")

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
        await self._send_text_message(
            context,
            chat_id,
            "Reset clears this chat's persisted sessions for all harnesses. Are you sure?",
            event_type="command_reset_confirm",
            reply_markup=reset_confirmation_keyboard(),
        )

    def _status_text(self, chat_id: int) -> str:
        session_id = self.state.get_session_id(chat_id, self.config.agent_provider)
        try:
            codex_status = f"available at {codex_executable()}"
        except RuntimeError as exc:
            codex_status = str(exc)
        return status_text(self.config, session_id=session_id, codex_status=codex_status)

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
        await self._send_text_message(
            context,
            chat_id,
            help_menu_text(),
            event_type="command_help_reply",
            reply_markup=help_menu_keyboard(),
        )

    def _voice_status_text(self) -> str:
        return voice_status_text(self.config)

    def _read_help_text(self) -> str:
        return read_help_text()

    def _commands_help_text(self) -> str:
        return commands_help_text()

    def _attachments_help_text(self) -> str:
        return attachments_help_text()

    def _workspace_help_text(self, chat_id: int) -> str:
        session_id = self.state.get_session_id(chat_id, self.config.agent_provider)
        return workspace_help_text(self.config, session_id=session_id, allowed_paths=self.config.allowed_paths)

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
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Current voice: {self.config.kokoro_voice}\nChoose a Kokoro voice:",
            reply_markup=voice_menu_markup(voices, self.config.kokoro_voice),
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
            self._voice_status_text(),
            event_type="command_voice_status_reply",
        )

    async def voice_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_voice_replies(update, context, enabled=True)

    async def voice_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_voice_replies(update, context, enabled=False)

    def _read_text_from_replied_message(self, update: Update) -> str:
        if not update.message or not update.message.reply_to_message or not update.effective_chat:
            return ""
        reply = update.message.reply_to_message
        record = self.message_store.find_by_telegram_message_id(
            chat_id=update.effective_chat.id,
            telegram_message_id=reply.message_id,
        )
        if record:
            return readable_message_text(
                record.get("text"),
                record.get("transcript"),
                json.dumps(record.get("metadata") or {}, ensure_ascii=True),
            )
        for value in (getattr(reply, "text", None), getattr(reply, "caption", None)):
            if value and value.strip():
                return value.strip()
        return ""

    async def _send_read_voice(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> bool:
        spoken_text = spoken_reply_text(text)
        if not spoken_text:
            await self._send_text_message(context, chat_id, "I could not find readable text for that message.", event_type="command_read_empty")
            return False
        try:
            await self._send_voice_reply(context, chat_id, spoken_text, caption=None, force=True)
            return True
        except Exception as exc:
            LOGGER.exception("/read voice generation failed chat_id=%s", chat_id)
            await self._send_text_message(
                context,
                chat_id,
                f"I could not create the voice note: {friendly_voice_error(exc)}",
                event_type="command_read_error",
            )
            return False

    async def read_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        message_text = update.effective_message.text if update.effective_message else "/read"
        self._record_incoming_message(
            update,
            event_type="command_read",
            message_type="command",
            text=message_text,
        )
        if not self._authorized(chat_id):
            await self._send_text_message(context, chat_id, "Unauthorized chat.", event_type="unauthorized_chat")
            return

        _, _, args = (message_text or "/read").partition(" ")
        args_lower = " ".join(args.lower().strip().split())

        reply_text = self._read_text_from_replied_message(update)
        if reply_text:
            await self._send_read_voice(context, chat_id, reply_text)
            return

        if args_lower in {"next", "next response", "the next response", "my next response", "read next"}:
            self.state.arm_read_next(chat_id)
            await self._send_text_message(
                context,
                chat_id,
                "Okay. I will read my next reply once, then go back to normal text replies.",
                event_type="command_read_next_armed",
            )
            return

        latest = self.message_store.latest_assistant_reply_text(chat_id=chat_id)
        if not latest:
            await self._send_text_message(
                context,
                chat_id,
                "I do not have a previous assistant reply to read yet.",
                event_type="command_read_no_previous",
            )
            return
        await self._send_read_voice(context, chat_id, latest)

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

    async def update_from_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await self._send_text_message(
            context,
            chat_id,
            self._manual_update_summary(ref),
            event_type="command_update_confirm",
            reply_markup=manual_update_keyboard(),
        )

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
        await query.edit_message_text("Cleared the persisted sessions for this chat.")

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
        if action == "voice_status":
            await self._send_text_message(
                context,
                chat_id,
                self._voice_status_text(),
                event_type="menu_voice_status_reply",
            )
            return
        if action == "voice_on" or action == "voice_off":
            enabled = action == "voice_on"
            self.config.voice_replies_enabled = enabled
            await asyncio.to_thread(
                update_operator_env,
                {"TELEGRAM_OPERATOR_VOICE_REPLIES_ENABLED": "true" if enabled else "false"},
            )
            await self._send_text_message(
                context,
                chat_id,
                f"Voice replies {'enabled' if enabled else 'disabled'}. This is active now and saved for restart.",
                event_type=f"menu_{action}_reply",
            )
            return
        if action == "read":
            await self._send_text_message(context, chat_id, self._read_help_text(), event_type="menu_read_help")
            return
        if action == "commands":
            await self._send_text_message(context, chat_id, self._commands_help_text(), event_type="menu_commands_help")
            return
        if action == "attachments":
            await self._send_text_message(context, chat_id, self._attachments_help_text(), event_type="menu_attachments_help")
            return
        if action == "workspace":
            await self._send_text_message(context, chat_id, self._workspace_help_text(chat_id), event_type="menu_workspace_help")
            return
        if action == "update":
            ref = self._manual_update_ref()
            self.pending_manual_updates[chat_id] = ref
            await self._send_text_message(
                context,
                chat_id,
                self._manual_update_summary(ref),
                event_type="menu_update_confirm",
                reply_markup=manual_update_keyboard(),
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
        await query.answer("Updating from source.")
        result = await asyncio.to_thread(self._pull_manual_update, ref)
        await self._send_text_message(context, chat_id, result, event_type="manual_update_result")
        try:
            await query.edit_message_text(result[:3900])
        except Exception:
            LOGGER.exception("Failed to edit manual update callback message chat_id=%s", chat_id)


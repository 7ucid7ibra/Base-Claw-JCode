from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from operator_core.config import OperatorConfig


def reset_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, reset session", callback_data="reset:confirm"),
                InlineKeyboardButton("Cancel", callback_data="reset:cancel"),
            ]
        ]
    )


def manual_update_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, update", callback_data="update:confirm"),
                InlineKeyboardButton("Cancel", callback_data="update:cancel"),
            ]
        ]
    )


def help_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Status", callback_data="menu:status"),
                InlineKeyboardButton("Commands", callback_data="menu:commands"),
            ],
            [
                InlineKeyboardButton("Voice picker", callback_data="menu:voice"),
                InlineKeyboardButton("Voice on", callback_data="menu:voice_on"),
                InlineKeyboardButton("Voice off", callback_data="menu:voice_off"),
            ],
            [
                InlineKeyboardButton("Read help", callback_data="menu:read"),
                InlineKeyboardButton("Attachments", callback_data="menu:attachments"),
            ],
            [
                InlineKeyboardButton("Update", callback_data="menu:update"),
                InlineKeyboardButton("Restart", callback_data="menu:restart"),
            ],
            [
                InlineKeyboardButton("Reset session", callback_data="menu:reset"),
                InlineKeyboardButton("Workspace", callback_data="menu:workspace"),
            ],
        ]
    )


def help_menu_text() -> str:
    return (
        "BaseClaw command center.\n\n"
        "Use the buttons for common controls, or send a command directly.\n\n"
        "Most used commands:\n"
        "/status - show current setup\n"
        "/read - make a voice note from my last reply\n"
        "/read next - read only my next reply\n"
        "/voice_on and /voice_off - control automatic voice replies\n"
        "/update - pull the configured source update\n"
        "/restart - restart the Telegram operator\n"
        "/reset - clear this chat's agent session"
    )


def status_text(config: OperatorConfig, *, session_id: str | None, codex_status: str) -> str:
    return (
        f"Provider: {config.agent_provider}\n"
        f"Model provider: {config.jcode_provider_id or 'n/a'}\n"
        f"Model/base URL: {config.codex_model or 'default'} / {config.jcode_base_url or 'provider default'}\n"
        f"Workdir: {config.workdir}\n"
        f"Session: {session_id or 'none'}\n"
        f"Access scope: {config.access_scope}\n"
        f"Action mode: {config.action_mode}\n"
        f"Shared context: {'on' if config.shared_context_enabled else 'off'}\n"
        f"Allowed paths: {', '.join(str(path) for path in config.allowed_paths) or 'none'}\n"
        f"Legacy safety mode: {config.safety_mode}\n"
        f"Codex: {codex_status}\n"
        f"Voice: {config.kokoro_voice}\n"
        f"Whisper model: {config.whisper_model_name}\n"
        f"Speech hosts: {', '.join(config.whisper_urls) or 'none'}\n"
        f"Voice replies: {'on' if config.voice_replies_enabled else 'off'}\n"
        f"Local speech fallback: {config.local_speech_fallback}"
    )


def voice_status_text(config: OperatorConfig) -> str:
    return (
        f"Automatic voice replies: {'on' if config.voice_replies_enabled else 'off'}\n"
        f"Current voice: {config.kokoro_voice}\n\n"
        "/voice opens the voice picker.\n"
        "/voice_on enables automatic voice replies.\n"
        "/voice_off keeps replies as text unless you use /read."
    )


def read_help_text() -> str:
    return (
        "Read commands:\n\n"
        "/read\n"
        "Creates a voice note from my latest assistant reply.\n\n"
        "Reply to a message with /read\n"
        "Creates a voice note from that replied-to message.\n\n"
        "/read next\n"
        "Reads only my next reply as a voice note, then goes back to normal text replies.\n\n"
        "This works even when automatic voice replies are off."
    )


def commands_help_text() -> str:
    return (
        "Built-in commands:\n\n"
        "/start - show startup/status summary\n"
        "/help - open this command center\n"
        "/status - show provider, model, paths, session, voice, and safety settings\n"
        "/reset - clear this chat's persisted agent session after confirmation\n"
        "/voice - choose a Kokoro voice\n"
        "/voice_status - show current voice settings\n"
        "/voice_on - enable automatic voice replies\n"
        "/voice_off - disable automatic voice replies\n"
        "/read - create a voice note from a previous or next reply\n"
        "/update - pull the configured BaseClaw source update after confirmation\n"
        "/restart - restart the Telegram operator\n"
        "/restart_operator - same as /restart"
    )


def attachments_help_text() -> str:
    return (
        "Attachments:\n\n"
        "You can send text, voice notes, photos, PDFs, documents, and videos.\n\n"
        "Voice notes are transcribed before they are passed into the agent.\n"
        "Images and files are saved locally and included as context.\n"
        "Replying to an older message gives me that message as context.\n\n"
        "For file delivery back to you, I create the file locally and hand it to the Telegram bridge."
    )


def workspace_help_text(
    config: OperatorConfig,
    *,
    session_id: str | None,
    allowed_paths: list[Path],
) -> str:
    allowed_paths_text = ", ".join(str(path) for path in allowed_paths) or "none"
    return (
        "Workspace and safety:\n\n"
        f"Workdir: {config.workdir}\n"
        f"Session: {session_id or 'none'}\n"
        f"Access scope: {config.access_scope}\n"
        f"Action mode: {config.action_mode}\n"
        f"Legacy safety mode: {config.safety_mode}\n"
        f"Allowed paths: {allowed_paths_text}\n\n"
        "Local skills, automations, scratch work, uploads, and artifacts belong in the agent workspace, not in public BaseClaw core."
    )


def voice_menu_markup(voices: list[str], current_voice: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            f"{'✓ ' if voice == current_voice else ''}{voice}",
            callback_data=f"voice:set:{voice}",
        )
        for voice in voices[:48]
    ]
    return InlineKeyboardMarkup([buttons[index : index + 2] for index in range(0, len(buttons), 2)])

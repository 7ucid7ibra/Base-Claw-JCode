from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from operator_core.attachments import (
    extract_pdf_text,
    is_text_document,
    is_video_file,
    read_text_document,
    safe_filename,
)
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

PHOTO_ALBUM_SETTLE_SECONDS = 1.5
LOGGER = logging.getLogger("telegram_operator")


class MediaHandlersMixin:
    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.document:
            return
        chat_id = update.effective_chat.id
        document = update.message.document
        filename = document.file_name or "document.pdf"
        mime_type = document.mime_type or ""
        is_pdf = mime_type.lower() == "application/pdf" or filename.lower().endswith(".pdf")
        is_video_document = is_video_file(filename, mime_type)
        is_text_file = is_text_document(filename, mime_type)
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
        if is_text_file and not is_pdf:
            keepalive = asyncio.create_task(self._chat_action_keepalive(context, chat_id, ChatAction.TYPING))
            try:
                upload_dir = self.config.workdir / "telegram_uploads" / "documents"
                upload_dir.mkdir(parents=True, exist_ok=True)
                saved_name = f"{update.message.message_id}_{safe_filename(filename)}"
                text_path = upload_dir / saved_name
                telegram_file = await context.bot.get_file(document.file_id)
                await telegram_file.download_to_drive(custom_path=str(text_path))
                text_content, truncated = await asyncio.to_thread(read_text_document, text_path)
                document_metadata.update(
                    {
                        "saved_path": str(text_path),
                        "extracted_chars": len(text_content),
                        "truncated": truncated,
                    }
                )
                self.message_store.append(
                    direction="in",
                    event_type="text_document_saved",
                    chat_id=chat_id,
                    telegram_message_id=update.message.message_id,
                    telegram_user_id=update.effective_user.id if update.effective_user else None,
                    telegram_username=update.effective_user.username if update.effective_user else None,
                    telegram_full_name=update.effective_user.full_name if update.effective_user else None,
                    message_type="text_document",
                    text=update.message.caption,
                    safe_mode=self.config.safe_mode,
                    metadata=document_metadata,
                )
            except Exception as exc:
                LOGGER.exception("Text document handling failed chat_id=%s", chat_id)
                await self._stop_keepalive(keepalive)
                await self._send_text_message(
                    context,
                    chat_id,
                    f"Text file handling failed before it reached Codex: {exc}",
                    event_type="text_document_failed",
                )
                return

            body = "\n\n".join(
                [
                    update.message.caption or "Please read this text file and tell me what is inside.",
                    "<internal_attachment_context>",
                    "Text document received.",
                    f"Filename: {filename}",
                    f"Saved locally as: {text_path}",
                    f"Extracted characters: {len(text_content)}",
                    "</internal_attachment_context>",
                    "Extracted text document content:",
                    text_content,
                ]
            )
            await self._process_user_message(update, context, body, keepalive=keepalive)
            return
        if not is_pdf:
            await self._send_text_message(
                context,
                chat_id,
                "I received a document, but it is not a PDF, video, or supported text file.",
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
            extracted_text, truncated = await asyncio.to_thread(extract_pdf_text, pdf_path)
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
                    "<internal_attachment_context>",
                    "PDF attachment received.",
                    f"Filename: {filename}",
                    f"Saved locally as: {pdf_path}",
                    f"Extracted characters: {extracted_chars}",
                    "</internal_attachment_context>",
                    "Extracted PDF text:",
                    extracted_text,
                ]
            )
        else:
            body = "\n\n".join(
                [
                    update.message.caption or "Please read this PDF and tell me what is inside.",
                    "<internal_attachment_context>",
                    "PDF attachment received, but no selectable text could be extracted.",
                    f"Filename: {filename}",
                    f"Saved locally as: {pdf_path}",
                    "This PDF likely needs OCR or vision analysis.",
                    "</internal_attachment_context>",
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
                "<internal_attachment_context>",
                "Telegram video received.",
                "\n".join(details),
                "Preserve the original file and use a working copy for edits.",
                "</internal_attachment_context>",
            ]
        )
        await self._process_user_message(update, context, body, keepalive=keepalive)

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
            "<internal_attachment_context>",
            f"{'Telegram photo album' if len(images) > 1 else 'Telegram photo'} received.",
            f"Saved image count: {len(images)}",
            "Saved locally as:",
            "\n".join(image_lines),
            "</internal_attachment_context>",
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

    def _local_slash_command_path(self, command: str) -> Optional[Path]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", command):
            return None
        command_dir = self.config.workdir / "slash_commands"
        for suffix in SLASH_COMMAND_EXTENSIONS:
            path = command_dir / f"{command}{suffix}"
            if path.is_file():
                return path
        return None

    def _local_slash_command_prompt(self, command: str, args: str) -> Optional[str]:
        path = self._local_slash_command_path(command)
        if not path:
            return None
        try:
            instructions = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Could not read local slash command /{command}: {exc}") from exc
        if not instructions:
            raise RuntimeError(f"Local slash command /{command} is empty: {path.name}")
        return (
            f"Local slash command invoked: /{command}\n\n"
            f"Command instructions from {path.name}:\n"
            f"{instructions}\n\n"
            f"User arguments:\n{args.strip() or '(none)'}"
        )


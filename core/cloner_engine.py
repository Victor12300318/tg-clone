from __future__ import annotations
import asyncio
import os
import io
import re
from typing import Optional, Dict, Any, List, Callable, Coroutine
from datetime import datetime

import pyrogram
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from pyrogram.errors import FloodWait, ChannelInvalid, PeerIdInvalid, RPCError

from core.config import TMP_DIR, REUPLOAD_MEMORY_LIMIT_MB
from core.database import (
    get_db_connection, get_task, update_task_status, update_task_progress, record_task_message,
    get_task_copied_message_ids, add_log
)
from core.models import TaskStatus, TaskMode, TaskResponse
from core.telegram_auth import telegram_auth


def apply_text_transformations(
    text: Optional[str],
    remove_captions: bool = False,
    remove_links: bool = False,
    remove_mentions: bool = False,
    custom_replacements: Optional[List[Dict[str, Any]]] = None,
    header_text: Optional[str] = "",
    footer_text: Optional[str] = "",
    is_pure_text: bool = False
) -> Optional[str]:
    if not text:
        # If there's only header or footer without original text
        parts = []
        if header_text and header_text.strip():
            parts.append(header_text.strip())
        if footer_text and footer_text.strip():
            parts.append(footer_text.strip())
        return "\n\n".join(parts) if parts else None

    # If remove_captions is active and this is a media caption (not pure text), strip it completely.
    # If is_pure_text is True (catalog text post), we preserve text without links!
    if remove_captions and not is_pure_text:
        parts = []
        if header_text and header_text.strip():
            parts.append(header_text.strip())
        if footer_text and footer_text.strip():
            parts.append(footer_text.strip())
        return "\n\n".join(parts) if parts else None

    result = text

    # 1. Remove links (always stripped if remove_links or remove_captions is active)
    if remove_links or remove_captions:
        result = re.sub(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.~:?#[\]@!$&\'()*+,;=]*', '', result)
        result = re.sub(r't\.me/[a-zA-Z0-9_+]+', '', result)

    # 2. Remove mentions (@username)
    if remove_mentions or remove_captions:
        result = re.sub(r'@[a-zA-Z0-9_]+', '', result)

    # 3. Custom replacements
    if custom_replacements:
        for rule in custom_replacements:
            if not rule.get("enabled", True):
                continue
            pattern = rule.get("pattern", "")
            replacement = rule.get("replacement", "")
            is_regex = rule.get("is_regex", False)

            if not pattern:
                continue

            if is_regex:
                try:
                    result = re.sub(pattern, replacement, result)
                except re.error:
                    pass
            else:
                result = result.replace(pattern, replacement)

    # 4. Cleanup extra whitespace
    result = re.sub(r'\n{3,}', '\n\n', result).strip()

    # 5. Header and Footer
    parts = []
    if header_text and header_text.strip():
        parts.append(header_text.strip())
    if result:
        parts.append(result)
    if footer_text and footer_text.strip():
        parts.append(footer_text.strip())

    return "\n\n".join(parts) if parts else None

    # 5. Header and Footer
    parts = []
    if header_text and header_text.strip():
        parts.append(header_text.strip())
    if result:
        parts.append(result)
    if footer_text and footer_text.strip():
        parts.append(footer_text.strip())

    return "\n\n".join(parts) if parts else None


def detect_media_type(message: Message) -> str:
    if message.empty or message.service or message.dice or message.location:
        return "empty"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "animation"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.poll:
        return "poll"
    if message.text:
        return "text"
    return "unknown"


def is_media_allowed(media_type: str, allowed_types: List[str]) -> bool:
    if not allowed_types or "all" in allowed_types:
        return True
    return media_type in allowed_types


def parse_chat_id(chat_str: str) -> Any:
    val = str(chat_str).strip()
    try:
        return int(val)
    except ValueError:
        return val


def _get_media_file_size(message: Message) -> Optional[int]:
    """Returns the file size in bytes for media messages, or None if unknown."""
    if not message:
        return None
    if message.photo:
        return getattr(message.photo, "file_size", None)
    if message.video:
        return getattr(message.video, "file_size", None)
    if message.document:
        return getattr(message.document, "file_size", None)
    if message.audio:
        return getattr(message.audio, "file_size", None)
    if message.voice:
        return getattr(message.voice, "file_size", None)
    if message.animation:
        return getattr(message.animation, "file_size", None)
    if message.sticker:
        return getattr(message.sticker, "file_size", None)
    if message.video_note:
        return getattr(message.video_note, "file_size", None)
    return None


def _is_forwards_restricted(exc: Exception) -> bool:
    """Checks whether an exception corresponds to Telegram's CHAT_FORWARDS_RESTRICTED error."""
    exc_str = str(exc)
    exc_name = exc.__class__.__name__
    return (
        "CHAT_FORWARDS_RESTRICTED" in exc_str
        or "ChatForwardsRestricted" in exc_name
        or "restricts forwarding" in exc_str.lower()
    )


class TaskPausedDuringWait(Exception):
    """Raised when a wait/sleep operation is interrupted because the task was paused."""
    pass


async def _interruptible_sleep(
    seconds: float,
    pause_event: Optional[asyncio.Event] = None,
    cancel_event: Optional[asyncio.Event] = None
) -> bool:
    """
    Sleeps for the given number of seconds in 0.25s slices.
    Returns True if completed naturally, or raises TaskPausedDuringWait / asyncio.CancelledError
    if paused or cancelled early.
    """
    if seconds <= 0:
        return True
    loop = asyncio.get_event_loop()
    end_time = loop.time() + seconds
    while True:
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError()
        if pause_event and not pause_event.is_set():
            raise TaskPausedDuringWait()
        remaining = end_time - loop.time()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(0.25, remaining))


class ClonerEngine:
    def __init__(self):
        self._running_tasks: Dict[int, asyncio.Task] = {}
        self._task_owners: Dict[int, int] = {}
        self._task_cancel_events: Dict[int, asyncio.Event] = {}
        self._task_pause_events: Dict[int, asyncio.Event] = {}
        self._live_sync_handlers: Dict[int, Any] = {}
        self._listeners: List[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = []
        self._reupload_warned_tasks: set[int] = set()

    def register_listener(self, callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]):
        self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]):
        if callback in self._listeners:
            self._listeners.remove(callback)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        if data.get("owner_id") is None and data.get("task_id") is not None:
            data["owner_id"] = self._task_owners.get(data["task_id"])
        payload = {"event": event_type, "data": data, "timestamp": datetime.now().strftime("%H:%M:%S")}
        for listener in self._listeners:
            try:
                await listener(payload)
            except Exception:
                pass

    async def log(self, task_id: Optional[int], level: str, message: str):
        await add_log(self._owner_of(task_id), task_id, level, message)
        await self.broadcast_event("log", {
            "task_id": task_id,
            "level": level,
            "message": message
        })

    def _owner_of(self, task_id: Optional[int]) -> Optional[int]:
        return self._task_owners.get(task_id) if task_id is not None else None

    async def _load_task(self, task_id: int):
        owner = self._owner_of(task_id)
        if owner is None:
            return None
        return await get_task(owner, task_id)

    async def _set_status(self, task_id: int, status: TaskStatus, owner_id: Optional[int] = None):
        owner = owner_id if owner_id is not None else self._owner_of(task_id)
        if owner is None:
            return
        await update_task_status(owner, task_id, status)

    async def _progress(self, task_id: int, **kwargs):
        owner = self._owner_of(task_id)
        if owner is None:
            return
        await update_task_progress(owner, task_id, **kwargs)

    async def _client(self, task_id: Optional[int]):
        owner = self._owner_of(task_id)
        if owner is None:
            return None
        return await telegram_auth.get_active_client(owner)

    def is_task_running(self, task_id: int) -> bool:
        return task_id in self._running_tasks and not self._running_tasks[task_id].done()

    async def start_task(self, owner_id: int, task_id: int) -> bool:
        task = await get_task(owner_id, task_id)
        if not task:
            return False

        if self.is_task_running(task_id):
            return True

        self._task_owners[task_id] = owner_id

        cancel_event = asyncio.Event()
        pause_event = asyncio.Event()
        pause_event.set()  # not paused by default

        self._task_cancel_events[task_id] = cancel_event
        self._task_pause_events[task_id] = pause_event

        if task.mode == TaskMode.HISTORICAL.value:
            worker = asyncio.create_task(self._run_historical_cloner(task_id, cancel_event, pause_event))
        else:
            worker = asyncio.create_task(self._run_live_sync_cloner(task_id, cancel_event, pause_event))

        self._running_tasks[task_id] = worker
        await self._set_status(task_id, TaskStatus.RUNNING)
        await self.log(task_id, "info", f"Tarefa '{task.name}' iniciada no modo {task.mode}.")
        await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.RUNNING.value})
        return True

    async def pause_task(self, owner_id: int, task_id: int) -> bool:
        if task_id in self._task_pause_events:
            self._task_pause_events[task_id].clear()
            await self._set_status(task_id, TaskStatus.PAUSED)
            await self.log(task_id, "warning", "Pausa solicitada — será efetivada após a conclusão da mensagem atual.")
            await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.PAUSED.value})
            return True
        else:
            # Handle state desync (e.g. server restarted or worker finished)
            task = await get_task(owner_id, task_id)
            if task and task.status == TaskStatus.RUNNING.value:
                await self._set_status(task_id, TaskStatus.PAUSED, owner_id=owner_id)
                await self.log(task_id, "warning", "Tarefa pausada.")
                await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.PAUSED.value})
                return True
            return False

    async def resume_task(self, owner_id: int, task_id: int) -> bool:
        if task_id in self._task_pause_events and self.is_task_running(task_id):
            self._task_pause_events[task_id].set()
            await self._set_status(task_id, TaskStatus.RUNNING)
            await self.log(task_id, "info", "Tarefa retomada pelo usuário.")
            await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.RUNNING.value})
            return True
        else:
            return await self.start_task(owner_id, task_id)

    async def sync_new_messages(self, owner_id: int, task_id: int) -> Dict[str, Any]:
        task = await get_task(owner_id, task_id)
        if not task:
            return {"success": False, "error": "Tarefa não encontrada."}

        if self.is_task_running(task_id):
            return {"success": False, "error": "A tarefa já está em execução."}

        self._task_owners[task_id] = owner_id

        client = await self._client(task_id)
        if not client:
            client = await telegram_auth.get_active_client(owner_id)
            if not client:
                return {"success": False, "error": "Nenhuma conta do Telegram conectada."}

        origin_chat = parse_chat_id(task.origin_chat)

        latest_origin_id = None
        try:
            try:
                await client.get_chat(origin_chat)
            except Exception:
                # Prime in-memory peer cache by fetching dialogs
                async for _ in client.get_dialogs(limit=100):
                    pass
                await client.get_chat(origin_chat)

            async for last_msg in client.get_chat_history(origin_chat, limit=1):
                latest_origin_id = last_msg.id
                break
        except Exception as e:
            return {"success": False, "error": f"Erro ao acessar histórico do canal de origem: {str(e)}"}

        if not latest_origin_id:
            return {"success": False, "error": "Não foi possível encontrar mensagens no canal de origem."}

        copied_ids = await get_task_copied_message_ids(task_id)
        max_copied = max(copied_ids) if copied_ids else 0
        last_processed_id = max(task.current_message_id or 0, max_copied, (task.start_message_id or 1) - 1)

        if latest_origin_id <= last_processed_id:
            return {
                "success": True,
                "new_messages": 0,
                "message": f"O canal de origem já está 100% atualizado (última mensagem ID {last_processed_id})."
            }

        new_start_id = last_processed_id + 1
        new_total_delta = latest_origin_id - last_processed_id
        original_start = task.start_message_id or 1
        new_total_scope = max(1, latest_origin_id - original_start + 1)

        # Update end_message_id and progress without moving original start_message_id
        await update_task_progress(
            owner_id,
            task_id,
            current_message_id=last_processed_id,
            total_messages=new_total_scope
        )
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE tasks SET end_message_id = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (latest_origin_id, datetime.now().isoformat(), task_id, owner_id)
            )
            await db.commit()

        started = await self.start_task(owner_id, task_id)
        if not started:
            return {"success": False, "error": "Falha ao reiniciar tarefa de clonagem."}

        await self.log(task_id, "info", f"Sincronização iniciada: {new_total_delta} novas mensagens encontradas (do ID {new_start_id} ao {latest_origin_id}).")

        return {
            "success": True,
            "new_messages": new_total_delta,
            "start_id": new_start_id,
            "end_id": latest_origin_id,
            "message": f"Sincronização iniciada: {new_total_delta} novas mensagens encontradas!"
        }

    async def cancel_task(self, owner_id: int, task_id: int) -> bool:
        self._task_owners[task_id] = owner_id
        if task_id in self._task_cancel_events:
            self._task_cancel_events[task_id].set()
        if task_id in self._task_pause_events:
            self._task_pause_events[task_id].set()  # Unblock if paused so it exits immediately

        task_obj = self._running_tasks.get(task_id)
        if task_obj and not task_obj.done():
            task_obj.cancel()

        client = await self._client(task_id)
        if client and task_id in self._live_sync_handlers:
            handler = self._live_sync_handlers.pop(task_id, None)
            if handler:
                try:
                    client.remove_handler(*handler)
                except Exception:
                    pass

        self._running_tasks.pop(task_id, None)
        self._task_cancel_events.pop(task_id, None)
        self._task_pause_events.pop(task_id, None)

        await self._set_status(task_id, TaskStatus.CANCELLED, owner_id=owner_id)
        await self.log(task_id, "warning", "Tarefa cancelada pelo usuário.")
        await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.CANCELLED.value})
        self._task_owners.pop(task_id, None)
        return True

    async def _send_media_via_reupload(
        self,
        client: Client,
        message: Message,
        dest_chat: Any,
        caption: Optional[str],
        media_type: str,
        task_id: int
    ) -> Optional[Message]:
        """
        Downloads the media and re-uploads it as a fresh message to bypass
        Telegram's CHAT_FORWARDS_RESTRICTED content protection.
        Uses in-memory RAM buffer if file size <= REUPLOAD_MEMORY_LIMIT_MB,
        otherwise uses temporary disk storage in TMP_DIR with guaranteed cleanup.
        """
        if task_id not in self._reupload_warned_tasks:
            self._reupload_warned_tasks.add(task_id)
            await self.log(task_id, "info", "Canal com proteção de conteúdo detectado: utilizando re-upload.")

        # Ensure client.me is set and has is_premium attribute to prevent Pyrogram save_file AttributeError
        if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
            try:
                client.me = await client.get_me()
            except Exception:
                pass
            if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                client.me = type("Me", (), {"is_premium": False, "id": getattr(getattr(client, "me", None), "id", 0)})()

        file_size = _get_media_file_size(message)
        max_bytes = REUPLOAD_MEMORY_LIMIT_MB * 1024 * 1024
        use_memory = file_size is not None and file_size <= max_bytes

        temp_file_path: Optional[str] = None
        media_input: Any = None

        try:
            if use_memory:
                media_input = await client.download_media(message, in_memory=True)
                if hasattr(media_input, "name") and not media_input.name:
                    ext = "jpg" if media_type == "photo" else ("mp4" if media_type in ("video", "animation") else "bin")
                    media_input.name = f"media_{message.id}.{ext}"
            else:
                temp_file_name = str(TMP_DIR / f"tg_tmp_{task_id}_{message.id}")
                temp_file_path = await client.download_media(message, file_name=temp_file_name)
                media_input = temp_file_path

            if not media_input:
                raise RuntimeError(f"Falha ao baixar mídia da mensagem {message.id} para re-upload.")

            if media_type == "photo":
                return await client.send_photo(chat_id=dest_chat, photo=media_input, caption=caption)
            elif media_type == "video":
                duration = getattr(message.video, "duration", 0) if message.video else 0
                width = getattr(message.video, "width", 0) if message.video else 0
                height = getattr(message.video, "height", 0) if message.video else 0
                return await client.send_video(
                    chat_id=dest_chat,
                    video=media_input,
                    caption=caption,
                    duration=duration,
                    width=width,
                    height=height
                )
            elif media_type == "document":
                file_name = getattr(message.document, "file_name", None) if message.document else None
                return await client.send_document(
                    chat_id=dest_chat,
                    document=media_input,
                    caption=caption,
                    file_name=file_name
                )
            elif media_type == "audio":
                title = getattr(message.audio, "title", None) if message.audio else None
                performer = getattr(message.audio, "performer", None) if message.audio else None
                duration = getattr(message.audio, "duration", 0) if message.audio else 0
                return await client.send_audio(
                    chat_id=dest_chat,
                    audio=media_input,
                    caption=caption,
                    title=title,
                    performer=performer,
                    duration=duration
                )
            elif media_type == "voice":
                duration = getattr(message.voice, "duration", 0) if message.voice else 0
                return await client.send_voice(
                    chat_id=dest_chat,
                    voice=media_input,
                    caption=caption,
                    duration=duration
                )
            elif media_type == "animation":
                return await client.send_animation(
                    chat_id=dest_chat,
                    animation=media_input,
                    caption=caption
                )
            elif media_type == "sticker":
                return await client.send_sticker(
                    chat_id=dest_chat,
                    sticker=media_input
                )
            elif media_type == "video_note":
                duration = getattr(message.video_note, "duration", 0) if message.video_note else 0
                return await client.send_video_note(
                    chat_id=dest_chat,
                    video_note=media_input,
                    duration=duration
                )
            else:
                return await client.send_document(
                    chat_id=dest_chat,
                    document=media_input,
                    caption=caption
                )
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception:
                    pass
            if use_memory and hasattr(media_input, "close"):
                try:
                    media_input.close()
                except Exception:
                    pass

    async def _send_transformed_message(
        self,
        client: Client,
        message: Message,
        dest_chat: Any,
        task: TaskResponse,
        clean_forward: bool = True,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> Optional[Message]:
        media_type = detect_media_type(message)
        original_caption = getattr(message, "caption", None) or getattr(message, "text", None)
        remove_captions = bool(getattr(task, "remove_captions", False))

        transformed_text = apply_text_transformations(
            text=original_caption,
            remove_captions=remove_captions,
            remove_links=task.remove_links,
            remove_mentions=task.remove_mentions,
            custom_replacements=task.custom_replacements,
            header_text=task.header_text,
            footer_text=task.footer_text,
            is_pure_text=(media_type == "text")
        )

        # For media copy: empty string "" explicitly clears caption, None retains source caption
        if remove_captions:
            media_caption = transformed_text if transformed_text is not None else ""
        else:
            media_caption = transformed_text if transformed_text is not None else None

        # Proactive check: if message has protected content, re-upload directly
        is_protected = bool(getattr(message, "has_protected_content", False))
        if is_protected and media_type not in ("text", "poll", "empty"):
            try:
                return await self._send_media_via_reupload(
                    client=client,
                    message=message,
                    dest_chat=dest_chat,
                    caption=transformed_text or None,
                    media_type=media_type,
                    task_id=task.id
                )
            except FloodWait as e:
                await self.log(task.id, "warning", f"FloodWait: Aguardando {e.value}s exigidos pelo Telegram...")
                await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
                return await self._send_transformed_message(client, message, dest_chat, task, clean_forward, pause_event, cancel_event)
            except Exception as e:
                await self.log(task.id, "error", f"Erro ao enviar mensagem {message.id}: {str(e)}")
                raise e

        try:
            if clean_forward:
                # Use Pyrogram's copy method which posts as a clean new message
                if media_type == "text":
                    return await client.send_message(
                        chat_id=dest_chat,
                        text=transformed_text or original_caption or " ",
                        disable_web_page_preview=False
                    )
                elif media_type == "poll":
                    return await client.send_poll(
                        chat_id=dest_chat,
                        question=message.poll.question,
                        options=[opt.text for opt in message.poll.options],
                        is_anonymous=message.poll.is_anonymous,
                        allows_multiple_answers=message.poll.allows_multiple_answers
                    )
                else:
                    # Photo, Video, Document, Audio, Voice, Animation, Sticker, Video Note
                    try:
                        return await message.copy(
                            chat_id=dest_chat,
                            caption=media_caption
                        )
                    except RPCError as e:
                        if _is_forwards_restricted(e):
                            return await self._send_media_via_reupload(
                                client=client,
                                message=message,
                                dest_chat=dest_chat,
                                caption=transformed_text or None,
                                media_type=media_type,
                                task_id=task.id
                            )
                        raise e
            else:
                # Direct Telegram native forward with source header
                try:
                    return await message.forward(chat_id=dest_chat)
                except RPCError as e:
                    if _is_forwards_restricted(e):
                        return await self._send_media_via_reupload(
                            client=client,
                            message=message,
                            dest_chat=dest_chat,
                            caption=transformed_text or None,
                            media_type=media_type,
                            task_id=task.id
                        )
                    raise e
        except FloodWait as e:
            await self.log(task.id, "warning", f"FloodWait: Aguardando {e.value}s exigidos pelo Telegram...")
            await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
            # Retry after flood wait
            return await self._send_transformed_message(client, message, dest_chat, task, clean_forward, pause_event, cancel_event)
        except Exception as e:
            await self.log(task.id, "error", f"Erro ao enviar mensagem {message.id}: {str(e)}")
            raise e

    async def _send_transformed_media_group(
        self,
        client: Client,
        origin_chat: Any,
        dest_chat: Any,
        media_group: List[Message],
        task: TaskResponse,
        transformed_caption: Optional[str] = None,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> List[Message]:
        if not media_group:
            return []

        first_msg = media_group[0]
        group_msg_ids = [m.id for m in media_group]

        # 1. Native forward if clean_forward is False
        if not task.clean_forward:
            try:
                return await client.forward_messages(
                    chat_id=dest_chat,
                    from_chat_id=origin_chat,
                    message_ids=group_msg_ids
                )
            except RPCError as e:
                if not _is_forwards_restricted(e):
                    raise e
                # Fallback to reupload if restricted

        # 2. Clean copy via copy_media_group
        try:
            return await client.copy_media_group(
                chat_id=dest_chat,
                from_chat_id=origin_chat,
                message_id=first_msg.id,
                captions=transformed_caption
            )
        except FloodWait as e:
            await self.log(task.id, "warning", f"FloodWait em álbum: Aguardando {e.value}s...")
            if pause_event and cancel_event:
                await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
            else:
                await asyncio.sleep(e.value + 1)
            return await self._send_transformed_media_group(client, origin_chat, dest_chat, media_group, task, transformed_caption, pause_event, cancel_event)
        except RPCError as e:
            if not _is_forwards_restricted(e):
                raise e
            # Fallback to reupload if restricted

        # 3. Protected content fallback: download and send_media_group
        if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
            try:
                client.me = await client.get_me()
            except Exception:
                pass
            if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                client.me = type("Me", (), {"is_premium": False, "id": getattr(getattr(client, "me", None), "id", 0)})()

        temp_files: List[str] = []
        input_media: List[Any] = []
        try:
            for idx, msg in enumerate(media_group):
                cap = transformed_caption if idx == 0 else None
                downloaded = await client.download_media(msg, in_memory=False)
                if not downloaded:
                    continue
                temp_files.append(downloaded)

                if getattr(msg, "photo", None):
                    input_media.append(InputMediaPhoto(downloaded, caption=cap))
                elif getattr(msg, "video", None):
                    input_media.append(InputMediaVideo(downloaded, caption=cap))
                elif getattr(msg, "audio", None):
                    input_media.append(InputMediaAudio(downloaded, caption=cap))
                else:
                    input_media.append(InputMediaDocument(downloaded, caption=cap))

            if input_media:
                return await client.send_media_group(chat_id=dest_chat, media=input_media)
            return []
        finally:
            for f_path in temp_files:
                if f_path and os.path.exists(f_path):
                    try:
                        os.remove(f_path)
                    except Exception:
                        pass

    async def _run_historical_cloner(self, task_id: int, cancel_event: asyncio.Event, pause_event: asyncio.Event):
        try:
            task = await self._load_task(task_id)
            if not task:
                return

            client = await self._client(task_id)
            if not client:
                await self.log(task_id, "error", "Telegram desconectado. Conecte sua conta para iniciar.")
                await self._set_status(task_id, TaskStatus.FAILED)
                return

            if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                try:
                    client.me = await client.get_me()
                except Exception:
                    pass
                if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                    client.me = type("Me", (), {"is_premium": False, "id": 0})()

            origin_chat = parse_chat_id(task.origin_chat)
            dest_chat = parse_chat_id(task.dest_chat)

            # Get origin chat info & last message id
            try:
                try:
                    origin_info = await client.get_chat(origin_chat)
                except Exception:
                    async for _ in client.get_dialogs(limit=100):
                        pass
                    origin_info = await client.get_chat(origin_chat)

                try:
                    dest_info = await client.get_chat(dest_chat)
                except Exception:
                    async for _ in client.get_dialogs(limit=100):
                        pass
                    dest_info = await client.get_chat(dest_chat)

                # Check if dest group was migrated to a supergroup
                if getattr(dest_info, "type", None) == enums.ChatType.GROUP and getattr(dest_info, "members_count", None) == 0:
                    try:
                        async for m in client.get_chat_history(dest_chat, limit=5):
                            migrated_id = getattr(m, "migrate_to_chat_id", None)
                            if migrated_id:
                                await self.log(task_id, "info", f"Grupo de destino migrado detectado. Redirecionando para supergrupo ID {migrated_id}")
                                dest_chat = migrated_id
                                dest_info = await client.get_chat(dest_chat)
                                async with get_db_connection() as db:
                                    await db.execute("UPDATE tasks SET dest_chat = ? WHERE id = ?", (str(migrated_id), task_id))
                                    await db.commit()
                                break
                    except Exception:
                        pass
            except Exception as e:
                await self.log(task_id, "error", f"Erro ao verificar canais: {str(e)}")
                await self._set_status(task_id, TaskStatus.FAILED)
                return

            # Find last message id from origin chat
            last_message_id = task.end_message_id
            if not last_message_id:
                try:
                    async for last_msg in client.get_chat_history(origin_chat, limit=1):
                        last_message_id = last_msg.id
                        break
                except Exception as e:
                    await self.log(task_id, "error", f"Falha ao obter histórico de mensagens: {str(e)}")
                    await self._set_status(task_id, TaskStatus.FAILED)
                    return

            if not last_message_id:
                last_message_id = 1

            start_id = task.start_message_id or 1
            total_msgs = max(1, last_message_id - start_id + 1)
            await self._progress(task_id, total_messages=total_msgs)

            already_copied_ids = set(await get_task_copied_message_ids(task_id))
            copied_count = task.copied_count
            skipped_count = task.skipped_count
            error_count = task.error_count
            processed_count = task.processed_messages

            await self.log(task_id, "info", f"Clonando do ID {start_id} até {last_message_id} ({total_msgs} mensagens)...")

            current_id = max(start_id, task.current_message_id) if task.current_message_id > 0 else start_id

            while current_id <= last_message_id:
                if cancel_event.is_set():
                    await self.log(task_id, "warning", "Processo cancelado.")
                    await self._set_status(task_id, TaskStatus.CANCELLED)
                    return

                # Wait if paused
                if not pause_event.is_set():
                    await pause_event.wait()
                    # Reload task from database upon resumption to apply any edits made while paused
                    fresh_task = await self._load_task(task_id)
                    if fresh_task:
                        task = fresh_task
                        origin_chat = parse_chat_id(task.origin_chat)
                        dest_chat = parse_chat_id(task.dest_chat)

                if current_id in already_copied_ids:
                    current_id += 1
                    continue

                # Fetch message
                try:
                    message = await client.get_messages(origin_chat, current_id)
                except FloodWait as e:
                    await self.log(task_id, "warning", f"FloodWait: Aguardando {e.value}s...")
                    try:
                        await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
                    except TaskPausedDuringWait:
                        await pause_event.wait()
                    continue
                except Exception as e:
                    await self.log(task_id, "warning", f"Msg {current_id}: Erro ao ler ({str(e)}). Pulando.")
                    skipped_count += 1
                    processed_count += 1
                    await record_task_message(task_id, current_id, "failed")
                    current_id += 1
                    continue

                # Check if message is part of an album (media group)
                if message and getattr(message, "media_group_id", None):
                    try:
                        media_group = await client.get_media_group(origin_chat, current_id)
                    except Exception:
                        media_group = None

                    if media_group and len(media_group) > 1:
                        group_msg_ids = sorted([m.id for m in media_group])
                        album_caption = next((m.caption for m in media_group if getattr(m, "caption", None)), None)
                        transformed_caption = apply_text_transformations(
                            text=album_caption,
                            remove_captions=bool(getattr(task, "remove_captions", False)),
                            remove_links=task.remove_links,
                            remove_mentions=task.remove_mentions,
                            custom_replacements=task.custom_replacements,
                            header_text=task.header_text,
                            footer_text=task.footer_text
                        )

                        try:
                            await pause_event.wait()
                            sent_group = await self._send_transformed_media_group(
                                client=client,
                                origin_chat=origin_chat,
                                dest_chat=dest_chat,
                                media_group=media_group,
                                task=task,
                                transformed_caption=transformed_caption,
                                pause_event=pause_event,
                                cancel_event=cancel_event
                            )

                            for m_idx, m_item in enumerate(media_group):
                                dest_id = sent_group[m_idx].id if (sent_group and m_idx < len(sent_group)) else None
                                await record_task_message(task_id, m_item.id, "copied", dest_message_id=dest_id, media_type="album")
                                already_copied_ids.add(m_item.id)

                            copied_count += len(media_group)
                            processed_count += len(media_group)
                            current_id = max(group_msg_ids)

                            await self.log(task_id, "success", f"Álbum com {len(media_group)} mídias (IDs: {min(group_msg_ids)} a {max(group_msg_ids)}) clonado com sucesso!")
                            await self._progress(
                                task_id,
                                current_message_id=current_id,
                                processed_messages=processed_count,
                                copied_count=copied_count,
                                skipped_count=skipped_count,
                                error_count=error_count
                            )
                            await self.broadcast_event("task_progress", {
                                "task_id": task_id,
                                "current": current_id,
                                "total": total_msgs,
                                "copied": copied_count,
                                "skipped": skipped_count,
                                "errors": error_count,
                                "percent": round(min(100.0, (processed_count / total_msgs) * 100), 1)
                            })
                            try:
                                await _interruptible_sleep(task.delay_seconds, pause_event, cancel_event)
                            except TaskPausedDuringWait:
                                await pause_event.wait()
                            current_id += 1
                            continue
                        except Exception as e:
                            await self.log(task_id, "error", f"Erro ao clonar álbum {group_msg_ids}: {str(e)}")

                media_type = detect_media_type(message) if message else "empty"

                if not message or media_type == "empty":
                    skipped_count += 1
                    processed_count += 1
                    await record_task_message(task_id, current_id, "skipped", media_type="empty")
                    await self._progress(
                        task_id,
                        current_message_id=current_id,
                        processed_messages=processed_count,
                        skipped_count=skipped_count
                    )
                    await self.broadcast_event("task_progress", {
                        "task_id": task_id,
                        "current": current_id,
                        "total": total_msgs,
                        "copied": copied_count,
                        "skipped": skipped_count,
                        "errors": error_count,
                        "percent": round((processed_count / total_msgs) * 100, 1)
                    })
                    try:
                        await _interruptible_sleep(task.skip_delay_seconds, pause_event, cancel_event)
                    except TaskPausedDuringWait:
                        await pause_event.wait()
                    current_id += 1
                    continue

                # Check if media type is allowed
                if not is_media_allowed(media_type, task.media_types):
                    skipped_count += 1
                    processed_count += 1
                    await record_task_message(task_id, current_id, "skipped", media_type=media_type)
                    await self._progress(
                        task_id,
                        current_message_id=current_id,
                        processed_messages=processed_count,
                        skipped_count=skipped_count
                    )
                    await self.broadcast_event("task_progress", {
                        "task_id": task_id,
                        "current": current_id,
                        "total": total_msgs,
                        "copied": copied_count,
                        "skipped": skipped_count,
                        "errors": error_count,
                        "percent": round((processed_count / total_msgs) * 100, 1)
                    })
                    try:
                        await _interruptible_sleep(task.skip_delay_seconds, pause_event, cancel_event)
                    except TaskPausedDuringWait:
                        await pause_event.wait()
                    current_id += 1
                    continue

                # If text message transforms into empty text (e.g. pure spam links stripped away), skip it.
                # If it has model name or catalog text, it passes through!
                if media_type == "text":
                    orig_text = getattr(message, "text", None) or getattr(message, "caption", None)
                    clean_text = apply_text_transformations(
                        text=orig_text,
                        remove_captions=bool(getattr(task, "remove_captions", False)),
                        remove_links=task.remove_links,
                        remove_mentions=task.remove_mentions,
                        custom_replacements=task.custom_replacements,
                        header_text=task.header_text,
                        footer_text=task.footer_text
                    )
                    if not clean_text:
                        skipped_count += 1
                        processed_count += 1
                        await record_task_message(task_id, current_id, "skipped", media_type="text")
                        await self._progress(
                            task_id,
                            current_message_id=current_id,
                            processed_messages=processed_count,
                            skipped_count=skipped_count
                        )
                        await self.broadcast_event("task_progress", {
                            "task_id": task_id,
                            "current": current_id,
                            "total": total_msgs,
                            "copied": copied_count,
                            "skipped": skipped_count,
                            "errors": error_count,
                            "percent": round((processed_count / total_msgs) * 100, 1)
                        })
                        try:
                            await _interruptible_sleep(task.skip_delay_seconds, pause_event, cancel_event)
                        except TaskPausedDuringWait:
                            await pause_event.wait()
                        current_id += 1
                        continue

                # Process and send message
                try:
                    await pause_event.wait()
                    sent = await self._send_transformed_message(
                        client, message, dest_chat, task, task.clean_forward,
                        pause_event=pause_event, cancel_event=cancel_event
                    )
                    copied_count += 1
                    processed_count += 1
                    dest_msg_id = sent.id if sent else None
                    await record_task_message(task_id, current_id, "copied", dest_message_id=dest_msg_id, media_type=media_type)
                    await self.log(task_id, "success", f"Mensagem {current_id}/{last_message_id} ({media_type}) clonada com sucesso!")
                except TaskPausedDuringWait:
                    await self.log(task_id, "warning", f"Pausa durante a mensagem {current_id}. Aguardando retomada...")
                    await pause_event.wait()
                    continue
                except Exception as e:
                    error_count += 1
                    processed_count += 1
                    await record_task_message(task_id, current_id, "error", media_type=media_type)
                    await self.log(task_id, "error", f"Msg {current_id}: Erro ao clonar: {str(e)}")

                await self._progress(
                    task_id,
                    current_message_id=current_id,
                    processed_messages=processed_count,
                    copied_count=copied_count,
                    skipped_count=skipped_count,
                    error_count=error_count
                )

                await self.broadcast_event("task_progress", {
                    "task_id": task_id,
                    "current": current_id,
                    "total": total_msgs,
                    "copied": copied_count,
                    "skipped": skipped_count,
                    "errors": error_count,
                    "percent": round((processed_count / total_msgs) * 100, 1)
                })

                try:
                    await _interruptible_sleep(task.delay_seconds, pause_event, cancel_event)
                except TaskPausedDuringWait:
                    await pause_event.wait()
                current_id += 1

            # Finished
            await self._set_status(task_id, TaskStatus.COMPLETED)
            await self.log(task_id, "success", f"Clonagem da tarefa '{task.name}' finalizada com sucesso! Total copiadas: {copied_count}.")
            await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.COMPLETED.value})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self.log(task_id, "error", f"Erro fatal no motor de clonagem: {str(e)}")
            await self._set_status(task_id, TaskStatus.FAILED)
        finally:
            self._running_tasks.pop(task_id, None)

    async def _run_live_sync_cloner(self, task_id: int, cancel_event: asyncio.Event, pause_event: Optional[asyncio.Event] = None):
        try:
            task = await self._load_task(task_id)
            if not task:
                return

            client = await self._client(task_id)
            if not client:
                await self.log(task_id, "error", "Telegram desconectado. Conecte sua conta para iniciar.")
                await self._set_status(task_id, TaskStatus.FAILED)
                return

            if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                try:
                    client.me = await client.get_me()
                except Exception:
                    pass
                if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                    client.me = type("Me", (), {"is_premium": False, "id": 0})()

            origin_chat = parse_chat_id(task.origin_chat)
            dest_chat = parse_chat_id(task.dest_chat)

            try:
                try:
                    await client.get_chat(origin_chat)
                except Exception:
                    async for _ in client.get_dialogs(limit=100):
                        pass
                    await client.get_chat(origin_chat)

                try:
                    dest_info = await client.get_chat(dest_chat)
                except Exception:
                    async for _ in client.get_dialogs(limit=100):
                        pass
                    dest_info = await client.get_chat(dest_chat)

                if getattr(dest_info, "type", None) == enums.ChatType.GROUP and getattr(dest_info, "members_count", None) == 0:
                    try:
                        async for m in client.get_chat_history(dest_chat, limit=5):
                            migrated_id = getattr(m, "migrate_to_chat_id", None)
                            if migrated_id:
                                dest_chat = migrated_id
                                async with get_db_connection() as db:
                                    await db.execute("UPDATE tasks SET dest_chat = ? WHERE id = ?", (str(migrated_id), task_id))
                                    await db.commit()
                                break
                    except Exception:
                        pass
            except Exception as e:
                await self.log(task_id, "error", f"Erro ao acessar canais para Live Sync: {str(e)}")
                await self._set_status(task_id, TaskStatus.FAILED)
                return

            await self.log(task_id, "info", f"Modo Tempo Real ativo! Monitorando novas postagens no canal {task.origin_chat}...")

            # Define dynamic message handler for this task
            async def _live_message_handler(_, message: Message):
                if cancel_event.is_set():
                    return
                if pause_event and not pause_event.is_set():
                    # Ignored while paused
                    return

                # Ensure message is from the watched origin chat
                if message.chat.id != origin_chat and getattr(message.chat, "username", None) != str(origin_chat).replace("@", ""):
                    return

                media_type = detect_media_type(message)
                if not is_media_allowed(media_type, task.media_types):
                    await self.log(task_id, "info", f"[Live] Mensagem {message.id} ignorada pelo filtro de tipo ({media_type}).")
                    return

                if media_type == "text":
                    orig_text = getattr(message, "text", None) or getattr(message, "caption", None)
                    clean_text = apply_text_transformations(
                        text=orig_text,
                        remove_captions=bool(getattr(task, "remove_captions", False)),
                        remove_links=task.remove_links,
                        remove_mentions=task.remove_mentions,
                        custom_replacements=task.custom_replacements,
                        header_text=task.header_text,
                        footer_text=task.footer_text
                    )
                    if not clean_text:
                        await self.log(task_id, "info", f"[Live] Mensagem de texto {message.id} ignorada por não conter texto válido após filtros.")
                        return

                # Check if message belongs to an album in Live mode
                if getattr(message, "media_group_id", None):
                    copied_ids = set(await get_task_copied_message_ids(task_id))
                    if message.id in copied_ids:
                        return
                    try:
                        media_group = await client.get_media_group(origin_chat, message.id)
                    except Exception:
                        media_group = None

                    if media_group and len(media_group) > 1:
                        first_id = min(m.id for m in media_group)
                        if message.id == first_id:
                            album_caption = next((m.caption for m in media_group if getattr(m, "caption", None)), None)
                            transformed_caption = apply_text_transformations(
                                text=album_caption,
                                remove_captions=bool(getattr(task, "remove_captions", False)),
                                remove_links=task.remove_links,
                                remove_mentions=task.remove_mentions,
                                custom_replacements=task.custom_replacements,
                                header_text=task.header_text,
                                footer_text=task.footer_text
                            )
                            try:
                                sent_group = await self._send_transformed_media_group(
                                    client=client,
                                    origin_chat=origin_chat,
                                    dest_chat=dest_chat,
                                    media_group=media_group,
                                    task=task,
                                    transformed_caption=transformed_caption,
                                    pause_event=pause_event,
                                    cancel_event=cancel_event
                                )
                                for m_idx, m_item in enumerate(media_group):
                                    dest_id = sent_group[m_idx].id if (sent_group and m_idx < len(sent_group)) else None
                                    await record_task_message(task_id, m_item.id, "copied", dest_message_id=dest_id, media_type="album")
                                await self.log(task_id, "success", f"[Live] Álbum com {len(media_group)} mídias sincronizado para o destino!")
                                t = await self._load_task(task_id)
                                if t:
                                    await self._progress(task_id, copied_count=t.copied_count + len(media_group), processed_messages=t.processed_messages + len(media_group))
                                return
                            except Exception as e:
                                await self.log(task_id, "error", f"[Live] Erro ao sincronizar álbum: {str(e)}")
                        else:
                            return

                try:
                    sent = await self._send_transformed_message(
                        client, message, dest_chat, task, task.clean_forward,
                        pause_event=pause_event, cancel_event=cancel_event
                    )
                    await record_task_message(task_id, message.id, "copied", dest_message_id=sent.id if sent else None, media_type=media_type)
                    await self.log(task_id, "success", f"[Live] Nova mensagem {message.id} ({media_type}) sincronizada para o destino!")
                    
                    t = await self._load_task(task_id)
                    if t:
                        await self._progress(task_id, copied_count=t.copied_count + 1, processed_messages=t.processed_messages + 1)
                        await self.broadcast_event("task_progress", {
                            "task_id": task_id,
                            "current": message.id,
                            "total": t.processed_messages + 1,
                            "copied": t.copied_count + 1,
                            "skipped": t.skipped_count,
                            "errors": t.error_count,
                            "percent": 100.0
                        })
                except TaskPausedDuringWait:
                    pass
                except Exception as e:
                    await self.log(task_id, "error", f"[Live] Erro ao sincronizar mensagem {message.id}: {str(e)}")

            # Register live handler in Pyrogram client
            chat_filter = filters.chat(origin_chat)
            handler = client.add_handler(pyrogram.handlers.MessageHandler(_live_message_handler, chat_filter))
            self._live_sync_handlers[task_id] = (handler, pyrogram.handlers.MessageHandler)

            # Keep task alive until cancelled
            while not cancel_event.is_set():
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self.log(task_id, "error", f"Erro no Live Sync: {str(e)}")
            await self._set_status(task_id, TaskStatus.FAILED)
        finally:
            client = await self._client(task_id)
            if client and task_id in self._live_sync_handlers:
                handler = self._live_sync_handlers.pop(task_id, None)
                if handler:
                    try:
                        client.remove_handler(*handler)
                    except Exception:
                        pass
            self._running_tasks.pop(task_id, None)


# Global singleton instance
cloner_engine = ClonerEngine()

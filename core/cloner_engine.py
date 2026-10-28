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

from core.config import (
    TMP_DIR, REUPLOAD_MEMORY_LIMIT_MB,
    TG_MAX_CONCURRENT_TRANSMISSIONS,
    CLONER_FETCH_BATCH_SIZE,
    CLONER_PREFETCH_CONCURRENCY,
    CLONER_PREFETCH_QUEUE_SIZE,
    CLONER_DB_BATCH_SIZE,
)
from core.database import (
    get_db_connection, get_task, update_task_status, update_task_progress,
    record_task_message, record_task_messages_batch,
    get_task_copied_message_ids, recover_failed_task_messages, add_log
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


def _has_protected_content(obj: Any) -> bool:
    """Safely returns True only when has_protected_content is explicitly True."""
    return getattr(obj, "has_protected_content", False) is True


def _int_attr(obj: Any, attr: str, default: int = 0) -> int:
    """Safely extracts an integer attribute from a Pyrogram media object."""
    if not obj or isinstance(obj, bool):
        return default
    val = getattr(obj, attr, default)
    return val if isinstance(val, int) and not isinstance(val, bool) else default


def _str_attr(obj: Any, attr: str, default: Optional[str] = None) -> Optional[str]:
    """Safely extracts a string attribute from a Pyrogram media object."""
    if not obj or isinstance(obj, bool):
        return default
    val = getattr(obj, attr, default)
    return val if isinstance(val, str) else default


def _get_video_thumb_file_id(message: Message) -> Optional[str]:
    """Extracts the first thumbnail file_id of a video message if available."""
    video = getattr(message, "video", None)
    if not video or isinstance(video, bool):
        return None
    thumbs = getattr(video, "thumbs", None)
    if not isinstance(thumbs, (list, tuple)) or not thumbs:
        return None
    first_thumb = thumbs[0]
    file_id = getattr(first_thumb, "file_id", None)
    return file_id if isinstance(file_id, str) and file_id else None


async def _ensure_client_ready(client: Client) -> None:
    """Ensures client.me is populated and configures file transmission semaphores."""
    if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
        try:
            client.me = await client.get_me()
        except Exception:
            pass
        if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
            client.me = type("Me", (), {"is_premium": False, "id": getattr(getattr(client, "me", None), "id", 0)})()

    curr_limit = getattr(client, "max_concurrent_transmissions", None)
    if isinstance(curr_limit, int) and curr_limit != TG_MAX_CONCURRENT_TRANSMISSIONS:
        client.max_concurrent_transmissions = TG_MAX_CONCURRENT_TRANSMISSIONS
        client.save_file_semaphore = asyncio.Semaphore(TG_MAX_CONCURRENT_TRANSMISSIONS)
        client.get_file_semaphore = asyncio.Semaphore(TG_MAX_CONCURRENT_TRANSMISSIONS)

    if not hasattr(client, "_media_io_lock"):
        client._media_io_lock = asyncio.Lock()


class _DownloadedMedia:
    """Holds a downloaded media buffer/file and optional thumbnail with guaranteed cleanup."""
    __slots__ = ("media_input", "temp_file_path", "use_memory", "thumb_input", "thumb_temp_path")

    def __init__(
        self,
        media_input: Any,
        temp_file_path: Optional[str] = None,
        use_memory: bool = False,
        thumb_input: Any = None,
        thumb_temp_path: Optional[str] = None,
    ):
        self.media_input = media_input
        self.temp_file_path = temp_file_path
        self.use_memory = use_memory
        self.thumb_input = thumb_input
        self.thumb_temp_path = thumb_temp_path

    def rewind(self) -> None:
        for buf in (self.media_input, self.thumb_input):
            if buf is not None and hasattr(buf, "seek"):
                try:
                    buf.seek(0)
                except Exception:
                    pass

    def cleanup(self) -> None:
        for f_path in (self.temp_file_path, self.thumb_temp_path):
            if f_path and isinstance(f_path, str) and os.path.exists(f_path):
                try:
                    os.remove(f_path)
                except Exception:
                    pass
        for buf in (self.media_input, self.thumb_input):
            if buf is not None and hasattr(buf, "close"):
                try:
                    buf.close()
                except Exception:
                    pass


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

    async def _download_single_media(
        self,
        client: Client,
        message: Message,
        media_type: str,
        task_id: int,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> _DownloadedMedia:
        """
        Downloads a single message's media (and video thumbnail if available) sequentially
        over the persistent media session, retrying on FloodWait or transient socket drops.
        """
        await _ensure_client_ready(client)

        file_size = _get_media_file_size(message)
        max_bytes = REUPLOAD_MEMORY_LIMIT_MB * 1024 * 1024
        use_memory = isinstance(file_size, int) and file_size <= max_bytes

        msg_id = getattr(message, "id", 0)
        current_msg = message
        temp_file_path: Optional[str] = None
        media_input: Any = None
        thumb_input: Any = None

        async def _refresh_message_reference() -> None:
            nonlocal current_msg
            if not msg_id:
                return
            origin_chat_id = getattr(getattr(current_msg, "chat", None), "id", None)
            if origin_chat_id is None:
                t_obj = await self._load_task(task_id)
                if t_obj:
                    origin_chat_id = parse_chat_id(t_obj.origin_chat)
            if origin_chat_id is None:
                return
            try:
                fresh = await client.get_messages(origin_chat_id, msg_id)
                if isinstance(fresh, list):
                    fresh = fresh[0] if fresh else None
                if fresh is not None and not getattr(fresh, "empty", False):
                    current_msg = fresh
            except FloodWait as fw:
                await self.log(task_id, "warning", f"FloodWait ao atualizar referência da mídia {msg_id}: Aguardando {fw.value}s...")
                await _interruptible_sleep(fw.value + 1, pause_event, cancel_event)
            except Exception:
                pass

        async def _dl_main() -> Any:
            nonlocal temp_file_path
            attempt = 0
            io_lock = getattr(client, "_media_io_lock", None)
            while True:
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError()
                try:
                    if use_memory:
                        if io_lock is not None:
                            async with io_lock:
                                buf = await client.download_media(current_msg, in_memory=True)
                        else:
                            buf = await client.download_media(current_msg, in_memory=True)
                        if isinstance(buf, io.BytesIO):
                            actual_size = buf.getbuffer().nbytes
                            if actual_size == 0 or (
                                isinstance(file_size, int)
                                and file_size > 1024 * 1024
                                and actual_size < file_size
                                and actual_size % (1024 * 1024) == 0
                            ):
                                buf.close()
                                raise OSError(f"Download incompleto ({actual_size}/{file_size} bytes)")
                        if buf is not None and hasattr(buf, "name") and not buf.name:
                            ext = "jpg" if media_type == "photo" else ("mp4" if media_type in ("video", "animation") else "bin")
                            buf.name = f"media_{msg_id}.{ext}"
                        if buf is None:
                            last_err = getattr(client, "_last_download_error", None)
                            if isinstance(last_err, FloodWait):
                                raise last_err
                            detail = f" ({last_err})" if isinstance(last_err, BaseException) else ""
                            raise OSError(f"Download retornou vazio para mensagem {msg_id}{detail}")
                        return buf
                    else:
                        temp_file_name = str(TMP_DIR / f"tg_tmp_{task_id}_{msg_id}")
                        if io_lock is not None:
                            async with io_lock:
                                dl_res = await client.download_media(current_msg, file_name=temp_file_name)
                        else:
                            dl_res = await client.download_media(current_msg, file_name=temp_file_name)
                        if isinstance(dl_res, str):
                            temp_file_path = dl_res
                            if os.path.exists(dl_res):
                                actual_size = os.path.getsize(dl_res)
                                if actual_size == 0 or (
                                    isinstance(file_size, int)
                                    and file_size > 1024 * 1024
                                    and actual_size < file_size
                                    and actual_size % (1024 * 1024) == 0
                                ):
                                    try:
                                        os.remove(dl_res)
                                    except Exception:
                                        pass
                                    temp_file_path = None
                                    raise OSError(f"Download em disco incompleto ({actual_size}/{file_size} bytes)")
                        if not dl_res:
                            last_err = getattr(client, "_last_download_error", None)
                            if isinstance(last_err, FloodWait):
                                raise last_err
                            detail = f" ({last_err})" if isinstance(last_err, BaseException) else ""
                            raise OSError(f"Download em disco retornou vazio para mensagem {msg_id}{detail}")
                        return dl_res
                except FloodWait as e:
                    await self.log(task_id, "warning", f"FloodWait no download da mídia {msg_id}: Aguardando {e.value}s...")
                    await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
                    await _refresh_message_reference()
                except (OSError, TimeoutError, RPCError) as e:
                    attempt += 1
                    if attempt >= 3:
                        raise RuntimeError(f"Falha ao baixar mídia da mensagem {msg_id} após {attempt} tentativas: {e}") from e
                    await _interruptible_sleep(1.5 * attempt, pause_event, cancel_event)
                    await _refresh_message_reference()

        # Sequential download serialized via _media_io_lock prevents TCPTransport collisions
        media_input = await _dl_main()

        if not media_input:
            if thumb_input and hasattr(thumb_input, "close"):
                try:
                    thumb_input.close()
                except Exception:
                    pass
            raise RuntimeError(f"Falha ao baixar mídia da mensagem {msg_id} para re-upload.")

        return _DownloadedMedia(
            media_input=media_input,
            temp_file_path=temp_file_path,
            use_memory=use_memory,
            thumb_input=thumb_input,
        )

    async def _download_album_media(
        self,
        client: Client,
        media_group: List[Message],
        task_id: int,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> List[_DownloadedMedia]:
        """Downloads all media items of an album sequentially over the persistent media session."""
        await _ensure_client_ready(client)

        downloaded_list: List[_DownloadedMedia] = []
        try:
            for msg in media_group:
                m_type = detect_media_type(msg)
                dl_item = await self._download_single_media(
                    client=client,
                    message=msg,
                    media_type=m_type,
                    task_id=task_id,
                    pause_event=pause_event,
                    cancel_event=cancel_event,
                )
                downloaded_list.append(dl_item)
            return downloaded_list
        except BaseException:
            for item in downloaded_list:
                item.cleanup()
            raise

    async def _send_media_via_reupload(
        self,
        client: Client,
        message: Message,
        dest_chat: Any,
        caption: Optional[str],
        media_type: str,
        task_id: int,
        downloaded: Optional[_DownloadedMedia] = None,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Optional[Message]:
        """
        Downloads the media (or uses a prefetched buffer) and re-uploads it as a fresh
        message to bypass Telegram's CHAT_FORWARDS_RESTRICTED content protection.
        """
        if task_id not in self._reupload_warned_tasks:
            self._reupload_warned_tasks.add(task_id)
            await self.log(task_id, "info", "Canal com proteção de conteúdo detectado: utilizando re-upload.")

        await _ensure_client_ready(client)

        if downloaded is None:
            downloaded = await self._download_single_media(
                client=client,
                message=message,
                media_type=media_type,
                task_id=task_id,
                pause_event=pause_event,
                cancel_event=cancel_event,
            )

        io_lock = getattr(client, "_media_io_lock", None)

        async def _do_send_single(media_input: Any) -> Optional[Message]:
            if media_type == "photo":
                return await client.send_photo(chat_id=dest_chat, photo=media_input, caption=caption)
            elif media_type == "video":
                duration = _int_attr(getattr(message, "video", None), "duration", 0)
                width = _int_attr(getattr(message, "video", None), "width", 0)
                height = _int_attr(getattr(message, "video", None), "height", 0)
                video_kwargs: Dict[str, Any] = {
                    "chat_id": dest_chat,
                    "video": media_input,
                    "caption": caption,
                    "duration": duration,
                    "width": width,
                    "height": height,
                    "supports_streaming": True,
                }
                if downloaded.thumb_input is not None:
                    video_kwargs["thumb"] = downloaded.thumb_input
                return await client.send_video(**video_kwargs)
            elif media_type == "document":
                file_name = _str_attr(getattr(message, "document", None), "file_name", None)
                return await client.send_document(
                    chat_id=dest_chat,
                    document=media_input,
                    caption=caption,
                    file_name=file_name
                )
            elif media_type == "audio":
                title = _str_attr(getattr(message, "audio", None), "title", None)
                performer = _str_attr(getattr(message, "audio", None), "performer", None)
                duration = _int_attr(getattr(message, "audio", None), "duration", 0)
                return await client.send_audio(
                    chat_id=dest_chat,
                    audio=media_input,
                    caption=caption,
                    title=title,
                    performer=performer,
                    duration=duration
                )
            elif media_type == "voice":
                duration = _int_attr(getattr(message, "voice", None), "duration", 0)
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
                duration = _int_attr(getattr(message, "video_note", None), "duration", 0)
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

        try:
            while True:
                media_input = downloaded.media_input
                try:
                    if io_lock is not None:
                        async with io_lock:
                            return await _do_send_single(media_input)
                    else:
                        return await _do_send_single(media_input)
                except FloodWait as e:
                    await self.log(task_id, "warning", f"FloodWait no upload da mensagem {message.id}: Aguardando {e.value}s...")
                    await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
                    downloaded.rewind()
        finally:
            downloaded.cleanup()

    async def _send_transformed_message(
        self,
        client: Client,
        message: Message,
        dest_chat: Any,
        task: TaskResponse,
        clean_forward: bool = True,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None,
        prefetched_media: Optional[_DownloadedMedia] = None,
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

        # Proactive check: if message has protected content or channel is known protected, re-upload directly
        is_protected = (
            _has_protected_content(message)
            or (task.id in self._reupload_warned_tasks)
            or (prefetched_media is not None)
        )
        if is_protected and media_type not in ("text", "poll", "empty"):
            try:
                return await self._send_media_via_reupload(
                    client=client,
                    message=message,
                    dest_chat=dest_chat,
                    caption=transformed_text or None,
                    media_type=media_type,
                    task_id=task.id,
                    downloaded=prefetched_media,
                    pause_event=pause_event,
                    cancel_event=cancel_event,
                )
            except TaskPausedDuringWait:
                raise
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
                                task_id=task.id,
                                pause_event=pause_event,
                                cancel_event=cancel_event,
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
                            task_id=task.id,
                            pause_event=pause_event,
                            cancel_event=cancel_event,
                        )
                    raise e
        except FloodWait as e:
            await self.log(task.id, "warning", f"FloodWait: Aguardando {e.value}s exigidos pelo Telegram...")
            await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
            # Retry after flood wait
            return await self._send_transformed_message(client, message, dest_chat, task, clean_forward, pause_event, cancel_event)
        except TaskPausedDuringWait:
            raise
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
        cancel_event: Optional[asyncio.Event] = None,
        prefetched_album: Optional[List[_DownloadedMedia]] = None,
    ) -> List[Message]:
        if not media_group:
            return []

        first_msg = media_group[0]
        group_msg_ids = [m.id for m in media_group]

        is_protected = (
            any(_has_protected_content(m) for m in media_group)
            or (task.id in self._reupload_warned_tasks)
            or (prefetched_album is not None)
        )

        if not is_protected:
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
                return await self._send_transformed_media_group(
                    client, origin_chat, dest_chat, media_group, task, transformed_caption, pause_event, cancel_event
                )
            except RPCError as e:
                if not _is_forwards_restricted(e):
                    raise e
                # Fallback to reupload if restricted

        # 3. Protected content path: parallel download and send_media_group
        if task.id not in self._reupload_warned_tasks:
            self._reupload_warned_tasks.add(task.id)
            await self.log(task.id, "info", "Canal com proteção de conteúdo detectado: utilizando re-upload paralelo.")

        await _ensure_client_ready(client)

        downloaded_items: List[_DownloadedMedia] = (
            prefetched_album
            if prefetched_album is not None
            else await self._download_album_media(client, media_group, task.id, pause_event, cancel_event)
        )

        try:
            input_media: List[Any] = []
            for idx, (msg, dl_item) in enumerate(zip(media_group, downloaded_items)):
                cap = transformed_caption if idx == 0 else None
                media_input = dl_item.media_input
                if not media_input:
                    continue

                if getattr(msg, "photo", None):
                    input_media.append(InputMediaPhoto(media_input, caption=cap))
                elif getattr(msg, "video", None):
                    vid_obj = getattr(msg, "video", None)
                    vid_kwargs: Dict[str, Any] = {
                        "media": media_input,
                        "caption": cap or "",
                        "width": _int_attr(vid_obj, "width", 0),
                        "height": _int_attr(vid_obj, "height", 0),
                        "duration": _int_attr(vid_obj, "duration", 0),
                        "supports_streaming": True,
                    }
                    if dl_item.thumb_input is not None:
                        vid_kwargs["thumb"] = dl_item.thumb_input
                    input_media.append(InputMediaVideo(**vid_kwargs))
                elif getattr(msg, "audio", None):
                    aud_obj = getattr(msg, "audio", None)
                    input_media.append(
                        InputMediaAudio(
                            media_input,
                            caption=cap or "",
                            duration=_int_attr(aud_obj, "duration", 0),
                            performer=_str_attr(aud_obj, "performer", None),
                            title=_str_attr(aud_obj, "title", None),
                        )
                    )
                else:
                    input_media.append(InputMediaDocument(media_input, caption=cap or ""))

            if not input_media:
                return []

            io_lock = getattr(client, "_media_io_lock", None)
            upload_attempts = 0
            while True:
                try:
                    if io_lock is not None:
                        async with io_lock:
                            return await client.send_media_group(chat_id=dest_chat, media=input_media)
                    else:
                        return await client.send_media_group(chat_id=dest_chat, media=input_media)
                except FloodWait as e:
                    await self.log(task.id, "warning", f"FloodWait no envio de álbum: Aguardando {e.value}s...")
                    if pause_event and cancel_event:
                        await _interruptible_sleep(e.value + 1, pause_event, cancel_event)
                    else:
                        await asyncio.sleep(e.value + 1)
                    for dl_item in downloaded_items:
                        dl_item.rewind()
                except (RPCError, OSError) as e:
                    if "FILE_PART" in str(e).upper() and upload_attempts < 2:
                        upload_attempts += 1
                        await self.log(
                            task.id,
                            "warning",
                            f"Parte de mídia ausente no upload do álbum (tentativa {upload_attempts}/2), reenviando..."
                        )
                        if pause_event and cancel_event:
                            await _interruptible_sleep(1.5 * upload_attempts, pause_event, cancel_event)
                        else:
                            await asyncio.sleep(1.5 * upload_attempts)
                        for dl_item in downloaded_items:
                            dl_item.rewind()
                        continue
                    raise
        finally:
            for dl_item in downloaded_items:
                dl_item.cleanup()

    async def _fetch_messages_batch(
        self,
        client: Client,
        origin_chat: Any,
        message_ids: List[int],
        task_id: int,
        pause_event: Optional[asyncio.Event] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Dict[int, Message]:
        """Fetches up to 200 messages in a single MTProto request, retrying on FloodWait."""
        if not message_ids:
            return {}
        while True:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError()
            try:
                raw_res = await client.get_messages(origin_chat, message_ids)
                msgs = raw_res if isinstance(raw_res, list) else ([raw_res] if raw_res is not None else [])
                result: Dict[int, Message] = {}
                for m in msgs:
                    if m is not None:
                        m_id = getattr(m, "id", None)
                        if isinstance(m_id, int):
                            result[m_id] = m
                return result
            except FloodWait as e:
                await self.log(task_id, "warning", f"FloodWait ao ler lote de mensagens: Aguardando {e.value}s...")
                await _interruptible_sleep(e.value + 1, pause_event, cancel_event)

    async def _run_historical_cloner(self, task_id: int, cancel_event: asyncio.Event, pause_event: asyncio.Event):
        pending_db_records: List[Dict[str, Any]] = []
        last_db_flush = asyncio.get_event_loop().time()

        async def _flush_db(cur_id: int, proc: int, cop: int, skip: int, err: int, force: bool = False) -> None:
            nonlocal last_db_flush
            now_t = asyncio.get_event_loop().time()
            if not force and len(pending_db_records) < CLONER_DB_BATCH_SIZE and (now_t - last_db_flush) < 2.0:
                return
            if pending_db_records:
                to_save = list(pending_db_records)
                pending_db_records.clear()
                await record_task_messages_batch(task_id, to_save)
            await self._progress(
                task_id,
                current_message_id=cur_id,
                processed_messages=proc,
                copied_count=cop,
                skipped_count=skip,
                error_count=err,
            )
            last_db_flush = now_t

        try:
            task = await self._load_task(task_id)
            if not task:
                return

            client = await self._client(task_id)
            if not client:
                await self.log(task_id, "error", "Telegram desconectado. Conecte sua conta para iniciar.")
                await self._set_status(task_id, TaskStatus.FAILED)
                return

            await _ensure_client_ready(client)

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

                if _has_protected_content(origin_info) and task_id not in self._reupload_warned_tasks:
                    self._reupload_warned_tasks.add(task_id)
                    await self.log(
                        task_id,
                        "info",
                        "Canal com proteção de conteúdo detectado: modo turbo (leitura em lote + prefetch paralelo) ativado."
                    )

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

            # Auto-recover any previously failed/errored messages so resuming retries them
            owner_id = self._owner_of(task_id)
            if owner_id is not None:
                rewound_to = await recover_failed_task_messages(owner_id, task_id)
                if rewound_to is not None:
                    reloaded = await self._load_task(task_id)
                    if reloaded:
                        task = reloaded
                    await self.log(
                        task_id,
                        "info",
                        f"Reprocessando mensagens com falha anterior a partir do ID {rewound_to} (mensagens já copiadas serão preservadas)."
                    )

            copied_count = task.copied_count
            skipped_count = task.skipped_count
            error_count = task.error_count
            processed_count = task.processed_messages

            await self.log(task_id, "info", f"Clonando do ID {start_id} até {last_message_id} ({total_msgs} mensagens)...")

            current_id = max(start_id, task.current_message_id) if task.current_message_id > 0 else start_id
            prefetch_sem = asyncio.Semaphore(max(1, CLONER_PREFETCH_CONCURRENCY))

            while current_id <= last_message_id:
                if cancel_event.is_set():
                    await _flush_db(current_id, processed_count, copied_count, skipped_count, error_count, force=True)
                    await self.log(task_id, "warning", "Processo cancelado.")
                    await self._set_status(task_id, TaskStatus.CANCELLED)
                    return

                if not pause_event.is_set():
                    await _flush_db(current_id, processed_count, copied_count, skipped_count, error_count, force=True)
                    await pause_event.wait()
                    fresh_task = await self._load_task(task_id)
                    if fresh_task:
                        task = fresh_task
                        origin_chat = parse_chat_id(task.origin_chat)
                        dest_chat = parse_chat_id(task.dest_chat)

                # Advance past already copied IDs quickly
                while current_id <= last_message_id and current_id in already_copied_ids:
                    current_id += 1
                if current_id > last_message_id:
                    break

                batch_end = min(current_id + CLONER_FETCH_BATCH_SIZE - 1, last_message_id)
                ids_to_fetch = [
                    mid for mid in range(current_id, batch_end + 1)
                    if mid not in already_copied_ids
                ]

                try:
                    msg_map = await self._fetch_messages_batch(
                        client, origin_chat, ids_to_fetch, task_id, pause_event, cancel_event
                    )
                except TaskPausedDuringWait:
                    await _flush_db(current_id, processed_count, copied_count, skipped_count, error_count, force=True)
                    await pause_event.wait()
                    continue
                except Exception as e:
                    await self.log(task_id, "warning", f"Erro ao ler lote {current_id}..{batch_end} ({str(e)}). Pulando lote.")
                    for mid in ids_to_fetch:
                        skipped_count += 1
                        processed_count += 1
                        pending_db_records.append({
                            "origin_message_id": mid,
                            "status": "failed",
                            "dest_message_id": None,
                            "media_type": None,
                        })
                    current_id = batch_end + 1
                    await _flush_db(batch_end, processed_count, copied_count, skipped_count, error_count, force=True)
                    continue

                # If the last message of the batch belongs to an album, fetch up to 10 lookahead messages so the album isn't split
                edge_msg = msg_map.get(batch_end)
                if edge_msg and getattr(edge_msg, "media_group_id", None) and batch_end < last_message_id:
                    lookahead_ids = [
                        mid for mid in range(batch_end + 1, min(batch_end + 11, last_message_id + 1))
                        if mid not in already_copied_ids
                    ]
                    if lookahead_ids:
                        try:
                            extra_map = await self._fetch_messages_batch(
                                client, origin_chat, lookahead_ids, task_id, pause_event, cancel_event
                            )
                            msg_map.update(extra_map)
                        except Exception:
                            pass

                # Group messages by media_group_id within the fetched map
                albums_by_gid: Dict[Any, List[Message]] = {}
                for m in msg_map.values():
                    gid = getattr(m, "media_group_id", None)
                    if gid is not None:
                        albums_by_gid.setdefault(gid, []).append(m)
                for gid in albums_by_gid:
                    albums_by_gid[gid].sort(key=lambda x: x.id)

                # Build ordered operations for this batch
                ops: List[Dict[str, Any]] = []
                grouped_seen: set[int] = set()
                cursor = current_id

                while cursor <= batch_end:
                    if cursor in already_copied_ids or cursor in grouped_seen:
                        cursor += 1
                        continue

                    message = msg_map.get(cursor)
                    gid = getattr(message, "media_group_id", None) if message else None

                    if message and gid is not None:
                        media_group = albums_by_gid.get(gid, [])
                        if len(media_group) <= 1:
                            try:
                                fetched_group = await client.get_media_group(origin_chat, cursor)
                                if fetched_group and len(fetched_group) > 1:
                                    media_group = sorted(fetched_group, key=lambda x: x.id)
                            except Exception:
                                pass

                        if media_group and len(media_group) > 1:
                            for gm in media_group:
                                grouped_seen.add(gm.id)
                            ops.append({
                                "kind": "album",
                                "id": cursor,
                                "max_id": max(gm.id for gm in media_group),
                                "media_group": media_group,
                            })
                            cursor = max(cursor + 1, max(gm.id for gm in media_group) + 1)
                            continue

                    media_type = detect_media_type(message) if message else "empty"
                    if not message or media_type == "empty":
                        ops.append({"kind": "skip", "id": cursor, "media_type": "empty"})
                        cursor += 1
                        continue

                    if not is_media_allowed(media_type, task.media_types):
                        ops.append({"kind": "skip", "id": cursor, "media_type": media_type})
                        cursor += 1
                        continue

                    if media_type == "text":
                        orig_text = getattr(message, "text", None) or getattr(message, "caption", None)
                        clean_text = apply_text_transformations(
                            text=orig_text,
                            remove_captions=bool(getattr(task, "remove_captions", False)),
                            remove_links=task.remove_links,
                            remove_mentions=task.remove_mentions,
                            custom_replacements=task.custom_replacements,
                            header_text=task.header_text,
                            footer_text=task.footer_text,
                        )
                        if not clean_text:
                            ops.append({"kind": "skip", "id": cursor, "media_type": "text"})
                            cursor += 1
                            continue

                    ops.append({
                        "kind": "single",
                        "id": cursor,
                        "message": message,
                        "media_type": media_type,
                    })
                    cursor += 1

                # Sliding-window prefetch manager for protected media/albums in this batch
                send_op_indices = [
                    idx for idx, op in enumerate(ops)
                    if op["kind"] in ("single", "album")
                ]
                prefetch_tasks: Dict[int, asyncio.Task] = {}

                async def _prefetch_op(op_dict: Dict[str, Any]) -> Any:
                    async with prefetch_sem:
                        if op_dict["kind"] == "album":
                            return await self._download_album_media(
                                client, op_dict["media_group"], task_id, pause_event, cancel_event
                            )
                        else:
                            return await self._download_single_media(
                                client, op_dict["message"], op_dict["media_type"], task_id, pause_event, cancel_event
                            )

                def _schedule_prefetch_window(current_send_pos: int) -> None:
                    window_end = min(len(send_op_indices), current_send_pos + CLONER_PREFETCH_QUEUE_SIZE)
                    for pos in range(current_send_pos, window_end):
                        op_idx = send_op_indices[pos]
                        if op_idx in prefetch_tasks:
                            continue
                        op_candidate = ops[op_idx]
                        if op_candidate["kind"] == "album":
                            needs_dl = (
                                (task_id in self._reupload_warned_tasks)
                                or any(_has_protected_content(m) for m in op_candidate["media_group"])
                            )
                        else:
                            m_type = op_candidate["media_type"]
                            needs_dl = m_type not in ("text", "poll", "empty") and (
                                (task_id in self._reupload_warned_tasks)
                                or _has_protected_content(op_candidate["message"])
                            )
                        if needs_dl:
                            prefetch_tasks[op_idx] = asyncio.create_task(_prefetch_op(op_candidate))

                async def _cleanup_prefetch_tasks() -> None:
                    for p_task in list(prefetch_tasks.values()):
                        if not p_task.done():
                            p_task.cancel()
                    for p_task in list(prefetch_tasks.values()):
                        try:
                            res = await p_task
                            if isinstance(res, _DownloadedMedia):
                                res.cleanup()
                            elif isinstance(res, list):
                                for item in res:
                                    if isinstance(item, _DownloadedMedia):
                                        item.cleanup()
                        except BaseException:
                            pass
                    prefetch_tasks.clear()

                send_pos_map = {op_idx: pos for pos, op_idx in enumerate(send_op_indices)}

                try:
                    op_i = 0
                    while op_i < len(ops):
                        op = ops[op_i]
                        op_id = op["id"]

                        if cancel_event.is_set():
                            await _cleanup_prefetch_tasks()
                            await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count, force=True)
                            await self.log(task_id, "warning", "Processo cancelado.")
                            await self._set_status(task_id, TaskStatus.CANCELLED)
                            return

                        if not pause_event.is_set():
                            await _cleanup_prefetch_tasks()
                            await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count, force=True)
                            await pause_event.wait()
                            fresh_task = await self._load_task(task_id)
                            if fresh_task:
                                task = fresh_task
                                origin_chat = parse_chat_id(task.origin_chat)
                                dest_chat = parse_chat_id(task.dest_chat)

                        if op["kind"] == "skip":
                            skipped_count += 1
                            processed_count += 1
                            already_copied_ids.add(op_id)
                            pending_db_records.append({
                                "origin_message_id": op_id,
                                "status": "skipped",
                                "dest_message_id": None,
                                "media_type": op["media_type"],
                            })
                            await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count)
                            await self.broadcast_event("task_progress", {
                                "task_id": task_id,
                                "current": op_id,
                                "total": total_msgs,
                                "copied": copied_count,
                                "skipped": skipped_count,
                                "errors": error_count,
                                "percent": round(min(100.0, (processed_count / total_msgs) * 100), 1),
                            })
                            op_i += 1
                            continue

                        # Schedule prefetch window for current & upcoming media items
                        if op_i in send_pos_map:
                            _schedule_prefetch_window(send_pos_map[op_i])

                        prefetched_data = None
                        if op_i in prefetch_tasks:
                            p_task = prefetch_tasks.pop(op_i)
                            try:
                                prefetched_data = await p_task
                            except TaskPausedDuringWait:
                                await _cleanup_prefetch_tasks()
                                await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count, force=True)
                                await pause_event.wait()
                                continue
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                prefetched_data = None

                        if op["kind"] == "album":
                            media_group = op["media_group"]
                            group_msg_ids = [m.id for m in media_group]
                            album_caption = next((m.caption for m in media_group if getattr(m, "caption", None)), None)
                            transformed_caption = apply_text_transformations(
                                text=album_caption,
                                remove_captions=bool(getattr(task, "remove_captions", False)),
                                remove_links=task.remove_links,
                                remove_mentions=task.remove_mentions,
                                custom_replacements=task.custom_replacements,
                                header_text=task.header_text,
                                footer_text=task.footer_text,
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
                                    cancel_event=cancel_event,
                                    prefetched_album=prefetched_data,
                                )
                               # Ensure next items start prefetching if protected mode was just discovered
                                if op_i in send_pos_map:
                                    _schedule_prefetch_window(send_pos_map[op_i] + 1)

                                for m_idx, m_item in enumerate(media_group):
                                    dest_id = sent_group[m_idx].id if (sent_group and m_idx < len(sent_group)) else None
                                    pending_db_records.append({
                                        "origin_message_id": m_item.id,
                                        "status": "copied",
                                        "dest_message_id": dest_id,
                                        "media_type": "album",
                                    })
                                    already_copied_ids.add(m_item.id)

                                copied_count += len(media_group)
                                processed_count += len(media_group)
                                max_gid = max(group_msg_ids)

                                await self.log(
                                    task_id,
                                    "success",
                                    f"Álbum com {len(media_group)} mídias (IDs: {min(group_msg_ids)} a {max_gid}) clonado com sucesso!"
                                )
                                await _flush_db(max_gid, processed_count, copied_count, skipped_count, error_count)
                                await self.broadcast_event("task_progress", {
                                    "task_id": task_id,
                                    "current": max_gid,
                                    "total": total_msgs,
                                    "copied": copied_count,
                                    "skipped": skipped_count,
                                    "errors": error_count,
                                    "percent": round(min(100.0, (processed_count / total_msgs) * 100), 1),
                                })
                                try:
                                    await _interruptible_sleep(task.delay_seconds, pause_event, cancel_event)
                                except TaskPausedDuringWait:
                                    await _cleanup_prefetch_tasks()
                                    await _flush_db(max_gid, processed_count, copied_count, skipped_count, error_count, force=True)
                                    await pause_event.wait()
                                op_i += 1
                                continue
                            except TaskPausedDuringWait:
                                await _cleanup_prefetch_tasks()
                                await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count, force=True)
                                await pause_event.wait()
                                continue
                            except Exception as e:
                                error_count += len(media_group)
                                processed_count += len(media_group)
                                for m_item in media_group:
                                    pending_db_records.append({
                                        "origin_message_id": m_item.id,
                                        "status": "error",
                                        "dest_message_id": None,
                                        "media_type": "album",
                                    })
                                    already_copied_ids.add(m_item.id)
                                await self.log(task_id, "error", f"Erro ao clonar álbum {group_msg_ids}: {str(e)}")
                                await _flush_db(max(group_msg_ids), processed_count, copied_count, skipped_count, error_count)
                                op_i += 1
                                continue

                        # Single message
                        message = op["message"]
                        media_type = op["media_type"]
                        try:
                            await pause_event.wait()
                            sent = await self._send_transformed_message(
                                client,
                                message,
                                dest_chat,
                                task,
                                task.clean_forward,
                                pause_event=pause_event,
                                cancel_event=cancel_event,
                                prefetched_media=prefetched_data,
                            )
                            # Ensure next items start prefetching if protected mode was just discovered
                            if op_i in send_pos_map:
                                _schedule_prefetch_window(send_pos_map[op_i] + 1)

                            copied_count += 1
                            processed_count += 1
                            dest_msg_id = sent.id if sent else None
                            already_copied_ids.add(op_id)
                            pending_db_records.append({
                                "origin_message_id": op_id,
                                "status": "copied",
                                "dest_message_id": dest_msg_id,
                                "media_type": media_type,
                            })
                            # Persist log to SQL for small tasks or periodic milestones; always broadcast to live UI
                            if total_msgs <= 500 or copied_count <= 5 or copied_count % 25 == 0:
                                await self.log(task_id, "success", f"Mensagem {op_id}/{last_message_id} ({media_type}) clonada com sucesso!")
                            else:
                                await self.broadcast_event("log", {
                                    "task_id": task_id,
                                    "level": "success",
                                    "message": f"Mensagem {op_id}/{last_message_id} ({media_type}) clonada com sucesso!",
                                })
                        except TaskPausedDuringWait:
                            await _cleanup_prefetch_tasks()
                            await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count, force=True)
                            await self.log(task_id, "warning", f"Pausa durante a mensagem {op_id}. Aguardando retomada...")
                            await pause_event.wait()
                            continue
                        except Exception as e:
                            error_count += 1
                            processed_count += 1
                            already_copied_ids.add(op_id)
                            pending_db_records.append({
                                "origin_message_id": op_id,
                                "status": "error",
                                "dest_message_id": None,
                                "media_type": media_type,
                            })
                            await self.log(task_id, "error", f"Msg {op_id}: Erro ao clonar: {str(e)}")

                        await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count)
                        await self.broadcast_event("task_progress", {
                            "task_id": task_id,
                            "current": op_id,
                            "total": total_msgs,
                            "copied": copied_count,
                            "skipped": skipped_count,
                            "errors": error_count,
                            "percent": round(min(100.0, (processed_count / total_msgs) * 100), 1),
                        })

                        try:
                            await _interruptible_sleep(task.delay_seconds, pause_event, cancel_event)
                        except TaskPausedDuringWait:
                            await _cleanup_prefetch_tasks()
                            await _flush_db(op_id, processed_count, copied_count, skipped_count, error_count, force=True)
                            await pause_event.wait()
                        op_i += 1
                finally:
                    await _cleanup_prefetch_tasks()

                current_id = max(cursor, batch_end + 1)

            # Flush any remaining buffered records before marking completed
            await _flush_db(last_message_id, processed_count, copied_count, skipped_count, error_count, force=True)
            await self._set_status(task_id, TaskStatus.COMPLETED)
            await self.log(task_id, "success", f"Clonagem da tarefa '{task.name}' finalizada com sucesso! Total copiadas: {copied_count}.")
            await self.broadcast_event("task_status", {"task_id": task_id, "status": TaskStatus.COMPLETED.value})
        except asyncio.CancelledError:
            try:
                await record_task_messages_batch(task_id, list(pending_db_records))
                pending_db_records.clear()
            except Exception:
                pass
        except Exception as e:
            try:
                await record_task_messages_batch(task_id, list(pending_db_records))
                pending_db_records.clear()
            except Exception:
                pass
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

            await _ensure_client_ready(client)

            origin_chat = parse_chat_id(task.origin_chat)
            dest_chat = parse_chat_id(task.dest_chat)

            try:
                try:
                    origin_info = await client.get_chat(origin_chat)
                except Exception:
                    async for _ in client.get_dialogs(limit=100):
                        pass
                    origin_info = await client.get_chat(origin_chat)

                if _has_protected_content(origin_info) and task_id not in self._reupload_warned_tasks:
                    self._reupload_warned_tasks.add(task_id)

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
                                batch_recs = []
                                for m_idx, m_item in enumerate(media_group):
                                    dest_id = sent_group[m_idx].id if (sent_group and m_idx < len(sent_group)) else None
                                    batch_recs.append({
                                        "origin_message_id": m_item.id,
                                        "status": "copied",
                                        "dest_message_id": dest_id,
                                        "media_type": "album",
                                    })
                                await record_task_messages_batch(task_id, batch_recs)
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

from __future__ import annotations
import asyncio
import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable, Coroutine

import pyrogram
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError

from core.config import TMP_DIR
from core.database import (
    get_post, get_all_posts, update_post_status, update_post_next_run, update_post_content,
    get_all_managed_groups, get_managed_group_by_id, record_post_delivery,
    get_latest_deliveries_for_post, update_delivery_status, add_log
)
from core.models import PostResponse, PostStatus, ScheduleType, RecurrenceRule, PostUpdate
from core.telegram_auth import telegram_auth
from core.cloner_engine import parse_chat_id


def compute_next_run(rule: Optional[Dict[str, Any] | RecurrenceRule], from_dt: Optional[datetime] = None) -> Optional[datetime]:
    if not rule:
        return None
    if from_dt is None:
        from_dt = datetime.now()

    freq = rule.get("freq") if isinstance(rule, dict) else getattr(rule, "freq", None)
    if not freq:
        return None

    if freq == "interval":
        hours = rule.get("interval_hours", 1) if isinstance(rule, dict) else getattr(rule, "interval_hours", 1)
        return from_dt + timedelta(hours=int(hours or 1))

    time_hhmm = (rule.get("time_hhmm") if isinstance(rule, dict) else getattr(rule, "time_hhmm", "09:00")) or "09:00"
    try:
        target_hour, target_min = map(int, time_hhmm.split(":"))
    except Exception:
        target_hour, target_min = 9, 0

    if freq == "daily":
        candidate = from_dt.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        if candidate <= from_dt:
            candidate += timedelta(days=1)
        return candidate

    if freq == "weekly":
        target_weekday = int(rule.get("weekday", 0) if isinstance(rule, dict) else getattr(rule, "weekday", 0))
        days_ahead = (target_weekday - from_dt.weekday()) % 7
        candidate = from_dt.replace(hour=target_hour, minute=target_min, second=0, microsecond=0) + timedelta(days=days_ahead)
        if candidate <= from_dt:
            candidate += timedelta(days=7)
        return candidate

    return None


class PublisherEngine:
    def __init__(self):
        self._scheduler_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._listeners: List[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = []

    def register_listener(self, callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]):
        self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]):
        if callback in self._listeners:
            self._listeners.remove(callback)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        payload = {"event": event_type, "data": data, "timestamp": datetime.now().strftime("%H:%M:%S")}
        for listener in self._listeners:
            try:
                await listener(payload)
            except Exception:
                pass

    async def log(self, owner_id: Optional[int], level: str, message: str, task_id: Optional[int] = None):
        await add_log(owner_id, task_id, level, f"[Publicador] {message}")
        await self.broadcast_event("publisher_log", {
            "owner_id": owner_id,
            "level": level,
            "message": message
        })

    def start_scheduler_loop(self):
        if self._scheduler_task is None or self._scheduler_task.done():
            self._stop_event.clear()
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler_loop(self):
        if self._scheduler_task and not self._scheduler_task.done():
            self._stop_event.set()
            self._scheduler_task.cancel()

    async def _scheduler_loop(self):
        """Periodically scans posts table for scheduled posts ready to be sent."""
        while not self._stop_event.is_set():
            try:
                posts = await get_all_posts()
                now_str = datetime.now().isoformat()
                for post in posts:
                    if post.status == PostStatus.SCHEDULED.value and post.next_run_at:
                        if post.next_run_at <= now_str:
                            await self.execute_post_run(post.owner_id, post.id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                pass

            try:
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                break

    async def execute_post_run(self, owner_id: int, post_id: int) -> bool:
        """Executes a single publishing run of a post to all its target groups."""
        post = await get_post(owner_id, post_id)
        if not post:
            return False

        client = await telegram_auth.get_active_client(owner_id)
        if not client:
            await self.log(owner_id, "error", f"Conta do Telegram desconectada para o usuário #{owner_id}. Não foi possível publicar post '{post.name}'.")
            await update_post_status(post_id, PostStatus.FAILED)
            return False

        if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
            try:
                client.me = await client.get_me()
            except Exception:
                pass
            if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                client.me = type("Me", (), {"is_premium": False, "id": 0})()

        await update_post_status(post_id, PostStatus.PUBLISHING)
        await self.log(owner_id, "info", f"Iniciando envio do post '{post.name}' para {len(post.target_group_ids)} grupos...")

        success_count = 0
        failure_count = 0

        # Retrieve managed groups
        all_groups = await get_all_managed_groups(owner_id)
        groups_map = {g.id: g for g in all_groups}
        target_groups = [groups_map[gid] for gid in post.target_group_ids if gid in groups_map]

        if not target_groups:
            await self.log(owner_id, "warning", f"Nenhum grupo de destino válido encontrado para post '{post.name}'.")
            await update_post_status(post_id, PostStatus.FAILED)
            return False

        media_exists = post.media_path and os.path.exists(post.media_path)
        if post.media_path and not media_exists:
            await self.log(owner_id, "error", f"Arquivo de mídia não encontrado em {post.media_path} para post '{post.name}'.")

        for group in target_groups:
            chat_id = parse_chat_id(group.chat_id)
            try:
                sent_msg: Optional[Message] = None

                if media_exists:
                    if post.media_type == "photo":
                        sent_msg = await client.send_photo(chat_id=chat_id, photo=post.media_path, caption=post.text or None)
                    elif post.media_type == "video":
                        sent_msg = await client.send_video(chat_id=chat_id, video=post.media_path, caption=post.text or None)
                    else:
                        sent_msg = await client.send_document(chat_id=chat_id, document=post.media_path, caption=post.text or None)
                else:
                    sent_msg = await client.send_message(chat_id=chat_id, text=post.text or " ")

                msg_id = sent_msg.id if sent_msg else None
                await record_post_delivery(
                    post_id=post_id,
                    chat_id=group.chat_id,
                    chat_title=group.title,
                    message_id=msg_id,
                    status="sent"
                )
                success_count += 1
                await self.log(owner_id, "success", f"Post '{post.name}' enviado para '{group.title}' (Msg ID: {msg_id}).")
            except FloodWait as e:
                await self.log(owner_id, "warning", f"FloodWait de {e.value}s no grupo '{group.title}'. Aguardando...")
                await asyncio.sleep(e.value + 1)
                # Retry once
                try:
                    if media_exists:
                        if post.media_type == "photo":
                            sent_msg = await client.send_photo(chat_id=chat_id, photo=post.media_path, caption=post.text or None)
                        elif post.media_type == "video":
                            sent_msg = await client.send_video(chat_id=chat_id, video=post.media_path, caption=post.text or None)
                        else:
                            sent_msg = await client.send_document(chat_id=chat_id, document=post.media_path, caption=post.text or None)
                    else:
                        sent_msg = await client.send_message(chat_id=chat_id, text=post.text or " ")
                    msg_id = sent_msg.id if sent_msg else None
                    await record_post_delivery(post_id, group.chat_id, group.title, msg_id, "sent")
                    success_count += 1
                except Exception as retry_err:
                    failure_count += 1
                    await record_post_delivery(post_id, group.chat_id, group.title, None, "failed", str(retry_err))
            except Exception as e:
                failure_count += 1
                await record_post_delivery(
                    post_id=post_id,
                    chat_id=group.chat_id,
                    chat_title=group.title,
                    message_id=None,
                    status="failed",
                    error=str(e)
                )
                await self.log(owner_id, "error", f"Falha ao enviar post '{post.name}' para '{group.title}': {str(e)}")

            # Short spacing between groups to respect rate limits
            await asyncio.sleep(1.0)

        # Handle post completion & recurrence
        now_str = datetime.now().isoformat()
        if post.schedule_type == ScheduleType.RECURRING.value and post.recurrence_rule:
            next_run_dt = compute_next_run(post.recurrence_rule, datetime.now())
            next_run_str = next_run_dt.isoformat() if next_run_dt else None
            await update_post_next_run(post_id, next_run_at=next_run_str, last_run_at=now_str)
            await update_post_status(post_id, PostStatus.SCHEDULED)
            await self.log(owner_id, "info", f"Post recorrente '{post.name}' reagendado para {next_run_str}.")
        else:
            if failure_count == 0 and success_count > 0:
                final_status = PostStatus.PUBLISHED
            elif success_count > 0 and failure_count > 0:
                final_status = PostStatus.PARTIALLY_FAILED
            else:
                final_status = PostStatus.FAILED
            await update_post_status(post_id, final_status)
            await update_post_next_run(post_id, next_run_at=None, last_run_at=now_str)

        await self.broadcast_event("post_updated", {"post_id": post_id, "owner_id": owner_id})
        return success_count > 0

    async def bulk_edit_post(self, owner_id: int, post_id: int, new_text: str) -> Dict[str, Any]:
        """Edits the text/caption of all sent messages for a given post across all destination groups."""
        post = await get_post(owner_id, post_id)
        if not post:
            return {"success": False, "error": "Post não encontrado."}

        client = await telegram_auth.get_active_client(owner_id)
        if not client:
            return {"success": False, "error": "Telegram desconectado."}

        deliveries = await get_latest_deliveries_for_post(post_id)
        successful_deliveries = [d for d in deliveries if d.message_id and d.status in ("sent", "edited")]

        edited_count = 0
        error_count = 0

        for d in successful_deliveries:
            chat_id = parse_chat_id(d.chat_id)
            try:
                if post.media_type:
                    await client.edit_message_caption(chat_id=chat_id, message_id=d.message_id, caption=new_text)
                else:
                    await client.edit_message_text(chat_id=chat_id, message_id=d.message_id, text=new_text)
                await update_delivery_status(d.id, "edited")
                edited_count += 1
            except Exception as e:
                error_count += 1
                await self.log(owner_id, "error", f"Erro ao editar mensagem no grupo {d.chat_title}: {str(e)}")

        # Update post text in DB
        await update_post_content(owner_id, post_id, PostUpdate(text=new_text))
        await self.log(owner_id, "info", f"Edição em massa do post '{post.name}' concluída: {edited_count} editadas, {error_count} erros.")
        await self.broadcast_event("post_updated", {"post_id": post_id, "owner_id": owner_id})

        return {"success": True, "edited_count": edited_count, "error_count": error_count}

    async def bulk_delete_post(self, owner_id: int, post_id: int) -> Dict[str, Any]:
        """Deletes sent messages for a given post across all destination groups."""
        post = await get_post(owner_id, post_id)
        if not post:
            return {"success": False, "error": "Post não encontrado."}

        client = await telegram_auth.get_active_client(owner_id)
        if not client:
            return {"success": False, "error": "Telegram desconectado."}

        deliveries = await get_latest_deliveries_for_post(post_id)
        messages_to_delete = [d for d in deliveries if d.message_id and d.status in ("sent", "edited")]

        deleted_count = 0
        error_count = 0

        for d in messages_to_delete:
            chat_id = parse_chat_id(d.chat_id)
            try:
                await client.delete_messages(chat_id=chat_id, message_ids=[d.message_id])
                await update_delivery_status(d.id, "deleted")
                deleted_count += 1
            except Exception as e:
                error_count += 1
                await self.log(owner_id, "error", f"Erro ao apagar mensagem no grupo {d.chat_title}: {str(e)}")

        await self.log(owner_id, "info", f"Exclusão em massa do post '{post.name}' concluída: {deleted_count} apagadas.")
        await self.broadcast_event("post_updated", {"post_id": post_id, "owner_id": owner_id})

        return {"success": True, "deleted_count": deleted_count, "error_count": error_count}


# Global singleton instance
publisher_engine = PublisherEngine()

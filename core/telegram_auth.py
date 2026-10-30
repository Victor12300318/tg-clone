from __future__ import annotations
import os
import time
import email.utils
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx
import pyrogram
from pyrogram import Client, errors
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired,
    PasswordHashInvalid, ApiIdInvalid, PhoneNumberInvalid,
    FloodWait, ChannelInvalid, PeerIdInvalid, UserDeactivated, UserDeactivatedBan
)

from core.config import (
    SESSIONS_DIR, TG_API_ID, TG_API_HASH,
    TG_WORKERS, TG_MAX_CONCURRENT_TRANSMISSIONS
)
from core.database import (
    save_account, get_active_account, clear_active_account, add_log
)
from core.security import encrypt_session, decrypt_session
from core.models import AccountType, AccountStatusResponse


def _sync_telegram_clock():
    """Compensa desvios no relógio do sistema local para evitar erro de pacote MTProto no Telegram."""
    try:
        r = httpx.head("https://telegram.org", timeout=4.0)
        if "Date" in r.headers:
            server_time = email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp()
            offset = int(server_time - time.time())
            if abs(offset) > 10:
                class SyncedMsgId:
                    last_time = 0
                    offset = 0
                    def __new__(cls) -> int:
                        now = int(time.time()) + offset
                        cls.offset = (cls.offset + 4) if now == cls.last_time else 0
                        msg_id = (now * 2 ** 32) + cls.offset
                        cls.last_time = now
                        return msg_id

                import pyrogram.session.session as ss
                import pyrogram.session.internals as si
                import pyrogram.session.internals.msg_id as sm
                ss.MsgId = SyncedMsgId
                si.MsgId = SyncedMsgId
                sm.MsgId = SyncedMsgId
    except Exception:
        pass


_sync_telegram_clock()


def _patch_pyrogram_upload_recovery():
    """
    Patches Pyrogram 2.0.106's save_file and send_media_group so that:
    1. save_file workers retry individual 512KB parts if a chunk upload fails.
    2. send_media_group catches FilePartMissing (FILE_PART_X_MISSING) during
       messages.UploadMedia and uploads the missing chunk automatically,
       matching the behavior of send_video / send_photo / send_document.
    Leaves Client.get_file, Client.handle_download, and Session.invoke 100% native
    so each download uses a clean, self-contained media session.
    """
    import io
    import math
    import re
    from hashlib import md5
    from pathlib import PurePath
    import pyrogram.client as pc
    from pyrogram import raw, types, utils
    from pyrogram.errors import FilePartMissing
    from pyrogram.methods import Methods

    if getattr(pc.Client, "_tg_clone_patched", False):
        return

    async def _patched_save_file(
        self: Client,
        path,
        file_id: int = None,
        file_part: int = 0,
        progress=None,
        progress_args: tuple = (),
    ):
        """
        Patched save_file that retries individual file parts inside workers instead of
        silently swallowing exceptions and causing [400 FILE_PART_X_MISSING].
        """
        async with self.save_file_semaphore:
            if path is None:
                return None

            async def worker(session):
                while True:
                    data = await queue.get()
                    if data is None:
                        return

                    for attempt in range(4):
                        try:
                            await session.invoke(data)
                            break
                        except Exception as e:
                            if attempt == 3:
                                pc.log.exception(e)
                            else:
                                if not hasattr(session, "_restart_lock"):
                                    session._restart_lock = asyncio.Lock()
                                async with session._restart_lock:
                                    conn = getattr(session, "connection", None)
                                    proto = getattr(conn, "protocol", None) if conn else None
                                    if not proto or getattr(proto, "closed", False) or not session.is_started.is_set():
                                        try:
                                            await session.restart()
                                        except Exception:
                                            pass
                                await asyncio.sleep(0.75 * (attempt + 1))

            part_size = 512 * 1024

            if isinstance(path, (str, PurePath)):
                fp = open(path, "rb")
            elif isinstance(path, io.IOBase):
                fp = path
            else:
                raise ValueError("Invalid file. Expected a file path as string or a binary (not text) file pointer")

            file_name = getattr(fp, "name", "file.jpg")

            fp.seek(0, os.SEEK_END)
            file_size = fp.tell()
            fp.seek(0)

            if file_size == 0:
                raise ValueError("File size equals to 0 B")

            file_size_limit_mib = 4000 if getattr(getattr(self, "me", None), "is_premium", False) else 2000

            if file_size > file_size_limit_mib * 1024 * 1024:
                raise ValueError(f"Can't upload files bigger than {file_size_limit_mib} MiB")

            file_total_parts = int(math.ceil(file_size / part_size))
            is_big = file_size > 10 * 1024 * 1024
            workers_count = 4 if is_big else 1
            is_missing_part = file_id is not None
            file_id = file_id or self.rnd_id()
            md5_sum = md5() if not is_big and not is_missing_part else None
            session = pc.Session(
                self,
                await self.storage.dc_id(),
                await self.storage.auth_key(),
                await self.storage.test_mode(),
                is_media=True,
            )
            workers = [self.loop.create_task(worker(session)) for _ in range(workers_count)]
            queue = asyncio.Queue(1)

            try:
                await session.start()

                fp.seek(part_size * file_part)

                while True:
                    chunk = fp.read(part_size)

                    if not chunk:
                        if not is_big and not is_missing_part:
                            md5_sum = "".join([hex(i)[2:].zfill(2) for i in md5_sum.digest()])
                        break

                    if is_big:
                        rpc = raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            file_total_parts=file_total_parts,
                            bytes=chunk,
                        )
                    else:
                        rpc = raw.functions.upload.SaveFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            bytes=chunk,
                        )

                    await queue.put(rpc)

                    if is_missing_part:
                        return

                    if not is_big and not is_missing_part:
                        md5_sum.update(chunk)

                    file_part += 1

                    if progress:
                        func = pc.functools.partial(
                            progress,
                            min(file_part * part_size, file_size),
                            file_size,
                            *progress_args,
                        )

                        if pc.inspect.iscoroutinefunction(progress):
                            await func()
                        else:
                            await self.loop.run_in_executor(self.executor, func)
            except pyrogram.StopTransmission:
                raise
            except Exception as e:
                pc.log.exception(e)
            else:
                if is_big:
                    return raw.types.InputFileBig(
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                    )
                else:
                    return raw.types.InputFile(
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                        md5_checksum=md5_sum,
                    )
            finally:
                for _ in workers:
                    await queue.put(None)

                await asyncio.gather(*workers)

                await session.stop()

                if isinstance(path, (str, PurePath)):
                    fp.close()

    async def _patched_send_media_group(
        self: Client,
        chat_id,
        media,
        disable_notification: bool = None,
        reply_to_message_id: int = None,
        schedule_date=None,
        protect_content: bool = None,
    ):
        """
        Patched send_media_group that catches FilePartMissing (FILE_PART_X_MISSING)
        during messages.UploadMedia and re-uploads the missing chunk automatically,
        matching the behavior of send_video / send_photo / send_document.
        """
        multi_media = []
        peer = await self.resolve_peer(chat_id)

        async def _upload_with_missing_part_recovery(source_media, build_input_media_fn):
            while True:
                try:
                    return await self.invoke(
                        raw.functions.messages.UploadMedia(
                            peer=peer,
                            media=build_input_media_fn(),
                        )
                    )
                except FilePartMissing as e:
                    await self.save_file(source_media, file_id=uploaded_file.id, file_part=e.value)

        for i in media:
            if isinstance(i, types.InputMediaPhoto):
                if isinstance(i.media, str):
                    if os.path.isfile(i.media):
                        uploaded_file = await self.save_file(i.media)
                        media_res = await _upload_with_missing_part_recovery(
                            i.media,
                            lambda: raw.types.InputMediaUploadedPhoto(
                                file=uploaded_file,
                                spoiler=i.has_spoiler,
                            ),
                        )
                        media_obj = raw.types.InputMediaPhoto(
                            id=raw.types.InputPhoto(
                                id=media_res.photo.id,
                                access_hash=media_res.photo.access_hash,
                                file_reference=media_res.photo.file_reference,
                            ),
                            spoiler=i.has_spoiler,
                        )
                    elif re.match("^https?://", i.media):
                        media_res = await self.invoke(
                            raw.functions.messages.UploadMedia(
                                peer=peer,
                                media=raw.types.InputMediaPhotoExternal(
                                    url=i.media,
                                    spoiler=i.has_spoiler,
                                ),
                            )
                        )
                        media_obj = raw.types.InputMediaPhoto(
                            id=raw.types.InputPhoto(
                                id=media_res.photo.id,
                                access_hash=media_res.photo.access_hash,
                                file_reference=media_res.photo.file_reference,
                            ),
                            spoiler=i.has_spoiler,
                        )
                    else:
                        media_obj = utils.get_input_media_from_file_id(i.media, pc.FileType.PHOTO)
                else:
                    uploaded_file = await self.save_file(i.media)
                    media_res = await _upload_with_missing_part_recovery(
                        i.media,
                        lambda: raw.types.InputMediaUploadedPhoto(
                            file=uploaded_file,
                            spoiler=i.has_spoiler,
                        ),
                    )
                    media_obj = raw.types.InputMediaPhoto(
                        id=raw.types.InputPhoto(
                            id=media_res.photo.id,
                            access_hash=media_res.photo.access_hash,
                            file_reference=media_res.photo.file_reference,
                        ),
                        spoiler=i.has_spoiler,
                    )
            elif isinstance(i, types.InputMediaVideo):
                if isinstance(i.media, str):
                    if os.path.isfile(i.media):
                        uploaded_file = await self.save_file(i.media)
                        uploaded_thumb = await self.save_file(i.thumb)
                        media_res = await _upload_with_missing_part_recovery(
                            i.media,
                            lambda: raw.types.InputMediaUploadedDocument(
                                file=uploaded_file,
                                thumb=uploaded_thumb,
                                spoiler=i.has_spoiler,
                                mime_type=self.guess_mime_type(i.media) or "video/mp4",
                                attributes=[
                                    raw.types.DocumentAttributeVideo(
                                        supports_streaming=i.supports_streaming or None,
                                        duration=i.duration,
                                        w=i.width,
                                        h=i.height,
                                    ),
                                    raw.types.DocumentAttributeFilename(file_name=os.path.basename(i.media)),
                                ],
                            ),
                        )
                        media_obj = raw.types.InputMediaDocument(
                            id=raw.types.InputDocument(
                                id=media_res.document.id,
                                access_hash=media_res.document.access_hash,
                                file_reference=media_res.document.file_reference,
                            ),
                            spoiler=i.has_spoiler,
                        )
                    elif re.match("^https?://", i.media):
                        media_res = await self.invoke(
                            raw.functions.messages.UploadMedia(
                                peer=peer,
                                media=raw.types.InputMediaDocumentExternal(
                                    url=i.media,
                                    spoiler=i.has_spoiler,
                                ),
                            )
                        )
                        media_obj = raw.types.InputMediaDocument(
                            id=raw.types.InputDocument(
                                id=media_res.document.id,
                                access_hash=media_res.document.access_hash,
                                file_reference=media_res.document.file_reference,
                            ),
                            spoiler=i.has_spoiler,
                        )
                    else:
                        media_obj = utils.get_input_media_from_file_id(i.media, pc.FileType.VIDEO)
                else:
                    uploaded_file = await self.save_file(i.media)
                    uploaded_thumb = await self.save_file(i.thumb)
                    fname = getattr(i.media, "name", "video.mp4")
                    media_res = await _upload_with_missing_part_recovery(
                        i.media,
                        lambda: raw.types.InputMediaUploadedDocument(
                            file=uploaded_file,
                            thumb=uploaded_thumb,
                            spoiler=i.has_spoiler,
                            mime_type=self.guess_mime_type(fname) or "video/mp4",
                            attributes=[
                                raw.types.DocumentAttributeVideo(
                                    supports_streaming=i.supports_streaming or None,
                                    duration=i.duration,
                                    w=i.width,
                                    h=i.height,
                                ),
                                raw.types.DocumentAttributeFilename(file_name=fname),
                            ],
                        ),
                    )
                    media_obj = raw.types.InputMediaDocument(
                        id=raw.types.InputDocument(
                            id=media_res.document.id,
                            access_hash=media_res.document.access_hash,
                            file_reference=media_res.document.file_reference,
                        ),
                        spoiler=i.has_spoiler,
                    )
            elif isinstance(i, types.InputMediaAudio):
                if isinstance(i.media, str):
                    if os.path.isfile(i.media):
                        uploaded_file = await self.save_file(i.media)
                        uploaded_thumb = await self.save_file(i.thumb)
                        media_res = await _upload_with_missing_part_recovery(
                            i.media,
                            lambda: raw.types.InputMediaUploadedDocument(
                                mime_type=self.guess_mime_type(i.media) or "audio/mpeg",
                                file=uploaded_file,
                                thumb=uploaded_thumb,
                                attributes=[
                                    raw.types.DocumentAttributeAudio(
                                        duration=i.duration,
                                        performer=i.performer,
                                        title=i.title,
                                    ),
                                    raw.types.DocumentAttributeFilename(file_name=os.path.basename(i.media)),
                                ],
                            ),
                        )
                        media_obj = raw.types.InputMediaDocument(
                            id=raw.types.InputDocument(
                                id=media_res.document.id,
                                access_hash=media_res.document.access_hash,
                                file_reference=media_res.document.file_reference,
                            )
                        )
                    elif re.match("^https?://", i.media):
                        media_res = await self.invoke(
                            raw.functions.messages.UploadMedia(
                                peer=peer,
                                media=raw.types.InputMediaDocumentExternal(url=i.media),
                            )
                        )
                        media_obj = raw.types.InputMediaDocument(
                            id=raw.types.InputDocument(
                                id=media_res.document.id,
                                access_hash=media_res.document.access_hash,
                                file_reference=media_res.document.file_reference,
                            )
                        )
                    else:
                        media_obj = utils.get_input_media_from_file_id(i.media, pc.FileType.AUDIO)
                else:
                    uploaded_file = await self.save_file(i.media)
                    uploaded_thumb = await self.save_file(i.thumb)
                    fname = getattr(i.media, "name", "audio.mp3")
                    media_res = await _upload_with_missing_part_recovery(
                        i.media,
                        lambda: raw.types.InputMediaUploadedDocument(
                            mime_type=self.guess_mime_type(fname) or "audio/mpeg",
                            file=uploaded_file,
                            thumb=uploaded_thumb,
                            attributes=[
                                raw.types.DocumentAttributeAudio(
                                    duration=i.duration,
                                    performer=i.performer,
                                    title=i.title,
                                ),
                                raw.types.DocumentAttributeFilename(file_name=fname),
                            ],
                        ),
                    )
                    media_obj = raw.types.InputMediaDocument(
                        id=raw.types.InputDocument(
                            id=media_res.document.id,
                            access_hash=media_res.document.access_hash,
                            file_reference=media_res.document.file_reference,
                        )
                    )
            elif isinstance(i, types.InputMediaDocument):
                if isinstance(i.media, str):
                    if os.path.isfile(i.media):
                        uploaded_file = await self.save_file(i.media)
                        uploaded_thumb = await self.save_file(i.thumb)
                        media_res = await _upload_with_missing_part_recovery(
                            i.media,
                            lambda: raw.types.InputMediaUploadedDocument(
                                mime_type=self.guess_mime_type(i.media) or "application/zip",
                                file=uploaded_file,
                                thumb=uploaded_thumb,
                                attributes=[
                                    raw.types.DocumentAttributeFilename(file_name=os.path.basename(i.media))
                                ],
                            ),
                        )
                        media_obj = raw.types.InputMediaDocument(
                            id=raw.types.InputDocument(
                                id=media_res.document.id,
                                access_hash=media_res.document.access_hash,
                                file_reference=media_res.document.file_reference,
                            )
                        )
                    elif re.match("^https?://", i.media):
                        media_res = await self.invoke(
                            raw.functions.messages.UploadMedia(
                                peer=peer,
                                media=raw.types.InputMediaDocumentExternal(url=i.media),
                            )
                        )
                        media_obj = raw.types.InputMediaDocument(
                            id=raw.types.InputDocument(
                                id=media_res.document.id,
                                access_hash=media_res.document.access_hash,
                                file_reference=media_res.document.file_reference,
                            )
                        )
                    else:
                        media_obj = utils.get_input_media_from_file_id(i.media, pc.FileType.DOCUMENT)
                else:
                    uploaded_file = await self.save_file(i.media)
                    uploaded_thumb = await self.save_file(i.thumb)
                    fname = getattr(i.media, "name", "file.zip")
                    media_res = await _upload_with_missing_part_recovery(
                        i.media,
                        lambda: raw.types.InputMediaUploadedDocument(
                            mime_type=self.guess_mime_type(fname) or "application/zip",
                            file=uploaded_file,
                            thumb=uploaded_thumb,
                            attributes=[
                                raw.types.DocumentAttributeFilename(file_name=fname)
                            ],
                        ),
                    )
                    media_obj = raw.types.InputMediaDocument(
                        id=raw.types.InputDocument(
                            id=media_res.document.id,
                            access_hash=media_res.document.access_hash,
                            file_reference=media_res.document.file_reference,
                        )
                    )
            else:
                raise ValueError(f"{i.__class__.__name__} is not a supported type for send_media_group")

            multi_media.append(
                raw.types.InputSingleMedia(
                    media=media_obj,
                    random_id=self.rnd_id(),
                    **await self.parser.parse(i.caption, i.parse_mode),
                )
            )

        r = await self.invoke(
            raw.functions.messages.SendMultiMedia(
                peer=peer,
                multi_media=multi_media,
                silent=disable_notification or None,
                reply_to_msg_id=reply_to_message_id,
                schedule_date=utils.datetime_to_timestamp(schedule_date),
                noforwards=protect_content,
            ),
            sleep_threshold=60,
        )

        return await utils.parse_messages(
            self,
            raw.types.messages.Messages(
                messages=[
                    m.message
                    for m in filter(
                        lambda u: isinstance(
                            u,
                            (
                                raw.types.UpdateNewMessage,
                                raw.types.UpdateNewChannelMessage,
                                raw.types.UpdateNewScheduledMessage,
                            ),
                        ),
                        r.updates,
                    )
                ],
                users=r.users,
                chats=r.chats,
            ),
        )

    pc.Client.save_file = _patched_save_file
    pc.Client.send_media_group = _patched_send_media_group
    Methods.save_file = _patched_save_file
    Methods.send_media_group = _patched_send_media_group
    pc.Client._tg_clone_patched = True


_patch_pyrogram_upload_recovery()


class TelegramAuthManager:
    def __init__(self):
        self._pending_clients: Dict[str, Client] = {}
        self._active_clients: Dict[int, Client] = {}
        self._locks: Dict[int, asyncio.Lock] = {}

    def _lock_for(self, owner_id: int) -> asyncio.Lock:
        if owner_id not in self._locks:
            self._locks[owner_id] = asyncio.Lock()
        return self._locks[owner_id]

    async def get_active_client(self, owner_id: int) -> Optional[Client]:
        async with self._lock_for(owner_id):
            client = self._active_clients.get(owner_id)
            if client is not None and client.is_connected:
                return client

            account = await get_active_account(owner_id)
            if not account:
                return None

            try:
                api_id = TG_API_ID or account.get("api_id")
                api_hash = TG_API_HASH or account.get("api_hash")

                if account.get("session_string"):
                    client = Client(
                        name=f"owner_{owner_id}",
                        session_string=decrypt_session(account["session_string"]),
                        api_id=api_id,
                        api_hash=api_hash,
                        in_memory=True,
                        workers=TG_WORKERS,
                        max_concurrent_transmissions=TG_MAX_CONCURRENT_TRANSMISSIONS
                    )
                elif account.get("type") == AccountType.BOT.value and account.get("bot_token"):
                    client = Client(
                        name=f"owner_{owner_id}_bot",
                        api_id=api_id,
                        api_hash=api_hash,
                        bot_token=account["bot_token"],
                        in_memory=True,
                        workers=TG_WORKERS,
                        max_concurrent_transmissions=TG_MAX_CONCURRENT_TRANSMISSIONS
                    )
                else:
                    return None

                await client.start()
                if not getattr(client, "me", None):
                    try:
                        client.me = await client.get_me()
                    except Exception:
                        pass
                if not getattr(client, "me", None) or getattr(client.me, "is_premium", None) is None:
                    client.me = type("Me", (), {"is_premium": False, "id": account.get("tg_user_id", 0)})()

                self._active_clients[owner_id] = client

                # Prime in-memory peer cache so all channel access_hashes are ready
                try:
                    async for _ in client.get_dialogs(limit=200):
                        pass
                except Exception:
                    pass

                return client
            except Exception as e:
                await add_log(owner_id, None, "error", f"Falha ao conectar cliente ativo: {str(e)}")
                return None

    async def get_status(self, owner_id: int) -> AccountStatusResponse:
        account = await get_active_account(owner_id)
        if not account:
            return AccountStatusResponse(is_authenticated=False)

        client = await self.get_active_client(owner_id)
        if not client:
            return AccountStatusResponse(is_authenticated=False)

        try:
            me = await client.get_me()
            return AccountStatusResponse(
                is_authenticated=True,
                account_type=AccountType(account["type"]),
                account_name=f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or str(me.id),
                phone_number=account.get("phone_number"),
                user_id=me.id,
                username=me.username
            )
        except Exception:
            return AccountStatusResponse(is_authenticated=False)

    async def send_code(self, owner_id: int, phone_number: str) -> Dict[str, Any]:
        clean_phone = phone_number.strip().replace(" ", "").replace("-", "")

        # Stop and remove existing pending client for this phone if any
        if clean_phone in self._pending_clients:
            try:
                old_client = self._pending_clients.pop(clean_phone)
                if old_client.is_connected:
                    await old_client.disconnect()
            except Exception:
                pass

        client = Client(
            name=f"pending_{clean_phone}",
            api_id=TG_API_ID,
            api_hash=TG_API_HASH,
            in_memory=True,
            workers=TG_WORKERS,
            max_concurrent_transmissions=TG_MAX_CONCURRENT_TRANSMISSIONS
        )

        try:
            await client.connect()
            sent_code = await client.send_code(clean_phone)
            self._pending_clients[clean_phone] = client
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "phone_number": clean_phone,
                "message": "Código de verificação enviado com sucesso!"
            }
        except FloodWait as e:
            await client.disconnect()
            return {
                "success": False,
                "error": f"Muitas tentativas. Aguarde {e.value} segundos antes de tentar novamente."
            }
        except ApiIdInvalid:
            await client.disconnect()
            return {
                "success": False,
                "error": "API ID ou API HASH inválidos."
            }
        except PhoneNumberInvalid:
            await client.disconnect()
            return {
                "success": False,
                "error": "Número de telefone inválido. Use o formato internacional (ex: +5511999999999)."
            }
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            return {
                "success": False,
                "error": f"Erro ao enviar código: {str(e)}"
            }

    async def verify_code(
        self,
        owner_id: int,
        phone_number: str,
        phone_code_hash: str,
        phone_code: str
    ) -> Dict[str, Any]:
        clean_phone = phone_number.strip().replace(" ", "").replace("-", "")
        client = self._pending_clients.get(clean_phone)

        if not client:
            client = Client(
                name=f"pending_{clean_phone}",
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                in_memory=True
            )
            await client.connect()
            self._pending_clients[clean_phone] = client

        try:
            try:
                signed_in = await client.sign_in(
                    phone_number=clean_phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=phone_code.strip()
                )
            except SessionPasswordNeeded:
                return {
                    "success": True,
                    "requires_2fa": True,
                    "phone_number": clean_phone,
                    "message": "Autenticação em duas etapas (2FA) detectada. Digite sua senha."
                }

            # If sign in succeeded without 2FA
            session_str = await client.export_session_string()
            me = await client.get_me()
            client.me = me

            await save_account(
                owner_id=owner_id,
                account_type=AccountType.USER.value,
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                phone_number=clean_phone,
                session_string=encrypt_session(session_str),
                tg_user_id=me.id,
                username=me.username,
                first_name=me.first_name
            )

            self._swap_active_client(owner_id, client)
            self._pending_clients.pop(clean_phone, None)

            return {
                "success": True,
                "requires_2fa": False,
                "message": f"Conectado com sucesso como {me.first_name or me.username or me.id}!",
                "user": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username
                }
            }
        except PhoneCodeInvalid:
            return {"success": False, "error": "Código de verificação incorreto."}
        except PhoneCodeExpired:
            return {"success": False, "error": "Código de verificação expirou. Solicite um novo."}
        except Exception as e:
            return {"success": False, "error": f"Erro na validação do código: {str(e)}"}

    async def verify_password(
        self,
        owner_id: int,
        phone_number: str,
        password: str
    ) -> Dict[str, Any]:
        clean_phone = phone_number.strip().replace(" ", "").replace("-", "")
        client = self._pending_clients.get(clean_phone)

        if not client:
            return {
                "success": False,
                "error": "Sessão pendente expirou. Solicite o código SMS novamente."
            }

        try:
            await client.check_password(password=password)
            session_str = await client.export_session_string()
            me = await client.get_me()
            client.me = me

            await save_account(
                owner_id=owner_id,
                account_type=AccountType.USER.value,
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                phone_number=clean_phone,
                session_string=encrypt_session(session_str),
                tg_user_id=me.id,
                username=me.username,
                first_name=me.first_name
            )

            self._swap_active_client(owner_id, client)
            self._pending_clients.pop(clean_phone, None)

            return {
                "success": True,
                "message": f"Conectado com sucesso como {me.first_name or me.username or me.id}!",
                "user": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username
                }
            }
        except PasswordHashInvalid:
            return {"success": False, "error": "Senha 2FA incorreta."}
        except Exception as e:
            return {"success": False, "error": f"Erro na verificação 2FA: {str(e)}"}

    async def bot_login(self, owner_id: int, bot_token: str) -> Dict[str, Any]:
        client = Client(
            name=f"owner_{owner_id}_bot",
            api_id=TG_API_ID,
            api_hash=TG_API_HASH,
            bot_token=bot_token.strip(),
            in_memory=True,
            workers=TG_WORKERS,
            max_concurrent_transmissions=TG_MAX_CONCURRENT_TRANSMISSIONS
        )

        try:
            await client.start()
            session_str = await client.export_session_string()
            me = await client.get_me()
            client.me = me

            await save_account(
                owner_id=owner_id,
                account_type=AccountType.BOT.value,
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                bot_token=bot_token.strip(),
                session_string=encrypt_session(session_str),
                tg_user_id=me.id,
                username=me.username,
                first_name=me.first_name
            )

            self._swap_active_client(owner_id, client)

            return {
                "success": True,
                "message": f"Bot @{me.username} conectado com sucesso!",
                "user": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username
                }
            }
        except Exception as e:
            try:
                if client.is_connected:
                    await client.stop()
            except Exception:
                pass
            return {"success": False, "error": f"Erro ao conectar bot: {str(e)}"}

    def _swap_active_client(self, owner_id: int, client: Client) -> None:
        old = self._active_clients.get(owner_id)
        if old and old is not client and old.is_connected:
            try:
                asyncio.get_event_loop().create_task(old.stop())
            except Exception:
                pass
        self._active_clients[owner_id] = client

    async def logout(self, owner_id: int) -> bool:
        async with self._lock_for(owner_id):
            client = self._active_clients.pop(owner_id, None)
            if client:
                try:
                    if client.is_connected:
                        await client.stop()
                except Exception:
                    pass
            await clear_active_account(owner_id)
            return True

    async def get_dialogs_list(self, owner_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        client = await self.get_active_client(owner_id)
        if not client:
            return []

        dialogs = []
        try:
            async for dialog in client.get_dialogs(limit=limit):
                chat = dialog.chat
                chat_type = str(chat.type).split(".")[-1].lower()

                # Skip dead basic groups that were migrated to supergroups
                if getattr(dialog.top_message, "migrate_to_chat_id", None) is not None:
                    continue
                if chat_type == "group" and getattr(chat, "members_count", None) == 0:
                    continue

                dialogs.append({
                    "id": chat.id,
                    "title": chat.title or f"{chat.first_name or ''} {chat.last_name or ''}".strip() or str(chat.id),
                    "username": chat.username,
                    "type": chat_type,
                    "members_count": getattr(chat, "members_count", None)
                })
        except Exception as e:
            await add_log(owner_id, None, "error", f"Erro ao listar diálogos: {str(e)}")
        return dialogs

    async def check_chat(self, owner_id: int, chat_identifier: str) -> Dict[str, Any]:
        client = await self.get_active_client(owner_id)
        if not client:
            return {"valid": False, "error": "Nenhuma conta do Telegram conectada."}

        # Parse chat identifier (convert to int if digits or starts with -100)
        target: Any = chat_identifier.strip()
        try:
            target = int(target)
        except ValueError:
            pass

        try:
            chat = await client.get_chat(target)
            return {
                "valid": True,
                "id": chat.id,
                "title": chat.title or f"{chat.first_name or ''} {chat.last_name or ''}".strip() or str(chat.id),
                "username": chat.username,
                "type": str(chat.type).split(".")[-1].lower(),
                "description": getattr(chat, "description", None)
            }
        except ChannelInvalid:
            return {"valid": False, "error": "Acesso não permitido. Verifique se você faz parte do canal/grupo."}
        except PeerIdInvalid:
            return {"valid": False, "error": "ID ou Username de chat inválido."}
        except Exception as e:
            return {"valid": False, "error": f"Não foi possível acessar o chat: {str(e)}"}


# Global singleton instance
telegram_auth = TelegramAuthManager()

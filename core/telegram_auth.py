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

from core.config import SESSIONS_DIR, TG_API_ID, TG_API_HASH
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
                        in_memory=True
                    )
                elif account.get("type") == AccountType.BOT.value and account.get("bot_token"):
                    client = Client(
                        name=f"owner_{owner_id}_bot",
                        api_id=api_id,
                        api_hash=api_hash,
                        bot_token=account["bot_token"],
                        in_memory=True
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
            in_memory=True
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
            in_memory=True
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

from __future__ import annotations
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.models import (
    AccountStatusResponse, AuthSendCodeRequest,
    AuthVerifyCodeRequest, AuthVerifyPasswordRequest, AuthBotLoginRequest
)
from core.security import get_current_user
from core.telegram_auth import telegram_auth

router = APIRouter(
    prefix="/api/auth", tags=["auth"],
    dependencies=[Depends(get_current_user)]
)


class CheckChatRequest(BaseModel):
    chat_identifier: str


@router.get("/status", response_model=AccountStatusResponse)
async def get_auth_status(user_id: int = Depends(get_current_user)):
    return await telegram_auth.get_status(user_id)


@router.post("/send-code")
async def send_auth_code(req: AuthSendCodeRequest, user_id: int = Depends(get_current_user)):
    res = await telegram_auth.send_code(user_id, req.phone_number)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Erro ao enviar código."))
    return res


@router.post("/verify-code")
async def verify_auth_code(req: AuthVerifyCodeRequest, user_id: int = Depends(get_current_user)):
    res = await telegram_auth.verify_code(
        owner_id=user_id,
        phone_number=req.phone_number,
        phone_code_hash=req.phone_code_hash,
        phone_code=req.phone_code
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Código inválido."))
    return res


@router.post("/verify-password")
async def verify_auth_password(req: AuthVerifyPasswordRequest, user_id: int = Depends(get_current_user)):
    res = await telegram_auth.verify_password(
        owner_id=user_id,
        phone_number=req.phone_number,
        password=req.password
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Senha 2FA incorreta."))
    return res


@router.post("/bot-login")
async def bot_login(req: AuthBotLoginRequest, user_id: int = Depends(get_current_user)):
    res = await telegram_auth.bot_login(user_id, req.bot_token)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Falha ao autenticar Bot."))
    return res


@router.get("/dialogs")
async def get_dialogs(user_id: int = Depends(get_current_user), limit: int = Query(100, ge=1, le=500)):
    return await telegram_auth.get_dialogs_list(user_id, limit=limit)


@router.post("/check-chat")
async def check_chat(req: CheckChatRequest, user_id: int = Depends(get_current_user)):
    return await telegram_auth.check_chat(user_id, req.chat_identifier)


@router.post("/logout")
async def logout(user_id: int = Depends(get_current_user)):
    await telegram_auth.logout(user_id)
    return {"success": True, "message": "Desconectado com sucesso."}

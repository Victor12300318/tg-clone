from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends, HTTPException

from core.models import TaskCreate, TaskUpdate, TaskResponse, TaskStatus
from core.database import (
    create_task, get_task, get_all_tasks, update_task, delete_task, get_active_account, get_recent_logs
)
from core.security import get_current_user
from core.subscription import require_subscription
from core.telegram_auth import telegram_auth
from core.cloner_engine import cloner_engine

router = APIRouter(
    prefix="/api/tasks", tags=["tasks"],
    dependencies=[Depends(get_current_user)]
)


async def _ensure_telegram_connected(user_id: int):
    acc = await get_active_account(user_id)
    if not acc or not (acc.get("session_string") or acc.get("bot_token")):
        raise HTTPException(
            status_code=400,
            detail="Nenhuma conta do Telegram conectada. Por favor, conecte sua conta do Telegram no painel antes de clonar."
        )
    client = await telegram_auth.get_active_client(user_id)
    if not client or not client.is_connected:
        raise HTTPException(
            status_code=400,
            detail="Sua sessão do Telegram expirou ou está desconectada. Por favor, reconecte sua conta do Telegram no painel."
        )


@router.get("", response_model=List[TaskResponse])
async def list_tasks(user_id: int = Depends(get_current_user)):
    return await get_all_tasks(user_id)


@router.post("", response_model=TaskResponse)
async def create_new_task(task_in: TaskCreate, user_id: int = Depends(require_subscription)):
    origin_title = None
    dest_title = None

    # Try resolving chat titles from Telegram
    try:
        orig_info = await telegram_auth.check_chat(user_id, task_in.origin_chat)
        if orig_info.get("valid"):
            origin_title = orig_info.get("title")
    except Exception:
        pass

    try:
        dest_info = await telegram_auth.check_chat(user_id, task_in.dest_chat)
        if dest_info.get("valid"):
            dest_title = dest_info.get("title")
    except Exception:
        pass

    return await create_task(user_id, task_in, origin_title=origin_title, dest_title=dest_title)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task_by_id(task_id: int, user_id: int = Depends(get_current_user)):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return task


@router.get("/{task_id}/logs")
async def get_task_logs_endpoint(task_id: int, user_id: int = Depends(get_current_user), limit: int = 100):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    logs = await get_recent_logs(user_id, limit=limit, task_id=task_id)
    return [log.model_dump() if hasattr(log, "model_dump") else log.dict() for log in logs]


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task_endpoint(task_id: int, task_in: TaskUpdate, user_id: int = Depends(get_current_user)):
    existing = await get_task(user_id, task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    if existing.status == TaskStatus.RUNNING.value:
        raise HTTPException(
            status_code=400,
            detail="Não é possível editar uma tarefa em execução. Pause a tarefa primeiro."
        )

    origin_title = None
    dest_title = None

    if task_in.origin_chat and task_in.origin_chat != existing.origin_chat:
        try:
            orig_info = await telegram_auth.check_chat(user_id, task_in.origin_chat)
            if orig_info.get("valid"):
                origin_title = orig_info.get("title")
        except Exception:
            pass

    if task_in.dest_chat and task_in.dest_chat != existing.dest_chat:
        try:
            dest_info = await telegram_auth.check_chat(user_id, task_in.dest_chat)
            if dest_info.get("valid"):
                dest_title = dest_info.get("title")
        except Exception:
            pass

    updated = await update_task(user_id, task_id, task_in, origin_title=origin_title, dest_title=dest_title)
    if not updated:
        raise HTTPException(status_code=500, detail="Erro ao atualizar tarefa.")
    return updated


@router.post("/{task_id}/start")
async def start_task_endpoint(task_id: int, user_id: int = Depends(get_current_user)):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    await _ensure_telegram_connected(user_id)
    success = await cloner_engine.start_task(user_id, task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Não foi possível iniciar a tarefa.")
    return {"success": True, "message": "Tarefa iniciada com sucesso."}


@router.post("/{task_id}/pause")
async def pause_task_endpoint(task_id: int, user_id: int = Depends(get_current_user)):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    success = await cloner_engine.pause_task(user_id, task_id)
    return {"success": success}


@router.post("/{task_id}/resume")
async def resume_task_endpoint(task_id: int, user_id: int = Depends(get_current_user)):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    await _ensure_telegram_connected(user_id)
    success = await cloner_engine.resume_task(user_id, task_id)
    return {"success": success}


@router.post("/{task_id}/cancel")
async def cancel_task_endpoint(task_id: int, user_id: int = Depends(get_current_user)):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    success = await cloner_engine.cancel_task(user_id, task_id)
    return {"success": success}


@router.post("/{task_id}/sync-new")
async def sync_new_task_messages_endpoint(task_id: int, user_id: int = Depends(get_current_user)):
    task = await get_task(user_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    await _ensure_telegram_connected(user_id)
    res = await cloner_engine.sync_new_messages(user_id, task_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Erro ao sincronizar."))
    return res


@router.delete("/{task_id}")
async def delete_task_endpoint(task_id: int, user_id: int = Depends(get_current_user)):
    if cloner_engine.is_task_running(task_id):
        await cloner_engine.cancel_task(user_id, task_id)

    deleted = await delete_task(user_id, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return {"success": True, "message": "Tarefa removida com sucesso."}

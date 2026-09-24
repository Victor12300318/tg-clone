from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends, HTTPException

from core.models import ManagedGroupCreate, ManagedGroupResponse
from core.database import (
    add_managed_group, add_managed_groups_bulk, get_all_managed_groups,
    get_managed_group_by_id, delete_managed_group
)
from core.security import get_current_user

router = APIRouter(
    prefix="/api/groups", tags=["groups"],
    dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=List[ManagedGroupResponse])
async def list_groups(user_id: int = Depends(get_current_user)):
    return await get_all_managed_groups(user_id)


@router.post("", response_model=ManagedGroupResponse)
async def create_group(group_in: ManagedGroupCreate, user_id: int = Depends(get_current_user)):
    return await add_managed_group(user_id, group_in)


@router.post("/bulk", response_model=List[ManagedGroupResponse])
async def create_groups_bulk(groups_in: List[ManagedGroupCreate], user_id: int = Depends(get_current_user)):
    return await add_managed_groups_bulk(user_id, groups_in)


@router.delete("/{group_id}")
async def remove_group(group_id: int, user_id: int = Depends(get_current_user)):
    existing = await get_managed_group_by_id(user_id, group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    deleted = await delete_managed_group(user_id, group_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Erro ao excluir grupo.")
    return {"success": True, "message": "Grupo removido com sucesso."}

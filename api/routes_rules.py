from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends, HTTPException

from core.models import TextRuleCreate, TextRuleUpdate, TextRuleResponse
from core.database import (
    create_text_rule, get_all_text_rules, update_text_rule, delete_text_rule
)
from core.security import get_current_user

router = APIRouter(
    prefix="/api/rules", tags=["rules"],
    dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=List[TextRuleResponse])
async def list_rules(user_id: int = Depends(get_current_user)):
    return await get_all_text_rules(user_id)


@router.post("", response_model=TextRuleResponse)
async def create_rule(rule_in: TextRuleCreate, user_id: int = Depends(get_current_user)):
    return await create_text_rule(user_id, rule_in)


@router.patch("/{rule_id}", response_model=TextRuleResponse)
async def patch_rule(rule_id: int, rule_in: TextRuleUpdate, user_id: int = Depends(get_current_user)):
    updated = await update_text_rule(user_id, rule_id, rule_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    return updated


@router.delete("/{rule_id}")
async def remove_rule(rule_id: int, user_id: int = Depends(get_current_user)):
    deleted = await delete_text_rule(user_id, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    return {"success": True, "message": "Regra removida com sucesso."}

from __future__ import annotations
from fastapi import Depends, HTTPException

from core.database import get_user_by_id
from core.security import get_current_user


async def require_subscription(user_id: int = Depends(get_current_user)) -> int:
    """Gate de Assinatura: criação de recursos exige assinatura ativa. Stub sem billing."""
    user = await get_user_by_id(user_id)
    if not user or not user.get("subscription_active"):
        raise HTTPException(status_code=402, detail="Assinatura inativa.")
    return user_id

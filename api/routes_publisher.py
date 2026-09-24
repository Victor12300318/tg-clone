from __future__ import annotations
import asyncio
import os
import shutil
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from core.config import UPLOADS_DIR
from core.models import PostCreate, PostUpdate, PostResponse, PostDeliveryResponse
from core.database import (
    create_post, get_post, get_all_posts, update_post_content,
    delete_post, get_post_deliveries
)
from core.security import get_current_user
from core.subscription import require_subscription

router = APIRouter(
    prefix="/api/publisher", tags=["publisher"],
    dependencies=[Depends(get_current_user)]
)


@router.post("/uploads")
async def upload_media_file(file: UploadFile = File(...)):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")

    # Generate safe unique filename
    ext = os.path.splitext(file.filename)[1]
    safe_name = f"{uuid.uuid4().hex[:12]}_{file.filename}"
    file_path = UPLOADS_DIR / safe_name

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar arquivo: {str(e)}")

    # Detect media type from content_type or extension
    content_type = (file.content_type or "").lower()
    if "image" in content_type:
        media_type = "photo"
    elif "video" in content_type:
        media_type = "video"
    else:
        media_type = "document"

    return {
        "success": True,
        "filename": file.filename,
        "file_path": str(file_path),
        "media_type": media_type
    }


@router.get("/posts", response_model=List[PostResponse])
async def list_posts(user_id: int = Depends(get_current_user)):
    return await get_all_posts(user_id)


@router.post("/posts", response_model=PostResponse)
async def create_new_post(post_in: PostCreate, user_id: int = Depends(require_subscription)):
    post = await create_post(user_id, post_in)
    return post


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post_by_id(post_id: int, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")
    return post


@router.get("/posts/{post_id}/deliveries", response_model=List[PostDeliveryResponse])
async def get_deliveries_for_post(post_id: int, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")
    return await get_post_deliveries(user_id, post_id)


@router.put("/posts/{post_id}", response_model=PostResponse)
async def update_post(post_id: int, post_in: PostUpdate, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")
    updated = await update_post_content(user_id, post_id, post_in)
    if not updated:
        raise HTTPException(status_code=500, detail="Erro ao atualizar postagem.")
    return updated


from pydantic import BaseModel
from core.publisher_engine import publisher_engine


class BulkEditRequest(BaseModel):
    new_text: str


@router.post("/posts/{post_id}/publish-now")
async def publish_post_now(post_id: int, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")

    # Run in background
    asyncio.create_task(publisher_engine.execute_post_run(user_id, post_id))
    return {"success": True, "message": "Disparo iniciado com sucesso."}


@router.post("/posts/{post_id}/bulk-edit")
async def bulk_edit_post_endpoint(post_id: int, req: BulkEditRequest, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")

    result = await publisher_engine.bulk_edit_post(user_id, post_id, req.new_text)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Erro ao editar mensagens."))
    return result


@router.post("/posts/{post_id}/bulk-delete")
async def bulk_delete_post_endpoint(post_id: int, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")

    result = await publisher_engine.bulk_delete_post(user_id, post_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Erro ao apagar mensagens."))
    return result


@router.delete("/posts/{post_id}")
async def remove_post(post_id: int, user_id: int = Depends(get_current_user)):
    post = await get_post(user_id, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post não encontrado.")

    # Remove local uploaded media file if exists
    if post.media_path and os.path.exists(post.media_path):
        try:
            os.remove(post.media_path)
        except Exception:
            pass

    deleted = await delete_post(user_id, post_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Erro ao remover postagem.")
    return {"success": True, "message": "Postagem removida com sucesso."}

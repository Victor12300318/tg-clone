import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport
from app import app
from core.database import init_db, create_task, update_task_status, update_task_progress, record_task_message, save_account
from core.models import TaskCreate, TaskMode, TaskStatus
from core.security import verify_token
from core.cloner_engine import cloner_engine
from core.telegram_auth import telegram_auth


@pytest.mark.asyncio
async def test_sync_new_messages_endpoint(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_sync.db")
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Register user & account
            reg = await client.post("/api/users/register", json={"email": "sync@test.com", "password": "senha12345"})
            assert reg.status_code == 200
            token = reg.json()["token"]
            user_id = verify_token(token)
            headers = {"Authorization": f"Bearer {token}"}
            await save_account(user_id, "user", 123, "hash", "+5511999999999", session_string="mock_session")

            # Create task
            task_res = await client.post("/api/tasks", headers=headers, json={
                "name": "Sync Task",
                "mode": "historical",
                "origin_chat": "-1001",
                "dest_chat": "-1002",
                "start_message_id": 1,
                "end_message_id": 50,
                "media_types": ["all"]
            })
            assert task_res.status_code == 200
            task_id = task_res.json()["id"]

            # Mock task as completed up to message 50
            await update_task_status(user_id, task_id, TaskStatus.COMPLETED)
            await update_task_progress(user_id, task_id, current_message_id=50, processed_messages=50, copied_count=50)
            await record_task_message(task_id, 50, "copied")

            # Mock telegram client get_chat_history returning latest_id = 60 (10 new messages)
            mock_client = AsyncMock()
            latest_msg = MagicMock(id=60)
            async def mock_history(*args, **kwargs):
                yield latest_msg
            mock_client.get_chat_history = mock_history

            with patch.object(telegram_auth, "get_active_client", AsyncMock(return_value=mock_client)), \
                 patch.object(cloner_engine, "_client", AsyncMock(return_value=mock_client)), \
                 patch.object(cloner_engine, "start_task", AsyncMock(return_value=True)):

                res = await client.post(f"/api/tasks/{task_id}/sync-new", headers=headers)
                assert res.status_code == 200
                data = res.json()
                assert data["success"] is True
                assert data["new_messages"] == 10
                assert data["start_id"] == 51
                assert data["end_id"] == 60


@pytest.mark.asyncio
async def test_sync_new_messages_already_up_to_date(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_sync_uptodate.db")
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/users/register", json={"email": "sync2@test.com", "password": "senha12345"})
            token = reg.json()["token"]
            user_id = verify_token(token)
            headers = {"Authorization": f"Bearer {token}"}
            await save_account(user_id, "user", 123, "hash", "+5511999999999", session_string="mock_session")

            task_res = await client.post("/api/tasks", headers=headers, json={
                "name": "Sync Task Up to Date",
                "mode": "historical",
                "origin_chat": "-1001",
                "dest_chat": "-1002",
                "start_message_id": 1,
                "end_message_id": 50,
                "media_types": ["all"]
            })
            task_id = task_res.json()["id"]

            await update_task_status(user_id, task_id, TaskStatus.COMPLETED)
            await update_task_progress(user_id, task_id, current_message_id=50, processed_messages=50, copied_count=50)
            await record_task_message(task_id, 50, "copied")

            # Origin latest message is still 50
            mock_client = AsyncMock()
            latest_msg = MagicMock(id=50)
            async def mock_history(*args, **kwargs):
                yield latest_msg
            mock_client.get_chat_history = mock_history

            with patch.object(telegram_auth, "get_active_client", AsyncMock(return_value=mock_client)), \
                 patch.object(cloner_engine, "_client", AsyncMock(return_value=mock_client)):
                res = await client.post(f"/api/tasks/{task_id}/sync-new", headers=headers)
                assert res.status_code == 200
                data = res.json()
                assert data["success"] is True
                assert data["new_messages"] == 0
                assert "atualizado" in data["message"].lower()

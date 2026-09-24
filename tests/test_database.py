import pytest
import asyncio
import os
import tempfile
from pathlib import Path

from core.config import DATA_DIR, DATABASE_PATH
from core.database import (
    init_db, save_account, get_active_account, clear_active_account,
    create_task, get_task, get_all_tasks, update_task, update_task_status, update_task_progress,
    delete_task, record_task_message, get_task_copied_message_ids, get_live_tasks_all,
    create_text_rule, get_all_text_rules, update_text_rule, delete_text_rule, add_log, get_recent_logs
)
from core.models import TaskCreate, TaskUpdate, TaskMode, TaskStatus, TextRuleCreate, TextRuleUpdate


def test_database_url_normalization(monkeypatch):
    from core.config import normalize_database_url
    assert normalize_database_url("postgres://user:pass@host:5432/db") == "postgresql://user:pass@host:5432/db"
    assert normalize_database_url("postgresql://user:pass@host:5432/db") == "postgresql://user:pass@host:5432/db"
    assert normalize_database_url("") is None
    assert normalize_database_url(None) is None


@pytest.mark.asyncio
async def test_database_lifecycle(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = Path(tmpdir) / "test_cloner.db"
        monkeypatch.setattr("core.database.DATABASE_PATH", test_db_path)

        # 1. Initialize Database
        await init_db()
        assert test_db_path.exists()

        # 2. Account operations
        acc_id = await save_account(
            owner_id=1,
            account_type="user",
            api_id=123456,
            api_hash="abcdef",
            phone_number="+5511999999999",
            session_string="test_session_string",
            tg_user_id=1001,
            username="testuser",
            first_name="Victor"
        )
        assert acc_id > 0

        active_acc = await get_active_account(1)
        assert active_acc is not None
        assert active_acc["username"] == "testuser"
        assert active_acc["api_id"] == 123456

        await clear_active_account(1)
        cleared = await get_active_account(1)
        assert cleared is None

        # 3. Task operations
        task_in = TaskCreate(
            name="Clonagem Teste",
            mode=TaskMode.HISTORICAL,
            origin_chat="-1001234567890",
            dest_chat="-1009876543210",
            media_types=["photo", "video", "text"],
            clean_forward=True,
            delay_seconds=5.0,
            remove_captions=True,
            remove_links=True,
            header_text="[Canal VIP]",
            footer_text="Acesse: https://meusite.com"
        )
        created_task = await create_task(1, task_in, origin_title="Origem VIP", dest_title="Destino VIP")
        assert created_task.id is not None
        assert created_task.name == "Clonagem Teste"
        assert created_task.origin_title == "Origem VIP"
        assert created_task.remove_captions is True
        assert created_task.remove_links is True
        assert "photo" in created_task.media_types

        # Update progress
        await update_task_progress(1, created_task.id, current_message_id=15, total_messages=100, copied_count=10, skipped_count=5)
        updated = await get_task(1, created_task.id)
        assert updated.current_message_id == 15
        assert updated.copied_count == 10
        assert updated.skipped_count == 5

        # Update status
        await update_task_status(1, created_task.id, TaskStatus.RUNNING)
        updated_status = await get_task(1, created_task.id)
        assert updated_status.status == TaskStatus.RUNNING.value

        # Message cache
        await record_task_message(created_task.id, origin_message_id=1, status="copied", dest_message_id=50, media_type="photo")
        await record_task_message(created_task.id, origin_message_id=2, status="skipped", dest_message_id=None, media_type="sticker")
        copied_ids = await get_task_copied_message_ids(created_task.id)
        assert copied_ids == [1, 2]

        # Live tasks check
        live_task_in = TaskCreate(
            name="Live Sync",
            mode=TaskMode.LIVE_SYNC,
            origin_chat="-1001",
            dest_chat="-1002"
        )
        live_task = await create_task(1, live_task_in)
        await update_task_status(1, live_task.id, TaskStatus.RUNNING)
        live_tasks = await get_live_tasks_all()
        assert len(live_tasks) == 1
        assert live_tasks[0].id == live_task.id
        await delete_task(1, live_task.id)

        # Rules
        rule = await create_text_rule(1, TextRuleCreate(
            name="Trocar @original por @meucanal",
            rule_type="replace",
            pattern="@original",
            replacement="@meucanal",
            is_regex=False,
            enabled=True
        ))
        rules = await get_all_text_rules(1)
        assert len(rules) == 1
        assert rules[0].pattern == "@original"

        # Update Rule
        updated_rule = await update_text_rule(1, rule.id, TextRuleUpdate(enabled=False, replacement="@novo"))
        assert updated_rule is not None
        assert updated_rule.enabled is False
        assert updated_rule.replacement == "@novo"

        # Update Task Configuration
        updated_task_config = await update_task(1, created_task.id, TaskUpdate(name="Nome Editado", delay_seconds=20.0))
        assert updated_task_config is not None
        assert updated_task_config.name == "Nome Editado"
        assert updated_task_config.delay_seconds == 20.0

        # Logs
        await add_log(1, created_task.id, "info", "Iniciando clonagem...")
        await add_log(1, created_task.id, "success", "Mensagem 1 clonada!")
        logs = await get_recent_logs(1, task_id=created_task.id)
        assert len(logs) == 2
        assert logs[0].message == "Iniciando clonagem..."
        assert logs[1].message == "Mensagem 1 clonada!"

        # Delete task
        deleted = await delete_task(1, created_task.id)
        assert deleted is True
        assert await get_task(1, created_task.id) is None


@pytest.mark.asyncio
async def test_init_db_pg_mode(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch
    from core import config

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    executed_queries = []
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_transaction.start = AsyncMock()
    mock_transaction.commit = AsyncMock()
    mock_conn.transaction.return_value = mock_transaction

    async def fake_execute(query, *args):
        executed_queries.append(query)
        return "OK"

    async def fake_fetch(query, *args):
        executed_queries.append(query)
        if "SELECT id, email, password_hash FROM users" in query:
            return [{"id": 1, "email": "admin@example.com", "password_hash": "hash"}]
        return []

    mock_conn.execute = AsyncMock(side_effect=fake_execute)
    mock_conn.fetch = AsyncMock(side_effect=fake_fetch)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        await init_db()

    all_queries_str = "\n".join(executed_queries)
    assert "SERIAL PRIMARY KEY" in all_queries_str
    assert "AUTOINCREMENT" not in all_queries_str
    for tbl in [
        "users", "accounts", "tasks", "task_messages", "text_rules",
        "logs", "system_logs", "managed_groups", "posts", "post_deliveries"
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {tbl}" in all_queries_str


@pytest.mark.asyncio
async def test_create_user_pg_mode_unique_error(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch
    from core import config
    from core.database import create_user

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_transaction.start = AsyncMock()
    mock_conn.transaction.return_value = mock_transaction
    mock_conn.fetch = AsyncMock(
        side_effect=Exception("duplicate key value violates unique constraint 'users_email_key'")
    )

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        user_id = await create_user("duplicate@test.com", "hash")
        assert user_id is None


@pytest.mark.asyncio
async def test_record_task_message_pg_mode(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch
    from core import config
    from core.database import record_task_message

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_transaction.start = AsyncMock()
    mock_transaction.commit = AsyncMock()
    mock_conn.transaction.return_value = mock_transaction

    executed_queries = []

    async def fake_fetch(query, *args):
        executed_queries.append(query)
        return [{"id": 1}]

    mock_conn.fetch = AsyncMock(side_effect=fake_fetch)
    mock_conn.execute = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        await record_task_message(task_id=1, origin_message_id=10, status="copied", dest_message_id=20)

    assert len(executed_queries) == 1
    assert "ON CONFLICT (task_id, origin_message_id) DO UPDATE SET" in executed_queries[0]


@pytest.mark.asyncio
async def test_app_lifespan_closes_db_pool():
    from unittest.mock import AsyncMock, patch
    from app import lifespan, app

    with patch("app.init_db", AsyncMock()), \
         patch("app.get_live_tasks_all", AsyncMock(return_value=[])), \
         patch("app.publisher_engine.start_scheduler_loop"), \
         patch("app.publisher_engine.stop_scheduler_loop"), \
         patch("app.close_db_pool", AsyncMock()) as mock_close:
        async with lifespan(app):
            pass
        mock_close.assert_awaited_once()

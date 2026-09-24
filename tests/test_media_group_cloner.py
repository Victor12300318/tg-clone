import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pyrogram.errors import RPCError

from core.cloner_engine import cloner_engine
from core.models import TaskResponse, TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_send_transformed_media_group_copy():
    mock_client = AsyncMock()
    mock_client.copy_media_group = AsyncMock(return_value=[MagicMock(id=201), MagicMock(id=202)])

    m1 = MagicMock(id=101, media_group_id="mg_1", caption="Foto 1 https://t.me/spam", text=None, photo=True, video=False)
    m2 = MagicMock(id=102, media_group_id="mg_1", caption=None, text=None, photo=True, video=False)
    media_group = [m1, m2]

    task = TaskResponse(
        id=1,
        owner_id=1,
        name="Teste Album",
        mode=TaskMode.HISTORICAL,
        origin_chat="-1001",
        dest_chat="-1002",
        clean_forward=True,
        remove_links=True,
        status="pending",
        media_types=["all"],
        created_at="2026-01-01",
        updated_at="2026-01-01"
    )

    sent = await cloner_engine._send_transformed_media_group(
        client=mock_client,
        origin_chat="-1001",
        dest_chat="-1002",
        media_group=media_group,
        task=task,
        transformed_caption="Foto 1"
    )

    assert sent is not None
    assert len(sent) == 2
    mock_client.copy_media_group.assert_awaited_once_with(
        chat_id="-1002",
        from_chat_id="-1001",
        message_id=101,
        captions="Foto 1"
    )


@pytest.mark.asyncio
async def test_send_transformed_media_group_native_forward():
    mock_client = AsyncMock()
    mock_client.forward_messages = AsyncMock(return_value=[MagicMock(id=201), MagicMock(id=202)])

    m1 = MagicMock(id=101, media_group_id="mg_1", caption=None, text=None, photo=True, video=False)
    m2 = MagicMock(id=102, media_group_id="mg_1", caption=None, text=None, photo=True, video=False)
    media_group = [m1, m2]

    task = TaskResponse(
        id=1,
        owner_id=1,
        name="Teste Album",
        mode=TaskMode.HISTORICAL,
        origin_chat="-1001",
        dest_chat="-1002",
        clean_forward=False,
        status="pending",
        media_types=["all"],
        created_at="2026-01-01",
        updated_at="2026-01-01"
    )

    sent = await cloner_engine._send_transformed_media_group(
        client=mock_client,
        origin_chat="-1001",
        dest_chat="-1002",
        media_group=media_group,
        task=task,
        transformed_caption=None
    )

    assert sent is not None
    mock_client.forward_messages.assert_awaited_once_with(
        chat_id="-1002",
        from_chat_id="-1001",
        message_ids=[101, 102]
    )

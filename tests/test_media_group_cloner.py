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


@pytest.mark.asyncio
async def test_send_transformed_media_group_protected_skips_copy_and_preserves_video_metadata():
    import io
    from types import SimpleNamespace

    mock_client = AsyncMock()
    mock_client.copy_media_group = AsyncMock()
    mock_client.send_media_group = AsyncMock(return_value=[MagicMock(id=301), MagicMock(id=302)])

    async def fake_download(target, *args, **kwargs):
        buf = io.BytesIO(b"fake_bytes")
        buf.name = "test.bin"
        return buf

    mock_client.download_media = AsyncMock(side_effect=fake_download)

    m1 = SimpleNamespace(
        id=101,
        media_group_id="mg_prot",
        has_protected_content=True,
        caption="Album protegido",
        text=None,
        empty=False,
        service=False,
        dice=None,
        location=None,
        photo=SimpleNamespace(file_size=50000),
        video=None,
        document=None,
        sticker=None,
        animation=None,
        audio=None,
        voice=None,
        video_note=None,
        poll=None,
    )
    m2 = SimpleNamespace(
        id=102,
        media_group_id="mg_prot",
        has_protected_content=True,
        caption=None,
        text=None,
        empty=False,
        service=False,
        dice=None,
        location=None,
        photo=None,
        video=SimpleNamespace(
            file_size=120000,
            width=1920,
            height=1080,
            duration=45,
            thumbs=[SimpleNamespace(file_id="thumb_fid_102")],
        ),
        document=None,
        sticker=None,
        animation=None,
        audio=None,
        voice=None,
        video_note=None,
        poll=None,
    )

    task = TaskResponse(
        id=999,
        owner_id=1,
        name="Teste Album Protegido",
        mode=TaskMode.HISTORICAL,
        origin_chat="-1001",
        dest_chat="-1002",
        clean_forward=True,
        status="pending",
        media_types=["all"],
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )

    with patch.object(cloner_engine, "log", AsyncMock()):
        sent = await cloner_engine._send_transformed_media_group(
            client=mock_client,
            origin_chat="-1001",
            dest_chat="-1002",
            media_group=[m1, m2],
            task=task,
            transformed_caption="Album protegido",
        )

    assert len(sent) == 2
    # Must NOT waste an RPC call on copy_media_group when has_protected_content is True
    mock_client.copy_media_group.assert_not_awaited()
    mock_client.send_media_group.assert_awaited_once()
    sent_media_list = mock_client.send_media_group.call_args.kwargs["media"]
    assert len(sent_media_list) == 2
    video_media = sent_media_list[1]
    assert video_media.width == 1920
    assert video_media.height == 1080
    assert video_media.duration == 45
    assert video_media.supports_streaming is True
    assert video_media.thumb is not None
    cloner_engine._reupload_warned_tasks.discard(999)


@pytest.mark.asyncio
async def test_historical_cloner_batch_fetch_and_prefetch(monkeypatch):
    import io
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    from core.database import init_db, create_task, get_task, get_task_copied_message_ids
    from core.models import TaskCreate

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_batch.db")
        await init_db()

        created = await create_task(
            1,
            TaskCreate(
                name="Batch 170k Test",
                mode=TaskMode.HISTORICAL,
                origin_chat="-100100",
                dest_chat="-100200",
                start_message_id=1,
                end_message_id=5,
                delay_seconds=0.0,
                skip_delay_seconds=0.0,
                media_types=["all"],
            ),
        )

        mock_client = AsyncMock()
        mock_client.me = SimpleNamespace(is_premium=True, id=1)
        mock_client.max_concurrent_transmissions = 1
        mock_client.get_chat = AsyncMock(
            return_value=SimpleNamespace(id=-100100, type="channel", has_protected_content=True, members_count=100)
        )

        # Messages 1 & 2 = album, 3 = empty/deleted, 4 = protected video, 5 = protected photo
        msg_map = {
            1: SimpleNamespace(
                id=1, media_group_id="alb1", has_protected_content=True,
                caption="Legenda Album", text=None, empty=False, service=False, dice=None, location=None,
                photo=SimpleNamespace(file_size=10000), video=None, document=None, sticker=None,
                animation=None, audio=None, voice=None, video_note=None, poll=None,
            ),
            2: SimpleNamespace(
                id=2, media_group_id="alb1", has_protected_content=True,
                caption=None, text=None, empty=False, service=False, dice=None, location=None,
                photo=SimpleNamespace(file_size=12000), video=None, document=None, sticker=None,
                animation=None, audio=None, voice=None, video_note=None, poll=None,
            ),
            3: SimpleNamespace(
                id=3, media_group_id=None, has_protected_content=True,
                caption=None, text=None, empty=True, service=False, dice=None, location=None,
                photo=None, video=None, document=None, sticker=None,
                animation=None, audio=None, voice=None, video_note=None, poll=None,
            ),
            4: SimpleNamespace(
                id=4, media_group_id=None, has_protected_content=True,
                caption="Video 4", text=None, empty=False, service=False, dice=None, location=None,
                photo=None,
                video=SimpleNamespace(
                    file_size=50000, width=1280, height=720, duration=30,
                    thumbs=[SimpleNamespace(file_id="thumb_4")],
                ),
                document=None, sticker=None, animation=None, audio=None, voice=None, video_note=None, poll=None,
            ),
            5: SimpleNamespace(
                id=5, media_group_id=None, has_protected_content=True,
                caption="Foto 5", text=None, empty=False, service=False, dice=None, location=None,
                photo=SimpleNamespace(file_size=20000), video=None, document=None, sticker=None,
                animation=None, audio=None, voice=None, video_note=None, poll=None,
            ),
        }

        async def fake_get_messages(chat_id, message_ids):
            assert isinstance(message_ids, list)
            return [msg_map[mid] for mid in message_ids if mid in msg_map]

        async def fake_download_media(target, *args, **kwargs):
            buf = io.BytesIO(b"media_content")
            buf.name = "file.bin"
            return buf

        mock_client.get_messages = AsyncMock(side_effect=fake_get_messages)
        mock_client.get_media_group = AsyncMock()
        mock_client.download_media = AsyncMock(side_effect=fake_download_media)
        mock_client.send_media_group = AsyncMock(return_value=[SimpleNamespace(id=501), SimpleNamespace(id=502)])
        mock_client.send_video = AsyncMock(return_value=SimpleNamespace(id=504))
        mock_client.send_photo = AsyncMock(return_value=SimpleNamespace(id=505))

        cloner_engine._task_owners[created.id] = 1
        cancel_ev = asyncio.Event()
        pause_ev = asyncio.Event()
        pause_ev.set()

        with patch.object(cloner_engine, "_client", AsyncMock(return_value=mock_client)):
            await cloner_engine._run_historical_cloner(created.id, cancel_ev, pause_ev)

        # Single batch get_messages call for all 5 messages
        assert mock_client.get_messages.await_count == 1
        # Local grouping from batch means get_media_group was not needed
        mock_client.get_media_group.assert_not_awaited()
        # Client transmission limit configured to 1 per direction (1 download + 1 upload in parallel)
        assert mock_client.max_concurrent_transmissions == 1

        final_task = await get_task(1, created.id)
        assert final_task.status == TaskStatus.COMPLETED
        assert final_task.copied_count == 4
        assert final_task.skipped_count == 1
        assert final_task.processed_messages == 5

        recorded_ids = await get_task_copied_message_ids(created.id)
        assert recorded_ids == [1, 2, 3, 4, 5]
        cloner_engine._reupload_warned_tasks.discard(created.id)


@pytest.mark.asyncio
async def test_download_single_media_retries_on_closed_tcp_transport():
    import io
    from types import SimpleNamespace

    mock_client = AsyncMock()
    mock_client.me = SimpleNamespace(is_premium=True, id=1)
    mock_client.max_concurrent_transmissions = 1

    calls = 0

    async def flaky_download(target, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("unable to perform operation on <TCPTransport closed=True>; the handler is closed")
        if calls == 2:
            # Simulate Pyrogram returning an empty 0-byte BytesIO after swallowed error
            return io.BytesIO(b"")
        buf = io.BytesIO(b"valid_video_payload")
        buf.name = "video.mp4"
        return buf

    mock_client.download_media = AsyncMock(side_effect=flaky_download)

    msg = SimpleNamespace(
        id=77,
        video=SimpleNamespace(file_size=50000, width=1280, height=720, duration=15, thumbs=[]),
        photo=None, document=None, audio=None, voice=None, animation=None, sticker=None, video_note=None,
    )

    with patch("core.cloner_engine._interruptible_sleep", AsyncMock(return_value=True)):
        dl = await cloner_engine._download_single_media(mock_client, msg, "video", task_id=1)
        try:
            assert calls == 3
            assert dl.media_input.getvalue() == b"valid_video_payload"
        finally:
            dl.cleanup()

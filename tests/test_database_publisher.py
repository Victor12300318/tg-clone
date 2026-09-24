import pytest
import tempfile
from pathlib import Path
from core.database import (
    init_db, add_managed_group, get_all_managed_groups, delete_managed_group,
    create_post, get_post, get_all_posts, update_post_status, delete_post,
    record_post_delivery, get_post_deliveries
)
from core.models import ManagedGroupCreate, PostCreate, ScheduleType, PostStatus


@pytest.mark.asyncio
async def test_publisher_database_lifecycle(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = Path(tmpdir) / "test_pub.db"
        monkeypatch.setattr("core.database.DATABASE_PATH", test_db_path)
        await init_db()

        # 1. Managed Groups
        grp = await add_managed_group(1, ManagedGroupCreate(
            chat_id="-1001234567890",
            title="Grupo VIP Notícias",
            chat_type="supergroup",
            is_admin=True
        ))
        assert grp.id is not None
        assert grp.title == "Grupo VIP Notícias"

        groups = await get_all_managed_groups(1)
        assert len(groups) == 1
        assert groups[0].chat_id == "-1001234567890"

        # 2. Posts
        post = await create_post(1, PostCreate(
            name="Aviso Importante",
            text="🚨 **Aviso:** Manutenção hoje às 22h.",
            target_group_ids=[grp.id],
            schedule_type=ScheduleType.NOW
        ))
        assert post.id is not None
        assert post.status == PostStatus.DRAFT.value
        assert post.target_group_ids == [grp.id]

        posts = await get_all_posts(1)
        assert len(posts) == 1

        # 3. Post Deliveries
        delivery = await record_post_delivery(
            post_id=post.id,
            chat_id=grp.chat_id,
            chat_title=grp.title,
            message_id=999,
            status="sent"
        )
        assert delivery.id is not None
        assert delivery.message_id == 999

        deliveries = await get_post_deliveries(1, post.id)
        assert len(deliveries) == 1
        assert deliveries[0].status == "sent"

        # 4. Clean up
        assert await delete_post(1, post.id) is True
        assert await delete_managed_group(1, grp.id) is True

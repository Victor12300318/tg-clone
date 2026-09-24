import pytest
import asyncio
import tempfile
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from app import app
from core.database import init_db


@pytest.mark.asyncio
async def test_posts_crud_api(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_api_pub.db"
        monkeypatch.setattr("core.database.DATABASE_PATH", test_db)
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/users/register", json={"email": "t@example.com", "password": "senha12345"})
            client.headers["Authorization"] = f"Bearer {reg.json()['token']}"

            # 1. Upload mock media
            res_upload = await client.post(
                "/api/publisher/uploads",
                files={"file": ("banner.jpg", b"fake_image_bytes", "image/jpeg")}
            )
            assert res_upload.status_code == 200
            media_path = res_upload.json()["file_path"]
            assert "banner.jpg" in media_path

            # 2. Create post
            res_post = await client.post("/api/publisher/posts", json={
                "name": "Post de Lançamento",
                "text": "🔥 Lançamento Oficial hoje!",
                "media_path": media_path,
                "media_type": "photo",
                "target_group_ids": [1],
                "schedule_type": "once",
                "run_at": "2026-08-20T10:00:00"
            })
            assert res_post.status_code == 200
            post_id = res_post.json()["id"]

            # 3. Update post
            res_put = await client.put(f"/api/publisher/posts/{post_id}", json={
                "name": "Post Atualizado",
                "text": "🔥 Novo texto"
            })
            assert res_put.status_code == 200
            assert res_put.json()["name"] == "Post Atualizado"

            # 4. List posts
            res_list = await client.get("/api/publisher/posts")
            assert res_list.status_code == 200
            assert len(res_list.json()) == 1

            # 5. Test publish-now endpoint
            res_pub_now = await client.post(f"/api/publisher/posts/{post_id}/publish-now")
            assert res_pub_now.status_code == 200
            assert res_pub_now.json()["success"] is True

            # Wait briefly for background task
            await asyncio.sleep(0.3)

            # 6. Delete post
            res_del = await client.delete(f"/api/publisher/posts/{post_id}")
            assert res_del.status_code == 200

            # 7. Verify deletion
            res_get_del = await client.get(f"/api/publisher/posts/{post_id}")
            assert res_get_del.status_code == 404

            await asyncio.sleep(0.2)

import pytest
import tempfile
from pathlib import Path

from httpx import AsyncClient, ASGITransport

from app import app
from core.database import init_db, get_user_by_id


@pytest.mark.asyncio
async def test_subscription_gate(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_subs.db")
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/users/register", json={"email": "s@t.com", "password": "senha12345"})
            token = reg.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Subscription active by default: can create
            res = await client.post("/api/tasks", headers=headers, json={
                "name": "T1", "mode": "historical",
                "origin_chat": "-1001111111111", "dest_chat": "-1002222222222",
                "media_types": ["text"], "delay_seconds": 1.0
            })
            assert res.status_code == 200

            # Read still allowed after deactivation
            user = await get_user_by_id(1)
            assert user["subscription_active"] == 1

            # Deactivate (manual toggle until billing exists)
            import sqlite3
            conn = sqlite3.connect(Path(tmpdir) / "test_subs.db")
            conn.execute("UPDATE users SET subscription_active = 0")
            conn.commit()
            conn.close()

            res = await client.get("/api/tasks", headers=headers)
            assert res.status_code == 200

            res = await client.post("/api/tasks", headers=headers, json={
                "name": "T2", "mode": "historical",
                "origin_chat": "-1001111111111", "dest_chat": "-1002222222222",
                "media_types": ["text"], "delay_seconds": 1.0
            })
            assert res.status_code == 402

            res = await client.post("/api/publisher/posts", headers=headers, json={
                "name": "P", "text": "x", "target_group_ids": []
            })
            assert res.status_code == 402

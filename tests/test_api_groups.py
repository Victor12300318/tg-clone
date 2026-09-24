import pytest
import tempfile
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from app import app
from core.database import init_db


@pytest.mark.asyncio
async def test_managed_groups_api(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_api_groups.db"
        monkeypatch.setattr("core.database.DATABASE_PATH", test_db)
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/users/register", json={"email": "t@example.com", "password": "senha12345"})
            client.headers["Authorization"] = f"Bearer {reg.json()['token']}"

            # 1. Create single group
            res = await client.post("/api/groups", json={
                "chat_id": "-1001111111111",
                "title": "Grupo Teste 1",
                "chat_type": "supergroup",
                "is_admin": True
            })
            assert res.status_code == 200
            grp_id = res.json()["id"]
            assert res.json()["title"] == "Grupo Teste 1"

            # 2. List groups
            res_list = await client.get("/api/groups")
            assert res_list.status_code == 200
            assert len(res_list.json()) == 1

            # 3. Bulk import
            res_bulk = await client.post("/api/groups/bulk", json=[
                {"chat_id": "-1002222222222", "title": "Grupo 2", "chat_type": "channel", "is_admin": False},
                {"chat_id": "-1003333333333", "title": "Grupo 3", "chat_type": "supergroup", "is_admin": True}
            ])
            assert res_bulk.status_code == 200
            assert len(res_bulk.json()) == 2

            res_list_after_bulk = await client.get("/api/groups")
            assert len(res_list_after_bulk.json()) == 3

            # 4. Delete group
            res_del = await client.delete(f"/api/groups/{grp_id}")
            assert res_del.status_code == 200

            res_list_after_del = await client.get("/api/groups")
            assert len(res_list_after_del.json()) == 2

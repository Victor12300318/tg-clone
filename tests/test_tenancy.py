import pytest
import tempfile
from pathlib import Path

from httpx import AsyncClient, ASGITransport

from app import app
from core.database import (
    init_db, create_user, save_account, get_active_account, get_all_accounts
)
from core.security import hash_password


async def _auth_client(client: AsyncClient, email: str) -> AsyncClient:
    res = await client.post("/api/users/register", json={"email": email, "password": "senha12345"})
    assert res.status_code == 200, res.text
    client.headers["Authorization"] = f"Bearer {res.json()['token']}"
    return client


@pytest.mark.asyncio
async def test_tenant_isolation(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_tenancy.db")
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client_a, \
                   AsyncClient(transport=transport, base_url="http://test") as client_b:
            await _auth_client(client_a, "a@t.com")
            await _auth_client(client_b, "b@t.com")

            # A creates one of each resource
            rule = (await client_a.post("/api/rules", json={
                "name": "Regra A", "rule_type": "replace",
                "pattern": "@a", "replacement": "@b", "is_regex": False, "enabled": True
            })).json()
            task = (await client_a.post("/api/tasks", json={
                "name": "Tarefa A", "mode": "historical",
                "origin_chat": "-1001111111111", "dest_chat": "-1002222222222",
                "media_types": ["text"], "delay_seconds": 1.0
            })).json()
            group = (await client_a.post("/api/groups", json={
                "chat_id": "-1003333333333", "title": "Grupo A", "is_admin": True
            })).json()
            post = (await client_a.post("/api/publisher/posts", json={
                "name": "Post A", "text": "conteúdo", "target_group_ids": [group["id"]]
            })).json()

            # B sees nothing of A's
            assert (await client_b.get("/api/rules")).json() == []
            assert (await client_b.get("/api/tasks")).json() == []
            assert (await client_b.get("/api/groups")).json() == []
            assert (await client_b.get("/api/publisher/posts")).json() == []

            # B cannot read, mutate or delete A's resources by id
            assert (await client_b.get(f"/api/tasks/{task['id']}")).status_code == 404
            assert (await client_b.put(f"/api/tasks/{task['id']}", json={"name": "hack"})).status_code == 404
            assert (await client_b.delete(f"/api/tasks/{task['id']}")).status_code == 404
            assert (await client_b.post(f"/api/tasks/{task['id']}/start")).status_code == 404
            assert (await client_b.patch(f"/api/rules/{rule['id']}", json={"enabled": False})).status_code == 404
            assert (await client_b.delete(f"/api/rules/{rule['id']}")).status_code == 404
            assert (await client_b.delete(f"/api/groups/{group['id']}")).status_code == 404
            assert (await client_b.get(f"/api/publisher/posts/{post['id']}")).status_code == 404
            assert (await client_b.delete(f"/api/publisher/posts/{post['id']}")).status_code == 404

            # A still sees everything
            assert len((await client_a.get("/api/rules")).json()) == 1
            assert len((await client_a.get("/api/tasks")).json()) == 1
            assert len((await client_a.get("/api/groups")).json()) == 1
            assert len((await client_a.get("/api/publisher/posts")).json()) == 1


@pytest.mark.asyncio
async def test_accounts_scoped_per_owner(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_accounts.db")
        await init_db()

        uid_a = await create_user("a@t.com", hash_password("senha12345"))
        uid_b = await create_user("b@t.com", hash_password("senha12345"))

        # Two accounts for A -> only the last stays active for A
        await save_account(owner_id=uid_a, account_type="user", api_id=1, api_hash="h",
                           phone_number="+5501", session_string="s1", tg_user_id=111)
        await save_account(owner_id=uid_a, account_type="user", api_id=1, api_hash="h",
                           phone_number="+5502", session_string="s2", tg_user_id=112)
        # One account for B, does not deactivate A's
        await save_account(owner_id=uid_b, account_type="user", api_id=1, api_hash="h",
                           phone_number="+5503", session_string="s3", tg_user_id=211)

        active_a = await get_active_account(uid_a)
        active_b = await get_active_account(uid_b)
        assert active_a["session_string"] == "s2"
        assert active_b["session_string"] == "s3"

        all_a = await get_all_accounts(uid_a)
        assert len(all_a) == 2

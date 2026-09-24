import pytest
from httpx import AsyncClient, ASGITransport
import tempfile
from pathlib import Path

from app import app
from core.database import init_db
from core.models import TaskMode, TaskStatus


@pytest.mark.asyncio
async def test_api_routes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = Path(tmpdir) / "test_api_cloner.db"
        monkeypatch.setattr("core.database.DATABASE_PATH", test_db_path)
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/users/register", json={"email": "t@example.com", "password": "senha12345"})
            client.headers["Authorization"] = f"Bearer {reg.json()['token']}"

            # 1. Auth status
            auth_res = await client.get("/api/auth/status")
            assert auth_res.status_code == 200
            assert auth_res.json()["is_authenticated"] is False

            # 2. Rules CRUD
            rule_payload = {
                "name": "Remover canal",
                "rule_type": "replace",
                "pattern": "@antigocanal",
                "replacement": "@novocanal",
                "is_regex": False,
                "enabled": True
            }
            create_rule_res = await client.post("/api/rules", json=rule_payload)
            assert create_rule_res.status_code == 200
            rule_data = create_rule_res.json()
            assert rule_data["id"] is not None
            assert rule_data["pattern"] == "@antigocanal"

            # Patch rule
            patch_rule_res = await client.patch(f"/api/rules/{rule_data['id']}", json={"enabled": False, "replacement": "@outro"})
            assert patch_rule_res.status_code == 200
            assert patch_rule_res.json()["enabled"] is False
            assert patch_rule_res.json()["replacement"] == "@outro"

            list_rules_res = await client.get("/api/rules")
            assert list_rules_res.status_code == 200
            assert len(list_rules_res.json()) == 1

            # 3. Tasks CRUD
            task_payload = {
                "name": "Clonagem Canal Notícias",
                "mode": "historical",
                "origin_chat": "-1001111111111",
                "dest_chat": "-1002222222222",
                "start_message_id": 1,
                "end_message_id": 50,
                "media_types": ["photo", "video", "text"],
                "clean_forward": True,
                "delay_seconds": 3.0,
                "skip_delay_seconds": 0.5,
                "remove_links": True,
                "remove_mentions": True,
                "header_text": "📰 Notícias do Dia",
                "footer_text": "Siga-nos: @meucanal"
            }
            create_task_res = await client.post("/api/tasks", json=task_payload)
            assert create_task_res.status_code == 200
            task_data = create_task_res.json()
            assert task_data["id"] is not None
            assert task_data["name"] == "Clonagem Canal Notícias"
            assert task_data["status"] == "pending"
            task_id = task_data["id"]

            get_task_res = await client.get(f"/api/tasks/{task_id}")
            assert get_task_res.status_code == 200
            assert get_task_res.json()["origin_chat"] == "-1001111111111"

            # Update task while paused or pending
            update_task_res = await client.put(f"/api/tasks/{task_id}", json={
                "name": "Clonagem Atualizada",
                "delay_seconds": 15.0,
                "remove_captions": True
            })
            assert update_task_res.status_code == 200
            assert update_task_res.json()["name"] == "Clonagem Atualizada"
            assert update_task_res.json()["delay_seconds"] == 15.0
            assert update_task_res.json()["remove_captions"] is True

            list_tasks_res = await client.get("/api/tasks")
            assert list_tasks_res.status_code == 200
            assert len(list_tasks_res.json()) == 1

            # 4. Static frontend files
            root_res = await client.get("/")
            assert root_res.status_code == 200
            assert "Telegram Channel Cloner" in root_res.text

            css_res = await client.get("/static/css/style.css")
            assert css_res.status_code == 200

            js_res = await client.get("/static/js/app.js")
            assert js_res.status_code == 200

            # Delete rule
            del_rule_res = await client.delete(f"/api/rules/{rule_data['id']}")
            assert del_rule_res.status_code == 200

            # Delete task
            del_task_res = await client.delete(f"/api/tasks/{task_id}")
            assert del_task_res.status_code == 200

            # Verify deletion
            get_after_del = await client.get(f"/api/tasks/{task_id}")
            assert get_after_del.status_code == 404

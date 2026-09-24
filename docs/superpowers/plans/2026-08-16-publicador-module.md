# Módulo Publicador & Gestão de Grupos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o Módulo Publicador (Composer com texto + 1 mídia, multi-destino, agendamento com recorrência, edição/exclusão em massa) e a gestão de grupos gerenciados no TG Manager Pro.

**Architecture:** Módulo com API REST FastAPI (`/api/groups`, `/api/publisher`), persistência SQLite com `aiosqlite` (tabelas `managed_groups`, `posts`, `post_deliveries`), motor singleton assíncrono `publisher_engine` com scan loop de 20 segundos e interface SPA em TailwindCSS + Alpine.js com animações e design carmim.

**Tech Stack:** FastAPI, Pyrogram, aiosqlite, SQLite, Alpine.js, TailwindCSS, Lucide Icons, pytest.

## Global Constraints

- **Python 3.10+ / FastAPI / aiosqlite**
- **Zero novas dependências de terceiros** (sem APScheduler, usar scan loop nativo com `asyncio`)
- **Strict Carmine Palette:** acentos `rose-600` / `#e11d48`, dark background `#0a0a0c`, dark card `#141417`
- **Zero emojis** no HTML e JS — utilizar exclusivamente ícones Lucide (`<i data-lucide="..."></i>`)
- **TDD:** Escrever testes antes do código de produção para cada funcionalidade

---

### Task 1: Core Models & SQLite Schema for Groups, Posts and Deliveries

**Files:**
- Modify: `core/models.py`
- Modify: `core/database.py`
- Create: `tests/test_database_publisher.py`

**Interfaces:**
- Produces:
  - `ManagedGroupCreate`, `ManagedGroupResponse`
  - `ScheduleType`, `PostStatus`, `RecurrenceRule`, `PostCreate`, `PostUpdate`, `PostResponse`, `PostDeliveryResponse`
  - `add_managed_group`, `add_managed_groups_bulk`, `get_all_managed_groups`, `delete_managed_group`
  - `create_post`, `get_post`, `get_all_posts`, `update_post_status`, `update_post_next_run`, `update_post_content`, `delete_post`
  - `record_post_delivery`, `get_post_deliveries`, `get_latest_deliveries_for_post`, `update_delivery_status`

- [ ] **Step 1: Write the failing test for database tables and models**

```python
# tests/test_database_publisher.py
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
        grp = await add_managed_group(ManagedGroupCreate(
            chat_id="-1001234567890",
            title="Grupo VIP Notícias",
            chat_type="supergroup",
            is_admin=True
        ))
        assert grp.id is not None
        assert grp.title == "Grupo VIP Notícias"

        groups = await get_all_managed_groups()
        assert len(groups) == 1

        # 2. Posts
        post = await create_post(PostCreate(
            name="Aviso Importante",
            text="🚨 **Aviso:** Manutenção hoje às 22h.",
            target_group_ids=[grp.id],
            schedule_type=ScheduleType.NOW
        ))
        assert post.id is not None
        assert post.status == PostStatus.DRAFT.value
        assert post.target_group_ids == [grp.id]

        posts = await get_all_posts()
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

        deliveries = await get_post_deliveries(post.id)
        assert len(deliveries) == 1
        assert deliveries[0].status == "sent"

        # 4. Clean up
        assert await delete_post(post.id) is True
        assert await delete_managed_group(grp.id) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database_publisher.py -v`
Expected: FAIL with "ImportError: cannot import name 'add_managed_group'"

- [ ] **Step 3: Implement models in `core/models.py` and database methods in `core/database.py`**

Add schemas to `core/models.py`:
```python
class ScheduleType(str, Enum):
    NOW = "now"
    ONCE = "once"
    RECURRING = "recurring"


class PostStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ManagedGroupCreate(BaseModel):
    chat_id: str
    title: str
    chat_type: str = "supergroup"
    is_admin: bool = False


class ManagedGroupResponse(ManagedGroupCreate):
    id: int
    added_at: str


class RecurrenceRule(BaseModel):
    freq: str  # "interval", "daily", "weekly"
    interval_hours: Optional[int] = None
    weekday: Optional[int] = None  # 0=Monday ... 6=Sunday
    time_hhmm: Optional[str] = "09:00"


class PostCreate(BaseModel):
    name: str
    text: str
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    target_group_ids: List[int] = Field(default_factory=list)
    schedule_type: ScheduleType = ScheduleType.NOW
    recurrence_rule: Optional[RecurrenceRule] = None
    run_at: Optional[str] = None


class PostUpdate(BaseModel):
    name: Optional[str] = None
    text: Optional[str] = None
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    target_group_ids: Optional[List[int]] = None
    schedule_type: Optional[ScheduleType] = None
    recurrence_rule: Optional[RecurrenceRule] = None
    run_at: Optional[str] = None
    status: Optional[PostStatus] = None


class PostDeliveryResponse(BaseModel):
    id: int
    post_id: int
    chat_id: str
    chat_title: Optional[str] = None
    message_id: Optional[int] = None
    status: str
    error: Optional[str] = None
    sent_at: str


class PostResponse(BaseModel):
    id: int
    name: str
    text: str
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    target_group_ids: List[int] = Field(default_factory=list)
    status: str
    schedule_type: str
    recurrence_rule: Optional[RecurrenceRule] = None
    run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    created_at: str
    updated_at: str
    total_deliveries: int = 0
    successful_deliveries: int = 0
```

Add SQLite tables in `init_db()` and CRUD operations in `core/database.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_database_publisher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/database.py tests/test_database_publisher.py
git commit -m "feat(publisher): add database schema and models for groups, posts and deliveries"
```

---

### Task 2: Pure Recurrence Calculator (`compute_next_run`)

**Files:**
- Create: `core/publisher_engine.py`
- Create: `tests/test_publisher_engine.py`

**Interfaces:**
- Produces: `compute_next_run(rule: Dict[str, Any] | RecurrenceRule, from_dt: datetime) -> Optional[datetime]`

- [ ] **Step 1: Write the failing test for recurrence calculation**

```python
# tests/test_publisher_engine.py
import pytest
from datetime import datetime
from core.publisher_engine import compute_next_run


def test_compute_next_run_interval():
    from_dt = datetime(2026, 8, 16, 10, 0, 0)
    rule = {"freq": "interval", "interval_hours": 6}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 16, 16, 0, 0)


def test_compute_next_run_daily_future_today():
    from_dt = datetime(2026, 8, 16, 8, 0, 0)
    rule = {"freq": "daily", "time_hhmm": "14:30"}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 16, 14, 30, 0)


def test_compute_next_run_daily_past_today():
    from_dt = datetime(2026, 8, 16, 15, 0, 0)
    rule = {"freq": "daily", "time_hhmm": "09:00"}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 17, 9, 0, 0)


def test_compute_next_run_weekly():
    # 2026-08-16 is Sunday (weekday 6)
    from_dt = datetime(2026, 8, 16, 12, 0, 0)
    # Next Monday (weekday 0) at 10:00
    rule = {"freq": "weekly", "weekday": 0, "time_hhmm": "10:00"}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 17, 10, 0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_publisher_engine.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'core.publisher_engine'"

- [ ] **Step 3: Implement `compute_next_run` in `core/publisher_engine.py`**

```python
from datetime import datetime, timedelta
from typing import Optional, Dict, Any


def compute_next_run(rule: Dict[str, Any], from_dt: Optional[datetime] = None) -> Optional[datetime]:
    if not rule:
        return None
    if from_dt is None:
        from_dt = datetime.now()

    freq = rule.get("freq") if isinstance(rule, dict) else getattr(rule, "freq", None)

    if freq == "interval":
        hours = rule.get("interval_hours", 1) if isinstance(rule, dict) else getattr(rule, "interval_hours", 1)
        return from_dt + timedelta(hours=int(hours or 1))

    time_hhmm = (rule.get("time_hhmm") if isinstance(rule, dict) else getattr(rule, "time_hhmm", "09:00")) or "09:00"
    target_hour, target_min = map(int, time_hhmm.split(":"))

    if freq == "daily":
        candidate = from_dt.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        if candidate <= from_dt:
            candidate += timedelta(days=1)
        return candidate

    if freq == "weekly":
        target_weekday = int(rule.get("weekday", 0) if isinstance(rule, dict) else getattr(rule, "weekday", 0))
        days_ahead = (target_weekday - from_dt.weekday()) % 7
        candidate = from_dt.replace(hour=target_hour, minute=target_min, second=0, microsecond=0) + timedelta(days=days_ahead)
        if candidate <= from_dt:
            candidate += timedelta(days=7)
        return candidate

    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_publisher_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/publisher_engine.py tests/test_publisher_engine.py
git commit -m "feat(publisher): implement pure compute_next_run recurrence calculator"
```

---

### Task 3: Managed Groups API Routes (`/api/groups`)

**Files:**
- Create: `api/routes_groups.py`
- Modify: `app.py`
- Create: `tests/test_api_groups.py`

**Interfaces:**
- Produces: `GET /api/groups`, `POST /api/groups`, `POST /api/groups/bulk`, `DELETE /api/groups/{id}`

- [ ] **Step 1: Write the failing API test**

```python
# tests/test_api_groups.py
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
            # 1. Create single group
            res = await client.post("/api/groups", json={
                "chat_id": "-1001111111111",
                "title": "Grupo Teste 1",
                "chat_type": "supergroup",
                "is_admin": True
            })
            assert res.status_code == 200
            grp_id = res.json()["id"]

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

            # 4. Delete group
            res_del = await client.delete(f"/api/groups/{grp_id}")
            assert res_del.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_groups.py -v`
Expected: FAIL with 404 Not Found on `/api/groups`

- [ ] **Step 3: Implement `api/routes_groups.py` and register in `app.py`**

```python
# api/routes_groups.py
from typing import List
from fastapi import APIRouter, HTTPException
from core.models import ManagedGroupCreate, ManagedGroupResponse
from core.database import (
    add_managed_group, add_managed_groups_bulk, get_all_managed_groups, delete_managed_group
)

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=List[ManagedGroupResponse])
async def list_groups():
    return await get_all_managed_groups()


@router.post("", response_model=ManagedGroupResponse)
async def create_group(group_in: ManagedGroupCreate):
    return await add_managed_group(group_in)


@router.post("/bulk", response_model=List[ManagedGroupResponse])
async def create_groups_bulk(groups_in: List[ManagedGroupCreate]):
    return await add_managed_groups_bulk(groups_in)


@router.delete("/{group_id}")
async def remove_group(group_id: int):
    deleted = await delete_managed_group(group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    return {"success": True, "message": "Grupo removido com sucesso."}
```

Include router in `app.py`:
`app.include_router(routes_groups.router)`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_groups.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes_groups.py app.py tests/test_api_groups.py
git commit -m "feat(publisher): add managed groups REST endpoints"
```

---

### Task 4: Media Uploads & Posts CRUD API Routes

**Files:**
- Create: `api/routes_publisher.py`
- Modify: `app.py`
- Create: `tests/test_api_publisher.py`

**Interfaces:**
- Produces:
  - `POST /api/publisher/uploads` (multipart file upload saving to `data/uploads/`)
  - `GET /api/publisher/posts`, `GET /api/publisher/posts/{id}`
  - `POST /api/publisher/posts`, `PUT /api/publisher/posts/{id}`, `DELETE /api/publisher/posts/{id}`

- [ ] **Step 1: Write the failing test for Posts CRUD and Uploads**

```python
# tests/test_api_publisher.py
import pytest
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

            # 5. Delete post
            res_del = await client.delete(f"/api/publisher/posts/{post_id}")
            assert res_del.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_publisher.py -v`
Expected: FAIL with 404 Not Found on `/api/publisher/uploads`

- [ ] **Step 3: Implement `api/routes_publisher.py` and register in `app.py`**

Implement file upload saving to `DATA_DIR / "uploads"` and Posts CRUD endpoints.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_publisher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes_publisher.py app.py tests/test_api_publisher.py
git commit -m "feat(publisher): add posts CRUD and media upload endpoints"
```

---

### Task 5: Publisher Engine Execution, Bulk Ops & Scheduler Loop

**Files:**
- Modify: `core/publisher_engine.py`
- Modify: `api/routes_publisher.py`
- Modify: `app.py`
- Modify: `tests/test_publisher_engine.py`

**Interfaces:**
- Produces:
  - `publisher_engine.execute_post_run(post_id: int) -> bool`
  - `publisher_engine.bulk_edit_post(post_id: int, new_text: str) -> Dict[str, Any]`
  - `publisher_engine.bulk_delete_post(post_id: int) -> Dict[str, Any]`
  - `publisher_engine.start_scheduler_loop()` / `publisher_engine.stop_scheduler_loop()`
  - `POST /api/publisher/posts/{id}/publish-now`
  - `POST /api/publisher/posts/{id}/bulk-edit`
  - `POST /api/publisher/posts/{id}/bulk-delete`

- [ ] **Step 1: Write the failing unit tests for PublisherEngine**

```python
# In tests/test_publisher_engine.py
from core.publisher_engine import PublisherEngine, publisher_engine


def test_publisher_engine_singleton_init():
    assert publisher_engine is not None
    assert publisher_engine._scheduler_task is None or not publisher_engine._scheduler_task.done()
```

- [ ] **Step 2: Run test to verify it fails / check execution structure**

Run: `pytest tests/test_publisher_engine.py -v`

- [ ] **Step 3: Implement PublisherEngine execution logic & Lifespan integration**

Implement:
- `PublisherEngine` class with `execute_post_run`, `bulk_edit_post`, `bulk_delete_post`, `_scheduler_loop` (scanning every 20s for `next_run_at <= now`).
- Register scheduler in `app.py` `lifespan()`.
- Add endpoints `publish-now`, `bulk-edit`, `bulk-delete` in `api/routes_publisher.py`.

- [ ] **Step 4: Run all tests to verify they pass**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add core/publisher_engine.py api/routes_publisher.py app.py tests/test_publisher_engine.py
git commit -m "feat(publisher): implement engine broadcast execution, bulk ops and scheduler scan loop"
```

---

### Task 6: Frontend SPA — "Meus Grupos" View with Import Modal

**Files:**
- Modify: `static/index.html`
- Modify: `static/js/app.js`

**Interfaces:**
- Produces:
  - Sidebar item `users` ("Meus Grupos")
  - Section `activeTab === 'groups'`
  - Alpine state: `managedGroups`, `fetchManagedGroups()`, `importModalOpen`, `importSelectedGroups`, `deleteManagedGroup()`

- [ ] **Step 1: Add groups management state and API calls to `static/js/app.js`**

```javascript
// In static/js/app.js:
managedGroups: [],
groupsSearch: '',
importModalOpen: false,
selectedDialogsToImport: [],
importingGroups: false,

async fetchManagedGroups() {
    try {
        const res = await fetch('/api/groups');
        if (res.ok) this.managedGroups = await res.json();
    } catch (e) {
        console.error(e);
    } finally {
        this.refreshIcons();
    }
},

async importSelectedGroups() {
    if (this.selectedDialogsToImport.length === 0) {
        this.showToast('Selecione ao menos um grupo para importar.', 'warning');
        return;
    }
    this.importingGroups = true;
    try {
        const payload = this.selectedDialogsToImport.map(d => ({
            chat_id: String(d.id),
            title: d.title,
            chat_type: d.type || 'supergroup',
            is_admin: true
        }));
        const res = await fetch('/api/groups/bulk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            this.showToast(`${this.selectedDialogsToImport.length} grupos importados com sucesso!`, 'success');
            this.importModalOpen = false;
            this.selectedDialogsToImport = [];
            await this.fetchManagedGroups();
        }
    } catch (e) {
        this.showToast('Erro ao importar grupos.', 'error');
    } finally {
        this.importingGroups = false;
        this.refreshIcons();
    }
},

async removeManagedGroup(id) {
    if (!confirm('Deseja remover este grupo do registro?')) return;
    try {
        const res = await fetch(`/api/groups/${id}`, { method: 'DELETE' });
        if (res.ok) {
            this.showToast('Grupo removido.', 'info');
            await this.fetchManagedGroups();
        }
    } catch (e) {
        console.error(e);
    }
}
```

- [ ] **Step 2: Add "Meus Grupos" view and "Importar do Telegram" modal in `static/index.html`**

Add navigation item, main section with cards/table, and import dialog with search filter and multi-selection checkboxes.

- [ ] **Step 3: Run pytest to ensure static routes and API pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/js/app.js
git commit -m "feat(ui): add Meus Grupos tab and Telegram dialogs import modal"
```

---

### Task 7: Frontend SPA — "Publicador" View (Posts List & 3-Step Composer)

**Files:**
- Modify: `static/index.html`
- Modify: `static/js/app.js`

**Interfaces:**
- Produces:
  - Sidebar item `megaphone` ("Publicador")
  - Section `activeTab === 'publisher'` with Postagens / Criar Post (Composer)
  - 3-Step Composer: ① Conteúdo + Upload de Mídia com preview, ② Destinos (Checkboxes dos Meus Grupos), ③ Agendamento (Agora / Único / Recorrente) + Revisão
  - Bulk edit/delete modals and action buttons on post cards

- [ ] **Step 1: Implement Publisher frontend state and methods in `static/js/app.js`**

Implement `posts`, `fetchPosts()`, `composerStep`, `postForm`, `handleMediaUpload()`, `submitPost()`, `publishNow()`, `bulkEdit()`, `bulkDelete()`.

- [ ] **Step 2: Add Publisher markup in `static/index.html`**

Add post cards grid with status badges, delivery pills, and action dropdowns; add 3-step Composer wizard with file uploader and schedule picker.

- [ ] **Step 3: Run pytest to ensure no regressions**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/js/app.js
git commit -m "feat(ui): add Publicador view with 3-step Composer and bulk actions"
```

---

### Task 8: End-to-End Verification & Knowledge Graph Update

**Files:**
- Modify: `scripts/smoke_test.py`
- Modify: `graphify-out/*`

- [ ] **Step 1: Extend `scripts/smoke_test.py` to include Publisher and Groups validation**

Add automated smoke tests for `/api/groups`, `/api/publisher/posts`, and immediate broadcast.

- [ ] **Step 2: Run full automated test suite**

Run: `pytest -v`
Expected: 100% PASS

- [ ] **Step 3: Update knowledge graph with graphify**

Run: `graphify update`
Expected: Knowledge graph rebuilt and reports updated.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_test.py
git commit -m "chore(test): extend smoke test suite and update knowledge graph"
```

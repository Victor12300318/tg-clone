from __future__ import annotations
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from core.config import STATIC_DIR, HOST, PORT
from core.database import init_db, get_live_tasks_all, close_db_pool
from core.publisher_engine import publisher_engine
from core.cloner_engine import cloner_engine
from api.routes_auth import router as auth_router
from api.routes_users import router as users_router
from api.routes_tasks import router as tasks_router
from api.routes_rules import router as rules_router
from api.routes_groups import router as groups_router
from api.routes_publisher import router as publisher_router
from api.websocket import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables & background scheduler
    await init_db()
    # Re-arm live sync tasks that were running before a restart
    for task in await get_live_tasks_all():
        if task.owner_id:
            await cloner_engine.start_task(task.owner_id, task.id)
    publisher_engine.start_scheduler_loop()
    yield
    # Shutdown: stop background tasks
    publisher_engine.stop_scheduler_loop()
    await close_db_pool()


app = FastAPI(
    title="Telegram Channel Cloner",
    description="Interface Web para Clonagem e Sincronização de Canais do Telegram",
    version="2.0.0",
    lifespan=lifespan
)

# Same-origin SPA: no CORS middleware needed

# Include API Routers
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(rules_router)
app.include_router(groups_router)
app.include_router(publisher_router)
app.include_router(ws_router)

# Mount Static Assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_spa_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Telegram Cloner API is running. Create static/index.html for Web UI."}


if __name__ == "__main__":
    reload = os.getenv("RELOAD", "false").lower() in ("true", "1")
    print(f"\n[*] Servidor Telegram Cloner Web rodando em: http://{HOST}:{PORT}")
    print(f"[*] Documentacao interativa da API: http://{HOST}:{PORT}/docs\n")
    uvicorn.run("app:app", host=HOST, port=PORT, reload=reload)

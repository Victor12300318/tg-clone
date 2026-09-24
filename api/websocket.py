from __future__ import annotations
import json
from typing import List, Dict, Any, Optional, Tuple
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.cloner_engine import cloner_engine
from core.publisher_engine import publisher_engine
from core.database import get_recent_logs
from core.security import verify_token

router = APIRouter()


class WebSocketManager:
    def __init__(self):
        self.active_connections: List[Tuple[WebSocket, int]] = []

    async def connect(self, websocket: WebSocket, owner_id: int):
        await websocket.accept()
        self.active_connections.append((websocket, owner_id))
        # Send recent logs to newly connected client
        try:
            recent_logs = await get_recent_logs(owner_id, limit=50)
            await websocket.send_json({
                "event": "history_logs",
                "data": [log.model_dump() if hasattr(log, "model_dump") else log.dict() for log in recent_logs]
            })
        except Exception:
            pass

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [c for c in self.active_connections if c[0] is not websocket]

    async def broadcast(self, message: Dict[str, Any]):
        owner_id = (message.get("data") or {}).get("owner_id")
        disconnected = []
        for connection, conn_owner in self.active_connections:
            if owner_id is not None and conn_owner != owner_id:
                continue
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


ws_manager = WebSocketManager()


# Register engine event callbacks
async def on_engine_event(event_payload: Dict[str, Any]):
    await ws_manager.broadcast(event_payload)


cloner_engine.register_listener(on_engine_event)
publisher_engine.register_listener(on_engine_event)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    owner_id = verify_token(token) if token else None
    if owner_id is None:
        await websocket.close(code=4401)
        return

    await ws_manager.connect(websocket, owner_id)
    try:
        while True:
            # Keep receiving client pings or commands if any
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if parsed.get("action") == "ping":
                    await websocket.send_json({"event": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)

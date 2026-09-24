import pytest
from unittest.mock import AsyncMock, MagicMock

from api.websocket import WebSocketManager


@pytest.mark.asyncio
async def test_ws_broadcast_filters_by_owner():
    mgr = WebSocketManager()
    ws_a, ws_b, ws_sys = MagicMock(), MagicMock(), MagicMock()
    for ws in (ws_a, ws_b, ws_sys):
        ws.send_json = AsyncMock()

    mgr.active_connections = [(ws_a, 1), (ws_b, 2), (ws_sys, 3)]

    # Event from owner 1: only A receives
    await mgr.broadcast({"event": "log", "data": {"owner_id": 1, "task_id": 10, "message": "x"}})
    ws_a.send_json.assert_awaited_once()
    ws_b.send_json.assert_not_awaited()
    ws_sys.send_json.assert_not_awaited()

    # Event without owner (system): everyone receives
    ws_a.send_json.reset_mock()
    await mgr.broadcast({"event": "pong", "data": {}})
    assert ws_a.send_json.await_count == 1
    assert ws_b.send_json.await_count == 1
    assert ws_sys.send_json.await_count == 1

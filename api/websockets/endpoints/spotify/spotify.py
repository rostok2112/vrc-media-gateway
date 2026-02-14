import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from api import utils
from api.websockets.clients_name import ClientName
from api.websockets.registry import ws_registry


router = APIRouter()

@router.websocket("/spotify")
async def spotify_ws(ws: WebSocket):
    await ws.accept()

    ws_registry.register(ClientName.SPOTIFY, ws)

    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)

            if data.get("type") == "rpc_response":
                await ws_registry.handle_message(ClientName.SPOTIFY, data)
                continue

    except WebSocketDisconnect:
        ws_registry.unregister(ClientName.SPOTIFY)

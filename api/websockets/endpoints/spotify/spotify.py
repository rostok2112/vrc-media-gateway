import asyncio
import json
from api.routers.infrastructure.utils import get_latest_tunnel_url
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

            if data.get("type") == "rpc_request":
                rid = data.get("id")
                method = data.get("method")

                result = None

                if method == "tunnel":
                    result = {
                        "url": get_latest_tunnel_url()
                    }

                await ws.send_text(json.dumps({
                    "type": "rpc_response",
                    "id": rid,
                    "result": result
                }))
                continue

    except WebSocketDisconnect:
        ws_registry.unregister(ClientName.SPOTIFY)
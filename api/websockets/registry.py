import uuid
import json
import asyncio

import logging
log = logging.getLogger("ws")



class WSRegistry:

    def __init__(self):
        self.clients = {}
        self.pending = {}

    def register(self, name, ws):
        log.warning(f"WS REGISTER {name}")
        self.clients[name] = ws

    def unregister(self, name):
        log.warning(f"WS UNREGISTER {name}")
        self.clients.pop(name, None)

    def is_connected(self, name):
        connected = name in self.clients
        log.warning(f"WS is_connected({name}) = {connected}")
        return connected

    async def send(self, name, payload: dict):
        log.warning(f"WS SEND -> {name}: {payload}")
        ws = self.clients.get(name)
        if not ws:
            raise RuntimeError("WS client not connected")
        await ws.send_text(json.dumps(payload))

    async def rpc_call(self, name, method, params=None, timeout=3):
        log.warning(f"WS RPC CALL {method} {params}")

        ws = self.clients.get(name)
        if not ws:
            raise RuntimeError("WS client not connected")

        rid = str(uuid.uuid4())
        fut = asyncio.get_event_loop().create_future()
        self.pending[rid] = fut

        msg = {
            "type": "rpc_request",
            "id": rid,
            "method": method,
            "params": params or {}
        }

        log.warning(f"WS SEND RPC {msg}")

        await ws.send_text(json.dumps(msg))

        try:
            res = await asyncio.wait_for(fut, timeout)
            log.warning(f"WS RPC RESULT {res}")
            return res
        finally:
            self.pending.pop(rid, None)

    async def handle_message(self, name, data: dict):
        log.warning(f"WS RECEIVED {data}")

        if data.get("type") == "rpc_response":
            rid = data.get("id")
            fut = self.pending.get(rid)
            if fut and not fut.done():
                fut.set_result(data.get("result"))

ws_registry = WSRegistry()

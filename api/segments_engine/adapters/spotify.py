import asyncio
import logging
from api.segments_engine.types import AudioSourceAdapter
from api.websockets.clients_name import ClientName
from api.websockets.registry import ws_registry


log = logging.getLogger("segments.spotify")

class SpotifyAdapter(AudioSourceAdapter):
    def __init__(self, client_name: str = ClientName.SPOTIFY):
        self.client = client_name

    async def prepare_position(self, position_ms: int):
        # ask spotify extension to seek+play and confirm
        log.warning("SPOTIFY SEEK_PLAY position_ms=%s", position_ms)
        try:
            await ws_registry.rpc_call(self.client, "seek_play", {"position_ms": position_ms}, timeout=3.0)
        except Exception:
            # fallback: send simple seek and wait a bit
            try:
                await ws_registry.send(self.client, {"action": "seek", "position_ms": position_ms})
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def on_start(self):
        # no-op for now (placeholder)
        return

    async def on_stop(self):
        # on writer stop, pause Spotify to avoid auto-next
        try:
            await ws_registry.send(self.client, {"action": "pause"})
        except Exception:
            pass

import asyncio
import logging
import winappaudiorouter as war
from api import config
from api.segments_engine.types import AudioSourceAdapter
from api.websockets.clients_name import ClientName
from api.websockets.registry import ws_registry


log = logging.getLogger("segments.spotify")

class SpotifyAdapter(AudioSourceAdapter):
    def __init__(self, client_name: str = ClientName.SPOTIFY):
        self.client = client_name
        self._capture_active = False
        self.original_devices = {}

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
        """
        Called once before first PCM is required.

        Goal:
        Route Spotify output → capture playback device
        so user doesn't hear it but ffmpeg can read it.
        """
        if self._capture_active:
            return

        try:
            routed_devices = war.get_app_output_device(process_name=config.SPOTIFY)
            for pid, device_id in routed_devices.items():
                self.original_devices[pid] = device_id

            log.info("Spotify routed to capture device")
            
            result = war.set_app_output_device(process_name=config.SPOTIFY, device=config.AUDIO_SINK_INPUT_DEVICE)
            log.info(f"Spotify output routed to {config.AUDIO_SINK_INPUT_DEVICE}")

            self._capture_active = True
        except Exception:
            log.exception("Failed to route Spotify to capture device")

    async def on_stop(self):
        """
        Restore Spotify output → original playback device.
        """
        if not self._capture_active:
            return

        try:
            restored_any = False
            if self.original_devices:
                for pid, original_device_id in self.original_devices.items():
                    try:
                        war.set_app_output_device(process_id=pid, device=original_device_id)
                        log.info(f"Process {pid} restored to original device {original_device_id}")
                        restored_any = True
                    except Exception:
                        log.exception("Failed to restore Spotify device for pid=%s", pid)

            if not restored_any:
                result = war.clear_app_output_device(process_name=config.SPOTIFY)
                log.info("Spotify route override cleared: %s", result)

            self._capture_active = False
            self.original_devices = {}
        except Exception:
            log.exception("Failed to restore Spotify device")

        # optional: pause to avoid auto-next
        try:
            await ws_registry.rpc_call(self.client, "pause", {}, timeout=2.0)
        except Exception:
            try:
                await ws_registry.send(self.client, {"action": "pause"})
            except Exception:
                pass
    

from typing import Protocol

class AudioSourceAdapter(Protocol):
    """
    Adapter must implement prepare_position(position_ms) which prepares the source
    (seek + ensure playback) so that subsequent ffmpeg capture from device yields audio
    from requested position.
    """
    async def prepare_position(self, position_ms: int) -> None:
        ...

    async def on_start(self) -> None:
        """Optional hook before writer starts"""
        ...

    async def on_stop(self) -> None:
        """Optional hook after writer stops"""
        ...

import hashlib
from typing import Dict, Optional
from pathlib import Path
from api.segments_engine.writer import StreamWriter
from api.segments_engine.adapters import SpotifyAdapter
from api import config
import asyncio
import json

_writers = {}
_lock = asyncio.Lock()

def _default_ff_args():
    # Example for Windows DirectSound; adapt to your OS and audio sink
    # If you use a virtual audio cable, set correct input device
    input_args = config.FFMPEG_INPUT_ARGS  # recommend to put this in config
    audio_codec_args = config.FFMPEG_AUDIO_ARGS
    return input_args, audio_codec_args

async def get_or_create_writer(sid: str) -> StreamWriter:
    async with _lock:
        if sid in _writers:
            return _writers[sid]

        input_args, audio_codec_args = _default_ff_args()
        out_dir = Path(config.STREAMS) / sid
        adapter = SpotifyAdapter()

        writer = StreamWriter(
            sid=sid,
            out_dir=out_dir,
            ffmpeg_bin=config.FFMPEG,
            input_args=input_args,
            audio_codec_args=audio_codec_args,
            segment_time=int(config.SPOTIFY_HLS_OPTS.get("hls_time", 3)),
            source_adapter=adapter,
            hls_list_size=int(config.SPOTIFY_HLS_OPTS.get("hls_list_size", 6)),
        )
        _writers[sid] = writer
        return writer

async def start_stream_for_track(track_id: str, start_position_ms: int = 0) -> str:
    """
    Ensure writer is created & started for given spotify track_id.
    Returns sid.
    """
    sid = hashlib.md5(track_id.encode()).hexdigest()[:8]
    w = await get_or_create_writer(sid)
    # ensure source adapter knows which track to play
    try:
        await w.source.play_track(track_id, start_position_ms)
    except Exception:
        # adapter should implement play_track to start spotify playback on desktop
        pass

    # start ffmpeg in background (non-blocking); start handles idempotency
    asyncio.get_running_loop().create_task(w.start(start_position_ms))
    return sid

async def wait_ready(sid: str, timeout: float = 6.0) -> bool:
    w = await get_or_create_writer(sid)
    return await w.wait_ready(timeout=timeout)

async def stop_stream(sid: str):
    w = _writers.get(sid)
    if w:
        await w.stop()
        # optionally remove from registry
        async with _lock:
            try:
                del _writers[sid]
            except KeyError:
                pass
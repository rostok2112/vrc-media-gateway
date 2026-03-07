import time
from typing import Dict, Optional
from pathlib import Path
from api.segments_engine.writer import StreamWriter
from api.segments_engine.adapters import SpotifyAdapter
from api import config
import asyncio
import json

_writers: Dict[str, StreamWriter] = {}
_lock = asyncio.Lock()


def _bool_from_meta(value, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _default_ff_args():
    input_args = getattr(
        config,
        "FFMPEG_INPUT_ARGS",
        ["-f", "dshow", "-i", f"audio={config.AUDIO_SINK_OUTPUT_DEVICE}"]
    )

    audio_codec_args = getattr(
        config,
        "FFMPEG_AUDIO_ARGS",
        [
            "-c:a", config.AUDIO_TARGET.get("codec", "aac"),
            "-ac", str(config.AUDIO_TARGET.get("channels", 2)),
            "-ar", str(config.AUDIO_TARGET.get("samplerate", 48000)),
            "-b:a", config.AUDIO_TARGET.get("bitrate", "192k"),
        ]
    )

    return input_args, audio_codec_args


async def get_or_create_writer(sid: str) -> StreamWriter:
    async with _lock:
        w = _writers.get(sid)
        if w:
            return w

        input_args, audio_codec_args = _default_ff_args()
        out_dir = Path(config.STREAMS) / sid

        total_segments: Optional[int] = None
        segment_time = int(config.SPOTIFY_HLS_OPTS.get("hls_time", 3))
        prefetch = int(config.SPOTIFY_HLS_OPTS.get("prefetch", 2))
        show_info = _bool_from_meta(config.SPOTIFY_HLS_OPTS.get("show_info", True), True)
        meta_path = out_dir / "metadata.json"
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("total_segments") is not None:
                    total_segments = int(meta.get("total_segments"))
                if meta.get("segment_time") is not None:
                    segment_time = int(meta.get("segment_time"))
                elif meta.get("seg_time") is not None:
                    segment_time = int(meta.get("seg_time"))
                if meta.get("prefetch") is not None:
                    prefetch = int(meta.get("prefetch"))
                show_info = _bool_from_meta(meta.get("show_info"), show_info)
        except Exception:
            pass

        adapter = SpotifyAdapter()
        video_input_args = []
        video_codec_args = []
        if show_info:
            poster_path = out_dir / "audio_poster.png"
            if poster_path.exists() and poster_path.stat().st_size > 0:
                video_input_args = [
                    "-loop", "1",
                    "-i", str(poster_path),
                ]
                video_codec_args = [
                    "-vf", "setsar=1",
                    "-pix_fmt", "yuv420p",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "16",
                    "-profile:v", "high",
                    "-level", "4.2",
                ]

        w = StreamWriter(
            sid=sid,
            out_dir=out_dir,
            ffmpeg_bin=config.FFMPEG,
            input_args=input_args,
            audio_codec_args=audio_codec_args,
            video_input_args=video_input_args,
            video_codec_args=video_codec_args,
            segment_time=segment_time,
            source_adapter=adapter,
            total_segments=total_segments,
            prefetch=prefetch,
        )

        _writers[sid] = w
        return w


async def ensure_segment(sid: str, idx: int):
    w = await get_or_create_writer(sid)
    # ensure_segment will start writer if not running
    await w.ensure_segment(idx)


async def wait_prefetch(sid: str, timeout: Optional[float] = 2.0) -> bool:
    """
    Wait up to timeout for the writer to reach prefetch target.
    If timeout is None or <=0, wait indefinitely.
    """
    w = await get_or_create_writer(sid)

    # start writer if not running
    try:
        asyncio.get_running_loop().create_task(w.ensure_segment(0))
    except RuntimeError:
        # fallback if no running loop
        await w.ensure_segment(0)

    return await w.wait_prefetch(timeout)


async def start_prefetch(sid: str, timeout: Optional[float] = 2.0) -> bool:
    """
    Starts writer (via ensure_segment(0)) and then wait up to timeout for prefetch.
    If timeout is None or <=0 — wait indefinitely.
    """
    w = await get_or_create_writer(sid)
    # start ensure in background if not running
    try:
        asyncio.get_running_loop().create_task(w.ensure_segment(0))
    except RuntimeError:
        await w.ensure_segment(0)

    return await w.wait_prefetch(timeout)


async def stop_stream(sid: str):
    async with _lock:
        w = _writers.get(sid)
        if not w:
            return
        try:
            await w.stop()
        except Exception:
            pass
        try:
            del _writers[sid]
        except KeyError:
            pass


async def stop_all_streams():
    async with _lock:
        items = list(_writers.items())
        _writers.clear()

    for _, writer in items:
        try:
            await writer.stop()
        except Exception:
            pass

async def prefetch_gate(sid: str, timeout=0.5):

    w = await get_or_create_writer(sid)

    # START WRITER
    asyncio.get_running_loop().create_task(
        w.ensure_segment(0)
    )

    start = time.time()

    while time.time() - start < timeout:

        if (
            (w.out_dir / "segment_00000.ts").exists()
            and
            (w.out_dir / "segment_00001.ts").exists()
            and
            (w.out_dir / "segment_00002.ts").exists()
        ):
            return True

        await asyncio.sleep(0.05)

    return False

from typing import Dict, Optional
from pathlib import Path
from api.segments_engine.writer import StreamWriter
from api.segments_engine.adapters import SpotifyAdapter
from api import config
import asyncio
import json

_writers: Dict[str, StreamWriter] = {}
_registry_lock = asyncio.Lock()

def _default_ff_args():
    input_args = ["-f", "dshow", "-i", f"audio={config.AUDIO_SINK_OUTPUT_DEVICE}"]
    audio_codec_args = [
        "-c:a", config.AUDIO_TARGET.get("codec", "aac"),
        "-ac", str(config.AUDIO_TARGET.get("channels", 2)),
        "-ar", str(config.AUDIO_TARGET.get("samplerate", 48000)),
        "-b:a", config.AUDIO_TARGET.get("bitrate", "192k"),
    ]
    return input_args, audio_codec_args

async def get_or_create_writer(sid: str, kind: str = "spotify") -> StreamWriter:
    async with _registry_lock:
        w = _writers.get(sid)
        if w:
            return w
        input_args, audio_codec_args = _default_ff_args()
        out_dir = Path(config.STREAMS) / sid

        # read metadata if present to pass total_segments to writer
        total_segments = None
        meta_path = out_dir / "metadata.json"
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                total_segments = int(meta.get("total_segments")) if meta.get("total_segments") is not None else None
        except Exception:
            total_segments = None

        if kind == "spotify":
            adapter = SpotifyAdapter()
        else:
            adapter = SpotifyAdapter()

        w = StreamWriter(
            sid=sid,
            out_dir=out_dir,
            ffmpeg_bin=config.FFMPEG,
            input_args=input_args,
            audio_codec_args=audio_codec_args,
            segment_time=int(config.SPOTIFY_HLS_OPTS.get("hls_time", 2)),
            source_adapter=adapter,
            total_segments=total_segments,
        )
        _writers[sid] = w
        return w

def _segment_path(w, idx: int) -> Path:
    return w.out_dir / f"segment_{idx:05d}.ts"


def _segment_exists(w, idx: int) -> bool:
    p = _segment_path(w, idx)
    try:
        return p.exists() and p.stat().st_size > 1024
    except Exception:
        return False


def _segment_path(w, idx: int) -> Path:
    return w.out_dir / f"segment_{idx:05d}.ts"


def _segment_exists(w, idx: int) -> bool:
    p = _segment_path(w, idx)
    try:
        # 0-байт считаем отсутствующим (битым)
        return p.exists() and p.stat().st_size > 1024
    except Exception:
        return False


async def ensure_segment(sid: str, idx: int, kind: str = "spotify"):
    """
    Гарантировать наличие сегмента idx.

    Ключевая идея:
    - если сегмент уже есть → ничего не делаем
    - если это перемотка назад → дописываем ТОЛЬКО отсутствующие сегменты
      до первого уже существующего (чтобы НЕ перезаписать 8,9,10...)
    """
    w = await get_or_create_writer(sid, kind=kind)

    # 1) уже есть нормальный файл — выходим
    if _segment_exists(w, idx):
        return

    try:
        if w._is_running():
            current_next = getattr(w, "_next_index", 0)

            # --- BACKWARD SEEK ---
            if idx < current_next:
                # Найти первый существующий сегмент начиная с idx
                # (чтобы остановиться ПЕРЕД ним)
                max_scan = 500
                first_existing = None
                for i in range(idx, idx + max_scan):
                    if _segment_exists(w, i):
                        first_existing = i
                        break

                if first_existing is None:
                    # нет существующих впереди — допишем только idx
                    last_missing = idx
                else:
                    # дописываем только до первого существующего - 1
                    last_missing = max(idx, first_existing - 1)

                pos_ms = int(idx) * int(w.segment_time) * 1000
                await w.request_seek(pos_ms, stop_after_idx=last_missing)

            # --- FORWARD SEEK ДАЛЕКО ---
            elif idx > current_next + 1:
                pos_ms = int(idx) * int(w.segment_time) * 1000
                await w.request_seek(pos_ms)

            # иначе — обычный ход, просто ждём ниже
    except Exception:
        pass

    # дождаться сегмента
    await w.ensure_segment(idx)

    prefetch = int(config.SPOTIFY_HLS_OPTS.get("prefetch", 2))
    for j in range(1, prefetch + 1):
        next_idx = idx + j
        # если известно об общем числе сегментов — не prefetch за пределами
        if w.total_segments is not None and next_idx >= w.total_segments:
            break
        if not _segment_exists(w, next_idx):
            await w.wait_for_segment(next_idx, timeout=float(config.SPOTIFY_HLS_OPTS.get("hls_time", 10.0)) + 4.0)

async def stop_writer(sid: str):
    """
    Принудительно остановить writer по sid (если есть).
    """
    async with _registry_lock:
        w = _writers.get(sid)
        if not w:
            return
        try:
            await w.stop()
        except Exception:
            pass

async def set_total_segments(sid: str, total: int):
    w = await get_or_create_writer(sid)
    await w.set_total_segments(total)

def start_writer_background(sid: str, start_position_ms: int = 0):
    """
    Fire-and-forget writer start.
    Can be safely called from HTTP endpoint.
    Does NOT block.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop -> nothing we can do
        return False

    async def _bg():
        try:
            # get writer (creates if missing)
            w = await get_or_create_writer(sid, kind="spotify")

            # already running?
            if w._is_running():
                return

            seg_time = w.segment_time
            start_index = int(start_position_ms // 1000 // seg_time)
            start_ms = start_index * seg_time * 1000

            # this is actual ffmpeg start
            await w._start_at(start_index, start_ms)

        except Exception:
            log.exception("start_writer_background failed sid=%s", sid)

    loop.create_task(_bg())
    return True


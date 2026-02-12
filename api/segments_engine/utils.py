import asyncio
import math
from pathlib import Path
from api import config
from api.segments_engine import registry, writer


def get_segment_count(duration_ms: int, segment_time: int) -> str:
    total_segments_count = int(math.ceil((duration_ms / 1000) / segment_time))
    return total_segments_count

# Helper: сопоставление пути/файла сегмента (подстрой под вашу структуру)
def segment_path_for(sid: str, idx: int) -> Path:
    # предполагается: STREAM_OUT_DIR/<sid>/segment_00000.ts
    return config.STREAMS / sid / f"segment_{idx:05d}.ts"

async def ensure_segment_exists(sid: str, idx: int):
    """
    Пытаться дождаться/запустить генерацию сегмента для sid/idx.
    Подставьте сюда вызов в вашей системе (registry.ensure_segment / writer.ensure_segment / request_seek).
    """
    # попытка 1: registry.ensure_segment(sid, idx)
    try:
        if hasattr(registry, "ensure_segment"):
            # some implementations might be sync; try awaiting if coroutine
            res = registry.ensure_segment(sid, idx)
            if asyncio.iscoroutine(res):
                await res
            return
        if hasattr(registry, "request_seek"):
            res = registry.request_seek(sid, idx)
            if asyncio.iscoroutine(res):
                await res
            return
    except Exception:
        pass

    # Если не нашли — пробуем просто подождать коротко, чтобы не ломать поведение.
    # Можно настроить сюда свою логику для вызова ffmpeg/request_seek в вашей кодовой базе.
    await asyncio.sleep(0.5)
    # и если по-прежнему нет файла — пробуем вернуть ошибку выше
    seg_path = segment_path_for(sid, idx)
    if not seg_path.exists():
        raise RuntimeError("ensure_segment: can't generate segment — hook missing. Replace ensure_segment_exists with real call.")

import time
import math
import asyncio
from pathlib import Path
from fastapi import Request, Response
from typing import Dict


HOLD_MODE = "503"              # "503" (мы используем этот режим)
# В памяти: один gate на sid
_GATE_REGISTRY: Dict[str, "SegmentGate"] = {}

class SegmentGate:
    def __init__(self, seg_time: float):
        self.lock = asyncio.Lock()
        self.current_index = -1  # последний отданный индекс сегмента
        self.last_end_ts = 0.0   # timestamp (epoch) — когда закончится просмотр последнего отданного сегмента
        self.seg_time = seg_time

    def time_until_next(self) -> float:
        return max(0.0, self.last_end_ts - time.time())

    async def try_allow(self, idx: int, is_seek: bool = False):
        """
        Возвращает tuple (allowed: bool, wait_seconds: float)
        allowed == True -> caller должен убедиться, что сегмент доступен и вернуть его.
        is_seek=True -> разрешаем сразу и помечаем gate (для seek-play команды).
        """
        async with self.lock:
            now = time.time()

            if is_seek:
                # при явном seek — разрешаем немедленно
                self.current_index = idx
                self.last_end_ts = now + self.seg_time
                return True, 0.0

            # повторный запрос уже выданного сегмента
            if idx <= self.current_index:
                return True, 0.0

            # если это следующий сегмент в очереди
            if idx == self.current_index + 1:
                wait = self.time_until_next()
                if wait <= 1e-6:
                    # разрешаем и обновляем границы — сегмент "просмотрится" до now + seg_time
                    self.current_index = idx
                    self.last_end_ts = now + self.seg_time
                    return True, 0.0
                else:
                    return False, wait

            # idx > current+1 — слишком далеко вперед, рассчитаем время ожидания
            wait = self.time_until_next() + (idx - (self.current_index + 1)) * self.seg_time
            return False, wait

def get_gate_for_sid(sid: str, seg_time: float = 6.0) -> SegmentGate:
    g = _GATE_REGISTRY.get(sid)
    if g is None:
        g = SegmentGate(seg_time)
        _GATE_REGISTRY[sid] = g
    return g

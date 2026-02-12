import asyncio
import subprocess
import time
from pathlib import Path
from typing import Optional, List
import logging

from api.segments_engine.types import AudioSourceAdapter

log = logging.getLogger("segments.writer")


class StreamWriter:
    """
    Continuous segment writer with explicit stop/seek API.
    """

    def __init__(
        self,
        sid: str,
        out_dir: Path,
        ffmpeg_bin: str,
        input_args: List[str],
        audio_codec_args: List[str],
        segment_time: int,
        source_adapter: AudioSourceAdapter,
        total_segments: Optional[int] = None,
    ):
        self.sid = sid
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.ffmpeg_bin = ffmpeg_bin
        self.input_args = input_args
        self.audio_codec_args = audio_codec_args
        self.segment_time = segment_time
        self.source = source_adapter

        self.total_segments: Optional[int] = total_segments

        self.proc: Optional[subprocess.Popen] = None
        self._watch_task: Optional[asyncio.Task] = None

        self._ctrl_lock = asyncio.Lock()
        self._cond = asyncio.Condition()

        self._next_index = self._discover_next_index()

        self._stop_after_index: Optional[int] = None
        self._forced_stop_after_index: Optional[int] = None
        self._pending_seek_ms: Optional[int] = None

        log.info("Writer created sid=%s segment_time=%s", self.sid, self.segment_time)

    # -------------------------------------------------

    def _discover_next_index(self) -> int:
        files = sorted(self.out_dir.glob("segment_*.ts"))
        if not files:
            return 0
        try:
            last = files[-1].name
            n = int(last.replace("segment_", "").replace(".ts", ""))
            return n + 1
        except Exception:
            return len(files)

    def _seg_path(self, idx: int) -> Path:
        return self.out_dir / f"segment_{idx:05d}.ts"

    def _segment_exists(self, idx: int) -> bool:
        p = self._seg_path(idx)
        try:
            return p.exists() and p.stat().st_size > 1024
        except Exception:
            return False

    def _is_running(self) -> bool:
        return (
            self.proc is not None
            and self._watch_task is not None
            and not self._watch_task.done()
        )

    # -------------------------------------------------

    async def ensure_segment(self, idx: int, timeout: float = 20.0):
        path = self._seg_path(idx)
        if path.exists() and path.stat().st_size > 1024:
            return

        await self._ensure_running_at_index(idx)

        ok = await self._wait_for_segment(idx, timeout)
        if not ok:
            raise RuntimeError(f"timeout waiting for segment {idx}")

    async def wait_for_segment(self, idx: int, timeout: float = 20.0) -> bool:
        return await self._wait_for_segment(idx, timeout)

    async def request_seek(self, position_ms: int, stop_after_idx: int | None = None):
        async with self._ctrl_lock:

            # СБРОС старых стопов (КРИТИЧНО)
            self._stop_after_index = None

            if not self._is_running():
                start_index = int(position_ms // 1000 // self.segment_time)
                await self._start_at(start_index, position_ms)
                if stop_after_idx is not None:
                    self._stop_after_index = int(stop_after_idx)
                return

            # планируем seek
            self._pending_seek_ms = int(position_ms)

            current_idx = max(0, self._next_index)

            # ждём окончания текущего сегмента
            self._stop_after_index = current_idx

            if stop_after_idx is not None:
                self._forced_stop_after_index = int(stop_after_idx)

    async def stop(self):
        async with self._ctrl_lock:
            if self._is_running():
                current_idx = max(0, self._next_index)
                self._stop_after_index = current_idx

    # -------------------------------------------------

    async def _ensure_running_at_index(self, idx: int):
        async with self._ctrl_lock:
            if self._segment_exists(idx):
                return

            if self._is_running():
                if self._next_index > idx:
                    return
                return

            start_ms = idx * self.segment_time * 1000
            await self._start_at(idx, start_ms)

    async def _start_at(self, start_index: int, position_ms: int):
        """
        Start ffmpeg writing from start_index. Safety measures:
        - сбрасываем старые stop-флаги
        - удаляем 0-байт файлы на первых индексах (чтобы -n не ломал ffmpeg)
        - перепрыгиваем уже существующие ВАЛИДНЫЕ сегменты (не трогаем >1KB)
        - логируем быстрые падения ffmpeg
        """
        # СБРОС старых стоп-флагов — критично
        self._forced_stop_after_index = None
        if self.total_segments is not None:
            # последний индекс сегмента
            self._stop_after_index = int(self.total_segments)

        # Найти первый индекс, который либо отсутствует, либо битый (0 байт)
        i = start_index
        for _ in range(1000):
            p = self._seg_path(i)
            try:
                if p.exists():
                    # если файл 0 байт — удалим его (это placeholder от старого ffmpeg)
                    try:
                        if p.stat().st_size == 0:
                            p.unlink(missing_ok=True)
                            break  # после удаления можем использовать этот индекс
                    except Exception:
                        # ignore fs races
                        pass

                    # если валидный (больше порога) — пропускаем (не перезаписываем)
                    try:
                        if p.exists() and p.stat().st_size > 1024:
                            i += 1
                            continue
                    except Exception:
                        i += 1
                        continue
                else:
                    break
            except Exception:
                break

        start_index = i

        # Prepare audio source to required position
        await self.source.prepare_position(position_ms)

        seg_pattern = str(self.out_dir / "segment_%05d.ts")

        cmd = [
            self.ffmpeg_bin,
            "-n",  # <<< НЕ перезаписывать существующие файлы
            *self.input_args,
            "-vn",
            *self.audio_codec_args,
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_time),
            "-segment_format",
            "mpegts",
            "-reset_timestamps",
            "1",
            "-segment_start_number",
            str(start_index),
            seg_pattern,
        ]

        log.warning("FFMPEG START sid=%s start_index=%s position_ms=%s cmd=%s", self.sid, start_index, position_ms, " ".join(cmd[:6]) + " ...")

        # аккуратно убиваем старый процесс если есть
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=1.0)
            except Exception:
                if self.proc.poll() is None:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass

        # обновляем next index и стартуем ffmpeg
        self._next_index = start_index

        # START process (stderr kept so we can log quick failures)
        # NOTE: keep stdout/stderr to DEVNULL normally; but we will detect early exit via poll()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        loop = asyncio.get_event_loop()
        self._watch_task = loop.create_task(self._watch_loop())

        # если был forced stop (например при backward seek), применим его
        if self._forced_stop_after_index is not None:
            try:
                self._stop_after_index = int(self._forced_stop_after_index)
            finally:
                self._forced_stop_after_index = None

        # quick-check: если процесс умер очень быстро — логируем это
        await asyncio.sleep(0.05)
        if self.proc is not None and self.proc.poll() is not None:
            # ffmpeg вышел сразу — логим и raise, чтобы registry/endpoint поймали и попытались рефорсить
            log.error("FFMPEG exited immediately (rc=%s) for sid=%s start_index=%s", self.proc.poll(), self.sid, start_index)

    async def _watch_loop(self):
        last_seen = self._next_index - 1

        try:
            while True:
                # ЕСЛИ следующий сегмент уже существует — стоп (не перезаписывать)
                if self._segment_exists(last_seen + 1):
                    self._stop_after_index = last_seen

                files = sorted(self.out_dir.glob("segment_*.ts"))
                if files:
                    try:
                        idx = int(files[-1].name.replace("segment_", "").replace(".ts", ""))
                    except Exception:
                        idx = last_seen

                    if idx > last_seen:
                        log.info("SEGMENT WRITTEN sid=%s idx=%s", self.sid, idx)
                        async with self._cond:
                            for new_idx in range(last_seen + 1, idx + 1):
                                last_seen = new_idx
                                self._next_index = new_idx + 1
                                self._cond.notify_all()

                if self._stop_after_index is not None and last_seen >= self._stop_after_index:
                    self._stop_after_index = None
                    try:
                        if self.proc and self.proc.poll() is None:
                            self.proc.terminate()
                    except Exception:
                        pass

                    if self._pending_seek_ms is not None:
                        pending = self._pending_seek_ms
                        self._pending_seek_ms = None
                        new_start_idx = int(pending // 1000 // self.segment_time)
                        await asyncio.sleep(0.05)
                        await self._start_at(new_start_idx, pending)
                        last_seen = self._next_index - 1
                        continue
                    else:
                        break

                if self.proc and self.proc.poll() is not None:
                    break

                await asyncio.sleep(0.15)

        finally:
            log.warning("WRITER STOPPED sid=%s", self.sid)
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass

            self.proc = None
            self._watch_task = None

            try:
                asyncio.get_event_loop().create_task(self.source.on_stop())
            except Exception:
                pass

    async def _wait_for_segment(self, idx: int, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout

        async with self._cond:
            while time.time() < deadline:
                p = self._seg_path(idx)
                if p.exists() and p.stat().st_size > 1024:
                    return True

                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                try:
                    await asyncio.wait_for(
                        self._cond.wait(), timeout=min(1.0, remaining)
                    )
                except asyncio.TimeoutError:
                    pass

        return False
    
    async def set_total_segments(self, total: int):
        """
        Установить общее количество сегментов (для авто-остановки в конце трека)
        """
        self.total_segments = total
        # последний индекс сегмента
        if total is not None and total > 0:
            self._stop_after_index = total

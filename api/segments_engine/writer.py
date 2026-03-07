import asyncio
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, List
import logging

from api.segments_engine.types import AudioSourceAdapter

log = logging.getLogger("segments.writer")


class StreamWriter:
    def __init__(
        self,
        sid: str,
        out_dir: Path,
        ffmpeg_bin: str,
        input_args: List[str],
        audio_codec_args: List[str],
        segment_time: int,
        source_adapter,
        total_segments: Optional[int] = None,
        prefetch: int = 2,
        video_input_args: Optional[List[str]] = None,
        video_codec_args: Optional[List[str]] = None,
    ):
        self.sid = sid
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.ffmpeg_bin = ffmpeg_bin
        self.input_args = input_args
        self.audio_codec_args = audio_codec_args
        self.video_input_args = list(video_input_args or [])
        self.video_codec_args = list(video_codec_args or [])
        self.segment_time = int(segment_time)
        self.source = source_adapter
        self.total_segments = total_segments

        self.proc: Optional[subprocess.Popen] = None
        self._watch_task: Optional[asyncio.Task] = None

        self._cond = asyncio.Condition()
        # PREFETCH count (how many segments to have before we consider prefetch_ready)
        self._prefetch_target = int(prefetch)
        self._prefetch_ready = asyncio.Event()
        self._start_time_written = False

        self._vod_convert_task: Optional[asyncio.Task] = None
        self._vod_convert_lock = asyncio.Lock()

        # internal counters
        self._last_seen_index = -1

    def _seg(self, idx: int) -> Path:
        return self.out_dir / f"segment_{idx:05d}.ts"

    def _playlist(self) -> Path:
        return self.out_dir / "playlist.m3u8"

    def _meta(self) -> Path:
        return self.out_dir / "metadata.json"

    def _exists(self, idx: int) -> bool:
        p = self._seg(idx)
        return p.exists() and p.stat().st_size > 1024

    async def ensure_segment(self, idx: int):
        """
        Ensure that segment idx exists. Starts writer if necessary and waits.
        """
        if self._exists(idx):
            return

        await self._ensure_running(idx)

        async with self._cond:
            while not self._exists(idx):
                await self._cond.wait()

    async def _ensure_running(self, idx: int):
        if self.proc and self.proc.poll() is None:
            return

        # compute ms position from index
        pos_ms = idx * self.segment_time * 1000
        await self._start_at(idx, pos_ms)

    async def _start_at(self, start_index: int, position_ms: int):
        """
        Start ffmpeg HLS muxer at a given position (calls source.prepare_position first)
        """
        # ask source to prepare (seek or start)
        await self.source.on_start()
        await self.source.prepare_position(position_ms)

        playlist = str(self._playlist())
        seg_pattern = str(self.out_dir / "segment_%05d.ts")

        # IMPORTANT: use hls_base_url pointing to API segment endpoint (for X-Accel flow)
        base_url = f"/api/stream-spotify-segment/{self.sid}/"

        cmd = [self.ffmpeg_bin, "-n"]
        if self.video_input_args:
            cmd.extend(self.video_input_args)
        cmd.extend(self.input_args)

        if self.video_input_args:
            cmd.extend([
                "-map", "0:v:0",
                "-map", "1:a:0",
                *self.video_codec_args,
            ])
        else:
            cmd.extend(["-vn"])

        cmd.extend([
            *self.audio_codec_args,
            "-f", "hls",
            "-hls_time", str(self.segment_time),
            "-hls_list_size", "0",  # LIVE sliding window like working variant
            "-hls_flags", "append_list+omit_endlist",
            "-hls_segment_type", "mpegts",
            "-hls_base_url", base_url,
            "-hls_segment_filename", seg_pattern,
            playlist
        ])

        
        log.info("starting ffmpeg: %s", " ".join(cmd))
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._watch_task = asyncio.get_event_loop().create_task(self._watch_loop())

    async def _watch_loop(self):
        """
        Observe folder for new segments, notify waiters and set prefetch flag.
        """
        try:
            while True:
                if self.proc is None:
                    break
                if self.proc.poll() is not None:
                    break

                files = sorted(self.out_dir.glob("segment_*.ts"))
                if files:
                    idx = int(files[-1].stem.split("_")[1])
                    if idx > self._last_seen_index:
                        # notify any waiters about new segments
                        async with self._cond:
                            self._last_seen_index = idx
                            self._cond.notify_all()

                        # if we hit prefetch count — mark ready and write start_time
                        if (not self._prefetch_ready.is_set()) and (idx >= self._prefetch_target - 1):
                            self._prefetch_ready.set()
                            # write start_time into metadata if present
                            try:
                                if self._meta().exists():
                                    meta = json.loads(self._meta().read_text(encoding="utf-8"))
                                    if not meta.get("start_time"):
                                        meta["start_time"] = datetime.now(timezone.utc).isoformat()
                                        self._meta().write_text(json.dumps(meta), encoding="utf-8")
                            except Exception:
                                log.exception("failed writing metadata start_time")
                            # write start_time into metadata if present
                            try:
                                if self._meta().exists():
                                    meta = json.loads(self._meta().read_text(encoding="utf-8"))
                                    if not meta.get("start_time"):
                                        meta["start_time"] = datetime.now(timezone.utc).isoformat()
                                        self._meta().write_text(json.dumps(meta), encoding="utf-8")
                            except Exception:
                                log.exception("failed writing metadata start_time")

                        # if total_segments set, stop when reached
                        if (self.total_segments is not None) and (idx >= self.total_segments - 1):

                            log.info("HLS reached final segment — finalizing playlist")

                            try:
                                if self.proc and self.proc.poll() is None:
                                    self.proc.terminate()
                            except Exception:
                                pass

                            # WAIT for ffmpeg exit
                            try:
                                if self.proc:
                                    self.proc.wait(timeout=3)
                            except Exception:
                                pass

                            # FINALIZE PLAYLIST
                            try:
                                pl = self._playlist()
                                if pl.exists():
                                    txt = pl.read_text(encoding="utf-8")
                                    if "#EXT-X-ENDLIST" not in txt:
                                        pl.write_text(txt.strip() + "\n#EXT-X-ENDLIST\n", encoding="utf-8")

                                    # Schedule VOD conversion (guarded so we don't start twice)
                                    try:
                                        if not self._vod_convert_task or self._vod__convert_task.done():
                                            # create background task (no await) — it will wait 5s internally
                                            self._vod_convert_task = asyncio.get_event_loop().create_task(self._convert_to_vod())
                                    except Exception:
                                        log.exception("failed scheduling VOD convert task")
                            except Exception:
                                log.exception("failed to finalize playlist")

                            break

                await asyncio.sleep(0.12)
        finally:
            try:
                if self.proc and self.proc.poll() is None:
                    self.proc.terminate()
            except Exception:
                pass
            self.proc = None
            self._watch_task = None
            try:
                await self.source.on_stop()
            except Exception:
                pass

    async def wait_prefetch(self, timeout: Optional[float] = 2.0) -> bool:
        """
        Wait up to timeout seconds for prefetch_ready.
        If timeout is None or <= 0 — wait indefinitely.
        """
        try:
            if timeout is None or timeout <= 0:
                await self._prefetch_ready.wait()
                return True
            else:
                await asyncio.wait_for(self._prefetch_ready.wait(), timeout)
                return True
        except asyncio.TimeoutError:
            return False

    async def wait_ready_first_segment(self, timeout: float = 6.0) -> bool:
        """
        Backwards compatible helper — check playlist + first segment existence
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._seg(0).exists() and self._seg(0).stat().st_size > 1024 and self._playlist().exists():
                return True
            if self.proc and self.proc.poll() is not None:
                return False
            
            await asyncio.sleep(0.1)
        return False

    async def stop(self):

        # stop ffmpeg
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

            try:
                self.proc.wait(timeout=2)
            except Exception:
                pass

        # cancel watcher
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._watch_task = None

        # cancel delayed VOD conversion (important)
        try:
            if hasattr(self, "_vod_сonvert_task") and self._vod_convert_task and not self._vod_convert_task.done():
                self._vod_convert_task.cancel()
                try:
                    await self._vod_convert_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        except Exception:
            log.exception("failed to cancel vod task")

        self._vod_convert_task = None

        # wake ensure_segment waiters
        async with self._cond:
            self._cond.notify_all()

        self.proc = None

        try:
            await self.source.on_stop()
        except Exception:
            pass
    
    async def _convert_to_vod(self):

        async with self._vod_convert_lock:

            delay = ((self._prefetch_target + 1) * self.segment_time )   # wait for gap
            await asyncio.sleep(delay)

            if self.proc:
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    pass

            try:
                pl = self._playlist()
                if not pl.exists():
                    return

                lines = pl.read_text(encoding="utf-8").splitlines()
                new = []
                inserted = False

                for l in lines:

                    if l.startswith("#EXT-X-MEDIA-SEQUENCE"):
                        new.append("#EXT-X-MEDIA-SEQUENCE:0")
                        continue

                    if (not inserted) and l.startswith("#EXT-X-TARGETDURATION"):
                        new.append(l)
                        new.append("#EXT-X-PLAYLIST-TYPE:VOD")
                        inserted = True
                        continue

                    new.append(l)

                pl.write_text("\n".join(new) + "\n", encoding="utf-8")

            except Exception:
                log.exception("VOD convert failed")

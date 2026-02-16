import asyncio
import math
import subprocess
import time
from pathlib import Path
from typing import Optional, List
import logging

from api.segments_engine.types import AudioSourceAdapter

log = logging.getLogger("segments.writer")



class StreamWriter:
    """
    StreamWriter using ffmpeg HLS muxer to produce:
      - playlist.m3u8
      - segment_00000.ts, segment_00001.ts, ...
    Designed for AVPro/VRChat: mpegts + aac, sliding window.
    """

    def __init__(
        self,
        sid: str,
        out_dir: Path,
        ffmpeg_bin: str,
        input_args: List[str],
        audio_codec_args: List[str],
        segment_time: int = 3,
        source_adapter: AudioSourceAdapter = None,
        hls_list_size: int = 6,
    ):
        self.sid = sid
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.ffmpeg_bin = ffmpeg_bin
        self.input_args = input_args[:]  # e.g. ["-f", "dshow", "-i", "audio=..."]
        self.audio_codec_args = audio_codec_args[:]  # e.g. ["-c:a","aac","-b:a","192k"]
        self.segment_time = int(segment_time)
        self.source = source_adapter

        self.hls_list_size = int(hls_list_size)

        self.proc: Optional[subprocess.Popen] = None
        self._watch_task: Optional[asyncio.Task] = None

        self._stop_requested = False

        log.info("HLS StreamWriter created sid=%s seg_time=%s list_size=%s", sid, self.segment_time, self.hls_list_size)

    def _playlist_path(self) -> Path:
        return self.out_dir / "playlist.m3u8"

    def _first_segment_path(self) -> Path:
        return self.out_dir / "segment_00000.ts"

    def is_running(self) -> bool:
        return self.proc is not None and self._watch_task is not None and not self._watch_task.done()

    async def start(self, start_position_ms: int = 0):
        """
        Start ffmpeg HLS muxer writing playlist + segments.
        Uses ffmpeg's -f hls with mpegts segments and append_list/delete_segments flags.
        """
        if self.is_running():
            log.debug("Start called but already running sid=%s", self.sid)
            return

        # Prepare audio source position (adapter should handle Spotify device sync)
        if self.source is not None:
            try:
                await self.source.prepare_position(start_position_ms)
            except Exception:
                log.exception("source.prepare_position failed sid=%s", self.sid)

        seg_pattern = str(self.out_dir / "segment_%05d.ts")
        playlist = str(self.out_dir / "playlist.m3u8")

        # FFmpeg HLS muxer command
        cmd = [
            self.ffmpeg_bin,
            *self.input_args,
            "-vn",  # audio only
            *self.audio_codec_args,
            "-f", "hls",
            "-hls_time", str(self.segment_time),
            "-hls_list_size", str(self.hls_list_size),
            "-hls_flags", "append_list+delete_segments+omit_endlist",
            "-hls_allow_cache", "0",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", seg_pattern,
            playlist,
        ]

        log.info("Starting ffmpeg HLS sid=%s cmd=\"%s ...\"", self.sid, " ".join(cmd[:8]))

        # ensure no stale playlist/segments remain to confuse client
        try:
            for f in list(self.out_dir.glob("segment_*.ts")):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
            p = self._playlist_path()
            if p.exists():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            log.exception("Failed clearing old segments for sid=%s", self.sid)

        # Launch ffmpeg
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        loop = asyncio.get_event_loop()
        self._watch_task = loop.create_task(self._watch_loop())

    async def stop(self):
        self._stop_requested = True
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

    async def _watch_loop(self):
        """
        Monitor ffmpeg process and ensure we keep track of segments; if ffmpeg dies - log.
        """
        try:
            # wait until first segment + playlist appear or until ffmpeg dies
            timeout = 10.0
            start = time.time()
            while True:
                if self.proc is None:
                    break
                if self.proc.poll() is not None:
                    log.error("ffmpeg exited early for sid=%s rc=%s", self.sid, self.proc.poll())
                    break
                # first segment available?
                if self._first_segment_path().exists() and self._playlist_path().exists():
                    log.info("HLS initial readiness for sid=%s", self.sid)
                    break
                if time.time() - start > timeout:
                    log.warning("Timeout waiting for initial segment for sid=%s", self.sid)
                    break
                await asyncio.sleep(0.1)

            # now main loop: check process aliveness, and if stopped - break
            while True:
                if self.proc is None or self.proc.poll() is not None:
                    log.info("ffmpeg stopped for sid=%s", self.sid)
                    break
                if self._stop_requested:
                    # terminate gracefully
                    try:
                        self.proc.terminate()
                    except Exception:
                        pass
                    break
                await asyncio.sleep(0.5)
        except Exception:
            log.exception("Watcher failed for sid=%s", self.sid)
        finally:
            self.proc = None
            self._watch_task = None
            # on stop: optionally call adapter.on_stop
            try:
                if self.source is not None:
                    asyncio.get_event_loop().create_task(self.source.on_stop())
            except Exception:
                pass

    # utility: wait for readiness (playlist + first segment)
    async def wait_ready(self, timeout: float = 6.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self._playlist_path().exists() and self._first_segment_path().exists():
                # small additional check size
                try:
                    if self._first_segment_path().stat().st_size > 1024:
                        return True
                except Exception:
                    pass
            # if ffmpeg died -> abort
            if self.proc is not None and self.proc.poll() is not None:
                return False
            await asyncio.sleep(0.12)
        return False
from fastapi import APIRouter, Query, Response, HTTPException
from pathlib import Path
import hashlib, subprocess
from subprocess import CalledProcessError
from ..config import STREAMS, YTDLP, FFMPEG, COOKIES
from ..utils import run_cmd, rewrite_m3u8, normalize_yt_url, ffmpeg_audio_params

router = APIRouter()

@router.get("/stream-yt")
def stream_yt(url: str = Query(...)):
    video_id = hashlib.md5(url.encode()).hexdigest()
    out_dir = STREAMS / video_id
    m3u8 = out_dir / "index.m3u8"

    if not m3u8.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        video = out_dir / "video.mp4"
        norm_url = normalize_yt_url(url)
        cmd = [
            str(YTDLP),
            "--cookies", str(COOKIES),
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(video),
            norm_url,
            "-v"
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except CalledProcessError as e:
            # fallback without cookies
            fb1 = [
                str(YTDLP),
                "-f", "bv*+ba/b",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", str(video),
                norm_url,
                "-v"
            ]
            subprocess.run(fb1, check=True)

        ff = [
            str(FFMPEG), "-y", "-i", str(video),
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-b:v", "8000k", "-maxrate", "9000k", "-bufsize", "16000k",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        ] + ffmpeg_audio_params() + [
            "-af", "pan=stereo|FL<0.8*FL+0.6*FC+0.6*BL|FR<0.8*FR+0.6*FC+0.6*BR",
            "-f", "hls", "-hls_time", "4", "-hls_list_size", "0", "-hls_playlist_type", "vod",
            "-hls_flags", "independent_segments", str(m3u8)
        ]
        run_cmd(ff)
        rewrite_m3u8(m3u8, video_id)

    return Response(content=m3u8.read_text(), media_type="application/vnd.apple.mpegurl")

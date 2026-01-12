import hashlib
import subprocess
from subprocess import CalledProcessError
from pathlib import Path
import urllib.parse as urlparse
from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse


BASE = Path(__file__).resolve().parents[1]
STREAMS = BASE / "html" / "streams"
VIDEOS = BASE / "input"
COOKIES = BASE / "cookies.txt"

YTDLP = "yt-dlp.exe"
FFMPEG = "ffmpeg.exe"


app = FastAPI()

@app.get("/api/stream-sc")
def stream_sc(url: str = Query(...)):
    track_id = hashlib.md5(url.encode()).hexdigest()
    out_dir = STREAMS / track_id
    m3u8 = out_dir / "index.m3u8"

    if not m3u8.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        audio = out_dir / "audio.m4a"

        subprocess.run([
            str(YTDLP),
            "-f", "bestaudio",
            "-o", str(audio),
            url
        ], check=True)

        subprocess.run([
            str(FFMPEG), "-y",
            "-i", str(audio),
            "-vn",
            "-c:a", "aac",
            "-b:a", "256k",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            str(m3u8)
        ], check=True)

    return RedirectResponse(
        url=f"/streams/{track_id}/index.m3u8",
        status_code=302
    )

def normalize_yt_url(url: str) -> str:
    # Преобразует youtu.be -> full watch URL и убирает лишние параметры, если можно
    p = urlparse.urlparse(url)
    netloc = p.netloc.lower()
    if netloc.endswith("youtu.be"):
        video_id = p.path.lstrip("/")
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if "youtube" in netloc:
        qs = urlparse.parse_qs(p.query)
        if "v" in qs:
            return f"https://www.youtube.com/watch?v={qs['v'][0]}"
    return url

@app.get("/api/stream-yt")
def stream_yt(url: str = Query(...)):
    video_id = hashlib.md5(url.encode()).hexdigest()
    out_dir = STREAMS / video_id
    m3u8 = out_dir / "index.m3u8"

    if not m3u8.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        video = out_dir / "video.mp4"

        # нормализуем url (удаляем share-параметры типа si)
        norm_url = normalize_yt_url(url)

        cmd = [
            str(YTDLP),
            "--cookies", str(COOKIES),
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(video),
            norm_url,
            "-v"  # verbose — полезно в логах
        ]

        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except CalledProcessError as e:
            print("yt-dlp failed:", e.returncode)
            print("CMD:", " ".join(cmd))
            print("stdout:", e.stdout)
            print("stderr:", e.stderr)
            # фоллбек без cookie
            fb1 = [
                str(YTDLP),
                "-f", "bv*+ba/b",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", str(video),
                norm_url,
                "-v"
            ]
            try:
                proc2 = subprocess.run(fb1, check=True, capture_output=True, text=True)
            except CalledProcessError as e2:
                print("Fallback without cookies failed:", e2.returncode)
                print("stderr:", e2.stderr)
                raise

        subprocess.run([
            str(FFMPEG), "-y",
            "-i", str(video),
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.2",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-b:v", "8000k",
            "-maxrate", "9000k",
            "-bufsize", "16000k",
            "-c:a", "aac",
            "-b:a", "256k",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            str(m3u8)
        ], check=True)

    return RedirectResponse(
        url=f"/streams/{video_id}/index.m3u8",
        status_code=302
    )
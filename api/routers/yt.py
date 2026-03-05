import logging
from fastapi import APIRouter, Query, Response, HTTPException
import hashlib, subprocess, urllib.parse as urlparse

from api import config, utils

logger = logging.getLogger(__name__)

router = APIRouter()

def normalize_yt_url(url: str) -> str:
    p = urlparse.urlparse(url)
    if p.netloc.endswith("youtu.be"):
        vid = p.path.lstrip("/")
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
    if "youtube" in p.netloc:
        qs = urlparse.parse_qs(p.query)
        if "v" in qs:
            return f"https://www.youtube.com/watch?v={qs['v'][0]}"
    return url

@router.get("/stream-yt")
def stream_yt(url: str = Query(...)):
    norm = normalize_yt_url(url)
    sid = hashlib.md5(norm.encode()).hexdigest()
    out_dir = config.STREAMS / sid
    m3u8 = out_dir / "index.m3u8"

    if m3u8.exists():
        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
                "Content-Type": "application/vnd.apple.mpegurl",
            },
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    video_out = out_dir / "video.mp4"

    try:
        # пробуємо з cookies (як зараз)
        cmd = [
            config.YTDLP,
            "--js-runtimes", config.JS_RUNTIME,
            "--remote-components", "ejs:github",
            "--cookies", str(config.COOKIES),
            # або: omit '-f', "best" — краще дати yt-dlp самому обрати
            # "-f", "best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(video_out),
            norm,
        ]
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            # лог в stderr дуже допоможе зрозуміти причину
            logger.error("yt-dlp stderr: %s", p.stderr)
            raise subprocess.CalledProcessError(p.returncode, cmd, output=p.stdout, stderr=p.stderr)

    except subprocess.CalledProcessError:
        # fallback — пробуємо без cookies (іноді cookies файл у нестандартному форматі)
        try:
            cmd2 = [
                config.YTDLP,
                "--js-runtimes", config.JS_RUNTIME,
                "--remote-components", "ejs:github",
                # "-f", "best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", str(video_out),
                norm,
            ]
            p2 = subprocess.run(cmd2, check=False, capture_output=True, text=True, timeout=300)
            if p2.returncode != 0:
                logger.error("yt-dlp fallback stderr: %s", p2.stderr)
                raise subprocess.CalledProcessError(p2.returncode, cmd2, output=p2.stdout, stderr=p2.stderr)
        except subprocess.CalledProcessError as e:
            # повертаємо stderr в помилці, щоб не гадати
            raise HTTPException(status_code=500, detail=f"yt-dlp failed. stderr: {e.stderr[:2000]}")

    try:
        utils.video_to_hls(video_out, out_dir, sid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl",
        },
    )
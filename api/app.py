import hashlib
import re
import time
import requests
import subprocess
from subprocess import CalledProcessError
from pathlib import Path
import urllib.parse as urlparse
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse


BASE = Path(__file__).resolve().parents[1]
STREAMS = BASE / "html" / "streams"
VIDEOS = BASE / "input"
COOKIES = BASE / "cookies.txt"

YTDLP = "yt-dlp.exe"
FFMPEG = "ffmpeg.exe"


app = FastAPI()

def rewrite_m3u8(m3u8: Path, stream_id: str):
    text = m3u8.read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            out.append(line)
        else:
            out.append(f"/api/stream_segment/{stream_id}/{line.lstrip('./')}")
    m3u8.write_text("\n".join(out) + "\n", encoding="utf-8")

def run_cmd(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\nstdout:{p.stdout}\nstderr:{p.stderr}")
    return p

def extract_image_from_telegram_html(html: str, base_url: str = None):
    """
    Попытаться найти картинку в HTML тг поста.
    Ищем meta property="og:image", twitter:image, а также src в <img> и data-src в шаблонах.
    """
    # og:image
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'<img[^>]+class=["\'][^"\']*tgme_widget_message_photo_wrap[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        # data-src или other img tags
        m = re.search(r'data-src=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        return None
    img = m.group(1)
    if base_url and img.startswith("/"):
        return urlparse.urljoin(base_url, img)
    return img

def try_fetch_telegram_post(url: str, timeout=15):
    """
    Получить HTML поста Telegram. Попробуем 3 варианта:
     - оригинальный URL
     - заменим host на t.me/s/CHANNEL/ID (public view)
     - добавим headers (User-Agent)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36"
    }
    # try original
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r.text, r.url
    except Exception:
        pass

    # if /<channel>/<id> -> try /s/<channel>/<id>
    p = urlparse(url)
    if p.netloc.endswith("t.me"):
        parts = p.path.strip("/").split("/")
        if len(parts) >= 2:
            channel, msg = parts[0], parts[1]
            alt = f"https://t.me/s/{channel}/{msg}"
            try:
                r = requests.get(alt, headers=headers, timeout=timeout, allow_redirects=True)
                r.raise_for_status()
                return r.text, r.url
            except Exception:
                pass

    # final fail
    raise HTTPException(status_code=400, detail="can't fetch telegram post HTML; maybe it's private or blocked")

@app.get("/api/stream-tg-image")
def stream_tg_image(url: str = Query(..., description="Telegram post URL e.g. https://t.me/channel/1234"),
                 duration: int = Query(300, description="duration seconds"),
                 width: int = Query(1280), height: int = Query(720)):
    # validate
    if duration <= 0 or duration > 3600:
        raise HTTPException(status_code=400, detail="duration out of range")
    # fetch html
    html, final_url = try_fetch_telegram_post(url)
    img_url = extract_image_from_telegram_html(html, base_url=final_url)
    if not img_url:
        raise HTTPException(status_code=404, detail="no image found in telegram post")

    # normalize img_url: sometimes it is //... or relative
    if img_url.startswith("//"):
        img_url = "https:" + img_url
    img_id = hashlib.md5((img_url + f"{duration}{width}x{height}").encode()).hexdigest()
    out_dir = STREAMS / img_id
    m3u8 = out_dir / "index.m3u8"
    if m3u8.exists():
        return Response(status_code=200, headers={
            "X-Accel-Redirect": f"/streams/{img_id}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl"
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_img = out_dir / "src.jpg"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with requests.get(img_url, stream=True, headers=headers, timeout=15) as r:
            r.raise_for_status()
            total = 0
            with tmp_img.open("wb") as f:
                for chunk in r.iter_content(8192):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 8 * 1024 * 1024:
                        raise HTTPException(status_code=400, detail="image too large")
                    f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"failed to download image: {e}")

    video_tmp = out_dir / "video.mp4"
    cmd = [
        FFMPEG,
        "-y",
        "-loop", "1",
        "-i", str(tmp_img),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        str(video_tmp)
    ]
    run_cmd(cmd)

    hls_cmd = [
        FFMPEG,
        "-y",
        "-i", str(video_tmp),
        "-c:v", "copy",
        "-c:a", "copy",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_playlist_type", "vod",
        str(m3u8)
    ]
    run_cmd(hls_cmd)

    rewrite_m3u8(m3u8, img_id)

    return Response(
        content=m3u8.read_text(),
        media_type="application/vnd.apple.mpegurl"
    )

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
            "-profile:a", "aac_low",
            "-ac", "2",
            "-ar", "48000",
            "-b:a", "192k",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            str(m3u8)
        ], check=True)

        rewrite_m3u8(m3u8, track_id)

    return Response(
        content=m3u8.read_text(),
        media_type="application/vnd.apple.mpegurl"
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

            "-map", "0:v:0",
            "-map", "0:a:0",

            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.2",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-b:v", "8000k",
            "-maxrate", "9000k",
            "-bufsize", "16000k",
            "-g", "60",
            "-keyint_min", "60",
            "-sc_threshold", "0",

            "-c:a", "aac",
            "-profile:a", "aac_low",
            "-ac", "2",
            "-ar", "48000",
            "-b:a", "192k",
            "-af", "pan=stereo|FL<0.8*FL+0.6*FC+0.6*BL|FR<0.8*FR+0.6*FC+0.6*BR",

            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            "-hls_flags", "independent_segments",

            str(m3u8)
        ], check=True)

        rewrite_m3u8(m3u8, video_id)

    return Response(
        content=m3u8.read_text(),
        media_type="application/vnd.apple.mpegurl"
    )

@app.get("/api/stream_segment/{stream_id}/{filename}")
def stream_segment(stream_id: str, filename: str):
    path = STREAMS / stream_id / filename
    if not path.exists():
        raise HTTPException(status_code=404)

    if filename.endswith(".ts"):
        return FileResponse(path, media_type="video/mp2t")

    return FileResponse(
        path,
        media_type="application/vnd.apple.mpegurl"
    )

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parents[1]
STREAMS = BASE / "html" / "streams"
LOGS = BASE / "logs"
INPUT = BASE / "input"
OUTPUT = BASE / "output"
COOKIES = BASE / "cookies.txt"
CLOUDFLARED_LOG = LOGS / "cloudflared.log"

YTDLP = "yt-dlp.exe"
JS_RUNTIME = "node"
FFMPEG = "ffmpeg.exe"

AUDIO_SINK_OUTPUT_DEVICE = "CABLE Output (VB-Audio Virtual Cable)"

HLS_OPTS = {
    "hls_time": "4",
    "hls_list_size": "0",
    "hls_playlist_type": "vod",
    "hls_flags": "independent_segments",
}

SPOTIFY_HLS_OPTS = {
    "hls_time": 3,
    "hls_list_size": "0",
    "hls_playlist_type": "event",
    "prefetch_timeout": 0, 
    "prefetch": 3,
}

AUDIO_TARGET = {
    "codec": "aac",
    "profile": "aac_low",
    "channels": "2",
    "samplerate": "48000",
    "bitrate": "192k"
}

TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_PASSWORD = os.getenv("TG_PASSWORD", "")
TG_SESSION = os.getenv("TG_SESSION", "tg_session")

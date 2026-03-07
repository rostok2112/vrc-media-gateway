import os
from pathlib import Path
from dotenv import load_dotenv


BASE = Path(__file__).resolve().parents[1]
API_DIR = Path(__file__).resolve().parent
DOTENV_PATH = API_DIR / ".env"

if DOTENV_PATH.exists():
    load_dotenv(DOTENV_PATH)
else:
    load_dotenv()

STREAMS = BASE / "html" / "streams"
LOGS = BASE / "logs"
INPUT = BASE / "input"
OUTPUT = BASE / "output"
COOKIES = BASE / "cookies.txt"
CLOUDFLARED_LOG = LOGS / "cloudflared.log"
LOCAL_UPLOAD_MAX_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_BYTES", str(256 * 1024 * 1024 * 1024)))

YTDLP = "yt-dlp.exe"
JS_RUNTIME = "node"
FFMPEG = "ffmpeg.exe"
SPOTIFY = "Spotify.exe"

AUDIO_SINK_INPUT_DEVICE = "CABLE Input (VB-Audio Virtual Cable)"
AUDIO_SINK_OUTPUT_DEVICE = "CABLE Output (VB-Audio Virtual Cable)"

HLS_OPTS = {
    "hls_time": "4",
    "hls_list_size": "0",
    "hls_playlist_type": "vod",
    "hls_flags": "independent_segments",
}

SPOTIFY_HLS_OPTS = {
    "hls_time": 2,
    "hls_list_size": "0",
    "hls_playlist_type": "event",
    "prefetch_timeout": 0, 
    "prefetch": 10,
    "show_info": False,
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
_session = os.getenv("TG_SESSION", "tg_session")
TG_SESSION = _session if Path(_session).is_absolute() else str(BASE / _session)

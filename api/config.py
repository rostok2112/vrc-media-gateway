from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STREAMS = BASE / "html" / "streams"
VIDEOS = BASE / "input"
COOKIES = BASE / "cookies.txt"

YTDLP = "yt-dlp.exe"
FFMPEG = "ffmpeg.exe"

HLS_OPTS = {
    "hls_time": "4",
    "hls_list_size": "0",
    "hls_playlist_type": "vod",
    "hls_flags": "independent_segments",
}
AUDIO_TARGET = {
    "codec": "aac",
    "profile": "aac_low",
    "channels": "2",
    "samplerate": "48000",
    "bitrate": "192k"
}

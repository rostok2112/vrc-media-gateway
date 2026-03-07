# API Notes

This folder contains the FastAPI backend for VRChat Media Gateway.

The backend is responsible for:

- media download and conversion for YouTube, SoundCloud, Telegram photos/videos/stickers, generic images/audio/videos, animated GIF motion media, and local media
- websocket RPC for Spotify Desktop control
- live HLS segment generation for Spotify
- tunnel URL discovery from `logs/cloudflared.log`

## Runtime Model

- FastAPI listens on `127.0.0.1:5000`
- nginx on `127.0.0.1:8080` proxies `/api/*` and `/api/ws/*` to FastAPI
- generated HLS output is written to `../html/streams/`
- `/local-api/*` stays on loopback-only FastAPI and is intentionally outside the nginx/tunnel path

For full-stack usage, start the project from the repository root with:

```powershell
.\run stream server.bat
```

## Main Endpoint Groups

- `/api/stream-yt`
- `/api/stream-sc`
- `/api/stream-image`
- `/api/stream-audio`
- `/api/stream-video`
- `/api/stream-tg-media`
- `/api/stream-tg-image`
- `/api/stream-tg-video`
- `/api/tg-post-info`
- `/api/stream-spotify`
- `/api/stream-spotify-clear`
- `/api/tunnel`
- `/api/ws/spotify`
- `/local-api/stream-local-path-build-start`
- `/local-api/stream-local-upload-build-start`
- `/local-api/stream-local-build-status`
- `/local-api/clear-cache-all`

Animated GIF behavior:

- generic `.gif` sources are treated as motion/video instead of the still-image path
- Telegram animated GIF posts are classified as video-like media and converted through the Telegram video pipeline

Telegram sticker behavior:

- static Telegram stickers such as `.webp` are classified as image media and converted through the Telegram image path
- `video/webm` Telegram stickers are classified as video media and converted through the Telegram video path
- animated `.tgs` Telegram stickers are rendered through `rlottie_python`, converted to motion media, and then sent through the Telegram video path

Telegram post-text behavior:

- `/api/stream-tg-media`, `/api/stream-tg-image`, and `/api/stream-tg-video` accept optional `with_text=1`
- when `with_text=1`, Telegram post text is rendered on a black panel below the media instead of over the media
- text-only Telegram posts can also build a text-only HLS output through `/api/stream-tg-media`
- `/api/tg-post-info` is a lightweight metadata probe used by the browser extension to detect whether a Telegram post is text-only before starting a managed build

Non-Spotify HLS tuning:

- `/api/stream-yt`
- `/api/stream-sc`
- `/api/stream-image`
- `/api/stream-audio`
- `/api/stream-video`
- `/api/stream-tg-media`
- `/api/stream-tg-image`
- `/api/stream-tg-video`
- `/local-api/stream-local-path-build-start`
- `/local-api/stream-local-upload-build-start`

Those routes accept optional `segment_time=<seconds>`. If the param is missing, the backend uses the default from `HLS_OPTS` in [`config.py`](./config.py). The browser extension popup now stores that value in its own `Streaming settings` section and sends it for non-Spotify exports.

Python dependency note:

- `rlottie_python` is required for animated Telegram `.tgs` sticker export

## Telegram Config

The backend loads `api/.env` first and falls back to the root environment if that file is missing.

See [`api/.env.sample`](./.env.sample) for the Telegram variables:

- `TG_API_ID`
- `TG_API_HASH`
- `TG_PASSWORD`
- `TG_SESSION`

Generate a session from the repository root with:

```powershell
python auxillary/get_tg_session.py
```

## Spotify Note

Spotify features depend on the in-memory websocket registry in this backend. Use the single-process full-stack path when you need Spotify Desktop or Spotify Web export flows.

`/api/stream-spotify` accepts optional Spotify HLS tuning through query params:

- `segment_time=<seconds>`
- `prefetch=<count>`

Example:

```text
/api/stream-spotify?url=https://open.spotify.com/track/...&segment_time=2&prefetch=10
```

If those params are missing, the backend uses the defaults from `SPOTIFY_HLS_OPTS` in [`config.py`](./config.py).

The Spicetify bridge stores those values in its `Streaming settings` section and sends them through the `VRChat` button and the Spotify cache-clear flow so different Spotify streaming presets do not share the same cache folder.

## Local Media Note

Local media ingestion is split out from the public API on purpose.

- `/local-api/stream-local-path-build-start` accepts an absolute local filesystem path and only works when FastAPI is running on the same machine that can read that file
- `/local-api/stream-local-upload-build-start` accepts raw uploaded file bytes for image, video, and audio media
- `/local-api/stream-local-build-status` polls local build jobs
- `/local-api/clear-cache-all` stops active stream writers, resets build job state, and wipes generated cache directories on the local machine

Security rules for that path:

- local media routes reject non-loopback callers
- they are not intended to be proxied by nginx or exposed through Cloudflare Tunnel
- browser file pickers and drag-and-drop prefer filesystem handles when Chromium exposes them, and fall back to upload otherwise

Large local upload note:

- the backend upload ceiling is controlled by `LOCAL_UPLOAD_MAX_BYTES` in [`config.py`](./config.py)
- the default is `256 GiB`, which is intended for very large local media uploads on the same machine

The final playback URL still comes from `/streams/<sid>/index.m3u8`, but the local file ingestion step itself is local-only.

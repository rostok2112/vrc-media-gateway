# VRChat Media Gateway

VRChat Media Gateway is a Windows-first toolkit for turning web media into VRChat-friendly HLS streams. The current project is  YouTube/SoundCloud/Spotify/Telegram/General media proxy: it now includes a browser extension, a Spicetify bridge for Spotify Desktop, a FastAPI backend, websocket RPC for Spotify control, and a segment engine that can stream live audio into HLS playlists.


## What It Includes

- Browser extension in [`extension/`](./extension) with:
  - YouTube watch-page button
  - SoundCloud track-page button
  - Telegram Web context-menu export for images and videos
  - Spotify Web player buttons
  - Generic image context-menu export on any site
  - Settings popup with local/public endpoint handling
- Spotify Desktop bridge in [`spotify_extension/`](./spotify_extension) for Spicetify:
  - `VRChat` button
  - `Clear cache` button
  - `Settings` button
  - `Streaming settings` section for Spotify HLS prefetch tuning
  - `Restore audio output` action
- FastAPI backend in [`api/`](./api)
- Websocket RPC endpoint for Spotify control at `/api/ws/spotify`
- HLS segment engine for live Spotify capture in [`api/segments_engine/`](./api/segments_engine)
- nginx reverse proxy and static HLS serving through [`main.conf`](./main.conf)
- Optional Cloudflare Tunnel exposure for a public HTTPS URL

## Main Workflows

| Source | How it works now |
| --- | --- |
| YouTube | Browser button or direct API call downloads via `yt-dlp`, uses Node-based JS challenge solving, then converts to HLS |
| SoundCloud | Browser button appears only on track pages, preserves secret/private links when possible, downloads with `yt-dlp`, then converts to HLS |
| Telegram images | Telegram endpoints classify the Telegram post first, then use Telethon for the real media and HTML image parsing only as a photo fallback |
| Telegram videos | Telegram Web context menu and direct API calls can auto-detect video posts, download them through Telethon, then convert them to HLS |
| Generic images | Right-click any image on most sites and export it to a static HLS video |
| Spotify Web | Injected buttons in `open.spotify.com` generate `/api/stream-spotify` links and allow cache clearing |
| Spotify Desktop | Spicetify bridge talks to the backend over websocket, routes Spotify audio into a virtual cable, and writes live HLS segments |
| Local files | Legacy/manual flow through [`run convertion.bat`](./run%20convertion.bat) using files from `input/` |

## Platform Notes

- The full project is designed around Windows.
- The documented scripts are `.bat` files.
- Spotify capture uses `winappaudiorouter`, `Spotify.exe`, and DirectShow audio capture.
- The Spotify path expects VB-Audio Virtual Cable device names by default.
- YouTube, SoundCloud, image, and Telegram conversion logic is less OS-specific, but the repo setup and helper scripts still assume Windows.

## Requirements

Install these tools and make sure they are available in `PATH`:

- Python 3.10+
- `ffmpeg`
- `yt-dlp`
- `node`
- `nginx`
- `cloudflared` if you want public HTTPS URLs

Optional but required for specific features:

- Telegram API credentials and a logged-in Telegram session for Telegram media
- Spotify Desktop
- Spicetify CLI
- VB-Audio Virtual Cable

Install Python dependencies:

```powershell
pip install fastapi "uvicorn[standard]" requests python-dotenv telethon "qrcode[pil]" psutil winappaudiorouter
```

## Architecture

The normal runtime shape is:

1. Browser extension or Spicetify bridge triggers an `/api/...` endpoint.
2. nginx listens on `http://127.0.0.1:8080`, proxies `/api/*` and `/api/ws/*` to FastAPI on `127.0.0.1:5000`, and serves `html/streams/`.
3. FastAPI downloads or captures media and writes HLS output into `html/streams/<sid>/`.
4. VRChat receives the public HTTPS API URL from Cloudflare Tunnel, or a local URL for testing.

Important port split:

- FastAPI listens on `127.0.0.1:5000`
- nginx listens on `127.0.0.1:8080`
- The browser extension and Spotify websocket bridge should point at `8080`, not `5000`

## Setup

### 1. Configure nginx

[`main.conf`](./main.conf) already contains the expected layout:

- `/api/` -> FastAPI
- `/api/ws/` -> websocket proxy for Spotify RPC
- `/streams/` -> public HLS output from `html/streams/`

Start nginx with:

```powershell
.\run server.bat
```

### 2. Configure the backend

FastAPI is in [`api/`](./api). For most features there is no required backend config besides installed binaries.

For Telegram support:

1. Copy [`api/.env.sample`](./api/.env.sample) to `api/.env`
2. Fill in:

```env
TG_API_ID=...
TG_API_HASH=...
TG_PASSWORD=
TG_SESSION=tg_session.session
```

3. Generate a Telegram session:

```powershell
python auxillary/get_tg_session.py
```

The QR login helper writes the session to the path from `TG_SESSION`. By default that ends up in the repository root as `tg_session.session`.

If Telegram login is acting up, inspect the session with:

```powershell
python auxillary/telethon_check.py
```

### 3. Prepare YouTube and protected downloads

`yt-dlp` is invoked with Node-based JS challenge solving. Keep both `yt-dlp` and Node available in `PATH`.

If you need age-restricted or authenticated downloads:

- export cookies to `cookies.txt` in the repository root
- do not commit that file

If YouTube starts failing because of extractor changes, update `yt-dlp` with:

```powershell
.\update yt-dlp.bat
```

### 4. Load the browser extension

Load [`extension/`](./extension) as an unpacked Chromium extension.

The popup supports:

- `Use public URL (tunnel)`
- `Use local API for processing`
- local host and port
- public tunnel URL auto-detection or manual override

`Use local API for processing` means:

- requests are sent to the local stack on `127.0.0.1:8080`
- the copied result still uses the public Cloudflare Tunnel URL

That mode is useful when the tunnel is public but you want all downloading and transcoding to happen locally.

### 5. Prepare Spotify Desktop streaming

Spotify Desktop streaming is separate from Spotify Web link generation.

Required pieces:

- Spotify Desktop installed
- Spicetify CLI installed and working
- VB-Audio Virtual Cable installed

The defaults in [`api/config.py`](./api/config.py) expect these device names:

- `CABLE Input (VB-Audio Virtual Cable)`
- `CABLE Output (VB-Audio Virtual Cable)`

If your device names differ, change them in [`api/config.py`](./api/config.py).

Install the Spicetify bridge:

```powershell
.\install_stream_bridge.bat
```

After installation, start Spotify. The bridge will connect to:

```text
ws://127.0.0.1:8080/api/ws/spotify
```

The Spotify Desktop path works like this:

1. Backend asks the Spicetify bridge to load track metadata.
2. Bridge controls Spotify playback over websocket RPC.
3. Backend routes Spotify output to the configured virtual cable.
4. `ffmpeg` captures from the cable and writes HLS segments.
5. When the track ends, the playlist is finalized and converted into a replayable VOD-style result.

Spicetify settings now also include a `Streaming settings` button. That section stores:

- prefetch segments count
- prefetch segment duration in seconds
- a read-only total prefetch duration field

The `VRChat` button appends those values to Spotify links as:

```text
/api/stream-spotify?url=<spotify-track-url>&segment_time=<seconds>&prefetch=<count>
```

If either query parameter is omitted, the backend falls back to the defaults from [`api/config.py`](./api/config.py) `SPOTIFY_HLS_OPTS`.

## Running The Stack

Recommended full-stack command:

```powershell
.\run stream server.bat
```

That starts:

- nginx on `:8080`
- FastAPI on `:5000`
- a quick Cloudflare Tunnel with logs written to `logs/cloudflared.log`

You can also start parts separately:

```powershell
.\run server.bat
.\run api.bat
.\run tunnel.bat
```

Notes:

- For the complete feature set, prefer [`run stream server.bat`](./run%20stream%20server.bat).
- The Spotify websocket registry is in-memory, so single-process operation is the safe path for Spotify features.
- [`run api.bat`](./run%20api.bat) is mainly useful for direct HTTP testing and non-Spotify flows.

## Browser Extension Behavior

Site-specific behavior on the current branch:

- YouTube: injects a `VRChat` button only on watch pages and survives SPA navigation
- SoundCloud: injects only on real track pages, not artist/profile tabs
- Telegram Web: adds a `VRChat` entry to the message context menu and uses Telegram media auto-detection
- Spotify Web: adds `VRChat` and `Clear cache` buttons near the player controls
- Generic images: adds a `VRChat` item to the browser image context menu

The popup can also be used as a manual "paste URL -> get export link" tool.

## API Overview

Main HTTP endpoints:

- `GET /api/stream-yt?url=<youtube-url>`
- `GET /api/stream-sc?url=<soundcloud-url>`
- `GET /api/stream-image?url=<image-url>&duration=300&width=1280&height=720`
- `GET /api/stream-tg-media?url=<telegram-post-url>`
- `GET /api/stream-tg-image?url=<telegram-post-url>`
- `GET /api/stream-tg-video?url=<telegram-post-url>`
- `GET /api/stream-spotify?url=<spotify-track-url>&segment_time=<seconds>&prefetch=<count>`
- `POST /api/stream-spotify-clear?url=<spotify-track-url>&segment_time=<seconds>&prefetch=<count>`
- `GET /api/tunnel`

Spotify-specific delivery endpoints:

- `GET /api/stream-spotify-playlist/{sid}`
- `GET /api/stream-spotify-segment/{sid}/{filename}`
- `WS /api/ws/spotify`

Behavior notes:

- Most VOD endpoints build the stream on first request and then serve cached HLS from `html/streams/<sid>/`
- Spotify is segment-driven and uses the websocket bridge for metadata, seeking, playback start, cache clearing, and audio restoration
- The API endpoints are the stable links you usually want to copy into VRChat, not the raw `/streams/.../index.m3u8` file path

## Legacy Local File Mode

[`run convertion.bat`](./run%20convertion.bat) still works for manual local testing:

- place files in `input/`
- converted output is written to `output/`
- the latest stream is copied into `html/` for nginx to serve

This path is now the legacy/manual mode. The browser and API-driven flows are the main path.

## Useful Scripts

- [`run stream server.bat`](./run%20stream%20server.bat): nginx + API + Cloudflare Tunnel
- [`run server.bat`](./run%20server.bat): nginx only
- [`run api.bat`](./run%20api.bat): FastAPI only
- [`run tunnel.bat`](./run%20tunnel.bat): Cloudflare Tunnel only
- [`run convertion.bat`](./run%20convertion.bat): local file to HLS conversion
- [`install_stream_bridge.bat`](./install_stream_bridge.bat): install Spicetify bridge
- [`clear_cache.bat`](./clear_cache.bat): wipe cached output and generated streams
- [`update yt-dlp.bat`](./update%20yt-dlp.bat): update `yt-dlp`
- [`get telegram session.bat`](./get%20telegram%20session.bat): launch Telegram QR login helper

## Troubleshooting

### Tunnel auto-detection does not work

- Make sure `cloudflared` is running
- Make sure `logs/cloudflared.log` is being written
- The browser extension and Spicetify bridge read the latest `https://*.trycloudflare.com` URL from that log through `/api/tunnel`
- If needed, set the public URL manually in the popup/settings UI

### YouTube downloads fail

- Confirm `node` is installed and available in `PATH`
- Update `yt-dlp` with [`update yt-dlp.bat`](./update%20yt-dlp.bat)
- Add `cookies.txt` for age-restricted or logged-in content

### Telegram export fails

- Verify `api/.env`
- Regenerate the Telegram session
- Test the session with `python auxillary/telethon_check.py`
- The image endpoint uses Telethon first and HTML parsing second; if both fail, the post is likely inaccessible from the current session

### Spotify Desktop export does not start

- Start nginx before opening Spotify so websocket proxying exists on port `8080`
- Confirm the Spicetify bridge was installed with [`install_stream_bridge.bat`](./install_stream_bridge.bat)
- Confirm VB-Cable device names match [`api/config.py`](./api/config.py)
- If Spotify audio stays routed incorrectly after a failure, use the `Restore audio output` button in the Spicetify settings modal

### SoundCloud private tracks fail

- Use the extension on the actual track page
- The new SoundCloud logic tries to capture the secret/private share URL instead of only the public permalink
- Keep `cookies.txt` available if the backend needs authenticated access

### Cache cleanup

- Use the Spotify `Clear cache` button for per-track resets
- Use [`clear_cache.bat`](./clear_cache.bat) to wipe generated media under `output/` and `html/streams/`

## Project Layout

```text
api/                 FastAPI app, routers, websocket RPC, segment engine
extension/           Browser extension for YouTube, SoundCloud, Telegram, Spotify Web, images
spotify_extension/   Spicetify bridge for Spotify Desktop
html/streams/        Generated HLS output served by nginx
input/               Manual local-file input for legacy conversion mode
output/              Temporary downloaded media and conversion artifacts
logs/                cloudflared log and runtime logs
```

## License

[MIT](./LICENSE)

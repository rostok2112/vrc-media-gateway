# VRChat Media Gateway

VRChat Media Gateway is a Windows-first toolkit for turning web, Telegram, Spotify, and local media into VRChat-friendly HLS streams. It includes a Chromium browser extension, a Spicetify bridge for Spotify Desktop, a FastAPI backend, websocket RPC for Spotify control, and an HLS segment engine for live Spotify capture.


## What It Includes

- Browser extension in [`extension/`](./extension) with:
  - YouTube watch-page button
  - SoundCloud track-page button
- Telegram Web context-menu export for images, stickers, animated GIF posts, videos, music tracks, voice messages, and post-text variants
- Spotify Web player buttons
- Generic image context-menu export on any site, with animated GIFs treated as motion video
- Generic audio context-menu export on any site when the page exposes a direct audio URL
- Generic video context-menu export on any site when the page exposes a direct video URL
- Quick-link popup for URLs, pasted local paths, and dropped/selected local files
- Long-running popup builds keep running in the extension background and resume when the popup is reopened
- Settings popup with local/public endpoint handling and browser HLS segment duration control
- Spotify Desktop bridge in [`spotify_extension/`](./spotify_extension) for Spicetify:
  - `VRChat` button
  - `Clear cache` button
  - `Settings` button
  - `Streaming settings` section for Spotify HLS prefetch tuning and optional cover/title/artist poster output
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
| SoundCloud | Browser button appears only on track pages, preserves secret/private links when possible, downloads with `yt-dlp`, then converts to poster-style HLS with artwork, title, performer, and duration when SoundCloud metadata exposes them |
| Telegram images and static stickers | Telegram endpoints classify the Telegram post first, then use Telethon for real Telegram photos and static `.webp` stickers, with HTML image parsing only as a photo fallback |
| Telegram videos, animated GIFs, and motion stickers | Telegram Web context menu and direct API calls can auto-detect videos, animated GIF posts, video stickers, and animated `.tgs` stickers, download them through Telethon, then convert them to HLS |
| Telegram music tracks and voice messages | Telegram Web context menu and direct API calls can auto-detect Telegram audio documents and voice notes, download them through Telethon, then convert them to poster-style audio HLS with cover/title/performer when Telegram exposes that metadata |
| Telegram post text | Telegram Web can export `VRChat with post text`, which renders the post text on a black panel below the media; pasted text-only `t.me/...` quick links also build a text-only HLS stream automatically |
| Generic images | Right-click any still image on most sites and export it to a static HLS video |
| Generic audio | Right-click a site audio element with a direct media URL and export it to poster-style HLS; embedded tags and cover art are used when the source file exposes them |
| Generic videos | Right-click a site video with a direct media URL and export it to HLS |
| Spotify Web | Injected buttons in `open.spotify.com` generate `/api/stream-spotify` links and allow cache clearing |
| Spotify Desktop | Spicetify bridge talks to the backend over websocket, routes Spotify audio into a virtual cable, and writes live HLS segments; optional poster mode shows the current track cover, title, artist, and duration |
| Local media in popup | The browser extension popup can build HLS links from selected or dropped local image/video/audio files, or from pasted absolute local paths when the API can see that file on the same machine; local audio exports also use embedded tags and cover art when available, and large files prefer direct filesystem handles when Chromium exposes them |
| Legacy local files | Manual flow through [`run convertion.bat`](./run%20convertion.bat) using files from `input/` |

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
pip install fastapi "uvicorn[standard]" requests python-dotenv telethon "qrcode[pil]" psutil winappaudiorouter pillow rlottie_python
```

`rlottie_python` is used for Telegram animated `.tgs` sticker export.

## Architecture

The normal runtime shape is:

1. Browser extension or Spicetify bridge triggers an `/api/...` endpoint, or a loopback-only `/local-api/...` endpoint for local file ingestion.
2. nginx listens on `http://127.0.0.1:8080`, proxies `/api/*` and `/api/ws/*` to FastAPI on `127.0.0.1:5000`, and serves `html/streams/`.
3. FastAPI downloads, uploads, or captures media and writes HLS output into `html/streams/<sid>/`.
4. VRChat receives the public HTTPS URL from Cloudflare Tunnel, or a local URL for testing.

Important port split:

- FastAPI listens on `127.0.0.1:5000`
- nginx listens on `127.0.0.1:8080`
- The browser extension and Spotify websocket bridge should point at `8080`, not `5000`
- Exception: the browser extension popup talks to `127.0.0.1:5000/local-api/*` directly for local file uploads and local filesystem paths, so those routes never go through nginx or the public tunnel

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
- local file choose/drop for image, video, and audio
- pasted absolute local filesystem paths such as `C:\media\clip.mp4` or `file:///C:/media/clip.mp4`
- `Clear all cache` button for wiping generated streams and temporary output from the local machine

`Use local API for processing` means:

- requests are sent to the local stack on `127.0.0.1:8080`
- the copied result still uses the public Cloudflare Tunnel URL

That mode is useful when the tunnel is public but you want all downloading and transcoding to happen locally.

Local media security model:

- local file uploads and local path builds use loopback-only FastAPI routes under `/local-api/*`
- those routes are intentionally not proxied by nginx and should not be exposed through the tunnel
- pasted local paths only work when FastAPI is running on the same machine and can read that path directly
- file picker and drag-and-drop prefer the browser File System Access handle when Chromium exposes it, and fall back to local upload when they cannot

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
- `Show track cover/title/artist` checkbox for poster-style Spotify output

The `VRChat` button appends those values to Spotify links as:

```text
/api/stream-spotify?url=<spotify-track-url>&segment_time=<seconds>&prefetch=<count>&show_info=<0|1>
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
- SoundCloud: injects only on real track pages, not artist/profile tabs; exported tracks now build poster-style audio HLS with artwork/title/performer when available
- Telegram Web: adds `VRChat` and `VRChat with post text` entries to the message context menu, auto-detects images/stickers/videos/animated GIF posts/music tracks/voice messages, and hides the plain `VRChat` action on text-only posts
- Spotify Web: adds `VRChat` and `Clear cache` buttons near the player controls
- Generic images: adds a `VRChat` item to the browser image context menu; animated GIF URLs are routed through the video exporter
- Generic audio: adds a `VRChat` item to the browser audio context menu for direct audio URLs and renders poster-style audio HLS when tags/cover art are available
- Generic videos: adds a `VRChat` item to the browser video context menu for direct video URLs
- Popup quick-link: can build ready links from remote URLs, selected/dropped local media, and pasted absolute local paths

Managed popup flows now wait for the stream to be ready, keep running if the popup closes, and usually copy the final `/streams/<sid>/index.m3u8` URL instead of a still-building `/api/stream-*` URL.

The browser extension settings popup now also includes a `Streaming settings` section with:

- segment duration in seconds for non-Spotify builds

That value is appended as `segment_time=<seconds>` for YouTube, SoundCloud, Telegram, generic image/audio/video, and local media builds. If it is omitted, the backend falls back to the default value from [`api/config.py`](./api/config.py) `HLS_OPTS`.

## API Overview

Main HTTP endpoints:

- `GET /api/stream-yt?url=<youtube-url>`
- `GET /api/stream-sc?url=<soundcloud-url>`
- `GET /api/stream-image?url=<image-url>&duration=300&width=1280&height=720`
- `GET /api/stream-audio?url=<direct-audio-url>&referer=<page-url>`
- `GET /api/stream-video?url=<direct-video-url>&referer=<page-url>`
- `GET /api/stream-tg-media?url=<telegram-post-url>`
- `GET /api/stream-tg-image?url=<telegram-post-url>`
- `GET /api/stream-tg-video?url=<telegram-post-url>`
- `GET /api/stream-tg-audio?url=<telegram-post-url>`
- `GET /api/tg-post-info?url=<telegram-post-url>`
- `GET /api/stream-spotify?url=<spotify-track-url>&segment_time=<seconds>&prefetch=<count>&show_info=<0|1>`
- `POST /api/stream-spotify-clear?url=<spotify-track-url>&segment_time=<seconds>&prefetch=<count>&show_info=<0|1>`
- `GET /api/tunnel`

Local-only endpoints:

- `POST /local-api/stream-local-path-build-start`
- `POST /local-api/stream-local-upload-build-start`
- `GET /local-api/stream-local-build-status?job_id=<job-id>`
- `POST /local-api/clear-cache-all`

Spotify-specific delivery endpoints:

- `GET /api/stream-spotify-playlist/{sid}`
- `GET /api/stream-spotify-segment/{sid}/{filename}`
- `WS /api/ws/spotify`

Behavior notes:

- Most VOD endpoints build the stream on first request and then serve cached HLS from `html/streams/<sid>/`
- Non-Spotify VOD endpoints can also accept `segment_time=<seconds>` and use that value in both HLS generation and cache keys
- Telegram media endpoints also accept `with_text=1` to render the Telegram post text on a black panel under the media; text-only Telegram posts can build a text-only HLS output through the same flow
- Telegram sticker posts are supported too: static `.webp` stickers go through the image path, while `video/webm` and animated `.tgs` stickers go through the motion/video path
- Telegram music tracks and voice messages are supported too: they go through the Telegram audio path and build poster-style audio HLS output with cover/title/performer when Telegram exposes that metadata
- SoundCloud, generic audio, and local audio exports also build poster-style audio HLS output with cover/title/performer when the source metadata exposes them
- Spotify is segment-driven and uses the websocket bridge for metadata, seeking, playback start, cache clearing, and audio restoration; `show_info=1` enables poster-style cover/title/artist output on the live Spotify stream
- Managed popup flows usually resolve to the final `/streams/<sid>/index.m3u8` link after the build is ready
- Local media ingestion uses `/local-api/*` only on loopback; the final playback URL is still served from `/streams/...`
- Animated GIFs are treated as motion media and end up on the video/HLS path instead of the still-image path

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

### Local media export fails

- Start the FastAPI backend locally; the popup needs direct access to `127.0.0.1:5000` for `/local-api/*`
- Pasted local paths must be absolute paths on the same machine as the backend
- Large picked or dropped files use filesystem handles when Chromium supports them; otherwise the popup falls back to local upload and that extra local copy can take time
- Only local image, video, and audio formats are accepted
- Animated GIFs are exported as motion video instead of a static image loop

### Cache cleanup

- Use the Spotify `Clear cache` button for per-track resets
- Use the popup `Clear all cache` button for a loopback-only wipe of generated streams and temporary output
- Use [`clear_cache.bat`](./clear_cache.bat) to wipe generated media under `output/` and `html/streams/`

## Project Layout

```text
api/                 FastAPI app, routers, websocket RPC, segment engine
extension/           Browser extension for YouTube, SoundCloud, Telegram, Spotify Web, images, and local media popup export
spotify_extension/   Spicetify bridge for Spotify Desktop
html/streams/        Generated HLS output served by nginx
input/               Manual local-file input for legacy conversion mode
output/              Temporary downloaded media, upload cache, and conversion artifacts
logs/                cloudflared log and runtime logs
```

## License

[MIT](./LICENSE)

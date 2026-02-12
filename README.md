# VRChat Media Gateway: Local/Internet Media → HLS Proxy

**Short:** turn local media, SoundCloud tracks or YouTube videos into stable HLS streams that are playable in VRChat and other strict HLS players. Exposes a public HTTPS URL via Cloudflare Tunnel.

---

## What this project does
- Converts media files to HLS (`.m3u8 + .ts`) using **ffmpeg**.
- Downloads SoundCloud tracks via **yt-dlp** and converts them to HLS.
- Downloads YouTube videos via **yt-dlp** and converts them to HLS.
- Serves HLS streams with **nginx**.
- Exposes nginx through **Cloudflare Tunnel** to get a free public HTTPS domain.
- Provides **FastAPI** endpoints to automate: download → convert → serve → cached HLS URL.

**Result:** you call a single API URL you get a playable HLS stream that works in VRChat.

---

## Quick demo flow
1. Call:
```
https://<your-domain>.trycloudflare.com/api/stream-sc?url=<soundcloud-link>
```
2. Server downloads track, converts to HLS, places files in `html/streams/<id>/`.
3. nginx serves `/streams/<id>/index.m3u8` publicly.
4. Insert the API URL (the one from step 1) into Popcorn Palace or VRChat - the stream will play (after conversion finishes on first request).

---

## Requirements
- ffmpeg (in PATH)
- yt-dlp (in PATH)
- nginx (in PATH)
- cloudflared (in PATH)
- Node.js (in PATH)
- Python 3.10+
- Python packages: `fastapi`, `uvicorn`

Install Python deps:
```bash
pip install fastapi uvicorn[standard] requests telethon dotenv qrcode[pil] psutil
```

Node.js check:

```
node -v
where node
```

If node is not found:

```
setx PATH "%PATH%;C:\Program Files\nodejs\"
```

Restart terminal after that.

---

## Security / cookies note
- To download age-restricted or account-only YouTube videos you may need `cookies.txt`. Export cookies locally (e.g. browser extension) and save as `cookies.txt` in the repo root. **Never commit this file.**
- This project is intended for **personal use**. Respect platform ToS and copyright.

---

## Usage and prepare and run
1. Put `ffmpeg`, `yt-dlp`, `cloudflared` and `Node.js` in your PATH.
2. Configure nginx (`main.conf`) - example config is provided in repo.
3. Create Cloudflare Tunnel and note the public domain.
4. Start services (example `run_stream_server.bat`):
5. Test locally:

```
http://127.0.0.1:8080/   # nginx root
http://127.0.0.1:5000/api/stream-file?name=your.mp4  # FastAPI local test
```

Telegram preparation:
- Copy `api/.env.sample` → `api/.env`
- In `api/.env` replace ONLY:

```
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
```

Optional (if you use 2FA):

```
TG_PASSWORD=your_password
```

Generate Telegram session (QR):

```
python auxillary/get_tg_session.py
```

Scan QR in Telegram mobile:
Settings → Devices → Scan QR

After success file appears:

```
api/tg_session.session
```

YouTube note:

- yt-dlp now requires JS challenge solving. Run yt-dlp with Node enabled:

```
yt-dlp --js-runtimes node --remote-components ejs:github ...
```

## Run

---

Convert file to HLS from `./input` and place corresponding `.html/index.m3u8` file for streaming by nginx:

```
./"run convertion.bat"
```

nginx:

```
./"run server.bat"
```

cloudflared:

```
./"run tunnel.bat"
```

Streaming API:

```
./"run api.bat"
```

Combined:

```
./"run streamer server.bat"
```

---

## API endpoints (examples)
- `GET /api/stream-sc?url=<url>` - download SoundCloud track, convert to HLS
- `GET /api/stream-yt?url=<url>` - download YouTube, convert to HLS
- `GET /api/stream-tg-image?url=<url>` - download an image from Telegram, creating static video and convert to HLS
- `GET /api/stream-tg-video?name=<url>` - download an video from Telegram, convert to HLS
- `GET /api/stream-stream_image?url=<url>` -  download an image, creating static video and convert to HLS

### Behavior
- On first request the server will download/convert - expect ~10-30s (depends on file size and network). The result is cached under `html/streams/<id>/` for subsequent instant access.
- The API returns a playable URL (served by nginx). For maximum compatibility with VRChat, use `X-Accel-Redirect` or return `200 OK` with `.m3u8` body (avoid plain `302` redirects).

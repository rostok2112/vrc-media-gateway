# VRChat Media Gateway: Local/Internet Media → HLS Proxy

**Short:** turn local media, SoundCloud tracks or YouTube videos into stable HLS streams that are playable in VRChat and other strict HLS players. Exposes a public HTTPS URL via Cloudflare Tunnel.

---

## What this project does
- Converts media files to HLS (`.m3u8 + .ts`) using **ffmpeg**.
- Downloads SoundCloud tracks via **yt-dlp** and converts them to HLS.
- Downloads YouTube videos (optionally using `cookies.txt`) via **yt-dlp** and converts to HLS.
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
- Python 3.10+
- Python packages: `fastapi`, `uvicorn`

Install Python deps:
```bash
pip install fastapi uvicorn
```

---

## Security / cookies note
- To download age-restricted or account-only YouTube videos you may need `cookies.txt`. Export cookies locally (e.g. browser extension) and save as `cookies.txt` in the repo root. **Never commit this file.**
- This project is intended for **personal use**. Respect platform ToS and copyright.

---

## Usage and prepare and run
1. Put `ffmpeg`, `yt-dlp`, `cloudflared` in your PATH.
2. Configure nginx (`main.conf`) - example config is provided in repo.
3. Create Cloudflare Tunnel and note the public domain.
4. Start services (example `run_stream_server.bat`):
5. Test locally:
```
http://127.0.0.1:8080/   # nginx root
http://127.0.0.1:5000/api/stream-file?name=your.mp4  # FastAPI local test
```

---

## API endpoints (examples)
- `GET /api/stream-sc?url=<soundcloud_url>` - download SoundCloud track, convert to HLS, return playable URL
- `GET /api/stream-yt?url=<youtube_url>` - download YouTube (uses cookies.txt if present), convert to HLS
- `GET /api/stream-file?name=<filename>` - convert a local file placed in `videos/`

### Behavior
- On first request the server will download/convert - expect ~10-30s (depends on file size and network). The result is cached under `html/streams/<id>/` for subsequent instant access.
- The API returns a playable URL (served by nginx). For maximum compatibility with VRChat, use `X-Accel-Redirect` or return `200 OK` with `.m3u8` body (avoid plain `302` redirects).

---

## Troubleshooting (quick)
If the player shows `invalid or incomplete stream source`:
1. `curl -v` the API URL and the final `/streams/<id>/index.m3u8` URL. Check status codes and Content-Type. The API URL should yield `200 OK` and serve the playlist, or use `X-Accel-Redirect` so nginx serves the playlist.
2. Check `Content-Type` for `.m3u8` - must be `application/vnd.apple.mpegurl` and `.ts` must be `video/mp2t`.
3. Ensure `index.m3u8` exists and first `.ts` file has non-zero size.
4. Test with `ffplay` locally:
```bash
ffplay -i http://127.0.0.1:8080/streams/<id>/index.m3u8
```
5. If you use Cloudflare Tunnel, try bypassing it for debugging (`http://127.0.0.1:8080/...`) to ensure CF cache or rules aren't interfering.

---

## Development notes
- Use `X-Accel-Redirect` in production for best compatibility and performance.

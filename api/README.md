\# VRChat Media Gateway: Local/Internet Media → HLS Proxy



\*\*Short:\*\* turn local media, SoundCloud tracks or YouTube videos into stable HLS streams that are playable in VRChat and other strict HLS players. Exposes a public HTTPS URL via Cloudflare Tunnel.



---



\## What this project does

\- Converts media files to HLS (`.m3u8 + .ts`) using \*\*ffmpeg\*\*.

\- Downloads SoundCloud tracks via \*\*yt-dlp\*\* and converts them to HLS.

\- Downloads YouTube videos (optionally using `cookies.txt`) via \*\*yt-dlp\*\* and converts to HLS.

\- Serves HLS streams with \*\*nginx\*\*.

\- Exposes nginx through \*\*Cloudflare Tunnel\*\* to get a free public HTTPS domain.

\- Provides \*\*FastAPI\*\* endpoints to automate: download → convert → serve → cached HLS URL.



\*\*Result:\*\* you call a single API URL you get a playable HLS stream that works in VRChat.



---



\## Quick demo flow

1\. Call:

```

https://<your-domain>.trycloudflare.com/api/stream-sc?url=<soundcloud-link>

```

2\. Server downloads track, converts to HLS, places files in `html/streams/<id>/`.

3\. nginx serves `/streams/<id>/index.m3u8` publicly.

4\. Insert the API URL (the one from step 1) into Popcorn Palace or VRChat - the stream will play (after conversion finishes on first request).



---



\## Requirements

\- ffmpeg (in PATH)

\- yt-dlp (in PATH)

\- nginx (in PATH)

\- cloudflared (in PATH)

\- Python 3.10+

\- Python packages: `fastapi`, `uvicorn`



Install Python deps:

```bash

pip install fastapi uvicorn requests telethon dotenv qrcode\[pil]

```



---



\## Security / cookies note

\- To download age-restricted or account-only YouTube videos you may need `cookies.txt`. Export cookies locally (e.g. browser extension) and save as `cookies.txt` in the repo root. \*\*Never commit this file.\*\*

\- This project is intended for \*\*personal use\*\*. Respect platform ToS and copyright.


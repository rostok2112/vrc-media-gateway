# API Notes

This folder contains the FastAPI backend for VRChat Media Gateway.

The backend is responsible for:

- media download and conversion for YouTube, SoundCloud, Telegram, and images
- websocket RPC for Spotify Desktop control
- live HLS segment generation for Spotify
- tunnel URL discovery from `logs/cloudflared.log`

## Runtime Model

- FastAPI listens on `127.0.0.1:5000`
- nginx on `127.0.0.1:8080` proxies `/api/*` and `/api/ws/*` to FastAPI
- generated HLS output is written to `../html/streams/`

For full-stack usage, start the project from the repository root with:

```powershell
.\run stream server.bat
```

## Main Endpoint Groups

- `/api/stream-yt`
- `/api/stream-sc`
- `/api/stream-image`
- `/api/stream-tg-image`
- `/api/stream-tg-video`
- `/api/stream-spotify`
- `/api/stream-spotify-clear`
- `/api/tunnel`
- `/api/ws/spotify`

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

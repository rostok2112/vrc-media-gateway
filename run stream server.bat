@echo off
chcp 65001 >nul

echo ===============================
echo VRChat Media Gateway
echo ===============================
echo.

REM nginx (как было)
start "nginx" cmd /k nginx -c main.conf

REM FastAPI (единственное изменение под новую структуру)
start "api" cmd /k uvicorn api.main:app --host 127.0.0.1 --port 5000

REM cloudflared QUICK TUNNEL (КАК БЫЛО)
start "cloudflared" cmd /k cloudflared tunnel --url http://127.0.0.1:8080

echo.
echo ===============================
echo Public API (after cloudflared starts):
echo.
echo SoundCloud:
echo https://XXXX.trycloudflare.com/api/stream-sc?url=https://on.soundcloud.com/XXXX
echo.
echo YouTube:
echo https://XXXX.trycloudflare.com/api/stream-yt?url=https://youtu.be/XXXX
echo.
echo Telegram image:
echo https://XXXX.trycloudflare.com/api/stream-tg-image?url=https://t.me/channel/123
echo ===============================
echo.
echo Telegram video:
echo https://XXXX.trycloudflare.com/api/stream-tg-video?url=https://t.me/channel/123
echo ===============================
echo.
echo Image:
echo https://XXXX.trycloudflare.com/api/stream-image?url=https://t.me/channel/123
echo ===============================
echo.

pause

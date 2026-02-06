@echo off
chcp 65001 >nul

REM FastAPI 
start "api" cmd /k uvicorn api.main:app --host 127.0.0.1 --port 5000

echo.
echo ===============================
echo Public API (after cloudflared starts):
echo.
echo SoundCloud:
echo http://127.0.0.1:5000/api/stream-sc?url=https://on.soundcloud.com/XXXX
echo.
echo YouTube:
echo http://127.0.0.1:5000/api/stream-yt?url=https://youtu.be/XXXX
echo.
echo Telegram image:
echo http://127.0.0.1:5000/api/stream-tg-image?url=https://t.me/channel/123
echo ===============================
echo.
echo Telegram video:
echo http://X127.0.0.1:5000/api/stream-tg-video?url=https://t.me/channel/123
echo ===============================
echo.
echo Image:
echo http://127.0.0.1:5000/api/stream-image?url=https://t.me/channel/123
echo ===============================
echo.

pause

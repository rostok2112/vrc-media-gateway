@echo off
cd /d "%~dp0"

echo ==================================
echo Launching SoundCloud HLS stack
echo ==================================

REM ---- NGINX ----
echo Starting NGINX...
taskkill /F /IM nginx.exe >nul 2>&1
start "NGINX" cmd /k nginx.exe -p "%cd%" -c main.conf

REM ---- FASTAPI ----
echo Starting FastAPI...
start "FastAPI API" cmd /k ^
cd /d "%cd%\api" ^& uvicorn app:app --host 127.0.0.1 --port 5000

REM ---- CLOUDFLARE ----
echo Starting Cloudflare Tunnel...
start "Cloudflare Tunnel" cmd /k cloudflared tunnel --url http://localhost:8080

echo.
echo ==================================
echo ALL SERVICES STARTED
echo ==================================
echo Use URL:
echo https://XXXX.trycloudflare.com/api/stream?url=SC_LINK
echo.
pause

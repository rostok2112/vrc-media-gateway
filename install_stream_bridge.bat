@echo off
setlocal enabledelayedexpansion

echo === Spicetify Stream Bridge installer ===

for /f "delims=" %%i in ('spicetify path userdata --bypass-admin') do (
    set USERDATA=%%i
)

set EXT_DIR=%USERDATA%\Extensions

echo Userdata: %USERDATA%
echo Extensions dir: %EXT_DIR%

if not exist "%EXT_DIR%" (
    echo ERROR: Extensions folder not found
    pause
    exit /b 1
)

echo Killing Spotify...
taskkill /IM spotify.exe /F >nul 2>&1

echo Copying stream_bridge.js...
copy /Y "%~dp0spotify_extension\stream_bridge.js" "%EXT_DIR%\stream_bridge.js"

if errorlevel 1 (
    echo ERROR: failed to copy extension
    pause
    exit /b 1
)

echo Enabling extension...
spicetify config extensions stream_bridge.js --bypass-admin

echo Rebuilding Spicetify (backup + apply)...
spicetify backup --bypass-admin
spicetify apply --force --bypass-admin

echo.
echo DONE.
echo Start Spotify.
pause
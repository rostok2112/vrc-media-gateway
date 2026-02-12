@echo off
setlocal enabledelayedexpansion

echo === Spicetify Stream Bridge installer ===

REM получаем путь к папке Extensions
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

REM копируем extension
echo Copying stream_bridge.js...
copy /Y "%~dp0spotify_extension\stream_bridge.js" "%EXT_DIR%\stream_bridge.js"

if errorlevel 1 (
    echo ERROR: failed to copy extension
    pause
    exit /b 1
)

REM включаем extension
echo Enabling extension...
spicetify config extensions stream_bridge.js --bypass-admin

REM применяем
echo Applying Spicetify...
spicetify apply --bypass-admin

echo.
echo DONE.
echo Restart Spotify if it is running.
pause
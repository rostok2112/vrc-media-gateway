@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set INPUT_DIR=input
set HTML_DIR=html
set CACHE_DIR=output

if not exist "%INPUT_DIR%" (
    echo [ERROR] input folder not found
    pause
    exit /b
)

if not exist "%HTML_DIR%" mkdir "%HTML_DIR%"
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"

echo Clearing html directory...
del /q "%HTML_DIR%\*.*" >nul 2>&1

for %%F in ("%INPUT_DIR%\*") do (

    set "INFILE=%%~fF"
    set "NAME=%%~nF"
    set "EXT=%%~xF"
    set "OUT_DIR=%CACHE_DIR%\%%~nF"

    echo.
    echo Processing: %%~nxF

    if not exist "!OUT_DIR!" mkdir "!OUT_DIR!"

    REM ===== AUDIO =====
    if /I "!EXT!"==".mp3" (
        set MODE=AUDIO
    ) else if /I "!EXT!"==".wav" (
        set MODE=AUDIO
    ) else if /I "!EXT!"==".flac" (
        set MODE=AUDIO
    ) else if /I "!EXT!"==".aac" (
        set MODE=AUDIO
    ) else if /I "!EXT!"==".ogg" (
        set MODE=AUDIO
    ) else (
        set MODE=VIDEO
    )

    if "!MODE!"=="AUDIO" (
        echo Audio detected
        ffmpeg -y -i "!INFILE!" ^
          -vn ^
          -c:a aac ^
          -b:a 256k ^
          -f hls ^
          -hls_time 4 ^
          -hls_list_size 0 ^
          -hls_playlist_type vod ^
          "!OUT_DIR!\index.m3u8"
    ) else (
        echo Video detected
        ffmpeg -y -i "!INFILE!" ^
          -c:v libx264 ^
          -preset fast ^
          -profile:v high ^
          -level 4.2 ^
          -pix_fmt yuv420p ^
          -b:v 12000k ^
          -maxrate 14000k ^
          -bufsize 28000k ^
          -g 60 ^
          -sc_threshold 0 ^
          -c:a aac ^
          -b:a 320k ^
          -f hls ^
          -hls_time 2 ^
          -hls_list_size 0 ^
          -hls_playlist_type vod ^
          "!OUT_DIR!\index.m3u8"
    )

    echo Updating active stream...
    del /q "%HTML_DIR%\*.*" >nul 2>&1
    xcopy "!OUT_DIR!\*" "%HTML_DIR%\" /E /I /Y >nul
)

echo.
echo DONE.
echo Active stream ready in html\
pause
